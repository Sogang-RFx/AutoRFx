"""Package intake (PKG-01..05, DOC-01..02): store originals untouched, make canonical PDFs."""

from autorfx.ingest.convert import ConversionError
from autorfx.ingest.store import (
    ACCEPT,
    IngestError,
    SamplePackage,
    identify_package,
    ingest_package,
    list_samples,
    package_digest,
    sha256_file,
)

__all__ = [
    "ACCEPT",
    "ConversionError",
    "IngestError",
    "SamplePackage",
    "identify_package",
    "ingest_package",
    "list_samples",
    "package_digest",
    "sha256_file",
]
