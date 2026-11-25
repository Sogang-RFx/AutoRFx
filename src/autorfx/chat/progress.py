"""Counting the statements of a draft that is still being written (docs/design/immersive-ui.md §5.2).

The model streams one JSON document. A statement counts once its object is closed, so a half-written one
is never reported. The scanner follows string and escape state and nesting, because a statement's own text
may hold quotes, braces, brackets and even the word ``"text"``.
"""

from __future__ import annotations

STATEMENTS_KEY = "statements"
ROOT_DEPTH = 1
"""Stack size inside the top-level object: its keys and values live here."""
LIST_DEPTH = 2
"""Stack size inside the ``statements`` array: each closed object at this level is one statement."""


class StatementCounter:
    """Feed pieces of the document in order; ``count`` is the number of closed statements so far."""

    def __init__(self) -> None:
        self.count = 0
        self._stack: list[str] = []
        self._in_string = False
        self._escaped = False
        self._expect_key = False
        self._key: list[str] = []
        self._last_key = ""
        self._in_list = False

    def feed(self, piece: str) -> int:
        for char in piece:
            if self._in_string:
                self._in_text(char)
            else:
                self._outside(char)
        return self.count

    # ── inside a string ─────────────────────────────────────────────────────
    def _in_text(self, char: str) -> None:
        if self._escaped:
            self._escaped = False
            self._collect(char)
        elif char == "\\":
            self._escaped = True
        elif char == '"':
            self._in_string = False
            if self._reading_key():
                self._last_key, self._key = "".join(self._key), []
                self._expect_key = False
        else:
            self._collect(char)

    def _reading_key(self) -> bool:
        return len(self._stack) == ROOT_DEPTH and self._expect_key

    def _collect(self, char: str) -> None:
        if self._reading_key():
            self._key.append(char)

    # ── structure ───────────────────────────────────────────────────────────
    def _outside(self, char: str) -> None:
        if char == '"':
            self._in_string = True
        elif char == "{":
            self._stack.append("{")
            self._expect_key = len(self._stack) == ROOT_DEPTH
        elif char == "[":
            self._open_list()
        elif char == "}":
            self._close("{")
        elif char == "]":
            self._close("[")
        elif char == "," and len(self._stack) == ROOT_DEPTH:
            self._expect_key = True

    def _open_list(self) -> None:
        opens_statements = len(self._stack) == ROOT_DEPTH and self._last_key == STATEMENTS_KEY
        self._stack.append("[")
        self._in_list = self._in_list or opens_statements

    def _close(self, opener: str) -> None:
        if not self._stack or self._stack[-1] != opener:
            return  # malformed input: keep what was counted
        self._stack.pop()
        if opener == "{" and self._in_list and len(self._stack) == LIST_DEPTH:
            self.count += 1
        if opener == "[" and len(self._stack) == ROOT_DEPTH:
            self._in_list = False


def count_statements(partial_json: str) -> int:
    """How many objects of ``statements`` are closed in this (possibly cut-off) draft."""
    return StatementCounter().feed(partial_json)
