"""Retrieval boundary and explicitly lexical SQL implementation."""

from __future__ import annotations

import re
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.models import (BuildingRecord, KnowledgeChunkRecord,
    KnowledgeDocumentRecord, KnowledgeDocumentVersionRecord)


class RetrievalProvider(Protocol):
    name: str
    semantic: bool
    def retrieve(self, session: Session, building_id: UUID, query: str, top_k: int) -> list[dict]: ...


class SQLAlchemyLexicalRetriever:
    """Portable SQLite/PostgreSQL token-overlap baseline; no vector-search claims."""
    name = "local_lexical_retrieval"
    semantic = False

    def retrieve(self, session: Session, building_id: UUID, query: str, top_k: int) -> list[dict]:
        terms = set(re.findall(r"[\w-]{2,}", query.casefold()))
        if not terms:
            return []
        organization_id = session.scalar(select(BuildingRecord.organization_id).where(
            BuildingRecord.building_id == building_id))
        if organization_id is None:
            return []
        rows = session.execute(select(KnowledgeChunkRecord, KnowledgeDocumentRecord,
                KnowledgeDocumentVersionRecord)
            .join(KnowledgeDocumentVersionRecord,
                KnowledgeChunkRecord.version_id == KnowledgeDocumentVersionRecord.version_id)
            .join(KnowledgeDocumentRecord,
                KnowledgeDocumentVersionRecord.document_id == KnowledgeDocumentRecord.document_id)
            .where(KnowledgeDocumentRecord.organization_id == organization_id,
                KnowledgeDocumentRecord.building_id == building_id,
                KnowledgeDocumentRecord.archived_at.is_(None),
                KnowledgeDocumentVersionRecord.is_active.is_(True),
                KnowledgeDocumentVersionRecord.ingestion_status == "READY")).all()
        scored = []
        for chunk, doc, version in rows:
            chunk_terms = set(re.findall(r"[\w-]{2,}", chunk.text.casefold()))
            common = terms & chunk_terms
            if not common:
                continue
            scored.append((len(common) / len(terms), chunk.sequence_number, chunk, doc, version))
        scored.sort(key=lambda item: (-item[0], str(item[3].document_id), item[1]))
        return [{"document_id": str(doc.document_id), "document_name": doc.name,
            "chunk_id": str(chunk.chunk_id), "text": chunk.text, "score": round(score, 6),
            "page": chunk.page_number, "section": chunk.section,
            "building_id": str(doc.building_id), "source": doc.source_reference,
            "version": version.version_number, "provenance": version.provenance,
            "simulated": doc.simulated, "category": doc.category,
            "floor_id": str(doc.floor_id) if doc.floor_id else None,
            "zone_id": str(doc.zone_id) if doc.zone_id else None}
            for score, _seq, chunk, doc, version in scored[:top_k]]
