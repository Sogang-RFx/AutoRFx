"""Receive a package: validate, store the originals read-only, make canonical PDFs.

The originals are copied byte for byte and never opened for writing (INV-1).
The RFQ id is derived from the content, so the same package always maps to the
same workspace and re-processing it is idempotent (PKG-05, INV-7).
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import stat
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from autorfx import guard
from autorfx.config import Settings
from autorfx.ingest.convert import ConversionError, make_canonical
from autorfx.jobs import JobReporter, NullReporter
from autorfx.models import FileKind, FileRecord, PackageManifest
from autorfx.workspace import Workspace

log = logging.getLogger(__name__)

KINDS: dict[str, FileKind] = {".pdf": "pdf", ".doc": "doc", ".docx": "docx", ".xlsx": "xlsx"}
MAGIC: dict[FileKind, bytes] = {
    "pdf": b"%PDF",
    "doc": b"\xd0\xcf\x11\xe0",
    "docx": b"PK\x03\x04",
    "xlsx": b"PK\x03\x04",
}
ACCEPT = tuple(KINDS)


class IngestError(ValueError):
    """The package was refused. The message names the file and the reason."""


@dataclass(frozen=True)
class SamplePackage:
    id: str
    label: str
    files: tuple[Path, ...]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def natural_key(name: str) -> list[object]:
    """Sort key that orders "file2" before "file10"."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", name)]


def package_digest(pairs: Iterable[tuple[str, str]]) -> str:
    """SHA-256 over the sorted ``name<TAB>sha256`` lines of the files."""
    lines = sorted(f"{name}\t{sha}\n" for name, sha in pairs)
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return slug[:63] or "package"


def _kind_of(path: Path) -> FileKind | None:
    return KINDS.get(path.suffix.casefold())


def list_samples(settings: Settings) -> tuple[SamplePackage, ...]:
    """Packages already on the server: one per sub-folder of the development corpus."""
    root = settings.samples_dir
    if not root.is_dir() or guard.is_held_out(root):
        return ()
    samples: list[SamplePackage] = []
    for folder in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: natural_key(p.name)):
        if guard.is_held_out(folder) or not guard.is_rfq_file(folder):
            continue
        files = sorted(
            (p for p in folder.rglob("*") if p.is_file() and guard.is_rfq_file(p) and _kind_of(p)),
            key=lambda p: natural_key(p.name),
        )
        if files:
            samples.append(SamplePackage(id=_slug(folder.name), label=folder.name, files=tuple(files)))
    return tuple(samples)


def _validate(sources: Sequence[Path], settings: Settings) -> list[tuple[Path, FileKind]]:
    if not sources:
        raise IngestError("파일이 없습니다. RFQ 파일을 하나 이상 올려 주세요.")
    if len(sources) > settings.max_files:
        raise IngestError(
            f"파일이 너무 많습니다 ({len(sources)}개). 한 번에 {settings.max_files}개까지 받습니다."
        )
    checked: list[tuple[Path, FileKind]] = []
    names: set[str] = set()
    limit = settings.max_file_mb * 1024 * 1024
    for source in sources:
        path = Path(source)
        name = path.name
        try:
            guard.assert_not_held_out(path)
        except guard.GuardError as exc:
            raise IngestError(str(exc)) from exc
        if not path.is_file():
            raise IngestError(f"'{name}': 파일을 찾을 수 없습니다.")
        if not guard.is_rfq_file(path):
            raise IngestError(f"'{name}': 안내 파일은 RFQ가 아니므로 받지 않습니다.")
        kind = _kind_of(path)
        if kind is None:
            raise IngestError(f"'{name}': 지원하지 않는 형식입니다. 받는 형식: {', '.join(ACCEPT)}")
        if name.casefold() in names:
            raise IngestError(f"'{name}': 같은 이름의 파일이 두 번 들어왔습니다.")
        names.add(name.casefold())
        size = path.stat().st_size
        if size == 0:
            raise IngestError(f"'{name}': 빈 파일입니다.")
        if size > limit:
            raise IngestError(f"'{name}': 파일이 너무 큽니다. 파일당 {settings.max_file_mb}MB까지 받습니다.")
        with path.open("rb") as handle:
            head = handle.read(8)
        if not head.startswith(MAGIC[kind]):
            raise IngestError(f"'{name}': 내용이 확장자({path.suffix})와 맞지 않습니다.")
        checked.append((path, kind))
    return sorted(checked, key=lambda item: natural_key(item[0].name))


def _store_original(source: Path, target: Path, expected: str) -> None:
    """Copy byte for byte, make read-only, and check the copy against the source hash."""
    if target.is_file() and sha256_file(target) == expected:
        return
    if target.exists():
        target.chmod(stat.S_IRUSR | stat.S_IWUSR)
        target.unlink()
    shutil.copyfile(source, target)
    target.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    if sha256_file(target) != expected:
        raise IngestError(f"'{source.name}': 보관한 사본이 원본과 다릅니다.")


def identify_package(sources: Sequence[Path], settings: Settings) -> str:
    """The id a package will get, after the validation that ingestion applies. Nothing is written."""
    files = _validate(sources, settings)
    return _rfq_id(package_digest((path.name, sha256_file(path)) for path, _ in files))


def _rfq_id(digest: str) -> str:
    return f"rfq-{digest[:12]}"


def ingest_package(
    sources: Sequence[Path],
    *,
    name: str,
    settings: Settings,
    is_sample: bool,
    now_ms: int,
    reporter: JobReporter | None = None,
) -> tuple[Workspace, PackageManifest]:
    """Validate, store and convert a package. Returns its workspace and manifest."""
    report = reporter or NullReporter()
    try:
        files = _validate(sources, settings)
        hashes = [sha256_file(path) for path, _ in files]
    except IngestError as exc:
        report.fail("store", str(exc))
        raise
    digest = package_digest((path.name, sha) for (path, _), sha in zip(files, hashes, strict=True))
    workspace = Workspace.for_rfq(settings.work_dir, _rfq_id(digest)).ensure()

    report.update("store", "running", f"파일 {len(files)}개")
    try:
        for (path, _), sha in zip(files, hashes, strict=True):
            _store_original(path, workspace.original_path(path.name), sha)
    except (IngestError, OSError) as exc:
        report.fail("store", str(exc))
        raise IngestError(str(exc)) from exc
    report.update("store", "done", f"파일 {len(files)}개 보관, 해시 확인")

    report.update("canonical", "running", f"0/{len(files)} 파일")
    records: list[FileRecord] = []
    for index, ((path, kind), sha) in enumerate(zip(files, hashes, strict=True), start=1):
        file_id = f"d{index:02d}"
        try:
            result = make_canonical(workspace.original_path(path.name), kind, workspace, file_id)
        except ConversionError as exc:
            message = f"'{path.name}': {exc}"
            report.fail("canonical", message)
            raise IngestError(message) from exc
        records.append(
            FileRecord(
                id=file_id,
                name=path.name,
                kind=kind,
                page_count=result.page_count,
                sha256=sha,
                canonical_tool=result.tool,
                canonical_sha256=result.sha256,
                warnings=result.warnings,
            )
        )
        report.update("canonical", "running", f"{index}/{len(files)} 파일")
    report.update("canonical", "done", f"파일 {len(files)}개, {sum(r.page_count for r in records)}쪽")

    manifest = PackageManifest(
        id=workspace.rfq_id,
        name=name,
        files=tuple(records),
        page_count=sum(r.page_count for r in records),
        sha256=digest,
        is_sample=is_sample,
        created_at=now_ms,
    )
    workspace.save_manifest(manifest)
    log.info("ingested %s: %d files, %d pages", manifest.id, len(records), manifest.page_count)
    return workspace, manifest
