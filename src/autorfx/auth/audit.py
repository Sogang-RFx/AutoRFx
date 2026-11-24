"""Audit log: ``work/_auth/audit.jsonl``, append only.

It says who did what to which id. Passwords, tokens, RFQ text and questions are
never written. An entry that cannot be written is an error for the caller: an
action that must be recorded does not go through unrecorded in silence.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from autorfx.auth.store import AUTH_DIR, AuthError, private_folder

log = logging.getLogger(__name__)

Outcome = Literal["ok", "denied", "failed"]
RECENT = 200
MAX_FIELD = 200
FIELDS = {"at", "actor", "event", "target", "outcome", "detail"}


class AuditLog:
    def __init__(self, work_dir: Path, *, now: Callable[[], float] = time.time) -> None:
        self.path = Path(work_dir) / AUTH_DIR / "audit.jsonl"
        self._now = now
        self._lock = threading.Lock()

    def record(
        self,
        event: str,
        *,
        actor: str | None,
        target: str | None = None,
        outcome: Outcome = "ok",
        detail: str = "",
    ) -> None:
        entry = {
            "at": int(self._now() * 1000),
            "actor": actor,
            "event": event,
            "target": target[:MAX_FIELD] if target is not None else None,
            "outcome": outcome,
            "detail": detail[:MAX_FIELD],
        }
        line = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8", "replace")
        try:
            with self._lock:
                self._append(line)
        except OSError as exc:
            log.exception("audit log cannot be written")
            raise AuthError(
                503, "audit_unavailable", "감사 기록을 남길 수 없어 처리하지 않았습니다."
            ) from exc

    def _append(self, line: bytes) -> None:
        private_folder(self.path.parent)
        handle = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(handle, "ab") as out:
            out.write(line)

    def recent(self, limit: int = RECENT) -> list[dict]:
        """The last entries, newest first."""
        try:
            with self.path.open("rb") as lines:
                tail = deque(lines, maxlen=limit)
        except FileNotFoundError:
            return []
        except OSError as exc:
            log.exception("audit log cannot be read")
            raise AuthError(503, "audit_unavailable", "감사 기록을 읽을 수 없습니다.") from exc
        found = [entry for line in tail if (entry := _parsed(line)) is not None]
        return found[::-1]


def _parsed(line: bytes) -> dict | None:
    try:
        entry = json.loads(line)
    except ValueError:
        entry = None
    if not isinstance(entry, dict) or set(entry) != FIELDS:
        # A line cut off by a crash; the others are still served.
        log.warning("audit log: a line that cannot be read was left out")
        return None
    return entry
