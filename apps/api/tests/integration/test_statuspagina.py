"""Statuspagina in Peil door de echte app (PostgreSQL + Hub-simulator): rendering per toestand, CSP zonder JavaScript,
foutweergave met vaste code + selecteerbare trace-ID, en nooit weerspiegelde invoer (ONTWERPTAAL §13.2, contract 1.2
§3.5/§3.7). Axe, overloop op 320 px en schermafdrukken: ``tests/ui`` (Playwright) over dezelfde uitvoer."""

from __future__ import annotations

import dataclasses
import re

import pytest
from fastapi.testclient import TestClient

from appstore.app import create_app
from appstore.config import Settings
from tests.fakehub import HUB_URL, FakeHub
from tests.integration.conftest import melding_code, pair, post_form

pytestmark = pytest.mark.integration

CSP = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
KOPPELTOESTANDEN = {
    "koppelformulier": 'action="/koppelen"',
    "gekoppeld": "Alle schermen van deze dienst staan in de Hub. Ontkoppelen of opnieuw koppelen gaat via de Hub.",
    "ontkoppeld": "De Hub heeft deze dienst ontkoppeld.",
    "ingetrokken": "De Hub heeft de koppeling ingetrokken.",
}


def peil_pagina(resp: object) -> str:
    """Gemeenschappelijke eisen aan elke statuspagina-respons; geeft de HTML."""
    html: str = resp.text  # type: ignore[attr-defined]
    headers = resp.headers  # type: ignore[attr-defined]
    assert headers["content-type"].startswith("text/html")
    assert headers["content-security-policy"] == CSP and headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff" and headers["referrer-policy"] == "no-referrer"
    assert html.startswith("<!doctype html>\n<!-- Peil statuspagina-sjabloon 1.1.0")
    assert "<script" not in html.lower() and "Herkomst:" not in html
    assert "{{" not in html and "<!--ALS:" not in html and "<!--ELK:" not in html and "<!--NIET:" not in html
    assert html.count("<h1>") == 1 and '<html lang="nl">' in html
    return html


def koppeltoestand(html: str) -> list[str]:
    return [naam for naam, teken in KOPPELTOESTANDEN.items() if teken in html]


def totaal(html: str) -> tuple[str, str]:
    m = re.search(r'<p class="totaal"><span class="pil pil-(\w+)">.*?</svg>([^<]+)</span><span>([^<]+)</span>', html)
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
    # na herladen: de opgeslagen laatste fout (§3.5 witte lijst: code + vaste melding), trace-ID van dat verzoek
    again = client.get("/")
    html = peil_pagina(again)
    assert "Laatste koppelpoging is mislukt" in html and "onbekend, verlopen of al gebruikt" in html
    assert melding_code(again) == "hub_weigerde"


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
    for _ in range(5):
        post_form(client, code="ABCD-EFGH-JKMN", fingerprint=hub.fingerprint())
    resp = post_form(client, code="ABCD-EFGH-JKMN", fingerprint=hub.fingerprint())
    assert resp.status_code == 429 and resp.headers["retry-after"] == "900"
    assert melding_code(resp) == "te_veel_pogingen" and "Wacht 15 minuten" in peil_pagina(resp)


def test_linked_page_has_no_form_and_neutral_linked_pill(client: TestClient, hub: FakeHub) -> None:
    pair(client, hub)
    first = client.get("/?melding=gekoppeld")
    html = peil_pagina(first)
    assert "Gekoppeld met de Hub" in html and "De Hub heeft de koppeling bevestigd." in html
    assert "Trace-ID" not in html  # een geslaagde koppeling is geen fout
    html = peil_pagina(client.get("/"))
    assert koppeltoestand(html) == ["gekoppeld"]
    assert (
        '<span class="pil pil-neutraal"><svg aria-hidden="true"><use href="#i-gekoppeld"/></svg>Gekoppeld</span>'
        in html
    )
    assert f"<code>{hub.fingerprint()}</code>" in html and f'<a href="{HUB_URL}">Hub openen</a>' in html
    assert "Koppelen met de Hub</button>" not in html and 'action="/ontkoppelen"' not in html
    assert totaal(html) == ("gezond", "Alles gezond")  # gereed, bevestigd en alle controles gezond
    assert "<dt>Gereed</dt><dd>Ja</dd>" in html


def test_reflected_query_values_are_ignored(client: TestClient) -> None:
    html = peil_pagina(client.get("/?melding=%3Cscript%3Ealert(1)%3C/script%3E"))
    assert "alert(1)" not in html and 'class="melding' not in html
    html = peil_pagina(client.get("/?melding=gekoppeld"))  # niet gekoppeld: geen valse succesmelding
    assert "Gekoppeld met de Hub" not in html


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
    (settings.secrets_dir / "hub-link.json").write_text("{kapot", encoding="utf-8")
    resp = client.get("/")
    html = peil_pagina(resp)
    assert koppeltoestand(html) == [] and melding_code(resp) == "koppelstatus_onleesbaar"
    assert totaal(html) == ("storing", "Storing")
