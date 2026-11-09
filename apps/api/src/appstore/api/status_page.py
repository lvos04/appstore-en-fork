"""Kleine Nederlandse statuspagina op ``GET /`` (zonder auth, zonder geheimen) + koppelen met een koppelcode.

Alle beheerschermen staan in de Hub-UI. Contract 1.2 §3.7:

* Het koppelformulier bestaat **alleen zolang de dienst nog nooit gekoppeld is** (``first_linked_at`` leeg). Daarna
  toont de pagina alleen de status (witte lijst §3.5) en "ontkoppelen of opnieuw koppelen gaat via de Hub"; geen
  ontkoppelen, geen lokale ontkoppelcode, geen herkoppelen na intrekking.
* Velden: Hub-adres (zelfde regels als ``HUB_URL``), koppelcode en de **verplichte** Hub-vingerafdruk.
* CSRF (double-submit-cookie, ``SameSite=Strict``, HttpOnly) + ``Origin``-controle; vaste Nederlandse meldingen; het
  Hub-adres van een mislukte poging wordt nergens bewaard.
* Max. 5 pogingen per 15 min per bron-IP (adres van de TCP-verbinding; ``X-Forwarded-For`` wordt nooit gebruikt) én
  max. 20 per 15 min voor de hele dienst (één API-proces → teller in het geheugen).

De CSS is nog de eigen P0-opmaak: de platform-ontwerptaal (§13) is nog niet geleverd (AS7, geblokkeerd).
"""

from __future__ import annotations

import html
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import APP_ID, __version__
from ..config import normalize_hub_url
from ..db import ping
from ..hubkeys import normalize_fingerprint, normalize_pairing_code
from ..hublink_store import LinkState, error_message
from ..models import CatalogEntry, CatalogSource, ManifestVersion
from ..timeutil import iso, utcnow
from .common import ctx, get_db, tid

router = APIRouter(tags=["statuspagina"])
DB = Annotated[Session, Depends(get_db)]
CSRF_COOKIE = "appstore_csrf"
WINDOW = 15 * 60

MELDINGEN = {
    "gekoppeld": ("ok", "Gekoppeld met de Hub."),
    "mislukt": ("fout", "Koppelen is niet gelukt; zie de reden bij 'Koppeling met de Hub'."),
    "ongeldig_adres": (
        "fout",
        "Vul een geldig Hub-adres in: http(s)://adres[:poort], zonder pad; geen loopback- of link-local-adres.",
    ),
    "ongeldige_code": ("fout", "De koppelcode moet 12 tekens zijn (XXXX-XXXX-XXXX), zoals de Hub hem toont."),
    "ongeldige_vingerafdruk": (
        "fout",
        "Vul de Hub-vingerafdruk in zoals de Hub hem naast de koppelcode toont (XXXX-XXXX-XXXX-XXXX).",
    ),
    "al_gekoppeld": ("ok", "Deze app is al eens gekoppeld. Ontkoppelen of opnieuw koppelen gaat via de Hub."),
    "bezig": ("fout", "Er loopt al een koppelpoging; wacht even en laad de pagina opnieuw."),
    "te_vaak": ("fout", "Te veel pogingen. Wacht 15 minuten en probeer het opnieuw."),
    "sessie": ("fout", "Het formulier is verlopen of kwam niet van deze pagina. Laad de pagina opnieuw."),
    "andere_hub": (
        "fout",
        "Dit Hub-adres verschilt van het adres (HUB_URL) waarmee de app is geïnstalleerd; koppelen met een andere Hub "
        "is geweigerd.",
    ),
}
STATE_LABEL = {
    "gekoppeld": "Gekoppeld",
    "nooit_gekoppeld": "Niet gekoppeld",
    "ontkoppeld": "Ontkoppeld door de Hub",
    "ingetrokken": "Koppeling ingetrokken door de Hub",
}


class _Attempts:
    """Schuivend venster per sleutel (bron-IP) én voor de hele dienst."""

    def __init__(self, per_ip: int, total: int, window: float) -> None:
        self.per_ip, self.total, self.window = per_ip, total, window
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._all: deque[float] = deque()
        self._lock = threading.Lock()

    def _trim(self, q: deque[float], now: float) -> None:
        while q and now - q[0] >= self.window:
            q.popleft()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            self._trim(q, now)
            self._trim(self._all, now)
            if len(q) >= self.per_ip or len(self._all) >= self.total:
                return False
            q.append(now)
            self._all.append(now)
            return True


def pairing_limiter() -> _Attempts:
    return _Attempts(per_ip=5, total=20, window=WINDOW)


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _form_allowed(link: LinkState) -> bool:
    return not link.corrupt and link.first_linked_at is None


def _same_origin(request: Request) -> bool:
    """``Origin`` moet gelijk zijn aan het eigen adres (schema + Host-header van dit verzoek)."""
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    if not origin or not host or origin == "null":
        return False
    parts = urlsplit(origin)
    return parts.scheme == request.url.scheme and parts.netloc.lower() == host.lower() and not parts.path


def _link_html(link: LinkState) -> str:
    if link.corrupt:
        return (
            '<p><span class="badge fout">onbekend</span>De koppelstatus (/appdata/secrets/hub-link.json) is onleesbaar; '
            "niets van de Hub wordt vertrouwd. Herstel: maak in de Hub een nieuw token en zet het in het formulier van "
            "de Custom App (HUB_ENROLLMENT_TOKEN).</p>"
        )
    pub = link.public()
    badge = {"gekoppeld": "ok", "nooit_gekoppeld": "onbekend"}.get(link.state, "fout")
    rows = [f'<p><span class="badge {badge}">{_e(STATE_LABEL.get(link.state, link.state))}</span>']
    if link.state == "gekoppeld":
        rows.append(
            f"<br>Hub: {_e(pub['hub_url'])} · via {_e(pub['via'])} · sinds {_e(pub['linked_at'])}"
            f" · token …{_e(pub['service_token_last4'])}"
            + (" · bevestigd" if link.confirmed_at else " · nog niet bevestigd door de Hub")
        )
    elif link.hub_url and link.state in ("ontkoppeld", "ingetrokken"):
        rows.append(f"<br>Laatste Hub: {_e(link.hub_url)}")
    if pub["keyset_fingerprint"]:
        rows.append(f"<br>Hub-vingerafdruk: <code>{_e(pub['keyset_fingerprint'])}</code> (vergelijk met de Hub-UI)")
    if pub["koppelen_bezig"]:
        rows.append("<br>Er loopt een koppelpoging.")
    if link.last_error:
        rows.append(f"<br>Laatste melding: {_e(error_message(link.last_error.code))} ({_e(link.last_error.at)})")
    if link.first_linked_at:
        rows.append("<br>Ontkoppelen of opnieuw koppelen gaat via de Hub.")
    rows.append("</p>")
    return "".join(rows)


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def status_page(request: Request, db: DB, melding: str | None = None) -> HTMLResponse:
    c = ctx(request)
    now = iso(utcnow())
    db_ok = ping(c.engine)
    link = c.store.read()
    if db_ok:
        n_entries = db.scalar(select(func.count()).select_from(CatalogEntry)) or 0
        n_rejected = (
            db.scalar(select(func.count()).select_from(ManifestVersion).where(ManifestVersion.valid.is_(False))) or 0
        )
        sources = db.scalars(select(CatalogSource).order_by(CatalogSource.name)).all()
    else:
        n_entries, n_rejected, sources = None, None, []
    token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(24)
    msg_html = ""
    if melding in MELDINGEN:
        kind, text = MELDINGEN[melding]
        msg_html = f'<p class="melding {kind}" role="status">{_e(text)}</p>'
    form_html = ""
    if _form_allowed(link):
        form_html = f"""
<section aria-labelledby="koppelen"><h2 id="koppelen">Koppelen met de Hub</h2>
<p>Aanbevolen: laat de Hub deze app installeren, of zet <code>HUB_URL</code>, <code>HUB_ENROLLMENT_TOKEN</code> en
<code>HUB_KEY_FINGERPRINT</code> in het formulier van de Custom App. Anders: maak in de Hub (Instellingen → Diensten) een
koppelcode voor <code>{_e(APP_ID)}</code> en vul hieronder het Hub-adres, de koppelcode en de vingerafdruk in die de
Hub erbij toont. Dit formulier verdwijnt na de eerste koppeling.</p>
<form method="post" action="/koppelen">
<input type="hidden" name="csrf" value="{_e(token)}">
<label for="hub_url">Hub-adres</label>
<input id="hub_url" name="hub_url" type="url" required placeholder="http://adres-van-de-nas:poort" value="{_e(c.settings.hub_url or "")}">
<label for="koppelcode">Koppelcode</label>
<input id="koppelcode" name="koppelcode" required autocomplete="off" maxlength="32" placeholder="XXXX-XXXX-XXXX">
<label for="vingerafdruk">Hub-vingerafdruk</label>
<input id="vingerafdruk" name="vingerafdruk" required autocomplete="off" maxlength="64" placeholder="XXXX-XXXX-XXXX-XXXX">
<button type="submit">Koppelen</button>
</form></section>"""
    rows = (
        "".join(
            f"<tr><td>{_e(s.name)}</td><td>{_e(s.kind)}</td><td>{'aan' if s.enabled else 'uit'}</td>"
            f"<td>{_e({'ok': 'gelukt', 'failed': 'mislukt', 'never': 'nog nooit'}.get(s.last_sync_status, s.last_sync_status))}</td>"
            f"<td>{_e(iso(s.last_sync_at) or '—')}</td></tr>"
            for s in sources
        )
        or '<tr><td colspan="5">Nog geen bronnen.</td></tr>'
    )
    body = f"""<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Appstore en fork — status</title>
<style>
:root{{color-scheme:light dark;--bg:#f7f7f8;--fg:#1b1b1f;--kaart:#fff;--rand:#d9d9de;--ok:#1f7a3a;--fout:#b3261e;--onb:#6b6b75}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121214;--fg:#ececf1;--kaart:#1d1d21;--rand:#34343b;--ok:#5cc27a;--fout:#ff8a80;--onb:#a0a0aa}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}}
main{{max-width:880px;margin:0 auto;padding:16px}}
section{{background:var(--kaart);border:1px solid var(--rand);border-radius:10px;padding:12px 16px;margin:12px 0}}
h1{{font-size:1.4rem}} h2{{font-size:1.1rem;margin:.2rem 0 .5rem}}
.badge{{display:inline-block;padding:2px 8px;border-radius:999px;font-weight:600;margin-right:6px;border:1px solid currentColor}}
.ok{{color:var(--ok)}} .fout{{color:var(--fout)}} .onbekend{{color:var(--onb)}}
.melding{{font-weight:600}} table{{width:100%;border-collapse:collapse;font-size:.95rem}}
td,th{{text-align:left;padding:4px 6px;border-bottom:1px solid var(--rand)}}
.tabel{{overflow-x:auto}} label{{display:block;margin-top:8px}}
input{{width:100%;max-width:420px;padding:8px;font:inherit;box-sizing:border-box;min-height:44px}}
button{{margin-top:10px;padding:10px 16px;font:inherit;min-height:44px;cursor:pointer}}
small{{color:var(--onb)}} code{{overflow-wrap:anywhere}}
</style></head><body><main>
<h1>Appstore en fork <small>versie {_e(__version__)}</small></h1>
<p>Appstore-catalogus van het TrueNAS Home Platform. Alle schermen staan in de Hub; deze pagina toont alleen de status.</p>
{msg_html}
<section aria-labelledby="dienst"><h2 id="dienst">Dienst</h2>
<p><span class="badge {"ok" if db_ok else "fout"}">{"actief" if db_ok else "storing"}</span>
database {"bereikbaar" if db_ok else "niet bereikbaar"} · bron: eigen controle · laatst bijgewerkt {_e(now)}</p></section>
<section aria-labelledby="hub"><h2 id="hub">Koppeling met de Hub</h2>{_link_html(link)}<small>bron: /appdata/secrets/hub-link.json · laatst bijgewerkt {_e(now)}</small></section>
{form_html}
<section aria-labelledby="cat"><h2 id="cat">Catalogus</h2>
<p>{_e(n_entries if n_entries is not None else "onbekend")} app(s) in de catalogus · {_e(n_rejected if n_rejected is not None else "onbekend")} geweigerde manifestversie(s)</p>
<div class="tabel"><table><thead><tr><th>Bron</th><th>Soort</th><th>Status</th><th>Laatste synchronisatie</th><th>Tijd (UTC)</th></tr></thead>
<tbody>{rows}</tbody></table></div><small>bron: eigen database · laatst bijgewerkt {_e(now)}</small></section>
<p><small><a href="/healthz">/healthz</a> · <a href="/readyz">/readyz</a> · <a href="/openapi.json">OpenAPI</a></small></p>
</main></body></html>"""
    resp = HTMLResponse(
        body,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )
    resp.set_cookie(CSRF_COOKIE, token, httponly=True, samesite="strict", max_age=3600)
    return resp


@router.post("/koppelen", include_in_schema=False)
def pair(
    request: Request,
    hub_url: Annotated[str, Form(max_length=512)] = "",
    koppelcode: Annotated[str, Form(max_length=64)] = "",
    vingerafdruk: Annotated[str, Form(max_length=64)] = "",
    csrf: Annotated[str, Form(max_length=64)] = "",
) -> RedirectResponse:
    def back(melding: str) -> RedirectResponse:
        return RedirectResponse(f"/?melding={melding}", status_code=303)

    cookie = request.cookies.get(CSRF_COOKIE)
    if not cookie or not csrf or not secrets.compare_digest(cookie, csrf) or not _same_origin(request):
        return back("sessie")
    c = ctx(request)
    link = c.store.read()
    if not _form_allowed(link):
        return back("al_gekoppeld")  # §3.7: na de eerste koppeling alleen via de Hub
    client = request.client.host if request.client else "onbekend"  # TCP-adres; nooit X-Forwarded-For
    if not request.app.state.pairing_attempts.allow(client):
        return back("te_vaak")
    try:
        url = normalize_hub_url(hub_url, allow_loopback=c.settings.allow_loopback_hub)
    except ValueError:
        url = None
    if not url:
        return back("ongeldig_adres")
    code = normalize_pairing_code(koppelcode)
    if code is None:
        return back("ongeldige_code")
    fingerprint = normalize_fingerprint(vingerafdruk)
    if fingerprint is None:
        return back("ongeldige_vingerafdruk")
    if c.settings.hub_url and url != c.settings.hub_url:
        # bij installatie via de Hub staat HUB_URL vast: nooit met een andere (mogelijk vreemde) Hub koppelen
        return back("andere_hub")
    # de claim van de poging gebeurt atomair in de client (bestandsvergrendeling): geen wedloop tussen twee
    # formulieren, of tussen het formulier en een env-token, en nooit na de eerste koppeling (§3.7)
    result = c.hub.pair_with_code(hub_url=url, pairing_code=code, fingerprint=fingerprint, trace_id=tid(request))
    if result.kind == "refused":
        return back("al_gekoppeld" if result.code == "al_gekoppeld" else "bezig")
    return back("gekoppeld" if result.ok else "mislukt")
