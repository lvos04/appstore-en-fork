"""Kleine Nederlandse statuspagina op ``GET /`` (zonder auth, zonder geheimen) + koppelen met een koppelcode.

Alle beheerschermen staan in de Hub-UI; deze pagina toont alleen status en biedt het koppelformulier voor een
handmatig geïnstalleerde app (contract §3).
"""

from __future__ import annotations

import html
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import APP_ID, __version__
from ..config import normalize_hub_url
from ..db import ping
from ..hublink import enroll, get_link
from ..models import CatalogEntry, CatalogSource, ManifestVersion
from ..timeutil import iso, utcnow
from .common import ctx, get_db, tid

router = APIRouter(tags=["statuspagina"])
DB = Annotated[Session, Depends(get_db)]
CSRF_COOKIE = "appstore_csrf"

MELDINGEN = {
    "gekoppeld": ("ok", "Gekoppeld met de Hub."),
    "mislukt": ("fout", "Koppelen is niet gelukt; zie de reden bij 'Koppeling met de Hub'."),
    "ongeldig": ("fout", "Vul een geldig Hub-adres (http(s)://…) en de koppelcode uit de Hub in."),
    "al_gekoppeld": ("ok", "Deze app is al gekoppeld. Ontkoppelen of opnieuw koppelen gaat via de Hub."),
    "te_vaak": ("fout", "Te veel pogingen. Wacht 15 minuten en probeer het opnieuw."),
    "sessie": ("fout", "Het formulier is verlopen. Laad de pagina opnieuw en probeer het nog eens."),
    "andere_hub": (
        "fout",
        "Dit Hub-adres verschilt van het adres (HUB_URL) waarmee de app is geïnstalleerd; koppelen met een andere Hub "
        "is geweigerd.",
    ),
}
LINK_STATUS = {
    "linked": "Gekoppeld",
    "not_linked": "Niet gekoppeld",
    "failed": "Koppelen mislukt",
    "revoked": "Koppeling ingetrokken door de Hub",
}


class _Attempts:
    def __init__(self, limit: int, window: float) -> None:
        self.limit, self.window = limit, window
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


def pairing_limiter() -> _Attempts:
    return _Attempts(limit=5, window=15 * 60)


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def status_page(request: Request, db: DB, melding: str | None = None) -> HTMLResponse:
    c = ctx(request)
    now = iso(utcnow())
    db_ok = ping(c.engine)
    link = get_link(db) if db_ok else None
    if db_ok:
        db.commit()
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
    if link is None:
        link_html = '<p><span class="badge onbekend">onbekend</span> database niet bereikbaar</p>'
    else:
        badge = "ok" if link.status == "linked" else ("fout" if link.status in ("failed", "revoked") else "onbekend")
        details = ""
        if link.status == "linked":
            details = (
                f"<br>Hub: {_e(link.hub_url)} · via {_e(link.linked_via)} · sinds {_e(iso(link.linked_at))}"
                f" · token …{_e(link.token_last4)}"
            )
        elif link.last_error:
            details = f"<br>Reden: {_e(link.last_error)}"
        link_html = (
            f'<p><span class="badge {badge}">{_e(LINK_STATUS.get(link.status, link.status))}</span>{details}</p>'
        )
    form_html = ""
    if link is not None and link.status != "linked":
        form_html = f"""
<section aria-labelledby="koppelen"><h2 id="koppelen">Koppelen met de Hub</h2>
<p>Maak in de Hub (Instellingen → Diensten) een koppelcode voor <code>{_e(APP_ID)}</code> en vul die hier in.</p>
<form method="post" action="/koppelen">
<input type="hidden" name="csrf" value="{_e(token)}">
<label for="hub_url">Hub-adres</label>
<input id="hub_url" name="hub_url" type="url" required placeholder="http://adres-van-de-nas:poort" value="{_e(link.hub_url or c.settings.hub_url or "")}">
<label for="koppelcode">Koppelcode</label>
<input id="koppelcode" name="koppelcode" required autocomplete="off" maxlength="64">
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
input{{width:100%;max-width:420px;padding:8px;font:inherit;box-sizing:border-box}}
button{{margin-top:10px;padding:10px 16px;font:inherit;min-height:44px;cursor:pointer}}
small{{color:var(--onb)}}
</style></head><body><main>
<h1>Appstore en fork <small>versie {_e(__version__)}</small></h1>
<p>Appstore-catalogus van het TrueNAS Home Platform. Alle schermen staan in de Hub; deze pagina toont alleen de status.</p>
{msg_html}
<section aria-labelledby="dienst"><h2 id="dienst">Dienst</h2>
<p><span class="badge {"ok" if db_ok else "fout"}">{"actief" if db_ok else "storing"}</span>
database {"bereikbaar" if db_ok else "niet bereikbaar"} · bron: eigen controle · laatst bijgewerkt {_e(now)}</p></section>
<section aria-labelledby="hub"><h2 id="hub">Koppeling met de Hub</h2>{link_html}<small>bron: eigen database · laatst bijgewerkt {_e(now)}</small></section>
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
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )
    resp.set_cookie(CSRF_COOKIE, token, httponly=True, samesite="strict", max_age=3600)
    return resp


@router.post("/koppelen", include_in_schema=False)
def pair(
    request: Request,
    db: DB,
    hub_url: Annotated[str, Form(max_length=512)] = "",
    koppelcode: Annotated[str, Form(max_length=64)] = "",
    csrf: Annotated[str, Form(max_length=64)] = "",
) -> RedirectResponse:
    def back(melding: str) -> RedirectResponse:
        return RedirectResponse(f"/?melding={melding}", status_code=303)

    cookie = request.cookies.get(CSRF_COOKIE)
    if not cookie or not csrf or not secrets.compare_digest(cookie, csrf):
        return back("sessie")
    client = request.client.host if request.client else "onbekend"
    if not request.app.state.pairing_attempts.allow(client):
        return back("te_vaak")
    code = koppelcode.strip()
    try:
        url = normalize_hub_url(hub_url)
    except ValueError:
        url = None
    if not url or len(code) < 6:
        return back("ongeldig")
    c = ctx(request)
    if c.settings.hub_url and url != c.settings.hub_url:
        # bij installatie via de Hub staat HUB_URL vast: nooit met een andere (mogelijk vreemde) Hub koppelen
        return back("andere_hub")
    link = get_link(db)
    if link.status == "linked":
        db.commit()
        return back("al_gekoppeld")
    result = enroll(
        db, c.settings, c.secrets, c.http_factory, hub_url=url, credential=code, via="koppelcode", trace_id=tid(request)
    )
    db.commit()
    request.app.state.authenticator.jwks.invalidate()
    return back("gekoppeld" if result.ok else "mislukt")
