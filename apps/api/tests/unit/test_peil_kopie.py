"""Versie-gepinde kopie van het Peil-statuspaginasjabloon (O-14, ONTWERPTAAL §13.2/§14) en de renderer.

* De kopie in ``design/ontwerptaal/`` is byte-gelijk aan de bron (sha256 in de herkomstkop en in ``HERKOMST.json``);
  elke wijziging aan de kopie, de kop of de pin faalt hier (drift). De vergelijking met ai-server-manager zelf draait in
  CI online (``tools/peil_kopie.py vergelijk --online``) en hier lokaal als de checkout ernaast staat.
* De renderer geeft voor de vier referentieprototypes van Peil 1.1.1 exact dezelfde bytes als de referentie-renderers
  ``bouw/sjabloon.mjs``/``bouw/sjabloon.py`` (sha256 van ``prototypes/statuspagina*.html`` op de gepinde commit), en is
  één doorgang (E07): een waarde wordt nooit opnieuw als sjabloon gelezen.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

from appstore import peil
from tests.conftest import FIXTURES, REPO_ROOT

MAP = REPO_ROOT / "design" / "ontwerptaal"
BRON = REPO_ROOT.parent / "ai-server-manager"
VERSIE = "1.1.1"
COMMIT = "0a7143bc0a94ed5317a9655fa785e73a1fb07ba1"
#: sha256 van ai-server-manager design/ontwerptaal/prototypes/statuspagina*.html @0a7143b (Peil 1.1.1); de gegevens
#: staan in tests/fixtures/peil/prototype-gegevens.json (DATA uit bouw/pagina-statuspagina.mjs)
PROTOTYPES = {
    "statuspagina": "c4c0059bf2e1221a70c1d5f8bf433f4eb4d6fce70bbd0804e7755b2f2596055c",
    "statuspagina-gekoppeld": "b790e598ebedbf594547945c59e385028d17ecfadb316f86f8342a8d5e3427fe",
    "statuspagina-onleesbaar": "4e1bf79e9c7d51ca807c13a19d3edfb62a4dec5a784a1551e0c3fd933bd47108",
    "statuspagina-bezig": "f49e6af34dc32c12fa20c41cd6da5227074146a300d99572bae1466253ec70c7",
}


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("peil_kopie", REPO_ROOT / "tools" / "peil_kopie.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["peil_kopie"] = mod  # nodig voor @dataclass
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Het hulpmiddel op een kopie van ``design/ontwerptaal`` (zodat de test hem mag beschadigen)."""
    mod = _tool()
    kopie = tmp_path / "ontwerptaal"
    shutil.copytree(MAP, kopie)
    monkeypatch.setattr(mod, "MAP", kopie)
    monkeypatch.setattr(mod, "HERKOMST", kopie / "HERKOMST.json")
    return mod


def test_copy_is_pinned_version_1_1_1_and_intact() -> None:
    assert _tool().controleer() == []
    pin = json.loads((MAP / "HERKOMST.json").read_text(encoding="utf-8"))
    assert pin["versie"] == VERSIE and pin["bron_repo"] == "lvos04/ai-server-manager"
    assert pin["bron_commit"] == COMMIT
    for naam in ("statuspagina.html", "statuspagina.css"):
        kop, _, rest = (MAP / naam).read_bytes().partition(b"\n")
        assert f"Herkomst: Peil {VERSIE} · sha256:".encode() + pin["bestanden"][naam]["sha256"].encode() in kop
        assert hashlib.sha256(rest).hexdigest() == pin["bestanden"][naam]["sha256"]


@pytest.mark.parametrize(
    ("bestand", "oud", "nieuw", "melding"),
    [
        ("statuspagina.html", b"Koppelen met de Hub</button>", b"Koppelen</button>", "inhoud gewijzigd"),
        ("statuspagina.css", b"--down:#B3141B", b"--down:#FF0000", "inhoud gewijzigd"),
        ("statuspagina.html", b"Herkomst: Peil 1.1.1", b"Herkomst: Peil 1.2.0", "HERKOMST.json"),
        ("statuspagina.css", b"/*! Herkomst: Peil", b"/*! Peil", "herkomstkop ontbreekt"),
        ("HERKOMST.json", b'"versie": "1.1.1"', b'"versie": "1.1.0"', "HERKOMST.json"),
    ],
)
def test_drift_in_copy_header_or_pin_fails(
    tool: ModuleType, bestand: str, oud: bytes, nieuw: bytes, melding: str
) -> None:
    pad = tool.MAP / bestand
    data = pad.read_bytes()
    assert oud in data
    pad.write_bytes(data.replace(oud, nieuw, 1))
    fouten = tool.controleer()
    assert fouten and any(melding in f for f in fouten), fouten


def test_drift_detected_when_header_and_pin_are_updated_together(tool: ModuleType) -> None:
    """Een lokale wijziging mét bijgewerkte sha256 in kop en pin valt alsnog op: de ingebedde CSS en de CSS-kopie
    lopen uiteen, en tegen de bron (lokaal of online) is het drift."""
    pad = tool.MAP / "statuspagina.css"
    kop, _, rest = pad.read_bytes().partition(b"\n")
    nieuw = rest.replace(b"--down:#B3141B", b"--down:#FF0000")
    oud_sha, nieuw_sha = hashlib.sha256(rest).hexdigest(), hashlib.sha256(nieuw).hexdigest()
    pad.write_bytes(kop.replace(oud_sha.encode(), nieuw_sha.encode()) + b"\n" + nieuw)
    pin = tool.HERKOMST.read_text(encoding="utf-8").replace(oud_sha, nieuw_sha)
    tool.HERKOMST.write_text(pin, encoding="utf-8")
    assert any("ingebedde <style>" in f for f in tool.controleer())


@pytest.mark.skipif(
    not (BRON / "design" / "ontwerptaal").is_dir(), reason="geen checkout van ai-server-manager ernaast"
)
def test_copy_equals_local_source_checkout() -> None:
    fouten, waarschuwingen = _tool().vergelijk(BRON, online=False)
    assert fouten == [], fouten
    for w in waarschuwingen:  # een nieuwere Peil-versie in de bron is geen drift, wel een signaal
        print(w)


def test_runtime_uses_the_pinned_copy_without_header() -> None:
    s = peil.sjabloon()
    pin = json.loads((MAP / "HERKOMST.json").read_text(encoding="utf-8"))
    assert s.intact and s.versie == pin["versie"] == VERSIE
    assert s.sha256 == pin["bestanden"]["statuspagina.html"]["sha256"]
    assert s.bron == f"{pin['bron_repo']}@{pin['bron_commit']}"
    assert s.tekst.startswith(f"<!doctype html>\n<!-- Peil statuspagina-sjabloon {VERSIE}")
    assert "Herkomst:" not in s.tekst


def test_tampered_copy_is_reported(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    kopie = tmp_path / "statuspagina.html"
    kopie.write_bytes((MAP / "statuspagina.html").read_bytes().replace(b"<h1>", b"<h1 class=x>"))
    assert peil.lees(kopie).intact is False
    assert "wijkt af van de herkomstkop" in caplog.text
    (tmp_path / "zonder-kop.html").write_bytes(b"<!doctype html>\n")
    with pytest.raises(peil.SjabloonError):
        peil.lees(tmp_path / "zonder-kop.html")


def test_template_is_framework_free_without_javascript_or_external_sources() -> None:
    tekst = peil.sjabloon().tekst
    lower = tekst.lower()
    assert "<script" not in lower and "javascript:" not in lower and "onclick=" not in lower
    assert "<link" not in lower and "<img" not in lower and "@import" not in lower and "url(" not in lower
    assert "https://" not in lower and "http://" not in tekst.replace("http(s)://", "")


@pytest.mark.parametrize("naam", sorted(PROTOTYPES))
def test_renderer_equals_reference_renderer_on_peil_prototypes(naam: str) -> None:
    gegevens = json.loads((FIXTURES / "peil" / "prototype-gegevens.json").read_text(encoding="utf-8"))[naam]
    html = peil.render(peil.sjabloon().tekst, gegevens)
    assert hashlib.sha256(html.encode("utf-8")).hexdigest() == PROTOTYPES[naam]


def test_renderer_blocks_escaping_and_single_pass() -> None:
    tpl = (
        "<p>{{a}}</p><!--ALS:ja-->ja<!--/ALS:ja--><!--ALS:nee-->nee<!--/ALS:nee--><!--NIET:nee-->niet<!--/NIET:nee-->"
        "<!--ELK:lijst--><i>{{n}}<!--ALS:r-->({{r}})<!--/ALS:r--></i><!--/ELK:lijst-->{{geheim}}"
    )
    out = peil.render(
        tpl,
        {
            "a": "<b>&\"'</b>",
            "ja": True,
            "nee": "",
            "lijst": [{"n": "x", "r": "r1"}, {"n": "{{geheim}}", "r": None}],
            "geheim": "G",
        },
    )
    assert out == "<p>&lt;b&gt;&amp;&quot;&#39;&lt;/b&gt;</p>janiet<i>x(r1)</i><i>{{geheim}}</i>G"


def test_renderer_nested_blocks_empty_list_and_booleans() -> None:
    """1.1.1 (E07): geneste blokken (ook dezelfde naam en NIET binnen ALS), lege lijst = onwaar, bool als in JS."""
    assert peil.render("<!--ALS:a--><!--ALS:a-->x<!--/ALS:a--><!--/ALS:a-->", {"a": 1}) == "x"
    assert peil.render("<!--ALS:l-->vol<!--/ALS:l--><!--NIET:l-->leeg<!--/NIET:l-->", {"l": []}) == "leeg"
    tpl = "<!--ALS:f--><!--NIET:b-->form<!--/NIET:b--><!--/ALS:f-->"
    assert peil.render(tpl, {"f": True, "b": False}) == "form"
    assert peil.render(tpl, {"f": True, "b": True}) == ""
    assert peil.render("{{t}}/{{f}}/{{n}}/{{z}}", {"t": True, "f": False, "n": None, "z": 0}) == "true/false//0"


@pytest.mark.parametrize(
    ("tpl", "melding"),
    [
        ("<!--ALS:a-->x", "niet gesloten"),
        ("<!--ALS:a--><!--NIET:b--><!--/ALS:a--><!--/NIET:b-->", "onverwachte sluiting"),
        ("x<!--/ELK:l-->", "onverwachte sluiting"),
    ],
)
def test_template_errors_are_reported_not_skipped(tpl: str, melding: str) -> None:
    with pytest.raises(peil.SjabloonError, match=melding):
        peil.render(tpl, {})


@pytest.mark.skipif(
    not (BRON / "design" / "ontwerptaal" / "bouw" / "sjabloon.py").is_file(),
    reason="geen checkout van ai-server-manager ernaast",
)
def test_renderer_equals_python_reference_of_peil() -> None:
    """Byte-gelijk met ``bouw/sjabloon.py`` van de bron, ook met accolades en tekens in waarden."""
    spec = importlib.util.spec_from_file_location(
        "peil_sjabloon", BRON / "design" / "ontwerptaal" / "bouw" / "sjabloon.py"
    )
    assert spec and spec.loader
    ref = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ref)
    tekst = peil.sjabloon().tekst
    alle = json.loads((FIXTURES / "peil" / "prototype-gegevens.json").read_text(encoding="utf-8"))
    extra = {
        **alle["statuspagina"],
        "checks": [{"naam": "{{csrf_token}} & <b>\"'", "toon": "onbekend", "label": "Onbekend", "tijd": "", "iso": ""}],
    }
    for data in [*alle.values(), extra]:
        assert peil.render(tekst, data) == ref.render(tekst, data)


def test_renderer_cannot_be_broken_out_of_by_values() -> None:
    out = peil.render('<a href="{{u}}">{{t}}</a><!--x {{c}} -->', {"u": '" onx="1', "t": "</a><script>", "c": "-->"})
    assert out == '<a href="&quot; onx=&quot;1">&lt;/a&gt;&lt;script&gt;</a><!--x --&gt; -->'
