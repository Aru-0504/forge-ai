"""Document ingestion, chunking, edge-case detection, and hybrid retrieval."""

from forgeai.core.ingest.chunker import chunk_document
from forgeai.core.ingest.index import HybridKnowledgeIndex, SearchHit
from forgeai.core.ingest.parsers import (
    IngestInputError,
    ParsedDocument,
    detect_scripts,
    parse_document,
)

__all__ = [
    "HybridKnowledgeIndex",
    "IngestInputError",
    "ParsedDocument",
    "SearchHit",
    "chunk_document",
    "detect_scripts",
    "parse_document",
]
