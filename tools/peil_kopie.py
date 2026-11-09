#!/usr/bin/env python3
"""Versie-gepinde kopie van het Peil-statuspaginasjabloon bewaken (ONTWERPTAAL §13.2/§14, O-14).

De kopieën in ``design/ontwerptaal/`` zijn byte-gelijk aan de bron in ``lvos04/ai-server-manager`` met één extra
eerste regel, de **herkomstkop**::

    <!-- Herkomst: Peil 1.1.0 · sha256:<64 hex> · bron lvos04/ai-server-manager@<commit> design/ontwerptaal/… · … -->
    /*! Herkomst: Peil 1.1.0 · sha256:<64 hex> · bron lvos04/ai-server-manager@<commit> design/ontwerptaal/… · … */

De sha256 in de kop is die van het bronbestand (= alles na de eerste regel). ``HERKOMST.json`` pint dezelfde waarden.

Opdrachten (alleen standaardbibliotheek; draait zonder het pakket te installeren):

* ``controleer`` — offline: kop, inhoud, ``HERKOMST.json`` en de ingebedde CSS kloppen met elkaar (CI, elke run).
* ``vergelijk --bron <map van ai-server-manager>`` — tegen een lokale checkout van de bron.
* ``vergelijk --online`` — tegen GitHub: de gepinde commit moet exact gelijk zijn; op de bronbranch is dezelfde
  versie met andere inhoud **drift** (fout); een nieuwere versie daar is een waarschuwing (bewust bijwerken).
* ``bijwerken --bron <map> --commit <sha> [--ref <branch>]`` — kopieën en ``HERKOMST.json`` opnieuw schrijven.

Uitgang 0 = in orde, 1 = drift of fout, 2 = verkeerd gebruik.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAP = REPO / "design" / "ontwerptaal"
HERKOMST = MAP / "HERKOMST.json"
BESTANDEN = ("statuspagina.html", "statuspagina.css")
RAW = "https://raw.githubusercontent.com/{repo}/{ref}/{pad}"

KOP = re.compile(
    r"^(?:<!--|/\*!) Herkomst: Peil (?P<versie>\d+\.\d+\.\d+) · sha256:(?P<sha>[0-9a-f]{64}) · "
    r"bron (?P<repo>[\w.-]+/[\w.-]+)@(?P<commit>[0-9a-f]{40}) (?P<pad>\S+) · [^\n]*?(?:-->|\*/)$"
)
#: versie zoals de bron hem zelf noemt (eerste commentaar van het sjabloon, kop van de CSS)
BRONVERSIE = {
    "statuspagina.html": re.compile(r"<!-- Peil statuspagina-sjabloon (\d+\.\d+\.\d+) · "),
    "statuspagina.css": re.compile(r"^/\*! Peil statuspagina\.css (\d+\.\d+\.\d+) · "),
}


@dataclass(frozen=True)
class Kop:
    versie: str
    sha: str
    repo: str
    commit: str
    pad: str


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def splits(data: bytes) -> tuple[Kop | None, bytes]:
    """Herkomstkop en bronbytes van een kopie (de kop is precies de eerste regel)."""
    eerste, _, rest = data.partition(b"\n")
    m = KOP.match(eerste.decode("utf-8", "replace"))
    if not m:
        return None, data
    return Kop(**m.groupdict()), rest


def bronversie(naam: str, bron: bytes) -> str | None:
    m = BRONVERSIE[naam].search(bron.decode("utf-8", "replace")[:600])
    return m.group(1) if m else None


def maak_kop(naam: str, versie: str, digest: str, repo: str, commit: str, pad: str) -> str:
    tekst = (
        f"Herkomst: Peil {versie} · sha256:{digest} · bron {repo}@{commit} {pad} · "
        "versie-gepinde kopie, niet met de hand wijzigen (tools/peil_kopie.py)"
    )
    return f"<!-- {tekst} -->" if naam.endswith(".html") else f"/*! {tekst} */"


def lees_herkomst() -> dict[str, object]:
    data = json.loads(HERKOMST.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("HERKOMST.json is geen object")
    return data


def ingebedde_css(sjabloon: bytes) -> bytes | None:
    m = re.search(rb"<head>.*?<style>(.*?)</style>", sjabloon, re.S)  # niet het woord <style> in de kopcommentaar
    return m.group(1) if m else None


def controleer() -> list[str]:
    """Offline controle van de kopieën tegen hun eigen kop en ``HERKOMST.json``."""
    fouten: list[str] = []
    try:
        pin = lees_herkomst()
    except (OSError, ValueError) as exc:
        return [f"HERKOMST.json onleesbaar: {exc}"]
    versie, repo, commit = pin.get("versie"), pin.get("bron_repo"), pin.get("bron_commit")
    bestanden = pin.get("bestanden")
    if not isinstance(bestanden, dict) or set(bestanden) != set(BESTANDEN):
        return [f"HERKOMST.json moet precies {', '.join(BESTANDEN)} pinnen"]
    bronnen: dict[str, bytes] = {}
    for naam in BESTANDEN:
        regel = bestanden[naam]
        pad = MAP / naam
        if not pad.is_file():
            fouten.append(f"{naam}: kopie ontbreekt")
            continue
        kop, bron = splits(pad.read_bytes())
        if kop is None:
            fouten.append(f"{naam}: herkomstkop ontbreekt of heeft een onbekend formaat")
            continue
        bronnen[naam] = bron
        werkelijk = sha256(bron)
        if werkelijk != kop.sha:
            fouten.append(f"{naam}: inhoud gewijzigd (sha256 {werkelijk} ≠ kop {kop.sha})")
        if not isinstance(regel, dict) or regel.get("sha256") != kop.sha or regel.get("bron_pad") != kop.pad:
            fouten.append(f"{naam}: kop en HERKOMST.json verschillen (sha256/bron_pad)")
        if (kop.versie, kop.repo, kop.commit) != (versie, repo, commit):
            fouten.append(f"{naam}: kop ({kop.versie}, {kop.repo}@{kop.commit}) ≠ HERKOMST.json")
        if bronversie(naam, bron) != kop.versie:
            fouten.append(f"{naam}: versie in de bron ({bronversie(naam, bron)}) ≠ kop ({kop.versie})")
    if len(bronnen) == len(BESTANDEN):
        css = ingebedde_css(bronnen["statuspagina.html"])
        if css is None or css.strip() != bronnen["statuspagina.css"].strip():
            fouten.append("statuspagina.html: de ingebedde <style> is niet gelijk aan statuspagina.css")
    return fouten


def haal(url: str) -> bytes | None:
    """GET met 4 pogingen (2/4/8 s); ``None`` bij 404."""
    laatste: Exception | None = None
    for poging in range(4):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - vaste https-URL
                return bytes(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            laatste = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            laatste = exc
        time.sleep(2 ** (poging + 1))
    raise RuntimeError(f"{url}: niet op te halen ({laatste})")


def vergelijk(bron_map: Path | None, online: bool) -> tuple[list[str], list[str]]:
    """Kopie ↔ bron. Geeft (fouten, waarschuwingen)."""
    fouten, waarschuwingen = controleer(), []
    pin = lees_herkomst()
    bestanden = pin["bestanden"]
    assert isinstance(bestanden, dict)
    for naam in BESTANDEN:
        kop, kopie = splits((MAP / naam).read_bytes())
        if kop is None:
            continue
        if bron_map is not None:
            pad = bron_map / kop.pad
            if not pad.is_file():
                fouten.append(f"{naam}: bron {pad} ontbreekt")
                continue
            bron = pad.read_bytes()
            if sha256(bron) != sha256(kopie):
                v = bronversie(naam, bron)
                if v == kop.versie:
                    fouten.append(f"{naam}: DRIFT — bron is gewijzigd zonder nieuwe versie ({v})")
                else:
                    waarschuwingen.append(f"{naam}: bron heeft Peil {v}, kopie is {kop.versie} — bewust bijwerken")
        if online:
            gepind = haal(RAW.format(repo=kop.repo, ref=kop.commit, pad=kop.pad))
            if gepind is None:
                fouten.append(f"{naam}: gepinde commit {kop.commit} niet gevonden bij {kop.repo}")
            elif sha256(gepind) != kop.sha:
                fouten.append(f"{naam}: DRIFT — bron op {kop.commit[:12]} heeft sha256 {sha256(gepind)}")
            refs = [str(pin.get("bron_ref") or ""), "main"]
            for ref in [r for i, r in enumerate(refs) if r and r not in refs[:i]]:
                actueel = haal(RAW.format(repo=kop.repo, ref=ref, pad=kop.pad))
                if actueel is None:
                    continue
                v = bronversie(naam, actueel)
                if sha256(actueel) == kop.sha:
                    pass
                elif v == kop.versie:
                    fouten.append(f"{naam}: DRIFT — {ref} heeft andere inhoud onder dezelfde versie {v}")
                else:
                    waarschuwingen.append(f"{naam}: {ref} heeft Peil {v}, kopie is {kop.versie} — bewust bijwerken")
                break
            else:
                waarschuwingen.append(
                    f"{naam}: niet gevonden op {' of '.join(refs)}; alleen de gepinde commit gecontroleerd"
                )
    return fouten, waarschuwingen


def bijwerken(bron_map: Path, commit: str, ref: str | None, repo: str) -> list[str]:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        return ["--commit moet een volledige commit-sha (40 hex) zijn"]
    pin: dict[str, object] = {
        "ontwerptaal": "Peil",
        "versie": None,
        "bron_repo": repo,
        "bron_commit": commit,
        "bron_ref": ref,
        "bestanden": {},
        "toelichting": "Versie-gepinde kopie (ONTWERPTAAL §13.2/§14, O-14). Bijwerken alleen met tools/peil_kopie.py bijwerken.",
    }
    bestanden: dict[str, dict[str, str]] = {}
    versies = set()
    for naam in BESTANDEN:
        pad = f"design/ontwerptaal/{naam}"
        bron = (bron_map / pad).read_bytes()
        versie = bronversie(naam, bron)
        if versie is None:
            return [f"{naam}: geen Peil-versie gevonden in de bron"]
        versies.add(versie)
        digest = sha256(bron)
        (MAP / naam).write_bytes(maak_kop(naam, versie, digest, repo, commit, pad).encode() + b"\n" + bron)
        bestanden[naam] = {"bron_pad": pad, "sha256": digest}
    if len(versies) != 1:
        return [f"bronbestanden hebben verschillende versies: {sorted(versies)}"]
    pin["versie"] = versies.pop()
    pin["bestanden"] = bestanden
    HERKOMST.write_text(json.dumps(pin, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return controleer()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="opdracht", required=True)
    sub.add_parser("controleer", help="offline: kop, inhoud en HERKOMST.json")
    v = sub.add_parser("vergelijk", help="tegen de bron (lokale checkout en/of GitHub)")
    v.add_argument("--bron", type=Path, help="map van een checkout van ai-server-manager")
    v.add_argument("--online", action="store_true", help="tegen GitHub (gepinde commit en bronbranch)")
    b = sub.add_parser("bijwerken", help="kopieën opnieuw schrijven uit een checkout")
    b.add_argument("--bron", type=Path, required=True)
    b.add_argument("--commit", required=True)
    b.add_argument("--ref")
    b.add_argument("--repo", default="lvos04/ai-server-manager")
    args = p.parse_args(argv)
    waarschuwingen: list[str] = []
    if args.opdracht == "controleer":
        fouten = controleer()
    elif args.opdracht == "vergelijk":
        if args.bron is None and not args.online:
            p.error("geef --bron en/of --online")
        fouten, waarschuwingen = vergelijk(args.bron, args.online)
    else:
        fouten = bijwerken(args.bron, args.commit, args.ref, args.repo)
    for w in waarschuwingen:
        print(f"::warning::Peil-kopie: {w}")
    for f in fouten:
        print(f"::error::Peil-kopie: {f}")
    if not fouten:
        pin = lees_herkomst()
        print(f"Peil-kopie in orde: versie {pin['versie']} · {pin['bron_repo']}@{str(pin['bron_commit'])[:12]}")
    return 1 if fouten else 0


if __name__ == "__main__":
    sys.exit(main())
