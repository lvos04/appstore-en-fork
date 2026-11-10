"""Peil-statuspaginasjabloon laden en renderen (ONTWERPTAAL §13.2; ADR-0009).

Het sjabloon is een **versie-gepinde kopie** (O-14) in ``design/ontwerptaal/statuspagina.html`` met als eerste regel
de herkomstkop ``<!-- Herkomst: Peil x.y.z · sha256:… · bron lvos04/ai-server-manager@<commit> … -->``. In de image
staat dezelfde kopie in ``appstore/ontwerptaal/`` (zie ``deploy/allinone/Dockerfile``). De kop wordt vóór het renderen
weggelaten, zodat de pagina met ``<!doctype html>`` begint; de sha256 wordt bij het laden gecontroleerd (CI faalt op
drift, ``tools/peil_kopie.py``; hier alleen een foutmelding in de log, de pagina blijft bruikbaar).

Plekken (Peil 1.1.1, ONTWERPTAAL §13.2, gelijk aan de referentie ``bouw/sjabloon.py``/``bouw/sjabloon.mjs``):
``{{x}}`` = tekst, altijd HTML-escaped; ``<!--ALS:x-->…<!--/ALS:x-->`` tonen als ``x`` waar is;
``<!--NIET:x-->…<!--/NIET:x-->`` tonen als ``x`` onwaar is; ``<!--ELK:lijst-->…<!--/ELK:lijst-->`` per item herhalen
(item-velden over de paginavelden heen). Blokken mogen genest zijn (ook met dezelfde naam). **Eén doorgang** (E07): het
sjabloon wordt eerst in een boom gelezen en daarna ingevuld; een ingevulde waarde wordt nooit opnieuw als sjabloon
gelezen. Waar = niet leeg (``False``, ``None``, ``''``, ``0`` en een lege lijst zijn onwaar); ``True``/``False`` worden
``true``/``false`` zoals in JavaScript. Een niet-gesloten of verkeerd genest blok is een :class:`SjabloonError`.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("appstore.peil")

SJABLOON = "statuspagina.html"
#: toon-waarden van pillen (vaste lijst uit het sjabloon 1.1.1, E03/E04); het symbool heet altijd ``#i-<toon>``
TONEN = frozenset(
    {
        "gezond",
        "beperkt",
        "storing",
        "onbekend",
        "verouderd",
        "niet-beschikbaar",
        "nog-niet",
        "niet-gekoppeld",
        "gate",
        "beleid",
        "feit",
    }
)
#: tonen die het totaal mogen bepalen (``totaal_toon``)
TOTAAL_TONEN = frozenset({"gezond", "beperkt", "storing", "onbekend"})
#: ``melding_rol`` (E05): ``alert`` = fout van dít verzoek, ``status`` = info na dít verzoek, leeg = bestaande toestand
MELDING_ROLLEN = frozenset({"alert", "status", ""})
#: melding-toon → icoon
MELDING_ICOON = {"fout": "storing", "info": "info"}

_KOP = re.compile(
    r"^<!-- Herkomst: Peil (?P<versie>\d+\.\d+\.\d+) · sha256:(?P<sha>[0-9a-f]{64}) · "
    r"bron (?P<repo>[\w.-]+/[\w.-]+)@(?P<commit>[0-9a-f]{40}) (?P<pad>\S+) · [^\n]*-->$"
)
_TOKEN = re.compile(r"<!--(/?)(ELK|ALS|NIET):(\w+)-->|\{\{(\w+)\}\}")
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
    if value is None:
        tekst = ""
    elif isinstance(value, bool):
        tekst = "true" if value else "false"
    else:
        tekst = str(value)
    return tekst.translate(_ESC)


@dataclass
class _Blok:
    soort: str
    naam: str
    kinderen: list[_Knoop] = field(default_factory=list)


@dataclass(frozen=True)
class _Plek:
    naam: str


_Knoop = str | _Plek | _Blok


def lees_boom(tpl: str) -> list[_Knoop]:
    """Lees het sjabloon in een boom (één keer); :class:`SjabloonError` bij een niet-gesloten of verkeerd genest blok."""
    wortel = _Blok("", "")
    stapel = [wortel]
    pos = 0
    for m in _TOKEN.finditer(tpl):
        top = stapel[-1]
        if m.start() > pos:
            top.kinderen.append(tpl[pos : m.start()])
        pos = m.end()
        sluit, soort, naam, plek = m.groups()
        if plek is not None:
            top.kinderen.append(_Plek(plek))
        elif not sluit:
            blok = _Blok(soort, naam)
            top.kinderen.append(blok)
            stapel.append(blok)
        else:
            if len(stapel) == 1 or top.soort != soort or top.naam != naam:
                raise SjabloonError(f"sjabloon: onverwachte sluiting {m.group(0)}")
            stapel.pop()
    if len(stapel) != 1:
        raise SjabloonError(f"sjabloon: blok niet gesloten: {stapel[-1].soort}:{stapel[-1].naam}")
    if pos < len(tpl):
        wortel.kinderen.append(tpl[pos:])
    return wortel.kinderen


def _schrijf(knopen: list[_Knoop], data: Mapping[str, Any], uit: list[str]) -> None:
    for k in knopen:
        if isinstance(k, str):
            uit.append(k)
        elif isinstance(k, _Plek):
            uit.append(escape(data.get(k.naam)))
        elif k.soort == "ELK":
            for item in data.get(k.naam) or []:
                _schrijf(k.kinderen, {**data, **item}, uit)
        elif k.soort == "ALS":
            if data.get(k.naam):
                _schrijf(k.kinderen, data, uit)
        elif not data.get(k.naam):  # NIET
            _schrijf(k.kinderen, data, uit)


@functools.lru_cache(maxsize=8)
def _boom(tpl: str) -> list[_Knoop]:
    return lees_boom(tpl)


def render(tpl: str, data: Mapping[str, Any]) -> str:
    """Sjabloon invullen in één doorgang (zie moduledoc)."""
    uit: list[str] = []
    _schrijf(_boom(tpl), data, uit)
    return "".join(uit)
