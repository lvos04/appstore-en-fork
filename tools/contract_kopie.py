#!/usr/bin/env python3
"""Byte-gelijke kopieën van het platformcontract bewaken (contract 1.3, kop; C1.3-ALG-01).

``docs/platform-v2/PLATFORM_CONTRACTEN.md`` en ``CONTRACT_1.3_CONFORMITEIT.md`` zijn kopieën van
``lvos04/ai-server-manager`` op een vaste bron-commit (``docs/platform-v2/CONTRACT_HERKOMST.json``).

* ``controleer`` — offline: sha256 van elke kopie gelijk aan de pin (ook in de unit-tests).
* ``vergelijk --online`` — tegen GitHub: de bron op de gepinde commit heeft exact die sha256 (fout bij verschil). Een
  nieuwere versie op de bronbranch is alleen een waarschuwing: een nieuwe kopie overnemen is een bewuste stap.
* ``vergelijk --bron <map>`` — tegen een lokale checkout van de bron (zelfde regels als online).

Alleen standaardbibliotheek. Uitgang 0 = in orde, 1 = drift of fout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs" / "platform-v2"
HERKOMST = DOCS / "CONTRACT_HERKOMST.json"
RAW = "https://raw.githubusercontent.com/{repo}/{ref}/{pad}"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pin() -> dict[str, object]:
    data = json.loads(HERKOMST.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("bestanden"), dict):
        raise ValueError("CONTRACT_HERKOMST.json heeft niet de verwachte vorm")
    return data


def controleer() -> list[str]:
    fouten: list[str] = []
    bestanden = pin()["bestanden"]
    assert isinstance(bestanden, dict)
    for naam, regel in bestanden.items():
        pad = DOCS / naam
        if not pad.is_file():
            fouten.append(f"{naam}: kopie ontbreekt")
            continue
        werkelijk = sha256(pad.read_bytes())
        if werkelijk != regel["sha256"]:
            fouten.append(f"{naam}: sha256 {werkelijk} ≠ pin {regel['sha256']} (nooit lokaal aanpassen)")
    return fouten


def haal(url: str) -> bytes | None:
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


def _git_show(bron: Path, ref: str, pad: str) -> bytes | None:
    try:
        git = shutil.which("git")
        if git is None:
            return None
        return subprocess.run(  # noqa: S603 - vaste argumenten, geen shell
            [git, "-C", str(bron), "show", f"{ref}:{pad}"], check=True, capture_output=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def vergelijk(bron: Path | None, online: bool) -> tuple[list[str], list[str]]:
    fouten, waarschuwingen = controleer(), []
    p = pin()
    repo, commit, ref = str(p["bron_repo"]), str(p["bron_commit"]), str(p.get("bron_ref") or "")
    bestanden = p["bestanden"]
    assert isinstance(bestanden, dict)
    for naam, regel in bestanden.items():
        pad = str(regel["bron_pad"])
        bronnen: list[tuple[str, bytes | None, bytes | None]] = []
        if online:
            bronnen.append(("GitHub", haal(RAW.format(repo=repo, ref=commit, pad=pad)), None))
            if ref:
                bronnen[-1] = ("GitHub", bronnen[-1][1], haal(RAW.format(repo=repo, ref=ref, pad=pad)))
        if bron is not None:
            bronnen.append(
                (str(bron), _git_show(bron, commit, pad), (bron / pad).read_bytes() if (bron / pad).is_file() else None)
            )
        for waar, gepind, actueel in bronnen:
            if gepind is None:
                fouten.append(f"{naam}: gepinde commit {commit[:12]} niet gevonden in {waar}")
            elif sha256(gepind) != regel["sha256"]:
                fouten.append(f"{naam}: DRIFT — {waar}@{commit[:12]} heeft sha256 {sha256(gepind)}")
            if actueel is not None and sha256(actueel) != regel["sha256"]:
                waarschuwingen.append(
                    f"{naam}: de bron ({waar}, werkversie) is nieuwer dan de kopie — bewust overnemen"
                )
    return fouten, waarschuwingen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="opdracht", required=True)
    sub.add_parser("controleer")
    v = sub.add_parser("vergelijk")
    v.add_argument("--bron", type=Path)
    v.add_argument("--online", action="store_true")
    args = parser.parse_args(argv)
    waarschuwingen: list[str] = []
    if args.opdracht == "controleer":
        fouten = controleer()
    else:
        if args.bron is None and not args.online:
            parser.error("geef --bron en/of --online")
        fouten, waarschuwingen = vergelijk(args.bron, args.online)
    for w in waarschuwingen:
        print(f"::warning::Contractkopie: {w}")
    for f in fouten:
        print(f"::error::Contractkopie: {f}")
    if not fouten:
        print(f"Contractkopie in orde: {pin()['contract']} · {pin()['bron_repo']}@{str(pin()['bron_commit'])[:12]}")
    return 1 if fouten else 0


if __name__ == "__main__":
    sys.exit(main())
