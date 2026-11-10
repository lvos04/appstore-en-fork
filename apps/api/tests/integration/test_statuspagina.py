"""Statuspagina in Peil 1.1.1 door de echte app (PostgreSQL + Hub-simulator): rendering per toestand, CSP zonder
JavaScript, foutweergave met vaste code + selecteerbare trace-ID, meldingsrol, nooit weerspiegelde invoer, en de
herkomst-, ``Host``-, pogingen- en meldingsregels van contract 1.3 §3.5/§3.7 (ONTWERPTAAL §13.2). Axe, overloop op
320 px en schermafdrukken: ``tests/ui`` (Playwright) over dezelfde uitvoer."""

from __future__ import annotations

import dataclasses
import logging
import re

import pytest
from fastapi.testclient import TestClient

from appstore.app import create_app
from appstore.config import Settings
from tests.fakehub import HUB_URL, FakeHub
from tests.integration.conftest import melding_code, pair, pair_form, post_form

pytestmark = pytest.mark.integration

CSP = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
KOPPELTOESTANDEN = {
    "koppelformulier": 'action="/koppelen"',
    "gekoppeld": "Alle schermen van deze dienst staan in de Hub. Ontkoppelen of opnieuw koppelen gaat via de Hub.",
    "ontkoppeld": "De Hub heeft deze dienst ontkoppeld.",
    "ingetrokken": "De Hub heeft de koppeling ingetrokken.",
    "koppelstatus_onleesbaar": "Hier staat bewust geen formulier.",
}


def peil_pagina(resp: object) -> str:
    """Gemeenschappelijke eisen aan elke statuspagina-respons; geeft de HTML."""
    html: str = resp.text  # type: ignore[attr-defined]
    headers = resp.headers  # type: ignore[attr-defined]
    assert headers["content-type"].startswith("text/html")
    assert headers["content-security-policy"] == CSP and headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff" and headers["referrer-policy"] == "same-origin"
    assert html.startswith("<!doctype html>\n<!-- Peil statuspagina-sjabloon 1.1.1")
    assert "<script" not in html.lower() and "Herkomst:" not in html
    assert "{{" not in html and "<!--ALS:" not in html and "<!--ELK:" not in html and "<!--NIET:" not in html
    assert html.count("<h1>") == 1 and '<html lang="nl">' in html
    return html


def koppeltoestand(html: str) -> list[str]:
    return [naam for naam, teken in KOPPELTOESTANDEN.items() if teken in html]


def totaal(html: str) -> tuple[str, str]:
    m = re.search(
        r'<p class="totaal"><span class="pil pil-([\w-]+)">.*?</svg>([^<]+)</span><span class="sr">: </span>'
        r"<span>([^<]+)</span>",
        html,
    )
    assert m, html[:3000]
    return m.group(1), m.group(2)


def test_not_linked_shows_form_with_three_required_fields(client: TestClient) -> None:
    html = peil_pagina(client.get("/"))
    assert koppeltoestand(html) == ["koppelformulier"]
    for veld in ('name="hub_adres"', 'name="koppelcode"', 'name="hub_vingerafdruk"'):
        tag = re.search(rf"<input [^>]*{veld}[^>]*>", html)
        assert tag and " required" in tag.group(0), veld
    assert re.search(r'<input type="hidden" name="csrf" value="[A-Za-z0-9_-]{16,64}">', html)
    assert "Geldig 60 minuten, één keer te gebruiken." in html
    assert totaal(html) == ("onbekend", "Niet gereed")  # eerste signaal nooit groen: nog niet gereed
    assert "<dt>Gereed</dt><dd>Nee — wacht op koppeling met de Hub</dd>" in html
    assert 'class="melding' not in html  # geen melding zonder aanleiding


def test_failed_pairing_shows_code_and_copyable_trace_id_without_echoing_input(
    client: TestClient, hub: FakeHub
) -> None:
    resp = post_form(client, hub_url=HUB_URL, code="ABCD-EFGH-JKMN", fingerprint=hub.fingerprint())
    html = peil_pagina(resp)
    assert resp.status_code == 400 and melding_code(resp) == "hub_weigerde"
    assert 'role="alert"' in html and "Koppelen is mislukt" in html
    assert f'<code class="selecteer">{resp.headers["X-Trace-Id"]}</code>' in html  # trace-ID van de poging zelf
    assert 'aria-describedby="code-hulp code-fout" aria-invalid="true"' in html  # veldfout bij de koppelcode
    assert '<a class="spring" href="#koppelcode">' in html and '<a href="#koppelcode">' in html
    assert koppeltoestand(html) == ["koppelformulier"]  # nog nooit gekoppeld: het formulier blijft
    assert "ABCD-EFGH-JKMN" not in html and "hub.test:30400" not in html  # invoer wordt nooit teruggetoond
    assert hub.fingerprint() not in html
    # na herladen: de opgeslagen laatste fout (§3.5 witte lijst: code + vaste melding) als bestaande toestand: geen
    # live-regio en geen trace-ID (Peil 1.1.1, E05/E06)
    again = client.get("/")
    html = peil_pagina(again)
    assert "Laatste koppelpoging is mislukt" in html and "onbekend, verlopen of al gebruikt" in html
    assert melding_code(again, trace=False) == "hub_weigerde"


def test_multiple_input_errors_are_all_marked_and_nothing_is_sent(client: TestClient, hub: FakeHub) -> None:
    resp = post_form(client, hub_url="http://localhost:30400", code="123", fingerprint="ABCD")
    html = peil_pagina(resp)
    assert resp.status_code == 422 and melding_code(resp) == "invoer_ongeldig"
    assert html.count('aria-invalid="true"') == 3 and html.count('class="veldfout"') == 3
    assert '<a class="spring" href="#hub">Naar het veld Adres van de Hub (fout)</a>' in html
    assert "localhost:30400" not in html.replace("Geen localhost", "") and hub.enroll_requests == []


def test_rate_limited_and_session_pages(client: TestClient, hub: FakeHub) -> None:
    resp = post_form(client, code="ABCD-EFGH-JKMN", fingerprint=hub.fingerprint(), csrf="vervalst")
    assert resp.status_code == 403 and melding_code(resp) == "sessie_verlopen"
    peil_pagina(resp)
    for _ in range(4):  # de vervalste poging hierboven telde al mee (herkomst was in orde)
        post_form(client, code="ABCD-EFGH-JKMN", fingerprint=hub.fingerprint())
    resp = post_form(client, code="ABCD-EFGH-JKMN", fingerprint=hub.fingerprint())
    assert resp.status_code == 429 and resp.headers["retry-after"] == "900"
    assert melding_code(resp) == "te_veel_pogingen" and "Wacht 15 minuten" in peil_pagina(resp)


def test_referrer_policy_keeps_the_browser_origin_and_null_origin_stays_refused(
    client: TestClient, hub: FakeHub
) -> None:
    """Ontwerpreview (docs/ontwerp/PEIL_REVIEW.md): met ``Referrer-Policy: no-referrer`` stuurt Chromium bij de
    formulier-POST ``Origin: null`` en faalde elke echte koppelpoging. Met ``same-origin`` stuurt de browser het eigen
    adres; ``Origin: null`` (sandbox, ander document) blijft geweigerd (contract 1.3 §3.7 punt 1–2)."""
    assert client.get("/").headers["referrer-policy"] == "same-origin"
    resp = post_form(client, code="ABCD-EFGH-JKMN", fingerprint=hub.fingerprint(), origin={"Origin": "null"})
    assert resp.status_code == 403 and melding_code(resp) == "herkomst_onjuist"


def test_linked_page_has_no_form_and_neutral_linked_pill(client: TestClient, hub: FakeHub) -> None:
    pair(client, hub)
    first = client.get(client.last_location)  # type: ignore[attr-defined]
    html = peil_pagina(first)
    assert "Gekoppeld met de Hub" in html and "De Hub heeft de koppeling bevestigd." in html
    assert 'role="status"' in html and 'role="alert"' not in html  # E05: info na dít verzoek
    assert "Trace-ID" not in html  # een geslaagde koppeling is geen fout
    html = peil_pagina(client.get("/"))
    assert koppeltoestand(html) == ["gekoppeld"]
    assert (
        '<span class="pil pil-neutraal"><svg aria-hidden="true"><use href="#i-gekoppeld"/></svg>Gekoppeld</span>'
        in html
    )
    assert f"<code>{hub.fingerprint()}</code>" in html
    assert f'<a class="knop knop-secundair" href="{HUB_URL}">Hub openen</a>' in html  # 1.1.1: knop van 44 px
    assert "Koppelen met de Hub</button>" not in html and 'action="/ontkoppelen"' not in html
    assert totaal(html) == ("gezond", "Alles gezond")  # gereed, bevestigd en alle controles gezond
    assert "<dt>Gereed</dt><dd>Ja</dd>" in html


def test_reflected_query_values_are_ignored(client: TestClient) -> None:
    for query in ("melding=%3Cscript%3Ealert(1)%3C/script%3E", "poging=%3Cscript%3E", "melding=gekoppeld"):
        html = peil_pagina(client.get(f"/?{query}"))
        assert "alert(1)" not in html and "<script" not in html and 'class="melding' not in html, query


def test_message_only_after_a_real_recent_attempt_of_this_browser(
    client: TestClient, hub: FakeHub, settings: Settings
) -> None:
    """§3.7: een melding hoort bij een echte, recente poging van déze browser (sleutel in het geheugen, gebonden aan
    het formuliercookie, ≤ 15 min), nooit bij alleen een query-parameter, en spreekt de koppelstatus nooit tegen."""
    pair(client, hub)
    location = client.last_location  # type: ignore[attr-defined]
    assert "Gekoppeld met de Hub" in client.get(location).text
    assert "Gekoppeld met de Hub" not in client.get("/?poging=" + "A" * 24).text  # verzonnen sleutel
    other = TestClient(client.app)  # een andere browser (ander cookie) met dezelfde URL: geen melding
    assert "Gekoppeld met de Hub" not in other.get(location).text
    outcomes = client.app.state.pairing_outcomes  # type: ignore[attr-defined]
    outcomes.ttl = 0.0  # na 15 minuten: verlopen
    assert "Gekoppeld met de Hub" not in client.get(location).text
    outcomes.ttl = 900.0
    client.app.state.ctx.store.update(lambda st: st.model_copy(update={"state": "ontkoppeld"}))  # type: ignore[attr-defined]
    assert "Gekoppeld met de Hub" not in client.get(location).text  # spreekt de koppelstatus nooit tegen


def test_revoked_and_unlinked_states(client: TestClient, hub: FakeHub) -> None:
    pair(client, hub)
    hub.revoke_all()
    assert client.app.state.ctx.hub.confirm(trace_id="t") is False  # type: ignore[attr-defined]
    html = peil_pagina(client.get("/"))
    assert koppeltoestand(html) == ["ingetrokken"] and "Opnieuw koppelen gaat via de Hub" in html
    assert totaal(html) == ("onbekend", "Niet gereed")
    assert "Laatste koppelpoging" not in html  # de intrekking staat al in het koppelblok
    client.app.state.ctx.store.update(lambda st: st.model_copy(update={"state": "ontkoppeld"}))  # type: ignore[attr-defined]
    html = peil_pagina(client.get("/"))
    assert koppeltoestand(html) == ["ontkoppeld"] and totaal(html)[1] == "Niet gereed"


def test_not_ready_when_database_is_gone_is_never_green(settings: Settings, client: TestClient, hub: FakeHub) -> None:
    pair(client, hub)
    weg = dataclasses.replace(settings, database_url="postgresql+psycopg://postgres@127.0.0.1:9/onbereikbaar")
    app = create_app(weg, http_factory=hub.client_factory())
    try:
        with TestClient(app) as c:
            html = peil_pagina(c.get("/"))
    finally:
        app.state.ctx.engine.dispose()
    assert totaal(html) == ("storing", "Storing")
    assert "Niet gereed — de database is niet bereikbaar." in html
    assert "De database is niet bereikbaar; we tonen geen aantal." in html
    assert koppeltoestand(html) == ["gekoppeld"]  # koppelstatus staat in /appdata, niet in de database


def test_unreadable_link_file_has_no_form_and_reports_a_fault(client: TestClient, settings: Settings) -> None:
    path = settings.secrets_dir / "hub-link.json"
    path.write_text("{kapot", encoding="utf-8")
    path.chmod(0o600)
    resp = client.get("/")
    html = peil_pagina(resp)
    assert koppeltoestand(html) == ["koppelstatus_onleesbaar"] and 'action="/koppelen"' not in html
    assert totaal(html) == ("storing", "Storing")
    assert "(code <code>koppelstatus_onleesbaar</code>)" in html  # reden_code in de controlerij (E06)
    assert 'role="alert"' not in html  # bestaande toestand: geen alert-spam (E05)
    ready = client.get("/readyz")
    assert ready.status_code == 200 and ready.json()["hub_link"]["last_error"]["code"] == "koppelstatus_onleesbaar"


def test_post_from_an_old_tab_with_unreadable_link_file_is_refused(
    client: TestClient, hub: FakeHub, settings: Settings
) -> None:
    """§3.7: geen formulier; een POST uit een oud tabblad gaat niet naar de Hub (409 koppelstatus_onleesbaar)."""
    client.get("/")
    path = settings.secrets_dir / "hub-link.json"
    path.write_text("{kapot", encoding="utf-8")
    path.chmod(0o600)
    resp = post_form(
        client, csrf=client.cookies.get("appstore_csrf"), code=hub.add_code(), fingerprint=hub.fingerprint()
    )
    assert resp.status_code == 409 and melding_code(resp) == "koppelstatus_onleesbaar"
    assert hub.enroll_requests == [] and path.read_text() == "{kapot"  # niets gewijzigd


# ------------------------------------------------------------------ herkomst en Host (contract 1.3 §3.7, A13/A14)
@pytest.mark.parametrize("site", ["same-site", "cross-site"])
def test_sec_fetch_site_other_than_same_origin_is_refused(client: TestClient, hub: FakeHub, site: str) -> None:
    resp = post_form(client, code=hub.add_code(), fingerprint=hub.fingerprint(), headers={"Sec-Fetch-Site": site})
    assert resp.status_code == 403 and melding_code(resp) == "herkomst_onjuist" and hub.enroll_requests == []


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_sec_fetch_site_same_origin_or_none_is_allowed(client: TestClient, hub: FakeHub, site: str) -> None:
    assert pair_form(client, code=hub.add_code(), fingerprint=hub.fingerprint(), headers={"Sec-Fetch-Site": site}) == (
        "gekoppeld"
    )


@pytest.mark.parametrize("host", ["hub.example.com", "nas.fritz.box:30430", "127.1:30430", "2130706433"])
def test_host_restriction_against_dns_rebinding(client: TestClient, hub: FakeHub, host: str) -> None:
    """DNS-rebinding: de aanvaller krijgt Origin = Host op een publieke naam; zo'n POST wordt nooit verwerkt."""
    resp = post_form(
        client,
        code=hub.add_code(),
        fingerprint=hub.fingerprint(),
        origin={"Origin": f"http://{host}"},
        headers={"Host": host, "Sec-Fetch-Site": "same-origin"},
    )
    assert resp.status_code == 403 and melding_code(resp) == "host_niet_toegestaan"
    assert "Open deze pagina via het IP-adres van de NAS, of koppel via de Hub." in resp.text
    assert hub.enroll_requests == []
    st = client.app.state.ctx.store.read()  # type: ignore[attr-defined]
    assert st.pending is None and st.last_error is None  # een geweigerd verzoek verandert niets


@pytest.mark.parametrize(
    "host", ["192.0.2.10:30430", "nas:30430", "nas.local:30430", "nas.home.arpa", "[2001:db8::1]:30430"]
)
def test_allowed_hosts_can_pair(client: TestClient, hub: FakeHub, host: str) -> None:
    resp = post_form(
        client,
        code=hub.add_code(),
        fingerprint=hub.fingerprint(),
        origin={"Origin": f"http://{host}"},
        headers={"Host": host},
    )
    assert resp.status_code == 303, resp.text[:500]


def test_get_via_a_public_name_says_pairing_needs_the_ip_address(client: TestClient) -> None:
    html = peil_pagina(client.get("/", headers={"Host": "nas.fritz.box:30430"}))
    assert "Koppelen kan niet via dit adres" in html and "<code>host_niet_toegestaan</code>" in html
    assert 'role="alert"' not in html


def test_every_post_after_origin_check_counts_as_an_attempt(client: TestClient, hub: FakeHub) -> None:
    """RV7: ook ongeldige invoer, een lopende poging en een al gekoppelde dienst tellen mee; geweigerde herkomst niet."""
    for _ in range(3):  # herkomst geweigerd: telt niet
        post_form(client, code="x", fingerprint="y", origin={"Origin": "http://aanvaller.test"})
    pair(client, hub)  # poging 1
    for _ in range(4):  # pogingen 2–5: al gekoppeld (409), niet naar de Hub
        assert pair_form(client, code=hub.add_code(), fingerprint=hub.fingerprint()) == "al_gekoppeld"
    n = len(hub.enroll_requests)
    assert pair_form(client, code=hub.add_code(), fingerprint=hub.fingerprint()) == "te_veel_pogingen"
    assert len(hub.enroll_requests) == n == 1


def test_one_log_line_per_attempt_without_hub_address_or_secrets(
    client: TestClient, hub: FakeHub, caplog: pytest.LogCaptureFixture
) -> None:
    code = hub.add_code()
    with caplog.at_level(logging.DEBUG, logger="appstore"):
        resp = post_form(client, hub_url="http://andere-hub.test:30400", code=code, fingerprint=hub.fingerprint())
    assert resp.status_code == 502
    lines = [r for r in caplog.records if r.levelno >= logging.INFO and r.name.startswith("appstore")]
    assert len(lines) == 1, [r.getMessage() for r in lines]
    text = lines[0].getMessage()
    assert "hub_onbereikbaar" in text and resp.headers["X-Trace-Id"] in text
    assert "andere-hub" not in caplog.text and code not in caplog.text and code.replace("-", "") not in caplog.text


def test_input_is_checked_whole_never_truncated(client: TestClient, hub: FakeHub) -> None:
    lang = {
        "hub_url": "http://" + "a" * 510 + ".test",
        "code": hub.add_code() + "x" * 40,
        "fingerprint": hub.fingerprint() + "-" * 60,
    }
    resp = post_form(client, **lang)
    assert resp.status_code == 422 and melding_code(resp) == "invoer_ongeldig"
    assert resp.text.count('aria-invalid="true"') == 3 and hub.enroll_requests == []
