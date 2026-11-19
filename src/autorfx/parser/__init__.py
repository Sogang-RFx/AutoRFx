"""Parsing (PARSE-01..04): canonical PDF or sheet layout to sentences with positions and sections."""

from __future__ import annotations

from autorfx.models import FileRecord, ParsedFile
from autorfx.parser.pdf import ParseError, parse_pdf
from autorfx.parser.sentences import detect_language, split_sentences
from autorfx.parser.sheets import parse_sheets
from autorfx.workspace import Workspace

__all__ = ["ParseError", "detect_language", "parse_file", "split_sentences"]


def parse_file(workspace: Workspace, file: FileRecord) -> ParsedFile:
    """Parse one file: spreadsheets from their layout, everything else from the canonical PDF."""
    layout = workspace.layout(file.id)
    if layout is not None and layout.rows:
        return parse_sheets(file.id, layout, file.page_count)
    return parse_pdf(file.id, workspace.canonical_pdf(file.id))
