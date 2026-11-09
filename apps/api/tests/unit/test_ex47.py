"""EX47 — Appstore ongeldige manifest: manifest met privileged host mount → deployment geweigerd vóór uitvoering.

Simulatie: er wordt niets geïnstalleerd; bewezen wordt dat de keten validator → vertaling géén app.create-payload
oplevert, zodat de executor niets kan uitvoeren. Status: Sim-getest (geen echte NAS).
"""

from __future__ import annotations

from appstore.manifest import validate_text
from appstore.manifest.translate import Target, translate
from tests.conftest import FIXTURES

EX47 = FIXTURES / "manifests" / "ex47-privileged-hostmount.manifest.yaml"


def test_ex47_refused_before_execution() -> None:
    report, model, _ = validate_text(EX47.read_bytes())
    assert report.valid is False
    assert model is None, "een ongeldig manifest mag nooit een model (en dus geen installatieplan) opleveren"
    codes = set(report.codes())
    assert {
        "privileged_forbidden",
        "forbidden_host_path",
        "docker_socket_forbidden",
        "mutable_tag_forbidden",
        "digest_missing",
        "host_network_without_reason",
        "capability_forbidden",
    } <= codes


def test_ex47_cannot_be_translated_even_if_forced() -> None:
    """Ook wie de validator omzeilt, krijgt geen payload: het getypte model weigert privileged in de waarden."""
    from appstore.manifest.truenas import IxAppValues

    try:
        IxAppValues.model_validate(
            {
                "image": {"repository": "x", "tag": "1"},
                "TZ": "Etc/UTC",
                "restart_policy": "no",
                "privileged": True,
                "resources": {"enable_resource_limits": False},
            }
        )
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("privileged=True mocht niet in IxAppValues")
    assert translate.__doc__ is not None and "gevalideerd" in (translate.__doc__ or "")
    _ = Target()
