"""Peil-statuspaginasjabloon laden en renderen (ONTWERPTAAL §13.2; ADR-0009).

Het sjabloon is een **versie-gepinde kopie** (O-14) in ``design/ontwerptaal/statuspagina.html`` met als eerste regel
de herkomstkop ``<!-- Herkomst: Peil x.y.z · sha256:… · bron lvos04/ai-server-manager@<commit> … -->``. In de image
staat dezelfde kopie in ``appstore/ontwerptaal/`` (zie ``deploy/allinone/Dockerfile``). De kop wordt vóór het renderen
weggelaten, zodat de pagina met ``<!doctype html>`` begint; de sha256 wordt bij het laden gecontroleerd (CI faalt op
drift, ``tools/peil_kopie.py``; hier alleen een foutmelding in de log, de pagina blijft bruikbaar).

Plekken (gelijk aan de referentie-renderer ``bouw/sjabloon.mjs``): ``{{x}}`` = tekst, altijd HTML-escaped;
``<!--ALS:x-->…<!--/ALS:x-->`` tonen als ``x`` waar is; ``<!--NIET:x-->…`` tonen als ``x`` onwaar is;
``<!--ELK:lijst-->…<!--/ELK:lijst-->`` per item herhalen (item-velden over de paginavelden heen).
**Verschil met de referentie (bewust, veiliger):** één doorgang — een ingevulde waarde wordt nooit opnieuw als
sjabloon gelezen, dus een bronnaam als ``{{csrf_token}}`` blijft letterlijk tekst.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("appstore.peil")

SJABLOON = "statuspagina.html"
#: toon-waarden van pillen (vaste lijst uit het sjabloon); iconen ``#i-<toon>`` bestaan alleen voor deze
TONEN = frozenset({"gezond", "beperkt", "storing", "onbekend", "gate"})
#: melding-toon → icoon
MELDING_ICOON = {"fout": "storing", "info": "info"}

_KOP = re.compile(
    r"^<!-- Herkomst: Peil (?P<versie>\d+\.\d+\.\d+) · sha256:(?P<sha>[0-9a-f]{64}) · "
    r"bron (?P<repo>[\w.-]+/[\w.-]+)@(?P<commit>[0-9a-f]{40}) (?P<pad>\S+) · [^\n]*-->$"
)
_TOKEN = re.compile(r"<!--(ELK|ALS|NIET):(\w+)-->(.*?)<!--/\1:\2-->|\{\{(\w+)\}\}", re.S)
_ESC = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})


class SjabloonError(RuntimeError):
    """Het sjabloon ontbreekt of heeft geen geldige herkomstkop."""


@dataclass(frozen=True)
class Sjabloon:
    versie: str
    sha256: str
    bron: str  # repo@commit
    tekst: str  # zonder herkomstkop
    intact: bool  # sha256 van de tekst gelijk aan de kop


def kandidaten() -> list[Path]:
    """Waar de kopie staat: in de image naast het pakket, anders in de broncode (``design/ontwerptaal``)."""
    hier = Path(__file__).resolve()
    return [hier.parent / "ontwerptaal" / SJABLOON, hier.parents[4] / "design" / "ontwerptaal" / SJABLOON]


def lees(pad: Path) -> Sjabloon:
    data = pad.read_bytes()
    eerste, _, rest = data.partition(b"\n")
    m = _KOP.match(eerste.decode("utf-8", "replace"))
    if not m:
        raise SjabloonError(f"{pad}: herkomstkop ontbreekt")
    intact = hashlib.sha256(rest).hexdigest() == m["sha"]
    if not intact:
        log.error("Peil-sjabloon %s wijkt af van de herkomstkop (sha256); de kopie is gewijzigd", pad)
    return Sjabloon(m["versie"], m["sha"], f"{m['repo']}@{m['commit']}", rest.decode("utf-8"), intact)


@functools.cache
def sjabloon() -> Sjabloon:
    for pad in kandidaten():
        if pad.is_file():
            return lees(pad)
    raise SjabloonError("Peil-statuspaginasjabloon niet gevonden: " + ", ".join(map(str, kandidaten())))


def escape(value: object) -> str:
    return ("" if value is None else str(value)).translate(_ESC)


def render(tpl: str, data: Mapping[str, Any]) -> str:
    """Sjabloon invullen in één doorgang (zie moduledoc)."""

    def vervang(m: re.Match[str]) -> str:
        soort, sleutel, inhoud, veld = m.groups()
        if veld is not None:
            return escape(data.get(veld))
        if soort == "ELK":
            return "".join(render(inhoud, {**data, **item}) for item in data.get(sleutel) or [])
        if soort == "ALS":
            return render(inhoud, data) if data.get(sleutel) else ""
        return "" if data.get(sleutel) else render(inhoud, data)

    return _TOKEN.sub(vervang, tpl)
