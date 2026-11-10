"""Hartslag van de worker (contract 1.3 §6, check ``worker``: ``observed_at`` = laatste hartslag).

De worker schrijft hooguit elke 10 s een klein bestand ``/appdata/state/worker-hartslag.json`` (atomair, 0600); de API
leest het voor ``GET /connector/v1/health``. Een bestand en geen databaserij: zo meldt de check ook een worker die de
database niet meer bereikt, en blijft de hartslag buiten database-exports en -herstel.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

FILE = "worker-hartslag.json"
#: na zoveel seconden zonder hartslag is de meting verouderd (§6.2: ``limits.max_age_s.worker`` in ``discover``)
MAX_AGE_S = 120
MIN_GAP_S = 10.0

_state = {"last_write": 0.0}


def path(data_dir: Path) -> Path:
    return data_dir / "state" / FILE


def beat(data_dir: Path, *, force: bool = False) -> bool:
    """Schrijf de hartslag (hooguit elke 10 s, tenzij ``force``). Geeft ``True`` als er geschreven is."""
    now = time.monotonic()
    if not force and now - _state["last_write"] < MIN_GAP_S:
        return False
    target = path(data_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "laatste_hartslag": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "pid": os.getpid(),
    }
    tmp = target.with_name(f".{FILE}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    _state["last_write"] = now
    return True


def last(data_dir: Path) -> datetime | None:
    """Tijd van de laatste hartslag, of ``None`` (nooit gemeten of onleesbaar)."""
    try:
        data = json.loads(path(data_dir).read_text(encoding="utf-8"))
        value = datetime.fromisoformat(str(data["laatste_hartslag"]).replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)
