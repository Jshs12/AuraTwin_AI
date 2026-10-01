"""Bounded extraction and deterministic chunking for supported document formats."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath


SUPPORTED_FORMATS = frozenset({".pdf", ".txt", ".md", ".markdown"})


class UnsupportedDocumentFormat(ValueError):
    pass


class DocumentExtractionError(ValueError):
    pass


@dataclass(frozen=True)
class ExtractedSection:
    text: str
    page: int | None = None
    section: str | None = None


@dataclass(frozen=True)
class KnowledgeChunk:
    sequence_number: int
    text: str
    page_number: int | None
    section: str | None


def extract_document(file_name: str, content: bytes) -> list[ExtractedSection]:
    suffix = PurePath(file_name).suffix.casefold()
    if suffix not in SUPPORTED_FORMATS:
        raise UnsupportedDocumentFormat("Unsupported document format. Upload PDF, TXT, or Markdown.")
    if not content:
        raise DocumentExtractionError("The uploaded document is empty.")
    if suffix in {".txt", ".md", ".markdown"}:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DocumentExtractionError("Text documents must use UTF-8 encoding.") from exc
        if suffix in {".md", ".markdown"}:
            sections: list[ExtractedSection] = []
            heading: str | None = None
            body: list[str] = []
            for line in text.splitlines():
                match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
                if match:
                    if "\n".join(body).strip():
                        sections.append(ExtractedSection("\n".join(body).strip(), section=heading))
                    heading, body = match.group(1).strip(), []
                else:
                    body.append(line)
            if "\n".join(body).strip():
                sections.append(ExtractedSection("\n".join(body).strip(), section=heading))
            return sections
        return [ExtractedSection(text.strip())] if text.strip() else []

    try:
        from pypdf import PdfReader
        import io
        reader = PdfReader(io.BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise DocumentExtractionError("Encrypted PDFs are not supported.")
        pages = [ExtractedSection((page.extract_text() or "").strip(), page=index)
                 for index, page in enumerate(reader.pages, start=1)]
    except DocumentExtractionError:
        raise
    except ImportError as exc:
        raise DocumentExtractionError("PDF extraction is unavailable because the PDF parser is not installed.") from exc
    except Exception as exc:
        raise DocumentExtractionError("PDF extraction failed.") from exc
    if not any(item.text for item in pages):
        raise DocumentExtractionError("No extractable text was found in the PDF.")
    return [item for item in pages if item.text]


def chunk_sections(sections: list[ExtractedSection], *, chunk_size: int = 1200,
                   overlap: int = 120) -> list[KnowledgeChunk]:
    if chunk_size < 64 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Chunk size must be at least 64 and overlap must be smaller than chunk size")
    chunks: list[KnowledgeChunk] = []
    seen: set[tuple[int | None, str | None, str]] = set()
    for section in sections:
        text = re.sub(r"[ \t]+", " ", section.text).strip()
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            if end < len(text):
                boundary = text.rfind(" ", start + chunk_size // 2, end)
                if boundary > start:
                    end = boundary
            body = text[start:end].strip()
            key = (section.page, section.section, re.sub(r"\s+", " ", body).casefold())
            if body and key not in seen:
                chunks.append(KnowledgeChunk(len(chunks), body, section.page, section.section))
                seen.add(key)
            if end >= len(text):
                break
            start = max(end - overlap, start + 1)
            while start < len(text) and text[start].isspace():
                start += 1
    return chunks
