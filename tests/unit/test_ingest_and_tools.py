"""Unit tests for parsers (including weird inputs), chunker, HybridKnowledgeIndex, and tools."""

from __future__ import annotations

import io

import httpx
from pypdf import PdfWriter

from forgeai.core.ingest.chunker import chunk_document
from forgeai.core.ingest.index import HybridKnowledgeIndex
from forgeai.core.ingest.parsers import parse_document
from forgeai.core.schemas import KnowledgeIndexData
from forgeai.core.tools.builtins import build_default_registry


def test_parse_markdown_redacts_pii_and_chunks_by_heading(hr_policy_bytes: bytes) -> None:
    parsed = parse_document("hr_policy.md", hr_policy_bytes, redact_pii=True)
    assert "[REDACTED_EMAIL]" in parsed.text
    assert "[REDACTED_PHONE]" in parsed.text
    chunks = chunk_document(parsed.filename, parsed.text, chunk_size=250, chunk_overlap=40)
    assert len(chunks) >= 3
    sections = {c.section for c in chunks}
    assert "Paid Time Off" in sections
    assert "Parental Leave" in sections


def test_weird_inputs_scanned_pdf_huge_csv_and_non_english() -> None:
    # 1. Scanned / blank PDF with 0 extractable text -> clear warning, no crash
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    pdf_buf = io.BytesIO()
    writer.write(pdf_buf)
    parsed_pdf = parse_document("scanned.pdf", pdf_buf.getvalue())
    assert parsed_pdf.text == ""
    assert any("Scanned or image-only PDF" in w for w in parsed_pdf.warnings)

    # 2. Huge CSV truncated cleanly at max_csv_rows
    csv_lines = ["sku,name,price"] + [f"SKU-{i},Part {i},{10 + i}" for i in range(50)]
    parsed_csv = parse_document("huge.csv", "\n".join(csv_lines).encode("utf-8"), max_csv_rows=15)
    assert parsed_csv.tables[0].truncated is True
    assert parsed_csv.tables[0].total_rows_seen == 50
    assert len(parsed_csv.tables[0].rows) == 15
    assert any("truncated to 15 rows" in w for w in parsed_csv.warnings)

    # 3. Non-English document (Japanese) detected cleanly
    jp_doc = "# 休暇規定\n正社員は年間20日の有給休暇を取得できます。".encode()
    parsed_jp = parse_document("policy_ja.md", jp_doc, redact_pii=False)
    assert "cjk" in parsed_jp.detected_scripts
    assert any("Non-English" in w for w in parsed_jp.warnings)


def test_parse_csv_builds_structured_table_and_tools_query_it(ship_spares_bytes: bytes) -> None:
    parsed = parse_document("ship_spares.csv", ship_spares_bytes, redact_pii=False)
    assert len(parsed.tables) == 1
    assert len(parsed.tables[0].rows) == 3

    chunks = chunk_document(parsed.filename, parsed.text)
    data = KnowledgeIndexData(
        sources=[parsed.filename],
        chunks=chunks,
        tables=parsed.tables,
        total_chars=len(parsed.text),
    )
    index = HybridKnowledgeIndex(data)
    hits = index.search("Fuel Injector Nozzle", top_k=2)
    assert hits and "SP-1001" in hits[0].chunk.text

    def mock_http(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=f"ok:{req.url}")

    with httpx.Client(transport=httpx.MockTransport(mock_http)) as mock_client:
        registry = build_default_registry(
            index,
            allowed_http_hosts=["api.example.com"],
            http_client=mock_client,
        )
        lookup = registry.execute("table_lookup", {"query": "SP-1001"})
        assert lookup.ok is True
        assert lookup.result["rows"][0]["part_number"] == "SP-1001"

        calc_trace = registry.execute("calc", {"expression": "4 * 320 + 150"})
        assert calc_trace.ok is True
        assert calc_trace.result["result"] == 1430.0

        # HTTP tool allows allowlisted public host and blocks non-allowlisted & private IPs
        ok_http = registry.execute("http_get", {"url": "https://api.example.com/status"})
        assert ok_http.ok is True
        assert ok_http.result["status_code"] == 200

        blocked_host = registry.execute("http_get", {"url": "https://evil.org/steal"})
        assert blocked_host.ok is False

        blocked_ssrf = registry.execute("http_get", {"url": "http://169.254.169.254/latest/meta-data/"})
        assert blocked_ssrf.ok is False

        disabled_tool = registry.execute("calc", {"expression": "1+1"}, enabled=["kb_search"])
        assert disabled_tool.ok is False
