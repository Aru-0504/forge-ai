"""Upload validation: extension allowlist, size bounds, magic-byte & archive checks."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

from forgeai.security.sanitize import sanitize_filename

ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".pdf", ".docx", ".html", ".htm"}
)

# Dangerous executable/script magic headers
_BLOCKED_MAGIC_PREFIXES: tuple[bytes, ...] = (
    b"MZ",              # Windows PE/EXE/DLL
    b"\x7fELF",         # Linux ELF binary
    b"\xca\xfe\xba\xbe",  # Mach-O / Java class
    b"\xcf\xfa\xed\xfe",  # Mach-O 64-bit
    b"#!/",             # Shell script shebang
)

_MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024  # 50 MB zip-bomb cap
_MAX_DOCX_ENTRIES = 500


class UploadValidationError(ValueError):
    """Raised when an uploaded file violates security or size constraints."""


@dataclass(frozen=True)
class ValidatedUpload:
    """Sanitized and validated uploaded file payload."""

    filename: str
    extension: str
    content: bytes
    size_bytes: int


def validate_upload(
    filename: str,
    content: bytes,
    *,
    max_bytes: int = 10 * 1024 * 1024,
    allowed_extensions: frozenset[str] = ALLOWED_EXTENSIONS,
) -> ValidatedUpload:
    """Validate a single uploaded file against extension, size, and content guards."""
    safe_name = sanitize_filename(filename)
    ext = Path(safe_name).suffix.lower()

    if ext not in allowed_extensions:
        raise UploadValidationError(
            f"Unsupported file extension {ext!r} for {safe_name!r}. "
            f"Allowed: {sorted(allowed_extensions)}"
        )

    size = len(content)
    if size == 0:
        raise UploadValidationError(f"Uploaded file {safe_name!r} is empty.")
    if size > max_bytes:
        raise UploadValidationError(
            f"Uploaded file {safe_name!r} ({size} bytes) exceeds limit ({max_bytes} bytes)."
        )

    head = content[:16]
    for magic in _BLOCKED_MAGIC_PREFIXES:
        if head.startswith(magic):
            raise UploadValidationError(f"Blocked binary or script header detected in {safe_name!r}.")

    if ext == ".pdf":
        if not content.lstrip()[:5].startswith(b"%PDF-"):
            raise UploadValidationError(f"File {safe_name!r} does not have a valid PDF header.")
    elif ext == ".docx":
        _validate_docx_archive(safe_name, content)
    else:
        # Text-based formats must not contain NUL bytes and must decode cleanly as UTF-8/latin-1
        if b"\x00" in content[:4096]:
            raise UploadValidationError(f"Binary NUL bytes detected in text file {safe_name!r}.")

    return ValidatedUpload(
        filename=safe_name,
        extension=ext,
        content=content,
        size_bytes=size,
    )


def _validate_docx_archive(filename: str, content: bytes) -> None:
    if not content.startswith(b"PK\x03\x04"):
        raise UploadValidationError(f"File {filename!r} is not a valid DOCX/ZIP archive.")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            infos = zf.infolist()
            if len(infos) > _MAX_DOCX_ENTRIES:
                raise UploadValidationError(f"DOCX archive {filename!r} contains too many entries.")
            total_uncompressed = 0
            for info in infos:
                if ".." in info.filename or info.filename.startswith(("/", "\\")):
                    raise UploadValidationError(f"Path traversal entry in DOCX {filename!r}.")
                total_uncompressed += info.file_size
                if total_uncompressed > _MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise UploadValidationError(
                        f"DOCX archive {filename!r} exceeds uncompressed size limit (zip-bomb guard)."
                    )
    except zipfile.BadZipFile as exc:
        raise UploadValidationError(f"Corrupt DOCX archive {filename!r}.") from exc


def validate_upload_batch(
    files: list[tuple[str, bytes]],
    *,
    max_files: int = 20,
    max_bytes_per_file: int = 10 * 1024 * 1024,
    max_total_bytes: int = 50 * 1024 * 1024,
) -> list[ValidatedUpload]:
    """Validate a batch of uploaded files with aggregate count and byte caps."""
    if not files:
        raise UploadValidationError("At least one file is required.")
    if len(files) > max_files:
        raise UploadValidationError(f"Too many files ({len(files)} > {max_files}).")

    validated: list[ValidatedUpload] = []
    total = 0
    seen_names: dict[str, int] = {}
    for raw_name, payload in files:
        item = validate_upload(raw_name, payload, max_bytes=max_bytes_per_file)
        total += item.size_bytes
        if total > max_total_bytes:
            raise UploadValidationError(
                f"Total upload size ({total} bytes) exceeds batch limit ({max_total_bytes} bytes)."
            )
        # Deduplicate colliding filenames deterministically
        count = seen_names.get(item.filename, 0)
        seen_names[item.filename] = count + 1
        if count > 0:
            p = Path(item.filename)
            dedup = f"{p.stem}_{count}{p.suffix}"
            item = ValidatedUpload(
                filename=dedup,
                extension=item.extension,
                content=item.content,
                size_bytes=item.size_bytes,
            )
        validated.append(item)

    return validated
