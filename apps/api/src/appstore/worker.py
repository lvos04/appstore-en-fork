"""Worker: synchronisatietaken, koppelen met het enrollment-token (contract 1.2 §3.6), bevestigen, token- en
sleutelrotatie (§3.8, §4.5), de outbox naar de Hub pushen (§7) en periodiek de app-inventaris van de Hub lezen
(B08-002, alleen lezen)."""

from __future__ import annotations

import logging
import random
import signal
import threading
import time
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from .api.common import AppContext
from .auth import purge_replay
from .bestaande_apps import load_register
from .catalog.adoption import ImportBusyError
from .catalog.decisions import ensure_register
from .catalog.importrun import run_import
from .catalog.service import ensure_bundled_source, sync_source
from .config import Settings
from .db import make_engine, make_sessionmaker
from .events import envelope, envelope_problems
from .hublink import HubLinkClient, ensure_link_file
from .hublink_store import HubLinkStore
from .models import CatalogSource, IdempotencyRecord, OutboxEvent, SyncJob
from .secrets_store import Secrets, ensure_secrets
from .timeutil import utcnow

log = logging.getLogger("appstore.worker")
MAX_DELIVERY_ATTEMPTS = 10


def enqueue_bundled_sync(db: Session) -> uuid.UUID | None:
    src = ensure_bundled_source(db)
    busy = db.scalar(select(SyncJob).where(SyncJob.source_id == src.id, SyncJob.status.in_(("queued", "running"))))
    if busy is not None or not src.enabled:
        return None
    job = SyncJob(
        id=uuid.uuid4(), source_id=src.id, status="queued", requested_by="system:start", requested_at=utcnow()
    )
    db.add(job)
    return job.id


def recover_interrupted(db: Session) -> int:
    rows = db.scalars(select(SyncJob).where(SyncJob.status == "running")).all()
    for job in rows:
        job.status, job.finished_at = "failed", utcnow()
        job.error = "Onderbroken door een herstart; vraag de synchronisatie opnieuw aan."
    return len(rows)


def process_one_job(c: AppContext) -> bool:
    """Pak één wachtende taak (FOR UPDATE SKIP LOCKED) en voer die uit. Geeft ``True`` als er iets gedaan is."""
    with c.sessions() as db:
        job = db.scalar(
            select(SyncJob)
            .where(SyncJob.status == "queued")
            .order_by(SyncJob.requested_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return False
        job.status, job.started_at = "running", utcnow()
        job_id, source_id = job.id, job.source_id
        db.commit()
    trace = str(uuid.uuid4())
    with c.sessions() as db:
        source = db.get(CatalogSource, source_id, with_for_update=True)
        job = db.get(SyncJob, job_id)
        assert job is not None
        if source is None or not source.enabled:
            job.status, job.finished_at, job.error = "failed", utcnow(), "Bron bestaat niet meer of staat uit."
            db.commit()
            return True
        try:
            outcome = sync_source(db, source, c.settings, c.secrets, c.http_factory, trace)
        except Exception as exc:  # nooit als succes loggen; taak eerlijk laten mislukken
            db.rollback()
            log.exception("synchronisatie mislukt")
            job = db.get(SyncJob, job_id)
            assert job is not None
            job.status, job.finished_at, job.error = "failed", utcnow(), f"Interne fout ({type(exc).__name__})."
            db.commit()
            return True
        job.status = "succeeded" if outcome.ok else "failed"
        job.finished_at = utcnow()
        job.result = outcome.as_dict()
        job.error = outcome.error_nl
        db.commit()
    return True


def try_enroll(c: AppContext) -> None:
    """Env-token (§3.2/§3.8): koppelt ook opnieuw als het token nieuw is, met backoff en 60-minutengrens."""
    result = c.hub.env_tick(str(uuid.uuid4()))
    if result is not None:
        log.info("koppelen met de Hub: %s", "gelukt" if result.ok else f"niet gelukt ({result.code})")


def hub_maintenance(c: AppContext) -> None:
    c.hub.maintenance_tick(str(uuid.uuid4()))


def push_outbox(c: AppContext, batch: int = 50) -> int:
    """Push events naar ``{HUB_URL}/api/v1/events/inbox`` (alleen als gekoppeld). Geeft het aantal afgeleverde events."""
    if not c.store.read().linked:
        return 0
    with c.sessions() as db:
        due = db.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.delivered_at.is_(None),
                OutboxEvent.dead_letter.is_(False),
                OutboxEvent.next_attempt_at <= utcnow(),
            )
            .order_by(OutboxEvent.seq)
            .limit(batch)
            .with_for_update(skip_locked=True)
        ).all()
        if not due:
            return 0
        delivered = 0
        trace = str(uuid.uuid4())
        for ev in due:
            body = envelope(ev)
            if problems := envelope_problems(body):
                # §7: vóór verzending tegen de grenzen van de inbox; zo'n event zou de Hub altijd weigeren
                log.error("event %s breekt de grenzen van de Hub-inbox: %s", ev.seq, "; ".join(problems))
                ev.dead_letter, ev.last_error = True, ("envelope_ongeldig: " + "; ".join(problems))[:500]
                continue
            try:
                resp = c.hub.call("POST", "/api/v1/events/inbox", json_body=body, trace_id=trace)
                if resp is None:  # intussen niet meer gekoppeld (ontkoppeld of ingetrokken)
                    break
                ok = resp.status_code in (200, 201, 202, 204, 409)  # 409 = al ontvangen (dedupe op event_id)
                err = None if ok else f"HTTP {resp.status_code}"
                if resp.status_code == 401:
                    # §3.8: de client controleerde al met services/self; alleen bij opnieuw 401 is het 'ingetrokken'
                    ev.last_error = err
                    break
            except httpx.HTTPError as exc:
                ok, err = False, type(exc).__name__
            if ok:
                ev.delivered_at = utcnow()
                delivered += 1
            else:
                ev.attempts += 1
                ev.last_error = err
                if ev.attempts >= MAX_DELIVERY_ATTEMPTS:
                    ev.dead_letter = True
                else:
                    delay = min(3600.0, 2.0**ev.attempts) + random.uniform(0, 1)  # noqa: S311
                    ev.next_attempt_at = utcnow() + timedelta(seconds=delay)
        db.commit()
        return delivered


def purge(c: AppContext) -> None:
    with c.sessions() as db:
        db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.created_at < utcnow() - timedelta(hours=24)))
        purge_replay(db)
        db.commit()


def periodic_import(c: AppContext) -> dict[str, Any] | None:
    """B08-002: de app-inventaris van de Hub lezen en het register bijwerken (alleen als gekoppeld en bevestigd).
    Nooit een actie op de NAS of in de Hub; een storing verandert niets."""
    st = c.store.read()
    if not st.linked or st.confirmed_at is None:
        return None
    with c.sessions() as db:
        try:
            result = run_import(db, c.hub, None, trace_id=str(uuid.uuid4()), now=utcnow())
        except ImportBusyError:
            db.rollback()
            return None
        db.commit()
    if result["status"] != "ok":
        log.warning("periodieke import: %s (%s)", result["status"], result["reason_code"])
    return result


def startup(c: AppContext) -> None:
    ensure_link_file(c.store, c.sessions, c.secrets)  # eenmalige overzetting uit de database (contract 1.2 §3.5)
    c.hub.recover_after_restart(process="worker")  # §3.6 stap 5
    with c.sessions() as db:
        recovered = recover_interrupted(db)
        enqueue_bundled_sync(db)
        ensure_register(db, load_register(), now=utcnow())  # beslisregister APP-01…APP-20 (idempotent)
        db.commit()
    if recovered:
        log.warning("%d onderbroken synchronisatie(s) als mislukt gemarkeerd", recovered)


def run_once(c: AppContext) -> dict[str, Any]:
    """Eén ronde van alle taken (ook bruikbaar in tests)."""
    jobs = 0
    while process_one_job(c):
        jobs += 1
    try_enroll(c)
    hub_maintenance(c)
    return {"jobs": jobs, "delivered": push_outbox(c)}


def build_context(settings: Settings, http_factory: Callable[[], httpx.Client] | None = None) -> AppContext:
    from .app import default_http_factory

    ensure_secrets(settings.secrets_dir)
    engine = make_engine(settings.database_url)
    sessions: sessionmaker[Session] = make_sessionmaker(engine)
    secrets = Secrets.load(settings.secrets_dir)
    factory = http_factory or default_http_factory(settings)
    store = HubLinkStore(settings.secrets_dir)
    return AppContext(
        settings=settings,
        engine=engine,
        sessions=sessions,
        secrets=secrets,
        http_factory=factory,
        store=store,
        hub=HubLinkClient(settings, store, secrets, factory, sessions),
    )


def main(settings: Settings | None = None) -> None:
    settings = settings or Settings.from_env()
    c = build_context(settings)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    startup(c)
    log.info("worker gestart")
    tick = 0
    next_import = time.monotonic() + 60.0  # eerst koppelen en de catalogus synchroniseren
    while not stop.is_set():
        try:
            while process_one_job(c) and not stop.is_set():
                pass
            try_enroll(c)  # goedkoop (leest alleen het bestand); de backoff zit in de client
            hub_maintenance(c)
            push_outbox(c)
            if settings.import_interval_s and time.monotonic() >= next_import:
                next_import = time.monotonic() + settings.import_interval_s
                periodic_import(c)
            if tick % 1800 == 0:
                purge(c)
        except Exception:
            log.exception("fout in de worker-lus; volgende ronde gaat door")
        tick += 1
        stop.wait(2)
    log.info("worker gestopt")
