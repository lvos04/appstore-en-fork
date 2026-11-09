"""Adoptiemanifest van een geadopteerde bestaande app (B08-002, platformcontract 1.2 §9-velden waar ze passen).

Alleen voor inventaris en beslisregister: ``kind: adoption``, ``installable: false``. ``install.method`` volgt wat de
app **nu** is (gemeten door de Hub): ``custom_app`` (standaard Custom App, ``ix-app``) of ``catalog_app``. Een app die
via *Install via YAML* (``custom_compose``) draait of waarvan de installatiewijze onbekend is, krijgt geen manifest:
compose is buiten het platformbeleid en gokken doen we niet. De validator van manifest v1 weigert een adoptiemanifest
(``adoption_manifest_not_installable``): er wordt nooit iets mee geïnstalleerd of herinstalleerd.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from ..bestaande_apps import KnownApp, KnownEntry
from ..manifest.canonical import canonical_json
from ..models import ExistingApp
from ..timeutil import iso

SCHEMA_FILE = "adoption-manifest-v1.schema.json"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class AdoptionManifestError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@lru_cache(maxsize=1)
def schema() -> dict[str, Any]:
    text = resources.files("appstore.manifest").joinpath(SCHEMA_FILE).read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)
    Draft202012Validator.check_schema(data)
    return data


def schema_errors(doc: dict[str, Any]) -> list[str]:
    return sorted(e.message for e in Draft202012Validator(schema()).iter_errors(doc))


def _ports(measured: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for i, p in enumerate(measured.get("ports") or [], start=1):
        proto = str(p.get("protocol") or "tcp").lower()
        out.append(
            {
                "name": f"poort-{i}",
                "container_port": p.get("container_port"),
                "host_port": p.get("host_port"),
                "host_ip": p.get("host_ip"),
                "protocol": proto if proto in ("tcp", "udp") else "tcp",
            }
        )
    return out


def _storage(measured: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for i, m in enumerate(measured.get("storage") or [], start=1):
        kind = m.get("kind") if m.get("kind") in ("hostpad", "ixvolume", "overig") else "overig"
        out.append(
            {
                "name": f"opslag-{i}",
                "mount": m.get("destination"),
                "source_kind": kind,
                "host_path": m.get("source") if kind == "hostpad" else None,
                "dataset": m.get("dataset"),
            }
        )
    return out


def build(row: ExistingApp, entry: KnownEntry, known: KnownApp | None) -> dict[str, Any]:
    """Bouw het adoptiemanifest uit de gemeten readback + register/manifest. Gooit :class:`AdoptionManifestError`."""
    if row.status != "adopted" or row.owner is None:
        raise AdoptionManifestError(
            "niet_adopted", "Alleen een geadopteerde app (eigenaar bevestigd) krijgt een adoptiemanifest."
        )
    measured = row.measured or {}
    method = row.install_method
    doc: dict[str, Any] = {
        "manifest_version": "1",
        "kind": "adoption",
        "installable": False,
        "app_id": entry.app_id,
        "name": entry.naam,
        "owner_repo": entry.owner_repo,
        "category": "bestaande_app",
        "description_nl": (
            f"Adoptiemanifest van de bestaande app '{row.runtime_name}' zoals de Hub hem meet. Alleen voor inventaris "
            "en beslisregister; het platform installeert of herinstalleert deze app hiermee nooit."
        ),
    }
    image = measured.get("image") or {}
    if method == "custom_app":
        if not image.get("repository") or not image.get("tag"):
            raise AdoptionManifestError(
                "meting_onvolledig", "De Hub meldt geen image-repository en -tag voor deze Custom App."
            )
        digest = image.get("digest")
        doc["install"] = {"method": "custom_app"}
        doc["image"] = {
            "repository": image["repository"],
            "tag": image["tag"],
            "digest": digest if isinstance(digest, str) and _DIGEST.match(digest) else None,
        }
    elif method == "catalog_app":
        cat = measured.get("catalog") or {}
        if not cat.get("app") or not cat.get("train") or not cat.get("version"):
            raise AdoptionManifestError(
                "meting_onvolledig", "De Hub meldt geen catalogusapp, trein en versie voor deze catalogusapp."
            )
        doc["install"] = {"method": "catalog_app"}
        doc["catalog"] = {"train": cat["train"], "app": cat["app"], "version": cat["version"]}
    elif method == "custom_compose":
        raise AdoptionManifestError(
            "installatiewijze_niet_ondersteund",
            "Deze app draait via 'Install via YAML' (compose). Dat valt buiten het platformbeleid; er wordt geen "
            "manifest voor gemaakt. De app blijft wel in het register en het beslisregister.",
        )
    else:
        raise AdoptionManifestError(
            "installatiewijze_onbekend", "De installatiewijze van deze app is onbekend; er wordt niets aangenomen."
        )
    readback = image.get("readback") if method == "custom_app" else None
    doc.update(
        {
            "network": {"ports": _ports(measured)},
            "storage": _storage(measured),
            "dependencies": [
                {"app_id": d.app_id, "kind": d.soort, "reason_nl": d.reden_nl, "source": d.bron}
                for d in (known.afhankelijk_van if known else [])
            ],
            "owner": {
                "soort": row.owner.get("soort"),
                "repo": row.owner.get("repo"),
                "bron": row.owner.get("bron"),
                "bevestigd_door": row.owner.get("bevestigd_door"),
                "bevestigd_op": row.owner.get("bevestigd_op"),
                "toelichting_nl": row.owner.get("toelichting_nl"),
            },
            "adoption": {
                "runtime_name": row.runtime_name,
                "hub_app_uuid": str(row.hub_app_uuid) if row.hub_app_uuid else None,
                "observed_at": iso(row.last_seen),
                "source": "hub GET /api/v1/apps/{id}",
                "match_basis": row.match_basis,
                "digest_readback": None
                if not isinstance(readback, dict)
                else {
                    "status": readback.get("status"),
                    "reden": readback.get("reden"),
                    "digests": list(readback.get("digests") or []),
                },
                "app_version": (measured.get("catalog") or {}).get("app_version"),
                "excel_id": known.excel_id if known else None,
                "intended_decision": None
                if known is None
                else {"code": known.voorgenomen_besluit.code, "tekst_nl": known.voorgenomen_besluit.tekst_nl},
            },
            "docs": [known.a_doc.removesuffix(".md")] if known else [],
            "validation_status": "inventory_readback",
        }
    )
    problems = schema_errors(doc)
    if problems:  # pragma: no cover - bewaakt door de unit-tests; nooit een ongeldig manifest teruggeven
        raise AdoptionManifestError("intern_schema", "; ".join(problems[:5]))
    return doc


def render(doc: dict[str, Any]) -> tuple[str, str]:
    """``(sha256:<hex> over canonieke JSON, YAML-tekst)``."""
    sha = "sha256:" + hashlib.sha256(canonical_json(doc)).hexdigest()
    head = (
        "# Adoptiemanifest (kind: adoption) — alleen inventaris; NIET installeerbaar.\n"
        "# Gegenereerd door appstore-en-fork uit de gemeten Hub-inventaris na bevestiging van de eigenaar.\n"
    )
    return sha, head + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
