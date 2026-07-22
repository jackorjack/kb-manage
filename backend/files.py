"""Filesystem validation and atomic document publication."""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple


ALLOWED_EXTENSIONS = {".md", ".docx"}


class FileValidationError(ValueError):
    pass


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def resolve_knowledge_base_path(raw_path: str) -> Path:
    raw_path = raw_path.strip()
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        raise FileValidationError("knowledge-base path must be absolute")
    if candidate.exists() and candidate.is_symlink():
        raise FileValidationError("knowledge-base path must not be a symbolic link")
    if candidate.exists() and not candidate.is_dir():
        raise FileValidationError("knowledge-base path must be a directory")
    if not candidate.exists():
        if not candidate.parent.exists() or not candidate.parent.is_dir():
            raise FileValidationError("parent directory does not exist")
        candidate.mkdir(parents=False, exist_ok=True)
    resolved = candidate.resolve()
    if resolved == Path(resolved.anchor):
        raise FileValidationError("filesystem root cannot be a knowledge base")
    return resolved


def assert_paths_do_not_overlap(candidate: Path, existing_paths: Iterable[Path]) -> None:
    for existing in existing_paths:
        existing = existing.resolve()
        if candidate == existing or _is_relative_to(candidate, existing) or _is_relative_to(existing, candidate):
            raise FileValidationError("knowledge-base directories must not overlap")


def validate_upload_filename(raw_name: str) -> Tuple[str, str, str]:
    if not raw_name or "\x00" in raw_name:
        raise FileValidationError("filename is required")
    if "/" in raw_name or "\\" in raw_name:
        raise FileValidationError("subdirectories are not supported")
    name = Path(raw_name).name
    if name in {".", ".."} or len(name) > 240:
        raise FileValidationError("invalid filename")
    extension = Path(name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise FileValidationError("only .docx and .md files are supported")
    stem = Path(name).stem.strip()
    if not stem or stem in {".", ".."}:
        raise FileValidationError("filename must have a name")
    return name, extension, f"{stem}.md"


def document_id(knowledge_base_id: str, name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"kb-manage:{knowledge_base_id}:{name}"))


def assert_target_path(knowledge_base_path: Path, name: str) -> Path:
    if "/" in name or "\\" in name or name in {".", ".."}:
        raise FileValidationError("invalid document name")
    target = (knowledge_base_path / name).resolve()
    if target.parent != knowledge_base_path.resolve():
        raise FileValidationError("document path escapes knowledge-base directory")
    if target.exists() and target.is_symlink():
        raise FileValidationError("symbolic-link documents are not supported")
    return target


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def save_upload(upload: Any, destination: Path, max_bytes: int) -> int:
    size = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise FileValidationError("file exceeds upload size limit")
            output.write(chunk)
    return size


def normalize_markdown(markdown: str, max_bytes: int) -> bytes:
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if normalized and not normalized.endswith("\n"):
        normalized += "\n"
    data = normalized.encode("utf-8")
    if len(data) > max_bytes:
        raise FileValidationError("converted Markdown exceeds output size limit")
    return data


def publish_staged_outputs(
    knowledge_base_path: Path,
    outputs: Sequence[Dict[str, str]],
    staging_dir: Path,
) -> List[Path]:
    """Publish a completed batch and restore old files if publication fails."""

    backup_dir = staging_dir / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    committed: List[Tuple[Path, Path]] = []
    try:
        for item in outputs:
            target = assert_target_path(knowledge_base_path, item["target_name"])
            staged = Path(item["output_path"]).resolve()
            if not staged.is_file() or staged.is_symlink():
                raise FileValidationError("staged output is missing or invalid")
            backup = backup_dir / item["target_name"]
            if target.exists():
                if not target.is_file():
                    raise FileValidationError("target path is not a regular file")
                target.replace(backup)
            staged.replace(target)
            committed.append((target, backup))
        return [target for target, _ in committed]
    except Exception:
        for target, backup in reversed(committed):
            if target.exists() or target.is_symlink():
                target.unlink()
            if backup.exists():
                backup.replace(target)
        raise


def remove_document_file(knowledge_base_path: Path, name: str) -> bool:
    target = assert_target_path(knowledge_base_path, name)
    if not target.exists():
        return False
    if not target.is_file():
        raise FileValidationError("document is not a regular file")
    target.unlink()
    return True


def list_markdown_files(knowledge_base_path: Path) -> List[Path]:
    if not knowledge_base_path.exists() or not knowledge_base_path.is_dir():
        return []
    return sorted(
        (
            entry
            for entry in knowledge_base_path.iterdir()
            if entry.is_file() and not entry.is_symlink() and entry.suffix.lower() == ".md"
        ),
        key=lambda value: value.name.lower(),
    )


def remove_staging_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
