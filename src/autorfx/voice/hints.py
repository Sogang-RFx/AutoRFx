"""The recognition hint: words of the rules, never of a document (docs/design/immersive-ui.md §6, INV-8)."""

from __future__ import annotations

from autorfx.rules import Ruleset
from autorfx.voice.limits import MAX_HINT_CHARS

SEPARATOR = ", "


def recognition_hint(ruleset: Ruleset, *, limit: int = MAX_HINT_CHARS) -> str:
    """Glossary words, what a user says first and the document wordings after, cut at ``limit`` characters."""
    spoken = (word for entry in ruleset.glossary for word in entry.terms)
    written = (word for entry in ruleset.glossary for word in entry.expands_to)
    chosen: list[str] = []
    seen: set[str] = set()
    size = 0
    for word in (*spoken, *written):
        word = word.strip()
        if not word or word.lower() in seen:
            continue
        added = size + len(word) + (len(SEPARATOR) if chosen else 0)
        if added > limit:
            break
        seen.add(word.lower())
        chosen.append(word)
        size = added
    return SEPARATOR.join(chosen)
