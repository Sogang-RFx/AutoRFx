"""Progress events of a chat answer (docs/design/immersive-ui.md §5, §5.1).

Only stage names and counts are ever sent: the words of a draft are not shown before they are verified
(CITE-01, CITE-02, INV-3). The observer watches; it cannot change an answer, and when it fails the answer
is the same.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Literal

log = logging.getLogger(__name__)

Stage = Literal["plan", "search", "write", "verify", "rewrite"]
Observer = Callable[[dict], None]


class Events:
    """Sends events to an observer, or nowhere. A listener that raises is logged once and then ignored."""

    def __init__(self, observer: Observer | None = None) -> None:
        self._observer = observer

    @property
    def watched(self) -> bool:
        return self._observer is not None

    def stage(self, stage: Stage, *, evidence: int | None = None) -> None:
        event: dict = {"type": "stage", "stage": stage}
        if evidence is not None:
            event["evidence"] = evidence
        self._send(event)

    def progress(self, drafted: int) -> None:
        self._send({"type": "progress", "drafted": drafted})

    def _send(self, event: dict) -> None:
        if self._observer is None:
            return
        try:
            self._observer(event)
        except Exception as exc:  # a broken listener is no reason to lose an answer (ENGINE-02)
            # The kind only: a listener's message could hold text it was handed.
            log.warning("chat observer failed (%s); not called again", type(exc).__name__)
            self._observer = None
