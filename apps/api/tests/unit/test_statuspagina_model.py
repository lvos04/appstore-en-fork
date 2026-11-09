"""Inhoud van de statuspagina (ONTWERPTAAL §13.2, contract 1.2 §3.5/§3.7) zonder database: totaalstatus, gereedheid,
koppeltoestand, catalogusregels en de velden voor het sjabloon."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from appstore.api import status_page as sp
from appstore.api.health import Readiness
from appstore.hublink_store import LastError, LinkState, Pending, Pins

NU = datetime(2026, 11, 9, 11, 56, 30, tzinfo=UTC)
OK = Readiness(database=True, schema="ok", current="0002", head="0002")
PINS = Pins(jwt=[], policy=[], keyset_fingerprint="MEGG-8GFG-2E4Y-K2RQ")


def link(state: str = "nooit_gekoppeld", **kw: Any) -> LinkState:
    base: dict[str, Any] = {"state": state}
    if state != "nooit_gekoppeld":
        base["first_linked_at"] = "2026-11-08T20:14:00Z"
    if state == "gekoppeld":
        base.update(
            hub_url="http://hub.test:30400",
            service_token="svc_" + "a" * 43,
            service_token_last4="aaaa",
            pins=PINS,
            linked_at="2026-11-08T20:14:00Z",
            confirmed_at="2026-11-08T20:14:01Z",
        )
    base.update(kw)
    return LinkState(**base)


def stand(lnk: LinkState | None = None, r: Readiness = OK, **kw: Any) -> sp.Stand:
    kw.setdefault("apps", 1)
    kw.setdefault("geweigerd", 0)
    kw.setdefault("bronnen", [sp.BronStand("ingebouwd", "bundled", True, "ok", NU)])
    return sp.Stand(NU, r, lnk or link(), **kw)


def data(s: sp.Stand, melding: sp.Melding | None = None) -> dict[str, Any]:
    return sp.gegevens(s, csrf="c" * 32, trace_id="3d8a1f4e-6b2c-4e95-a7d0-91c5b8e2f364", melding=melding)


NIET_GEREED = [
    ("nooit", stand(), "wacht op koppeling met de Hub"),
    ("ontkoppeld", stand(link("ontkoppeld")), "ontkoppeld door de Hub"),
    ("ingetrokken", stand(link("ingetrokken")), "koppeling ingetrokken door de Hub"),
    ("onbevestigd", stand(link("gekoppeld", confirmed_at=None)), "koppeling nog niet bevestigd door de Hub"),
    (
        "db weg",
        stand(link("gekoppeld"), Readiness(database=False, schema=None), apps=None, bronnen=None),
        "de database is niet bereikbaar",
    ),
    (
        "schema oud",
        stand(link("gekoppeld"), Readiness(True, "verouderd", "0001", "0002")),
        "het databaseschema is niet bijgewerkt",
    ),
    ("schema onbekend", stand(link("gekoppeld"), Readiness(True, "onbekend")), "de schemaversie is onbekend"),
]


@pytest.mark.parametrize(("_naam", "s", "waarom"), NIET_GEREED, ids=[n[0] for n in NIET_GEREED])
def test_first_signal_is_never_green_when_not_ready(_naam: str, s: sp.Stand, waarom: str) -> None:
    d = data(s)
    assert d["gereed"] == f"Nee — {waarom}"
    assert d["totaal_toon"] != "gezond" and d["totaal_label"] != "Alles gezond"
    assert waarom in d["totaal_toelichting"]
    assert d["totaal_toon"] in ("onbekend", "beperkt", "storing")


def test_corrupt_link_file_is_not_ready_and_a_fault() -> None:
    lnk = LinkState(first_linked_at="2026-11-09T00:00:00Z")
    lnk._corrupt = True
    d = data(stand(lnk))
    assert d["gereed"] == "Nee — de koppelstatus is onleesbaar" and d["totaal_toon"] == "storing"
    assert not any(d[k] for k in ("koppelformulier", "gekoppeld", "ontkoppeld", "ingetrokken"))
    assert sp.koppel_melding(lnk) is not None and sp.koppel_melding(lnk).code == "koppelstatus_onleesbaar"  # type: ignore[union-attr]


def test_all_healthy_only_when_ready_and_every_check_healthy() -> None:
    d = data(stand(link("gekoppeld")))
    assert (d["gereed"], d["totaal_toon"], d["totaal_label"]) == ("Ja", "gezond", "Alles gezond")
    assert [c["naam"] for c in d["checks"]] == ["Proces", "Eigen database", "Databaseschema"]  # geen koppelregel
    failed = [sp.BronStand("ingebouwd", "bundled", True, "failed", NU)]
    d = data(stand(link("gekoppeld"), bronnen=failed))
    assert (d["totaal_toon"], d["totaal_label"]) == ("beperkt", "Beperkt")
    assert d["totaal_toelichting"] == "Gereed; 1 onderdeel is beperkt."
    never = [sp.BronStand("git-bron", "git", True, "never", None)]
    d = data(stand(link("gekoppeld"), bronnen=never))
    assert (d["totaal_toon"], d["totaal_label"]) == ("onbekend", "Deels onbekend")


def test_gate_does_not_worsen_but_unknown_does() -> None:
    uit = [sp.BronStand("oud", "http", False, "never", None)]
    d = data(stand(link("gekoppeld"), apps=0, bronnen=uit))
    assert d["dienst_blok"][0]["toon"] == "gate" and d["dienst_blok"][1]["toon"] == "gate"
    assert d["totaal_toon"] == "gezond"


def test_exactly_one_link_state_and_form_only_before_first_link() -> None:
    for lnk, verwacht in [
        (link(), "koppelformulier"),
        (link("gekoppeld"), "gekoppeld"),
        (link("ontkoppeld"), "ontkoppeld"),
        (link("ingetrokken"), "ingetrokken"),
        (link("nooit_gekoppeld", first_linked_at="2026-11-08T20:14:00Z"), None),  # §3.7: nooit meer het formulier
    ]:
        d = data(stand(lnk))
        actief = [k for k in ("koppelformulier", "gekoppeld", "ontkoppeld", "ingetrokken") if d[k]]
        assert actief == ([verwacht] if verwacht else []), (lnk.state, actief)


def test_public_whitelist_only_never_the_service_token() -> None:
    lnk = link("gekoppeld")
    html = sp.pagina(stand(lnk), csrf="c" * 32, trace_id="t", melding=None)
    assert lnk.service_token and lnk.service_token not in html and "svc_" not in html
    assert "MEGG-8GFG-2E4Y-K2RQ" in html and "http://hub.test:30400" in html
    assert 'action="/koppelen"' not in html and 'action="/ontkoppelen"' not in html and 'action="/beheer"' not in html
    assert "Ontkoppelen of opnieuw koppelen gaat via de Hub" in html


def test_trace_id_only_with_a_code_and_never_for_plain_info() -> None:
    info = data(stand(link("gekoppeld")), sp.Melding("info", "Gekoppeld met de Hub", "Bevestigd."))
    assert info["melding"] and info["trace_id"] is None
    fout = data(stand(), sp.Melding("fout", "Koppelen is mislukt", "x", "hub_weigerde", "koppelcode"))
    assert fout["trace_id"] == "3d8a1f4e-6b2c-4e95-a7d0-91c5b8e2f364" and fout["melding_code"] == "hub_weigerde"
    assert fout["melding_link_doel"] == "koppelcode" and fout["melding_icoon"] == "storing"
    assert fout["titel_toestand"].startswith("koppelen is mislukt · niet gereed")


def test_field_errors_set_skip_link_and_fields() -> None:
    s = stand(veldfouten={"vingerafdruk": "v", "hub_adres": "h"})
    d = data(s)
    assert d["spring_doel"] == "hub" and d["spring_tekst"] == "Naar het veld Adres van de Hub (fout)"
    assert (d["fout_hub_adres"], d["fout_koppelcode"], d["fout_vingerafdruk"]) == ("h", None, "v")


def test_stored_last_error_message() -> None:
    lnk = link(last_error=LastError(code="hub_onbereikbaar", at="2026-11-09T10:00:00Z"))
    m = sp.koppel_melding(lnk)
    assert m is not None and m.toon == "fout" and m.code == "hub_onbereikbaar"
    assert "Hub was niet bereikbaar" in m.tekst and "9 nov 2026, 10:00 UTC" in m.tekst
    linked = link("gekoppeld", last_error=LastError(code="hub_weigerde", at="2026-11-09T10:00:00Z"))
    assert "De bestaande koppeling blijft gelden." in sp.koppel_melding(linked).tekst  # type: ignore[union-attr]
    revoked = link("ingetrokken", last_error=LastError(code="ingetrokken_door_hub", at="2026-11-09T10:00:00Z"))
    assert sp.koppel_melding(revoked) is None  # het koppelblok zegt het al
    pending = link(pending=Pending(kind="token", secret_sha256="ab" * 32, hub_url="http://h.test", started_at="x"))
    m = sp.koppel_melding(pending)
    assert m is not None and m.toon == "info" and m.code is None and "token" in m.tekst


def test_unknown_tone_is_refused() -> None:
    with pytest.raises(ValueError, match="onbekende toon"):
        sp.Controle("x", "groen", "Gezond")


def test_many_sources_are_capped_with_worst_state_of_the_rest() -> None:
    bronnen = [sp.BronStand(f"b{i:03}", "git", True, "ok", NU) for i in range(sp.MAX_BRONNEN + 3)]
    bronnen[-1] = sp.BronStand("zzz", "git", True, "failed", NU)
    rijen = sp.catalogus(stand(bronnen=bronnen))
    assert len(rijen) == 1 + sp.MAX_BRONNEN + 1
    assert rijen[-1].naam == "Nog 3 bronnen" and rijen[-1].toon == "beperkt"


def test_dutch_date_notation_in_utc() -> None:
    assert sp.datum_tijd(datetime(2026, 3, 1, 7, 5, tzinfo=UTC)) == "1 mrt 2026, 07:05 UTC"
    assert sp.datum_tijd(datetime(2026, 11, 9, 12, 0, tzinfo=UTC) + timedelta(hours=1)) == "9 nov 2026, 13:00 UTC"
    assert sp.datum_tijd(None) == "onbekend" and sp.parse("geen datum") is None
    assert sp.klok(NU) == "11:56 UTC" and sp.iso_z(NU) == "2026-11-09T11:56:30Z"


# ---------------------------------------------------------------- ontwerpreview Peil (docs/ontwerp/PEIL_REVIEW.md)
def test_review_sources_and_counts_in_dutch_and_as_a_measurement() -> None:
    """Geen Engelse bronsoort ("bundled") op de pagina; de aantallen zijn een meting van de eigen database."""
    bronnen = [  # volgorde zoals de database ze levert (op naam); de ingebouwde catalogus komt eerst
        sp.BronStand("community", "http", True, "failed", NU),
        sp.BronStand("eigen", "git", False, "never", None),
        sp.BronStand("ingebouwd", "bundled", True, "ok", NU),
    ]
    rijen = sp.catalogus(stand(apps=2, geweigerd=1, bronnen=bronnen))
    assert [r.naam for r in rijen] == [
        "Catalogusgegevens",
        "Ingebouwde catalogus",
        "Bron community (HTTP-adres)",
        "Bron eigen (Git-repository)",
    ]
    assert rijen[0].reden == (
        "Gelezen uit de eigen database: 2 apps in de catalogus; 1 manifestversie geweigerd door de validator."
    )
    assert [r.toon for r in rijen] == ["gezond", "gezond", "beperkt", "gate"]
    assert not any("bundled" in (r.naam + (r.reden or "")) for r in rijen)
    leeg = sp.catalogus(stand(apps=None, bronnen=None))
    assert (leeg[0].naam, leeg[0].toon) == ("Catalogusgegevens", "onbekend")  # nooit 0 als het onbekend is


@pytest.mark.parametrize(
    ("toestand", "reden"), [("ontkoppeld", "ontkoppeld"), ("ingetrokken", "koppeling ingetrokken")]
)
def test_review_decision_by_the_hub_is_not_called_partly_unknown(toestand: str, reden: str) -> None:
    d = data(stand(link(toestand)))
    assert (d["totaal_toon"], d["totaal_label"]) == ("onbekend", "Niet gereed")
    assert d["totaal_toelichting"] == f"De dienst weigert elke opdracht: {reden} door de Hub."
    assert "Deels onbekend" in data(stand())["totaal_toelichting"]  # wacht op koppeling: wél deels onbekend


def test_review_form_outcome_speaks_of_the_pairing_code_and_says_what_to_do() -> None:
    for code, tekst in sp.HUB_POGING.items():
        assert "token" not in tekst, code
        assert sp.VELDFOUT[code][0] in ("hub_adres", "koppelcode", "vingerafdruk")
    assert sp.HUB_POGING["hub_weigerde"].endswith("Maak in de Hub een nieuwe koppelcode en probeer het opnieuw.")
    m = sp.koppel_melding(link(last_error=LastError(code="hub_weigerde", at="2026-11-09T10:00:00Z")))
    assert m is not None and m.tekst.endswith("Maak in de Hub een nieuwe koppelcode of een nieuw koppeltoken.")


def test_review_unreadable_link_file_message_has_no_english_jargon() -> None:
    lnk = LinkState(first_linked_at="2026-11-09T00:00:00Z")
    lnk._corrupt = True
    m = sp.koppel_melding(lnk)
    assert m is not None and "enrollment" not in m.tekst and "HUB_ENROLLMENT_TOKEN" in m.tekst
    assert "nieuw koppeltoken" in m.tekst and "hub-link.json" not in m.tekst  # afbreken op "hub-" bij 320 px
