"""Path guards.

Two rules that code must not be able to break by accident:

* The held-out evaluation RFQs are never read during development. Any input
  path with a component named ``eval`` (``eval/``, ``eval.zip``, ``eval_v2`` …)
  is refused.
* Originals are never modified (INV-1). Everything the pipeline writes goes
  under the work or cache directory, never next to a source file.
"""

from __future__ import annotations

import re
from pathlib import Path

_HELD_OUT = re.compile(r"^eval([._\-].*)?$", re.IGNORECASE)
_NOT_RFQ = ("_", ".")


class GuardError(PermissionError):
    """A path was refused. The message says which rule."""


def is_held_out(path: Path) -> bool:
    return any(_HELD_OUT.match(part) for part in Path(path).parts)


def assert_not_held_out(path: Path) -> Path:
    """Refuse the held-out evaluation set."""
    if is_held_out(path):
        raise GuardError(f"평가용(eval) 자료는 개발 중에 읽을 수 없습니다: {path}")
    return path


def assert_inside(path: Path, root: Path) -> Path:
    """Refuse a path that resolves outside ``root`` (path traversal, symlinks)."""
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(Path(root).resolve()):
        raise GuardError(f"허용된 폴더 밖의 경로입니다: {path}")
    return resolved


def assert_writable(path: Path, *roots: Path) -> Path:
    """Allow writes only under one of ``roots`` (the work and cache directories)."""
    resolved = Path(path).resolve()
    if not any(resolved.is_relative_to(Path(root).resolve()) for root in roots):
        raise GuardError(f"산출물은 작업 폴더에만 쓸 수 있습니다: {path}")
    return resolved


def is_rfq_file(path: Path) -> bool:
    """False for guide files that sit beside RFQs: ``_SUMMARY.md``, ``README.md``, dotfiles."""
    name = Path(path).name
    return not name.startswith(_NOT_RFQ) and name.lower() != "readme.md"
