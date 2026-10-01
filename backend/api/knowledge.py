"""Authenticated building-scoped knowledge endpoints; retrieval is informational only."""

from __future__ import annotations

import os
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from backend.knowledge.ingestion import UnsupportedDocumentFormat
from backend.knowledge.schemas import KnowledgeMetadataUpdate, KnowledgeQuestion
from backend.knowledge.service import (KnowledgeConflict, KnowledgeNotFound,
    KnowledgeService, KnowledgeValidationError)
from backend.security.dependencies import bearer, get_current_user, require_building_access, require_organization_access
from backend.security.roles import Permission, has_permission


router = APIRouter(tags=["Building Knowledge"])
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024


def _knowledge_user(request: Request, building_id: str,
    credentials=Depends(bearer)):
    try:
        return get_current_user(request, credentials)
    except HTTPException:
        request.app.state.audit_service.record(action="knowledge_access_denied",
            resource="knowledge", building_id=building_id, success=False,
            metadata={"reason_code": "AUTHENTICATION_REQUIRED"})
        raise


def _service(request: Request) -> KnowledgeService:
    service = getattr(request.app.state, "knowledge_service", None)
    if service is None:
        try:
            chunk_size = int(os.getenv("KNOWLEDGE_CHUNK_SIZE", "1200"))
            overlap = int(os.getenv("KNOWLEDGE_CHUNK_OVERLAP", "120"))
        except ValueError:
            raise HTTPException(status_code=503, detail="Knowledge chunking configuration is invalid.") from None
        try:
            service = KnowledgeService(request.app.state.database_sessions,
                chunk_size=chunk_size, overlap=overlap)
        except ValueError:
            raise HTTPException(status_code=503, detail="Knowledge chunking configuration is invalid.") from None
        request.app.state.knowledge_service = service
    return service


def _authorize(request: Request, building_id: str, user, permission: Permission, action: str):
    audit = request.app.state.audit_service
    if not has_permission(user.role, permission):
        audit.record(user_id=user.user_id, role=user.role.value, action="knowledge_access_denied",
            resource="knowledge", building_id=building_id, success=False,
            metadata={"operation": action, "reason_code": "INSUFFICIENT_PERMISSION"})
        raise HTTPException(status_code=403, detail="Insufficient permission for building knowledge.")
    building = request.app.state.configuration_repository.resolve_building(building_id)
    if building is None:
        audit.record(user_id=user.user_id, role=user.role.value, action="knowledge_access_denied",
            resource="knowledge", building_id=building_id, success=False,
            metadata={"operation": action, "reason_code": "BUILDING_NOT_FOUND"})
        raise HTTPException(status_code=404, detail="Building not found.")
    try:
        require_organization_access(request, user, str(building.organization_id))
        require_building_access(str(building.building_id), user, request)
    except HTTPException:
        audit.record(user_id=user.user_id, role=user.role.value, action="knowledge_access_denied",
            resource="knowledge", building_id=str(building.building_id), success=False,
            metadata={"operation": action, "reason_code": "BUILDING_ACCESS_DENIED"})
        raise
    return building


def _audit(request: Request, user, action: str, building_id: str,
           document_id: str | None = None, success: bool = True, **metadata):
    request.app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
        action=action, resource="knowledge_document" if document_id else "knowledge",
        resource_id=document_id, building_id=building_id, success=success, metadata=metadata)


def _raise_service_error(exc: Exception):
    if isinstance(exc, KnowledgeNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from None
    if isinstance(exc, KnowledgeConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if isinstance(exc, UnsupportedDocumentFormat):
        raise HTTPException(status_code=415, detail=str(exc)) from None
    if isinstance(exc, KnowledgeValidationError):
        raise HTTPException(status_code=422, detail=str(exc)) from None
    raise exc


async def _read_document(file: UploadFile) -> bytes:
    content = await file.read(MAX_DOCUMENT_BYTES + 1)
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="Document exceeds the 10 MiB upload limit.")
    return content


@router.post("/buildings/{building_id}/knowledge/documents", status_code=status.HTTP_201_CREATED)
async def register_document(building_id: str, request: Request,
    file: UploadFile = File(...), name: str = Form(...), category: str = Form(...),
    description: str | None = Form(default=None), source_reference: str | None = Form(default=None),
    floor_id: str | None = Form(default=None), zone_id: str | None = Form(default=None),
    simulated: bool = Form(default=False), user=Depends(_knowledge_user)):
    building = _authorize(request, building_id, user, Permission.KNOWLEDGE_MANAGE, "document_register")
    content = await _read_document(file)
    try:
        result = _service(request).register(building_id=str(building.building_id),
            organization_id=str(building.organization_id), name=name, category=category,
            content=content, file_name=file.filename or "", description=description,
            source_reference=source_reference, floor_id=floor_id, zone_id=zone_id, simulated=simulated)
    except (KnowledgeNotFound, KnowledgeConflict, KnowledgeValidationError, UnsupportedDocumentFormat) as exc:
        _raise_service_error(exc)
    _audit(request, user, "knowledge_document_registered", str(building.building_id),
        result["document_id"], category=result["category"], simulated=result["simulated"])
    return result


@router.get("/buildings/{building_id}/knowledge/documents")
def list_documents(building_id: str, request: Request,
                   user=Depends(_knowledge_user)):
    building = _authorize(request, building_id, user, Permission.KNOWLEDGE_READ, "document_list")
    return {"documents": _service(request).list_documents(str(building.building_id))}


@router.get("/buildings/{building_id}/knowledge/documents/{document_id}")
def get_document(building_id: str, document_id: str, request: Request,
                 user=Depends(_knowledge_user)):
    building = _authorize(request, building_id, user, Permission.KNOWLEDGE_READ, "document_read")
    try:
        return _service(request).get_document(str(building.building_id), document_id)
    except KnowledgeNotFound as exc:
        _raise_service_error(exc)


@router.post("/buildings/{building_id}/knowledge/documents/{document_id}/versions", status_code=201)
async def add_version(building_id: str, document_id: str, request: Request,
    file: UploadFile = File(...), user=Depends(_knowledge_user)):
    building = _authorize(request, building_id, user, Permission.KNOWLEDGE_MANAGE, "document_version_register")
    content = await _read_document(file)
    try:
        result = _service(request).add_version(str(building.building_id), document_id,
                                               file_name=file.filename or "", content=content)
    except (KnowledgeNotFound, KnowledgeConflict, KnowledgeValidationError, UnsupportedDocumentFormat) as exc:
        _raise_service_error(exc)
    _audit(request, user, "knowledge_document_version_registered", str(building.building_id),
        document_id, version=result["versions"][-1]["version"])
    return result


@router.post("/buildings/{building_id}/knowledge/documents/{document_id}/ingest")
def ingest_document(building_id: str, document_id: str, request: Request,
                    user=Depends(get_current_user)):
    building = _authorize(request, building_id, user, Permission.KNOWLEDGE_MANAGE, "document_ingest")
    try:
        document = _service(request).get_document(str(building.building_id), document_id)
        _audit(request, user, "knowledge_document_ingestion_started", str(building.building_id),
            document_id, version=document["versions"][-1]["version"] if document["versions"] else None)
        result = _service(request).ingest(document_id)
    except (KnowledgeNotFound, KnowledgeConflict, KnowledgeValidationError) as exc:
        _raise_service_error(exc)
    success = result.get("ingestion_status") == "READY"
    _audit(request, user, "knowledge_document_ingestion_succeeded" if success else "knowledge_document_ingestion_failed",
        str(building.building_id), document_id, success=success,
        version=result.get("version"), reason_code=result.get("ingestion_error_code"))
    return {"document_id": document_id, "document_status": result["ingestion_status"], **result}


@router.patch("/buildings/{building_id}/knowledge/documents/{document_id}")
def update_document(building_id: str, document_id: str, body: KnowledgeMetadataUpdate,
                    request: Request, user=Depends(_knowledge_user)):
    building = _authorize(request, building_id, user, Permission.KNOWLEDGE_MANAGE, "document_update")
    try:
        result = _service(request).update_document(str(building.building_id), document_id,
                                                   body.model_dump(exclude_unset=True))
    except (KnowledgeNotFound, KnowledgeConflict, KnowledgeValidationError) as exc:
        _raise_service_error(exc)
    _audit(request, user, "knowledge_document_updated", str(building.building_id), document_id)
    return result


@router.post("/buildings/{building_id}/knowledge/documents/{document_id}/archive")
def archive_document(building_id: str, document_id: str, request: Request,
                     user=Depends(_knowledge_user)):
    building = _authorize(request, building_id, user, Permission.KNOWLEDGE_MANAGE, "document_archive")
    try:
        result = _service(request).archive(str(building.building_id), document_id)
    except KnowledgeNotFound as exc:
        _raise_service_error(exc)
    _audit(request, user, "knowledge_document_archived", str(building.building_id), document_id)
    return result


@router.post("/buildings/{building_id}/knowledge/retrieve")
def retrieve(building_id: str, body: KnowledgeQuestion, request: Request,
             user=Depends(_knowledge_user)):
    building = _authorize(request, building_id, user, Permission.KNOWLEDGE_READ, "retrieval")
    service = _service(request)
    results = service.retrieve(str(building.building_id), body.query, body.top_k)
    _audit(request, user, "knowledge_retrieved", str(building.building_id),
        result_count=len(results), query_length=len(body.query))
    return {"provider": service.retriever.name, "results": results,
            "semantic_search": service.retriever.semantic, "control_authority": "INFORMATIONAL_ONLY"}


@router.post("/buildings/{building_id}/knowledge/grounding-context")
def grounding_context(building_id: str, body: KnowledgeQuestion, request: Request,
                      user=Depends(_knowledge_user)):
    building = _authorize(request, building_id, user, Permission.KNOWLEDGE_READ, "grounding_context")
    result = _service(request).grounding_context(str(building.building_id), body.query, body.top_k)
    _audit(request, user, "knowledge_grounding_context_created", str(building.building_id),
        result_count=len(result["sources"]), query_length=len(body.query))
    return result
