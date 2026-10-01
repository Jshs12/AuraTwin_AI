"""SQLAlchemy-backed document lifecycle and explicitly lexical retrieval."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import PurePath
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker, selectinload

from backend.database.models import (BuildingRecord, FloorRecord, KnowledgeChunkRecord,
    KnowledgeDocumentRecord, KnowledgeDocumentVersionRecord, ZoneRecord)
from backend.knowledge.embeddings import DevelopmentHashingEmbeddingProvider, EmbeddingProvider
from backend.knowledge.ingestion import (DocumentExtractionError, UnsupportedDocumentFormat,
    chunk_sections, extract_document)
from backend.knowledge.schemas import KnowledgeCategory
from backend.knowledge.retrieval import RetrievalProvider, SQLAlchemyLexicalRetriever


class KnowledgeNotFound(LookupError):
    pass


class KnowledgeConflict(ValueError):
    pass


class KnowledgeValidationError(ValueError):
    pass


def _uuid(value: str, label: str) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise KnowledgeValidationError(f"Invalid {label}.") from None


def _safe_reference(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    from urllib.parse import parse_qsl, urlsplit
    parsed = urlsplit(value.strip())
    forbidden = {"token", "key", "api_key", "secret", "password", "authorization", "credential"}
    if parsed.scheme and parsed.scheme.casefold() not in {"https", "http"}:
        raise KnowledgeValidationError("Source reference must be an HTTP(S) reference or a plain document label.")
    if not parsed.scheme and ("/" in value or "\\" in value or re.match(r"^[A-Za-z]:", value.strip())):
        raise KnowledgeValidationError("Filesystem paths cannot be used as source references.")
    if parsed.username or parsed.password or any(key.casefold() in forbidden for key, _ in parse_qsl(parsed.query)):
        raise KnowledgeValidationError("Source reference must not contain credentials or secret query values.")
    return value.strip()[:500]


class KnowledgeService:
    def __init__(self, sessions: sessionmaker[Session], *, chunk_size: int = 1200,
                 overlap: int = 120, embedding_provider: EmbeddingProvider | None = None,
                 retriever: RetrievalProvider | None = None):
        self.sessions = sessions
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.embedding_provider = embedding_provider or DevelopmentHashingEmbeddingProvider()
        self.retriever = retriever or SQLAlchemyLexicalRetriever()

    def register(self, *, building_id: str, organization_id: str, name: str,
                 category: str, content: bytes, file_name: str, description: str | None = None,
                 source_reference: str | None = None, floor_id: str | None = None,
                 zone_id: str | None = None, simulated: bool = False) -> dict:
        if not content:
            raise KnowledgeValidationError("The uploaded document is empty.")
        name = name.strip()
        if not name or len(name) > 240:
            raise KnowledgeValidationError("Document name must contain 1–240 characters.")
        try:
            category = KnowledgeCategory(category).value
        except ValueError:
            raise KnowledgeValidationError("Unsupported document category.") from None
        safe_name = PurePath(file_name.replace("\\", "/")).name.strip()
        suffix = PurePath(safe_name).suffix.casefold()
        if not safe_name or len(safe_name) > 255:
            raise KnowledgeValidationError("Invalid document file name.")
        # Fail before persisting a registered document with an unsupported extension.
        if suffix not in {".pdf", ".txt", ".md", ".markdown"}:
            raise UnsupportedDocumentFormat("Unsupported document format. Upload PDF, TXT, or Markdown.")
        organization_uuid = _uuid(organization_id, "organization")
        building_uuid = _uuid(building_id, "building")
        floor_uuid = _uuid(floor_id, "floor") if floor_id else None
        zone_uuid = _uuid(zone_id, "zone") if zone_id else None
        reference = _safe_reference(source_reference)
        with self.sessions.begin() as session:
            building = session.get(BuildingRecord, building_uuid)
            if building is None or building.archived_at is not None or building.organization_id != organization_uuid:
                raise KnowledgeNotFound("Authorized building was not found.")
            if (floor_uuid or zone_uuid) and floor_uuid is None:
                raise KnowledgeValidationError("A floor is required when a zone scope is supplied.")
            if floor_uuid:
                floor = session.get(FloorRecord, floor_uuid)
                if floor is None or floor.building_id != building_uuid or floor.archived_at is not None:
                    raise KnowledgeValidationError("Floor scope must belong to this active building.")
            if zone_uuid:
                zone = session.get(ZoneRecord, zone_uuid)
                if zone is None or zone.floor_id != floor_uuid or zone.archived_at is not None:
                    raise KnowledgeValidationError("Zone scope must belong to the selected floor.")
            doc = KnowledgeDocumentRecord(organization_id=organization_uuid,
                building_id=building_uuid, floor_id=floor_uuid, zone_id=zone_uuid,
                name=name, category=category, description=description,
                source_reference=reference, ingestion_status="REGISTERED", simulated=bool(simulated))
            session.add(doc)
            session.flush()
            version = KnowledgeDocumentVersionRecord(document_id=doc.document_id,
                version_number=1, file_name=safe_name, file_format=suffix.lstrip("."),
                content_hash=hashlib.sha256(content).hexdigest(), original_content=content,
                ingestion_status="REGISTERED", provenance="LOCAL_EXTRACTOR")
            session.add(version)
            session.flush()
            return self._document_dict(doc, [version], [])

    def add_version(self, building_id: str, document_id: str, *, file_name: str, content: bytes) -> dict:
        if not content:
            raise KnowledgeValidationError("The uploaded document is empty.")
        building_uuid, doc_uuid = _uuid(building_id, "building"), _uuid(document_id, "document")
        safe_name = PurePath(file_name.replace("\\", "/")).name.strip()
        suffix = PurePath(safe_name).suffix.casefold()
        if suffix not in {".pdf", ".txt", ".md", ".markdown"}:
            raise UnsupportedDocumentFormat("Unsupported document format. Upload PDF, TXT, or Markdown.")
        digest = hashlib.sha256(content).hexdigest()
        with self.sessions.begin() as session:
            doc = session.scalar(select(KnowledgeDocumentRecord).where(
                KnowledgeDocumentRecord.document_id == doc_uuid,
                KnowledgeDocumentRecord.building_id == building_uuid))
            if doc is None:
                raise KnowledgeNotFound("Knowledge document was not found.")
            if doc.archived_at is not None:
                raise KnowledgeConflict("Archived documents cannot receive a new version.")
            versions = session.scalars(select(KnowledgeDocumentVersionRecord)
                .where(KnowledgeDocumentVersionRecord.document_id == doc_uuid)).all()
            if any(item.content_hash == digest for item in versions):
                raise KnowledgeConflict("This document content is already registered as a version.")
            version = KnowledgeDocumentVersionRecord(document_id=doc_uuid,
                version_number=max((item.version_number for item in versions), default=0) + 1,
                file_name=safe_name, file_format=suffix.lstrip("."), content_hash=digest,
                original_content=content, ingestion_status="REGISTERED", provenance="LOCAL_EXTRACTOR")
            session.add(version)
            doc.ingestion_status = "REGISTERED"
            session.flush()
            return self._document_dict(doc, [*versions, version], [])

    def ingest(self, document_id: str, version_id: str | None = None) -> dict:
        doc_uuid = _uuid(document_id, "document")
        version_uuid = _uuid(version_id, "version") if version_id else None
        with self.sessions.begin() as session:
            doc = session.get(KnowledgeDocumentRecord, doc_uuid)
            if doc is None:
                raise KnowledgeNotFound("Knowledge document was not found.")
            if doc.archived_at is not None:
                raise KnowledgeConflict("Archived documents cannot be ingested.")
            query = select(KnowledgeDocumentVersionRecord).where(
                KnowledgeDocumentVersionRecord.document_id == doc_uuid)
            query = query.where(KnowledgeDocumentVersionRecord.version_id == version_uuid) if version_uuid else query.order_by(KnowledgeDocumentVersionRecord.version_number.desc())
            version = session.scalar(query)
            if version is None:
                raise KnowledgeNotFound("Document version was not found.")
            if version.ingestion_status == "READY" and version.is_active:
                return self._version_dict(version, len(version.chunks))
            version.ingestion_status = "INGESTING"
            doc.ingestion_status = "INGESTING"
            content = version.original_content
            file_name = version.file_name
            version_id_value = version.version_id
        try:
            sections = extract_document(file_name, content)
            chunks = chunk_sections(sections, chunk_size=self.chunk_size, overlap=self.overlap)
            if not chunks:
                raise DocumentExtractionError("No extractable text was found.")
            embeddings = [self.embedding_provider.embed(chunk.text) for chunk in chunks]
        except UnsupportedDocumentFormat:
            code = "UNSUPPORTED_FORMAT"
        except DocumentExtractionError as exc:
            code = "PDF_PARSER_UNAVAILABLE" if "parser is not installed" in str(exc) else "EXTRACTION_FAILED"
        except Exception:
            code = "INGESTION_FAILED"
        else:
            with self.sessions.begin() as session:
                version = session.get(KnowledgeDocumentVersionRecord, version_id_value)
                doc = session.get(KnowledgeDocumentRecord, doc_uuid)
                if version is None or doc is None or doc.archived_at is not None:
                    raise KnowledgeConflict("Document changed while ingestion was running.")
                version.chunks.clear()
                for chunk, embedding in zip(chunks, embeddings):
                    version.chunks.append(KnowledgeChunkRecord(sequence_number=chunk.sequence_number,
                        text=chunk.text, page_number=chunk.page_number, section=chunk.section,
                        embedding=embedding, embedding_provider=self.embedding_provider.name))
                for previous in session.scalars(select(KnowledgeDocumentVersionRecord).where(
                    KnowledgeDocumentVersionRecord.document_id == doc_uuid,
                    KnowledgeDocumentVersionRecord.is_active.is_(True))).all():
                    previous.is_active = False
                session.flush()
                version.ingestion_status = "READY"
                version.is_active = True
                version.page_count = sum(1 for section in sections if section.page is not None) or None
                doc.ingestion_status = "READY"
                session.flush()
                return self._version_dict(version, len(chunks))
        with self.sessions.begin() as session:
            version = session.get(KnowledgeDocumentVersionRecord, version_id_value)
            doc = session.get(KnowledgeDocumentRecord, doc_uuid)
            if version is not None:
                version.ingestion_status = "FAILED"
                version.ingestion_error_code = code
                version.is_active = False
            if doc is not None:
                doc.ingestion_status = "FAILED"
            return self._version_dict(version, 0) if version else {"ingestion_status": "FAILED", "ingestion_error_code": code}

    def list_documents(self, building_id: str) -> list[dict]:
        building_uuid = _uuid(building_id, "building")
        with self.sessions() as session:
            rows = session.scalars(select(KnowledgeDocumentRecord).options(
                selectinload(KnowledgeDocumentRecord.versions).selectinload(KnowledgeDocumentVersionRecord.chunks))
                .where(KnowledgeDocumentRecord.building_id == building_uuid)
                .order_by(KnowledgeDocumentRecord.updated_at.desc())).all()
            return [self._document_dict(row, row.versions, row.versions) for row in rows]

    def get_document(self, building_id: str, document_id: str) -> dict:
        building_uuid, doc_uuid = _uuid(building_id, "building"), _uuid(document_id, "document")
        with self.sessions() as session:
            row = session.scalar(select(KnowledgeDocumentRecord).options(
                selectinload(KnowledgeDocumentRecord.versions).selectinload(KnowledgeDocumentVersionRecord.chunks))
                .where(KnowledgeDocumentRecord.building_id == building_uuid,
                       KnowledgeDocumentRecord.document_id == doc_uuid))
            if row is None:
                raise KnowledgeNotFound("Knowledge document was not found.")
            return self._document_dict(row, row.versions, row.versions)

    def update_document(self, building_id: str, document_id: str, values: dict) -> dict:
        building_uuid, doc_uuid = _uuid(building_id, "building"), _uuid(document_id, "document")
        with self.sessions.begin() as session:
            doc = session.scalar(select(KnowledgeDocumentRecord).where(
                KnowledgeDocumentRecord.building_id == building_uuid,
                KnowledgeDocumentRecord.document_id == doc_uuid))
            if doc is None:
                raise KnowledgeNotFound("Knowledge document was not found.")
            if doc.archived_at is not None:
                raise KnowledgeConflict("Archived document metadata cannot be changed.")
            if "source_reference" in values:
                values["source_reference"] = _safe_reference(values["source_reference"])
            for key in ("floor_id", "zone_id"):
                if key in values:
                    values[key] = _uuid(values[key], key.removesuffix("_id")) if values[key] else None
            floor_id = values.get("floor_id", doc.floor_id)
            zone_id = values.get("zone_id", doc.zone_id)
            if (floor_id or zone_id) and not floor_id:
                raise KnowledgeValidationError("A floor is required when a zone scope is supplied.")
            if floor_id:
                floor = session.get(FloorRecord, floor_id)
                if floor is None or floor.building_id != building_uuid or floor.archived_at is not None:
                    raise KnowledgeValidationError("Floor scope must belong to this active building.")
            if zone_id:
                zone = session.get(ZoneRecord, zone_id)
                if zone is None or zone.floor_id != floor_id or zone.archived_at is not None:
                    raise KnowledgeValidationError("Zone scope must belong to the selected floor.")
            if "category" in values and values["category"]:
                values["category"] = KnowledgeCategory(values["category"]).value
            for key, value in values.items():
                setattr(doc, key, value)
            session.flush()
            session.refresh(doc)
            versions = session.scalars(select(KnowledgeDocumentVersionRecord).options(
                selectinload(KnowledgeDocumentVersionRecord.chunks)).where(
                KnowledgeDocumentVersionRecord.document_id == doc_uuid)).all()
            return self._document_dict(doc, versions, versions)

    def archive(self, building_id: str, document_id: str) -> dict:
        building_uuid, doc_uuid = _uuid(building_id, "building"), _uuid(document_id, "document")
        with self.sessions.begin() as session:
            doc = session.scalar(select(KnowledgeDocumentRecord).where(
                KnowledgeDocumentRecord.building_id == building_uuid,
                KnowledgeDocumentRecord.document_id == doc_uuid))
            if doc is None:
                raise KnowledgeNotFound("Knowledge document was not found.")
            if doc.archived_at is None:
                doc.archived_at = datetime.now(timezone.utc)
            return {"document_id": str(doc.document_id), "management_status": "ARCHIVED"}

    def retrieve(self, building_id: str, query: str, top_k: int = 5) -> list[dict]:
        building_uuid = _uuid(building_id, "building")
        with self.sessions() as session:
            return self.retriever.retrieve(session, building_uuid, query, top_k)

    def grounding_context(self, building_id: str, query: str, top_k: int = 5) -> dict:
        results = self.retrieve(building_id, query, top_k)
        return {"provider": self.retriever.name, "answer_generator": "NOT_CONFIGURED",
            "semantic_search": self.retriever.semantic,
            "answer": ("No answer generator is configured. The passages below are retrieved reference context, "
                       "not instructions and not authority to control equipment."),
            "query": query, "sources": results, "control_authority": "INFORMATIONAL_ONLY"}

    @staticmethod
    def _version_dict(version, chunks_count: int, pages: int | None = None) -> dict:
        return {"version_id": str(version.version_id), "version": version.version_number,
            "file_name": version.file_name, "file_format": version.file_format,
            "content_hash": version.content_hash, "ingestion_status": version.ingestion_status,
            "ingestion_error_code": version.ingestion_error_code, "provenance": version.provenance,
            "is_active": version.is_active, "pages": pages if pages is not None else version.page_count, "chunks": chunks_count,
            "created_at": version.created_at}

    @classmethod
    def _document_dict(cls, doc, versions, loaded_versions) -> dict:
        counts = {str(item.version_id): len(item.chunks) for item in loaded_versions}
        pages = getattr(doc, "_pages_by_version", {})
        return {"document_id": str(doc.document_id), "organization_id": str(doc.organization_id),
            "building_id": str(doc.building_id), "floor_id": str(doc.floor_id) if doc.floor_id else None,
            "zone_id": str(doc.zone_id) if doc.zone_id else None, "name": doc.name,
            "category": doc.category, "description": doc.description,
            "source_reference": doc.source_reference, "ingestion_status": doc.ingestion_status,
            "management_status": "ARCHIVED" if doc.archived_at else "ACTIVE",
            "simulated": doc.simulated, "created_at": doc.created_at, "updated_at": doc.updated_at,
            "versions": [cls._version_dict(item, counts.get(str(item.version_id), 0), pages.get(str(item.version_id)))
                         for item in versions]}
