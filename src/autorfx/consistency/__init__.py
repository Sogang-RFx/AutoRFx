"""Cross-document consistency: entity mentions, normalisation, and the four kinds of issue."""

from autorfx.consistency.checks import ConsistencyReport, check_package
from autorfx.consistency.mentions import extract_mentions

__all__ = ["ConsistencyReport", "check_package", "extract_mentions"]
