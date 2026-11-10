"""App-inventaris van de Hub ophalen (B08-002), alleen lezen.

De appstore heeft **geen** TrueNAS-sleutel. De inventaris komt van de Hub-appcatalogus (B01-002 in
``ai-server-manager``, OpenAPI ``hub-api.openapi.json``):

* ``GET /api/v1/apps?alleen_aanwezig=false&limit=200[&cursor=…]`` — lijst + ``inventaris`` (status, observed_at,
  verouderd) van de laatste ``app.query``-meting;
* ``GET /api/v1/apps/{id}`` — detail: catalogus, images met digest-readback (``app.image.query``), poorten, opslag,
  datasets.

Aanroepen met ``Authorization: Bearer <service-token>`` en de vaste scope ``inventaris.lezen`` (contract 1.3 §4.4),
nooit redirects of proxy, begrensde antwoordgrootte, hooguit 3 pogingen bij een storing en bij 429/503 nooit eerder dan
``Retry-After`` (§5). Alles wat niet klopt wordt **nooit** als succes behandeld:

* niet gekoppeld, de Hub geeft de scope niet (403 ``scope_ontbreekt``; nooit een intrekking, §3.8), geeft dit niet aan
  diensten (403) of kent het niet (404) → ``capability_unavailable``;
* storing, 401, ongeldig antwoord, verouderde of onvolledige inventaris → ``unknown`` (er verandert dan niets).

Alleen een **volledige lijst uit één momentopname** telt (§5, paginering over een momentopname): elke pagina noemt
dezelfde ``snapshot_id`` (of, bij een Hub zonder ``snapshot_id``, dezelfde meting) en hetzelfde ``totaal``, en het
aantal gelezen apps is gelijk aan ``totaal``. Is de momentopname weg (409 ``snapshot_changed``), dan opnieuw beginnen
(hooguit twee keer). Anders ``unknown`` (``inventaris_gewijzigd_tijdens_lezen``/``inventaris_onvolledig``): een
mengsel van twee metingen of een afgekapte lijst zou apps ten onrechte als verdwenen markeren.

Versheid (§6.2): ``max_age`` standaard 900 s; geeft de Hub ``inventaris.max_age_s``, dan geldt de strengste van beide
(een afnemer is nooit soepeler dan de bron).
"""

from __future__ import annotations

import json
import logging
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..hublink import HubLinkClient, ResponseTooLargeError

log = logging.getLogger("appstore.import")

APPS_PATH = "/api/v1/apps"
PAGE_LIMIT = 200
MAX_PAGES = 10
MAX_APPS = 1000
MAX_LIST_BYTES = 4 * 1024 * 1024
MAX_DETAIL_BYTES = 1024 * 1024
ATTEMPTS = 3
BACKOFF_BASE = 0.5  # s; exponentieel met jitter (contract §5: max 3 pogingen)
MAX_AGE = timedelta(minutes=15)  # standaard max_age (§6.2); de Hub mag strenger zijn met inventaris.max_age_s
MAX_RETRY_AFTER = 30.0  # s: langer wachten doet de import niet in één ronde (eerlijk unknown, volgende ronde opnieuw)
SNAPSHOT_RESTARTS = 2
MAX_FUTURE = timedelta(minutes=2)

FetchStatus = Literal["ok", "unknown", "capability_unavailable"]

#: Vaste NL-melding per reden (de code is voor machines).
REASONS_NL: dict[str, str] = {
    "niet_gekoppeld": "De appstore is niet (meer) gekoppeld met de Hub; zonder Hub is de inventaris onbekend.",
    "scope_inventaris_ontbreekt": (
        "De Hub geeft deze dienst de leesscope inventaris.lezen niet (403 scope_ontbreekt). Een beheerder legt de "
        "scopes vast in de Hub; tot dan blijft de inventaris onbekend. Dit is geen intrekking van de koppeling."
    ),
    "hub_vraagt_wachten": (
        "De Hub vraagt langer te wachten (Retry-After); de import probeert het in een volgende ronde opnieuw."
    ),
    "hub_inventaris_niet_voor_diensten": (
        "De Hub geeft zijn app-inventaris (GET /api/v1/apps) niet aan diensten met een service-token. Deze Hub-versie "
        "moet dat eerst toestaan (alleen lezen); tot dan blijft de inventaris onbekend."
    ),
    "hub_kent_appcatalogus_niet": "Deze Hub-versie kent GET /api/v1/apps nog niet (appcatalogus B01-002).",
    "hub_weigert_token": (
        "De Hub weigerde het service-token (401). De koppeling is gecontroleerd met services/self; is die ingetrokken, "
        "dan moet de Hub opnieuw koppelen."
    ),
    "hub_onbereikbaar": "De Hub is niet bereikbaar of gaf een storing; de inventaris is onbekend.",
    "hub_redirect": "De Hub antwoordde met een doorverwijzing; die wordt nooit gevolgd.",
    "hub_antwoord_onverwacht": "De Hub gaf een onverwacht antwoord; de inventaris is onbekend.",
    "hub_antwoord_ongeldig": "Het antwoord van de Hub past niet in het verwachte schema; er is niets van gebruikt.",
    "hub_antwoord_te_groot": "Het antwoord van de Hub is groter dan toegestaan; er is niets van gebruikt.",
    "inventaris_niet_gemeten": "De Hub heeft (nog) geen geslaagde meting van de apps op de NAS.",
    "inventaris_verouderd": "De laatste app-meting van de Hub is verouderd; vernieuw eerst de inventaris in de Hub.",
    "inventaris_in_toekomst": "De meettijd van de Hub ligt in de toekomst (klokverschil); de meting is niet te vertrouwen.",
    "inventaris_te_groot": "De Hub meldt meer apps dan de appstore in één keer leest; er is niets gewijzigd.",
    "inventaris_gewijzigd_tijdens_lezen": (
        "De inventaris van de Hub veranderde tijdens het lezen (nieuwe meting of een app verdween); probeer het "
        "opnieuw."
    ),
    "inventaris_onvolledig": (
        "De Hub leverde een ander aantal apps dan hij zelf telt; een onvolledige lijst wordt nooit gebruikt."
    ),
}


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class HubImage(_Lenient):
    repository: str | None = Field(default=None, max_length=512)
    tag: str | None = Field(default=None, max_length=256)
    digest: str | None = Field(default=None, max_length=80)


class HubReadback(_Lenient):
    status: str | None = Field(default=None, max_length=32)
    reden: str | None = Field(default=None, max_length=128)
    digests: list[str] = Field(default_factory=list, max_length=50)


class HubImageDetail(HubImage):
    digest_bron: str | None = Field(default=None, max_length=32)
    readback: HubReadback | None = None


class HubPort(_Lenient):
    container_poort: int | None = Field(default=None, ge=0, le=65535)
    host_poort: int | None = Field(default=None, ge=0, le=65535)
    host_ip: str | None = Field(default=None, max_length=64)
    protocol: str | None = Field(default=None, max_length=8)


class HubMount(_Lenient):
    bron: str | None = Field(default=None, max_length=1024)
    doel: str | None = Field(default=None, max_length=1024)
    type: str | None = Field(default=None, max_length=32)
    dataset: str | None = Field(default=None, max_length=512)
    soort: str | None = Field(default=None, max_length=32)


class HubCatalog(_Lenient):
    app: str | None = Field(default=None, max_length=128)
    trein: str | None = Field(default=None, max_length=64)
    versie: str | None = Field(default=None, max_length=64)
    app_versie: str | None = Field(default=None, max_length=128)


class HubAppSummary(_Lenient):
    id: uuid.UUID
    runtime_id: str = Field(min_length=1, max_length=128)
    naam: str | None = Field(default=None, max_length=256)
    app_id: str | None = Field(default=None, max_length=64)
    installatiewijze: str | None = Field(default=None, max_length=32)
    image: HubImage = Field(default_factory=HubImage)
    runtime_staat: str | None = Field(default=None, max_length=64)
    aanwezig: bool
    laatst_gezien: datetime | None = None
    verdwenen_sinds: datetime | None = None


class HubAppDetail(HubAppSummary):
    catalogus: HubCatalog | None = None
    images: list[HubImageDetail] = Field(default_factory=list, max_length=50)
    poorten: list[HubPort] = Field(default_factory=list, max_length=200)
    opslag: list[HubMount] = Field(default_factory=list, max_length=200)
    datasets: list[str] = Field(default_factory=list, max_length=200)


class HubInventoryState(_Lenient):
    status: str | None = None
    reason_code: str | None = None
    observed_at: datetime | None = None
    verouderd: bool | None = None
    max_age_s: int | None = Field(default=None, ge=1, le=86400)


class HubListPage(_Lenient):
    items: list[HubAppSummary] = Field(max_length=PAGE_LIMIT)
    next_cursor: str | None = Field(default=None, max_length=512)
    totaal: int | None = Field(default=None, ge=0)
    snapshot_id: str | None = Field(default=None, max_length=128)
    inventaris: HubInventoryState


@dataclass
class FetchResult:
    status: FetchStatus
    reason_code: str | None = None
    detail: str | None = None
    observed_at: datetime | None = None
    apps: list[HubAppDetail] = field(default_factory=list)
    hub_reason_code: str | None = None
    calls: int = 0

    @property
    def message_nl(self) -> str | None:
        return REASONS_NL.get(self.reason_code) if self.reason_code else None

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "melding_nl": self.message_nl,
            "detail": self.detail,
            "hub_reason_code": self.hub_reason_code,
            "inventory_observed_at": self.observed_at.isoformat().replace("+00:00", "Z") if self.observed_at else None,
            "apps": len(self.apps),
            "hub_calls": self.calls,
        }


class _StopError(Exception):
    def __init__(self, status: FetchStatus, reason: str, detail: str | None = None) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.detail = detail


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _snapshot(page: HubListPage) -> tuple[Any, ...]:
    """Kenmerken van de momentopname waar een pagina uit komt; moeten op elke pagina gelijk zijn."""
    inv = page.inventaris
    observed = _aware(inv.observed_at) if inv.observed_at else None
    return page.snapshot_id, inv.status, inv.reason_code, observed, inv.verouderd, page.totaal


class _SnapshotGoneError(Exception):
    """409 ``snapshot_changed``: de momentopname bestaat niet meer; opnieuw beginnen (§5)."""


def _retry_after(headers: httpx.Headers) -> float | None:
    raw = headers.get("retry-after", "").strip()
    return float(raw) if raw.isdigit() else None


def _error_code(body: bytes) -> str | None:
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return str(data.get("code")) if isinstance(data, dict) and isinstance(data.get("code"), str) else None


class HubInventoryClient:
    def __init__(self, hub: HubLinkClient, *, sleep: Callable[[float], None] | None = None) -> None:
        self.hub = hub
        self.sleep = sleep or time.sleep
        self.calls = 0

    def _get(self, path: str, *, trace_id: str, max_bytes: int) -> Any:
        last: _StopError | None = None
        wait = 0.0
        for attempt in range(ATTEMPTS):
            if attempt:
                delay = min(4.0, BACKOFF_BASE * 2**attempt) + random.uniform(0, BACKOFF_BASE / 2)  # noqa: S311
                self.sleep(max(delay, wait))  # bij 429/503 nooit eerder dan Retry-After (§5)
            self.calls += 1
            try:
                res = self.hub.get_bounded(path, trace_id=trace_id, max_bytes=max_bytes)
            except ResponseTooLargeError as exc:
                raise _StopError("unknown", "hub_antwoord_te_groot", str(exc)) from exc
            except httpx.HTTPError as exc:
                last, wait = _StopError("unknown", "hub_onbereikbaar", type(exc).__name__), 0.0
                continue
            if res is None:
                raise _StopError("capability_unavailable", "niet_gekoppeld")
            status, body, headers = res
            if status == 200:
                try:
                    return json.loads(body)
                except ValueError as exc:
                    raise _StopError("unknown", "hub_antwoord_ongeldig", "geen JSON") from exc
            if status in (429, 503):
                after = _retry_after(headers)
                if after is not None and after > MAX_RETRY_AFTER:
                    raise _StopError("unknown", "hub_vraagt_wachten", f"http_{status}; retry_after={int(after)}")
                last, wait = _StopError("unknown", "hub_onbereikbaar", f"http_{status}"), after or 0.0
                continue
            if status >= 500:
                last, wait = _StopError("unknown", "hub_onbereikbaar", f"http_{status}"), 0.0
                continue
            if 300 <= status < 400:
                raise _StopError("unknown", "hub_redirect", f"http_{status}")
            if status == 401:
                if not self.hub.store.read().linked:
                    raise _StopError("capability_unavailable", "niet_gekoppeld", "ingetrokken")
                raise _StopError("unknown", "hub_weigert_token", "http_401")
            if status == 403:  # §4.4/§3.8: een 403 is nooit een intrekking
                if _error_code(body) == "scope_ontbreekt":
                    raise _StopError("capability_unavailable", "scope_inventaris_ontbreekt", "http_403")
                raise _StopError("capability_unavailable", "hub_inventaris_niet_voor_diensten", "http_403")
            if status == 404:
                raise _StopError("capability_unavailable", "hub_kent_appcatalogus_niet", "http_404")
            if status == 409 and _error_code(body) == "snapshot_changed":
                raise _SnapshotGoneError
            raise _StopError("unknown", "hub_antwoord_onverwacht", f"http_{status}")
        assert last is not None
        raise last

    def fetch(self, *, trace_id: str, now: datetime | None = None) -> FetchResult:
        now = now or datetime.now(UTC)
        self.calls = 0
        try:
            return self._fetch(trace_id, now)
        except _StopError as stop:
            log.warning("inventaris van de Hub niet bruikbaar: %s (%s)", stop.reason, stop.detail or "-")
            return FetchResult(stop.status, stop.reason, stop.detail, calls=self.calls)

    def _fetch(self, trace_id: str, now: datetime) -> FetchResult:
        if not self.hub.store.read().linked:
            raise _StopError("capability_unavailable", "niet_gekoppeld")
        for restart in range(SNAPSHOT_RESTARTS + 1):
            try:
                return self._fetch_snapshot(trace_id, now)
            except _SnapshotGoneError:
                log.info("momentopname van de Hub-inventaris verdween tijdens het lezen; opnieuw (%d)", restart + 1)
        raise _StopError("unknown", "inventaris_gewijzigd_tijdens_lezen", "409 snapshot_changed")

    def _fetch_snapshot(self, trace_id: str, now: datetime) -> FetchResult:
        summaries: list[HubAppSummary] = []
        cursor: str | None = None
        first: HubListPage | None = None
        for page_no in range(MAX_PAGES + 1):
            if page_no == MAX_PAGES:
                raise _StopError("unknown", "inventaris_te_groot", f"meer dan {MAX_PAGES} pagina's")
            path = f"{APPS_PATH}?alleen_aanwezig=false&limit={PAGE_LIMIT}"
            if cursor:
                path += f"&cursor={quote(cursor, safe='')}"
            raw = self._get(path, trace_id=trace_id, max_bytes=MAX_LIST_BYTES)
            try:
                page = HubListPage.model_validate(raw)
            except ValidationError as exc:
                raise _StopError("unknown", "hub_antwoord_ongeldig", f"lijst: {exc.error_count()} fout(en)") from exc
            if first is None:
                first = page
            elif _snapshot(page) != _snapshot(first):
                # de Hub pagineert met een offset over de laatste meting: een nieuwe meting (of een ander totaal)
                # tussen twee pagina's geeft een mengsel van twee metingen → nooit gebruiken (anders lijken apps
                # 'verdwenen' die alleen tussen de pagina's verschoven)
                raise _StopError("unknown", "inventaris_gewijzigd_tijdens_lezen", f"pagina {page_no + 1}")
            summaries.extend(page.items)
            if len(summaries) > MAX_APPS:
                raise _StopError("unknown", "inventaris_te_groot", f"meer dan {MAX_APPS} apps")
            if not page.next_cursor:
                break
            cursor = page.next_cursor
        assert first is not None
        state = first.inventaris
        if first.totaal is not None and len(summaries) != first.totaal:
            raise _StopError("unknown", "inventaris_onvolledig", f"{len(summaries)} gelezen, totaal {first.totaal}")
        observed = self._check_fresh(state, now)
        ids = [s.runtime_id for s in summaries]
        if len(ids) != len(set(ids)):
            raise _StopError("unknown", "hub_antwoord_ongeldig", "dubbele runtime_id")
        details: list[HubAppDetail] = []
        for s in summaries:
            try:
                raw = self._get(f"{APPS_PATH}/{s.id}", trace_id=trace_id, max_bytes=MAX_DETAIL_BYTES)
            except _StopError as stop:
                if stop.reason == "hub_kent_appcatalogus_niet":  # lijst wel, detail 404: app tussendoor verdwenen
                    raise _StopError("unknown", "inventaris_gewijzigd_tijdens_lezen", s.runtime_id) from stop
                raise
            try:
                detail = HubAppDetail.model_validate(raw)
            except ValidationError as exc:
                raise _StopError("unknown", "hub_antwoord_ongeldig", f"detail: {exc.error_count()} fout(en)") from exc
            if detail.id != s.id or detail.runtime_id != s.runtime_id:
                raise _StopError("unknown", "hub_antwoord_ongeldig", "detail hoort niet bij de lijst")
            details.append(detail)
        return FetchResult(
            "ok", observed_at=observed, apps=details, hub_reason_code=state.reason_code, calls=self.calls
        )

    @staticmethod
    def _check_fresh(state: HubInventoryState, now: datetime) -> datetime:
        if state.observed_at is None:
            raise _StopError("unknown", "inventaris_niet_gemeten", state.reason_code)
        observed = _aware(state.observed_at)
        if state.status != "ok" or state.verouderd:
            raise _StopError("unknown", "inventaris_verouderd", state.reason_code or state.status)
        if observed - now > MAX_FUTURE:
            raise _StopError("unknown", "inventaris_in_toekomst", observed.isoformat())
        if now - observed > max_age(state):
            raise _StopError("unknown", "inventaris_verouderd", observed.isoformat())
        return observed


def max_age(state: HubInventoryState) -> timedelta:
    """§6.2: standaard 900 s; geeft de Hub een eigen ``max_age_s``, dan de strengste (nooit soepeler dan de bron)."""
    if state.max_age_s is None:
        return MAX_AGE
    return min(MAX_AGE, timedelta(seconds=state.max_age_s))
