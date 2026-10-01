from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field


class KnowledgeCategory(StrEnum):
    HVAC_MANUAL = "HVAC_MANUAL"
    EQUIPMENT_MANUAL = "EQUIPMENT_MANUAL"
    OPERATING_POLICY = "OPERATING_POLICY"
    MAINTENANCE_PROCEDURE = "MAINTENANCE_PROCEDURE"
    BUILDING_GUIDE = "BUILDING_GUIDE"
    COMFORT_POLICY = "COMFORT_POLICY"
    SAFETY_POLICY = "SAFETY_POLICY"
    OTHER = "OTHER"


class DocumentStatus(StrEnum):
    REGISTERED = "REGISTERED"
    INGESTING = "INGESTING"
    READY = "READY"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


class KnowledgeMetadataUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=240)
    category: KnowledgeCategory | None = None
    description: str | None = Field(default=None, max_length=2000)
    source_reference: str | None = Field(default=None, max_length=500)
    floor_id: str | None = Field(default=None, max_length=64)
    zone_id: str | None = Field(default=None, max_length=100)


class KnowledgeQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=2, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)


class GroundingRequest(KnowledgeQuestion):
    pass


class KnowledgeChunkResult(BaseModel):
    document_id: str
    document_name: str
    chunk_id: str
    text: str
    score: float | None = None
    page: int | None = None
    section: str | None = None
    building_id: str
    source: str | None = None
    version: int


class GroundingContext(BaseModel):
    provider: str = "local_lexical_retrieval"
    answer_generator: str = "NOT_CONFIGURED"
    answer: str
    query: str
    sources: list[KnowledgeChunkResult]
    control_authority: str = "INFORMATIONAL_ONLY"


class DocumentVersionResponse(BaseModel):
    version_id: str
    version: int
    file_name: str
    file_format: str
    content_hash: str
    ingestion_status: DocumentStatus
    ingestion_error_code: str | None
    provenance: str
    is_active: bool
    pages: int | None = None
    chunks: int
    created_at: datetime


class KnowledgeDocumentResponse(BaseModel):
    document_id: str
    organization_id: str
    building_id: str
    floor_id: str | None
    zone_id: str | None
    name: str
    category: KnowledgeCategory
    description: str | None
    source_reference: str | None
    ingestion_status: DocumentStatus
    management_status: str
    simulated: bool
    created_at: datetime
    updated_at: datetime
    versions: list[DocumentVersionResponse]
