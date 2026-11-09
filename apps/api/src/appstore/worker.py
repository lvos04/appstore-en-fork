"""Worker: synchronisatietaken, automatisch koppelen met het enrollment-token en de outbox naar de Hub pushen."""

from __future__ import annotations

import logging
import random
import signal
import threading
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from .api.common import AppContext
from .catalog.service import ensure_bundled_source, sync_source
from .config import Settings
from .db import make_engine, make_sessionmaker
from .events import envelope
from .hublink import auto_enroll, mark_revoked, service_token
from .models import CatalogSource, HubLink, IdempotencyRecord, OutboxEvent, SyncJob
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
    with c.sessions() as db:
        result = auto_enroll(db, c.settings, c.secrets, c.http_factory, str(uuid.uuid4()))
        db.commit()
    if result is not None:
        log.info("koppelen met de Hub: %s", "gelukt" if result.ok else "mislukt")


def push_outbox(c: AppContext, batch: int = 50) -> int:
    """Push events naar ``{HUB_URL}/api/v1/events/inbox`` (alleen als gekoppeld). Geeft het aantal afgeleverde events."""
    with c.sessions() as db:
        link = db.get(HubLink, 1)
        if link is None or link.status != "linked" or not link.hub_url:
            return 0
        token = service_token(db, c.secrets)
        hub_url = link.hub_url
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
        if not due or token is None:
            return 0
        delivered = 0
        with c.http_factory() as client:
            for ev in due:
                try:
                    resp = client.post(
                        f"{hub_url}/api/v1/events/inbox",
                        json=envelope(ev),
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    ok = resp.status_code in (200, 201, 202, 204, 409)  # 409 = al ontvangen (dedupe op event_id)
                    if resp.status_code == 401:
                        mark_revoked(db, "De Hub weigert het service-token (401); koppel opnieuw via een koppelcode.")
                        ev.last_error = "HTTP 401"
                        break
                    err = None if ok else f"HTTP {resp.status_code}"
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
        db.commit()


def startup(c: AppContext) -> None:
    with c.sessions() as db:
        recovered = recover_interrupted(db)
        enqueue_bundled_sync(db)
        db.commit()
    if recovered:
        log.warning("%d onderbroken synchronisatie(s) als mislukt gemarkeerd", recovered)


def run_once(c: AppContext) -> dict[str, Any]:
    """Eén ronde van alle taken (ook bruikbaar in tests)."""
    jobs = 0
    while process_one_job(c):
        jobs += 1
    try_enroll(c)
    return {"jobs": jobs, "delivered": push_outbox(c)}


def build_context(settings: Settings, http_factory: Callable[[], httpx.Client] | None = None) -> AppContext:
    from .app import default_http_factory

    ensure_secrets(settings.secrets_dir)
    engine = make_engine(settings.database_url)
    sessions: sessionmaker[Session] = make_sessionmaker(engine)
    return AppContext(
        settings=settings,
        engine=engine,
        sessions=sessions,
        secrets=Secrets.load(settings.secrets_dir),
        http_factory=http_factory or default_http_factory(settings),
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
    while not stop.is_set():
        try:
            while process_one_job(c) and not stop.is_set():
                pass
            if tick % 8 == 0:
                try_enroll(c)
            push_outbox(c)
            if tick % 1800 == 0:
                purge(c)
        except Exception:
            log.exception("fout in de worker-lus; volgende ronde gaat door")
        tick += 1
        stop.wait(2)
    log.info("worker gestopt")
