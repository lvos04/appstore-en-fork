"""``appstore`` — dienstcommando's (in de image via de procesbewaker; voor ontwikkeling ook los).

appstore serve            API + statuspagina (uvicorn)
appstore worker           synchronisatie, koppelen, outbox
appstore migrate [--down REV]
appstore db-pending       exit 0 als er migraties wachten, 1 als het schema actueel is
appstore init-secrets     /appdata/secrets aanmaken (nooit overschrijven)
appstore hub-link-init    hub-link.json aanmaken; eenmalige overzetting van een oude koppeling uit de database
appstore openapi [--out PAD]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="appstore")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    sub.add_parser("worker")
    m = sub.add_parser("migrate")
    m.add_argument("--down", default=None, help="terug naar deze revisie (bv. base)")
    sub.add_parser("db-pending")
    sub.add_parser("init-secrets")
    sub.add_parser("hub-link-init")
    o = sub.add_parser("openapi")
    o.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    from .config import Settings

    settings = Settings.from_env()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("alembic").setLevel(logging.WARNING)

    if args.cmd == "serve":
        import uvicorn

        from .app import create_app

        uvicorn.run(
            create_app(settings),
            host=settings.host,
            port=settings.port,
            proxy_headers=False,
            server_header=False,
            log_level=settings.log_level,
        )
        return 0
    if args.cmd == "worker":
        from .worker import main as worker_main

        worker_main(settings)
        return 0
    if args.cmd == "migrate":
        from . import migrations

        if args.down:
            migrations.downgrade(settings.database_url, args.down)
        else:
            migrations.upgrade(settings.database_url)
        return 0
    if args.cmd == "db-pending":
        from . import migrations
        from .db import make_engine

        return 0 if migrations.pending(make_engine(settings.database_url), settings.database_url) else 1
    if args.cmd == "init-secrets":
        from .secrets_store import ensure_secrets

        created = ensure_secrets(settings.secrets_dir)
        print("nieuw aangemaakt: " + (", ".join(created) if created else "niets (alles bestond al)"))
        return 0
    if args.cmd == "hub-link-init":
        from .db import make_engine, make_sessionmaker
        from .hublink import ensure_link_file
        from .hublink_store import HubLinkStore
        from .secrets_store import Secrets

        engine = make_engine(settings.database_url)
        try:
            outcome = ensure_link_file(
                HubLinkStore(settings.secrets_dir), make_sessionmaker(engine), Secrets.load(settings.secrets_dir)
            )
        finally:
            engine.dispose()
        print(f"koppelstatus: {outcome}")
        return 0
    if args.cmd == "openapi":
        from .app import openapi_document

        text = json.dumps(openapi_document(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 0
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
