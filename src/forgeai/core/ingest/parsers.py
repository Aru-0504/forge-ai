"""Multi-format document parsers with edge-case guards (scanned PDFs, huge CSVs, non-English)."""

from __future__ import annotations

import csv
import io
import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from bs4 import BeautifulSoup
from docx import Document as DocxDocument
from pypdf import PdfReader

from forgeai.core.schemas import StructuredTable
from forgeai.security.sanitize import sanitize_text


class IngestInputError(ValueError):
    """Clear, user-facing ingestion failure (never a raw stack trace)."""


@dataclass
class ParsedDocument:
    """Normalized text, optional structured tables, scripts, and edge-case warnings."""

    filename: str
    extension: str
    text: str
    tables: list[StructuredTable] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    detected_scripts: list[str] = field(default_factory=lambda: ["latin"])
    metadata: dict[str, Any] = field(default_factory=dict)


def detect_scripts(text: str) -> list[str]:
    """Detect dominant Unicode scripts so non-English corpora are flagged cleanly."""
    counts: dict[str, int] = {}
    for ch in text[:8000]:
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        if "CJK" in name or "HIRAGANA" in name or "KATAKANA" in name or "HANGUL" in name:
            script = "cjk"
        elif "CYRILLIC" in name:
            script = "cyrillic"
        elif "ARABIC" in name:
            script = "arabic"
        elif "DEVANAGARI" in name:
            script = "devanagari"
        elif "LATIN" in name:
            script = "latin"
        else:
            script = "other"
        counts[script] = counts.get(script, 0) + 1
    if not counts:
        return ["unknown"]
    ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return [script for script, count in ordered if count >= 3] or [ordered[0][0]]


def _decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _parse_csv(
    filename: str,
    text: str,
    *,
    max_rows: int = 5000,
) -> tuple[str, list[StructuredTable], list[str]]:
    warnings: list[str] = []
    reader = csv.DictReader(io.StringIO(text))
    columns = [c.strip() for c in (reader.fieldnames or []) if c]
    if not columns:
        raise IngestInputError(f"CSV file {filename!r} has no readable header columns.")

    rows: list[dict[str, Any]] = []
    rendered_lines: list[str] = [f"# Table: {filename}", f"Columns: {', '.join(columns)}"]
    total_seen = 0
    truncated = False

    for idx, row in enumerate(reader, start=1):
        total_seen += 1
        if idx > max_rows:
            truncated = True
            continue
        clean_row = {
            k.strip(): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
            if k
        }
        rows.append(clean_row)
        # Render up to 250 rows into text chunks; full bounded rows remain in `table_lookup`
        if idx <= 250:
            rendered_lines.append(
                f"Row {idx}: " + " | ".join(f"{k}={v}" for k, v in clean_row.items())
            )

    if truncated:
        msg = (
            f"CSV {filename!r} had {total_seen} rows; truncated to {max_rows} rows "
            "to stay within memory and token budgets."
        )
        warnings.append(msg)
        rendered_lines.append(f"Note: {msg}")
    elif total_seen > 250:
        rendered_lines.append(
            f"Note: {total_seen} structured rows loaded into table_lookup index (first 250 previewed above)."
        )

    table = StructuredTable(
        source=filename,
        columns=columns,
        rows=rows,
        truncated=truncated,
        total_rows_seen=total_seen,
    )
    return "\n".join(rendered_lines), [table], warnings


def _parse_json(filename: str, text: str) -> tuple[str, list[StructuredTable]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IngestInputError(f"Invalid JSON in {filename!r}: {exc.msg}") from exc

    tables: list[StructuredTable] = []
    if isinstance(data, list) and data and all(isinstance(item, dict) for item in data):
        cols = list(dict.fromkeys(k for row in data for k in row.keys()))
        tables.append(
            StructuredTable(source=filename, columns=cols, rows=data[:5000], total_rows_seen=len(data))
        )
    pretty = json.dumps(data, indent=2, ensure_ascii=False)
    return f"# JSON Document: {filename}\n{pretty}", tables


def _parse_yaml(filename: str, text: str) -> tuple[str, list[StructuredTable]]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise IngestInputError(f"Invalid YAML in {filename!r}: {exc}") from exc
    tables: list[StructuredTable] = []
    if isinstance(data, list) and data and all(isinstance(item, dict) for item in data):
        cols = list(dict.fromkeys(k for row in data for k in row.keys()))
        tables.append(
            StructuredTable(source=filename, columns=cols, rows=data[:5000], total_rows_seen=len(data))
        )
    rendered = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    return f"# YAML Document: {filename}\n{rendered}", tables


def _parse_html(text: str) -> str:
    soup = BeautifulSoup(text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator="\n")


def _parse_pdf(filename: str, data: bytes) -> tuple[str, dict[str, Any], list[str]]:
    warnings: list[str] = []
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise IngestInputError(f"Could not read PDF {filename!r}: {exc}") from exc

    pages: list[str] = []
    for idx, page in enumerate(reader.pages, start=1):
        page_text = (page.extract_text() or "").strip()
        if page_text:
            pages.append(f"## Page {idx}\n{page_text}")

    if not pages:
        warnings.append(
            f"Scanned or image-only PDF detected in {filename!r} "
            f"({len(reader.pages)} page(s), 0 extractable characters); OCR preprocessing is required."
        )
    return "\n\n".join(pages), {"pages": len(reader.pages), "scanned_no_text": not pages}, warnings


def _parse_docx(filename: str, data: bytes) -> tuple[str, list[StructuredTable]]:
    try:
        doc = DocxDocument(io.BytesIO(data))
    except Exception as exc:
        raise IngestInputError(f"Could not read DOCX {filename!r}: {exc}") from exc

    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    tables: list[StructuredTable] = []
    for t_idx, table in enumerate(doc.tables, start=1):
        matrix = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        if len(matrix) >= 2:
            headers = matrix[0]
            rows = [dict(zip(headers, r, strict=False)) for r in matrix[1:]]
            tables.append(
                StructuredTable(
                    source=f"{filename}_table_{t_idx}",
                    columns=headers,
                    rows=rows,
                    total_rows_seen=len(rows),
                )
            )
            paragraphs.append("\n".join(" | ".join(r) for r in matrix))
    return "\n\n".join(paragraphs), tables


def parse_document(
    filename: str,
    content: bytes,
    *,
    redact_pii: bool = True,
    max_csv_rows: int = 5000,
) -> ParsedDocument:
    """Parse raw uploaded bytes into sanitized text, structured tables, and clear edge-case warnings."""
    ext = Path(filename).suffix.lower()
    tables: list[StructuredTable] = []
    warnings: list[str] = []
    meta: dict[str, Any] = {"size_bytes": len(content)}

    if ext == ".csv":
        raw_text = _decode_bytes(content)
        text, tables, csv_warnings = _parse_csv(filename, raw_text, max_rows=max_csv_rows)
        warnings.extend(csv_warnings)
    elif ext == ".json":
        raw_text = _decode_bytes(content)
        text, tables = _parse_json(filename, raw_text)
    elif ext in {".yaml", ".yml"}:
        raw_text = _decode_bytes(content)
        text, tables = _parse_yaml(filename, raw_text)
    elif ext in {".html", ".htm"}:
        text = _parse_html(_decode_bytes(content))
    elif ext == ".pdf":
        text, pdf_meta, pdf_warnings = _parse_pdf(filename, content)
        meta.update(pdf_meta)
        warnings.extend(pdf_warnings)
    elif ext == ".docx":
        text, tables = _parse_docx(filename, content)
    else:
        text = _decode_bytes(content)

    clean = sanitize_text(text, apply_pii_redaction=redact_pii)
    scripts = detect_scripts(clean) if clean else ["unknown"]
    if any(s not in {"latin", "unknown"} for s in scripts):
        warnings.append(
            f"Non-English script(s) detected in {filename!r}: {', '.join(scripts)}. "
            "Ensure your configured LLM supports these languages."
        )

    return ParsedDocument(
        filename=filename,
        extension=ext,
        text=clean,
        tables=tables,
        warnings=warnings,
        detected_scripts=scripts,
        metadata=meta,
    )
