"""Contract 1.3 §5 (A20, C1.3-ASF-11): elke muterende route vraagt een ``Idempotency-Key``, behalve de uitzonderingen
van §5 — elk met reden en bron.

Statische controle over alle routers: een nieuwe POST/PUT/PATCH/DELETE-route die ``run_idempotent`` niet aanroept,
laat deze test falen, tenzij hij hieronder met reden en bron (contract 1.3 §5 of ADR-0013) is uitgezonderd. Een
uitzondering die niet meer bestaat, faalt ook (de lijst veroudert niet stil). Let op §4.6: ook zonder
``Idempotency-Key`` verbruikt een POST de ``jti``.
"""

from __future__ import annotations

import ast
from pathlib import Path

API = Path(__file__).resolve().parents[2] / "src" / "appstore" / "api"

EXEMPT: dict[str, str] = {
    # formulier-POST van de statuspagina (RV10): een browserformulier kan geen header meesturen; bescherming via het
    # formuliertoken, de herkomstcontrole, de pogingenlimiet, koppelen_bezig en de eenmaligheid van de koppelcode
    "status_page.py:pair": "formulier-POST van een statuspagina (contract 1.3 §5, §3.7)",
    # connector: niet gebouwd → altijd 501 capability_unavailable, er wordt niets uitgevoerd
    "connector.py:propose": "zolang 501 capability_unavailable (contract 1.3 §5, §6)",
    "connector.py:apply": "zolang 501 capability_unavailable (contract 1.3 §5, §6)",
    "connector.py:rollback": "zolang 501 capability_unavailable (contract 1.3 §5, §6)",
    # rekenroutes met een body (manifest): geen bijwerking behalve een bewijsrecord, zoals …/evaluate en …/verify
    "catalog.py:validate": "geen bijwerking behalve bewijs, zoals …/verify (contract 1.3 §5; ADR-0013)",
    "catalog.py:preview": "geen bijwerking behalve bewijs, zoals …/evaluate (contract 1.3 §5; ADR-0013)",
    "catalog.py:import_preview": "geen bijwerking behalve bewijs, zoals …/evaluate (contract 1.3 §5; ADR-0013)",
}
MUTATING = ("post", "put", "patch", "delete")


def _mutating_routes() -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    out: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for f in sorted(API.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if any(
                isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in MUTATING
                for d in n.decorator_list
            ):
                out.append((f"{f.name}:{n.name}", n))
    return out


def _calls(fn: ast.AST) -> set[str]:
    names: set[str] = set()
    for c in ast.walk(fn):
        if isinstance(c, ast.Call):
            func = c.func
            names.add(func.id if isinstance(func, ast.Name) else getattr(func, "attr", ""))
    return names


def test_every_mutation_requires_an_idempotency_key_except_the_listed_exceptions() -> None:
    routes = _mutating_routes()
    assert len(routes) >= 15, "te weinig routes gevonden: klopt het pad naar de routers nog?"
    missing = [name for name, fn in routes if name not in EXEMPT and "run_idempotent" not in _calls(fn)]
    assert not missing, f"muterende routes zonder Idempotency-Key (of zonder reden in EXEMPT): {missing}"


def test_exceptions_exist_and_name_their_source() -> None:
    names = {name for name, _ in _mutating_routes()}
    stale = sorted(set(EXEMPT) - names)
    assert not stale, f"uitzonderingen voor routes die niet (meer) bestaan: {stale}"
    for name, reason in EXEMPT.items():
        assert "contract 1.3 §5" in reason, name
        if "§3.7" not in reason and "§6" not in reason:
            assert "ADR-0013" in reason, f"{name}: een uitzondering buiten de lijst van §5 vraagt een ADR"


def test_the_check_fails_on_a_new_route_without_key() -> None:
    """Negatief: een (nagebootste) nieuwe POST-route zonder run_idempotent valt op."""
    src = (
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
        "@router.post('/nieuw')\ndef nieuw_ding() -> None:\n    doe_iets()\n"
    )
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef))
    assert "run_idempotent" not in _calls(fn)
    assert "x.py:nieuw_ding" not in EXEMPT
