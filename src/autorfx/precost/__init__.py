"""Pre-cost and Go/No-Go: RFQ type, knockout questions, cost items, lifetime minimum quote,
shock range, NPV for reference, weighted score. Deterministic; no number comes from an LLM."""

from autorfx.precost.calc import PrecostInputError, RfqCostContext, evaluate, money
from autorfx.precost.company import Company, CompanyError, CompanyStore, StoredAnswer
from autorfx.precost.inputs import extract_cost_inputs
from autorfx.precost.lines import rfq_lines
from autorfx.precost.setup import award_mode, classify, pass_through, prepare, requirements

__all__ = [
    "Company",
    "CompanyError",
    "CompanyStore",
    "PrecostInputError",
    "RfqCostContext",
    "StoredAnswer",
    "award_mode",
    "classify",
    "evaluate",
    "extract_cost_inputs",
    "money",
    "pass_through",
    "prepare",
    "requirements",
    "rfq_lines",
]
