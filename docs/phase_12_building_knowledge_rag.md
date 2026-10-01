# Phase 12 — Building Knowledge and RAG Foundation

## Frontend audit and presentation approach

The frontend is React 19 with TypeScript and Vite. `Dashboard` is a stateful single-page section switcher; it does not use a URL router. Existing reusable presentation pieces include `Card`, `Badge`, and `Button`, with a dark industrial token set in `frontend/src/index.css` and component styles in `frontend/src/App.css`. Dashboard sections and their data hooks contain business state, so Phase 12 leaves those workflows and API contracts intact. Navigation is grouped under Operations, Intelligence, and Configuration without removing a section. The Knowledge panel reuses the existing cards, badges, color tokens, focus treatment, and responsive layout.

There is no Tailwind, Radix, or shadcn/ui setup. Adding shadcn would introduce a second styling/component convention and dependencies for a presentation-only phase, so lightweight components using the existing system are the lower-risk fit. No existing page's control, polling, WebSocket, telemetry, onboarding, or authorization behavior is redesigned.

## Architecture

```text
authenticated user
  -> existing role permission
  -> organization membership (operator) and authorized building access
  -> knowledge document and immutable versions
  -> explicit ingestion request
  -> extraction -> deterministic chunks -> development feature hash
  -> persisted chunks and metadata
  -> building-filtered lexical retrieval
  -> source passages / informational grounding context
```

Knowledge text is untrusted reference data. No model is called, no instruction is executed, and the knowledge service has no dependency on recommendation or control services. `grounding-context` explicitly identifies `answer_generator=NOT_CONFIGURED` and `control_authority=INFORMATIONAL_ONLY`.

## Persistence and lifecycle

Alembic revision `20261002_07` creates:

- `knowledge_documents`: organization/building and optional floor/zone scope, name, category, description, source reference, simulated label, lifecycle summary, and archive timestamp.
- `knowledge_document_versions`: immutable file bytes, sanitized file name/format, SHA-256 content hash, version, ingestion result, page count, provenance, and the active-ready marker.
- `knowledge_chunks`: ordered extracted text and optional page/section metadata, plus a development-only feature-hash vector and provider label.

Foreign keys bind documents to the building's organization, floor to building, and zone to floor. A partial unique index allows at most one active version per document. A new version is retained; it becomes active only after successful extraction/chunking. Failed versions are not activated. Archiving removes the document from retrieval while retaining its history. Files are stored in the configured SQL database, not written to user-controlled paths. Uploads are limited to 10 MiB.

Lifecycle is `REGISTERED -> INGESTING -> READY`; errors end in `FAILED`. Management lifecycle is `ACTIVE -> ARCHIVED`. Only active `READY` versions of non-archived documents are retrieved. The previously active ready version remains available if a newer version fails.

## Ingestion and retrieval limits

Supported formats are PDF (text extraction via pypdf), UTF-8 TXT, and Markdown. Scanned/image-only PDFs, encrypted PDFs, invalid encodings, and unsupported formats fail clearly; no OCR is performed. Markdown heading text is retained as section metadata. PDF pages are one-based. Text files have no fabricated page numbers.

Chunking is ordered, reproducible, non-empty, and metadata-preserving. Character limits are configured with `KNOWLEDGE_CHUNK_SIZE` (default 1200) and `KNOWLEDGE_CHUNK_OVERLAP` (default 120); overlap must remain smaller than chunk size. No document content is logged.

`EmbeddingProvider` and `RetrievalProvider` are replaceable ports. The default embedding provider has an explicitly named development hashing implementation. Its token hash vectors are persisted only to exercise the boundary; they are not semantic embeddings. The default retrieval implementation is portable SQLAlchemy lexical token overlap and its displayed score is the matching query-term fraction, not a vector similarity or answer confidence. There is no pgvector integration, external LLM, or production retrieval-quality claim.

## API

Every route is authenticated and resolves a real configured building before the operation. Operators need organization membership and assigned building access; ADMIN may read building knowledge under the existing oversight model but cannot manage documents. No new role was introduced.

- `POST /api/buildings/{building_id}/knowledge/documents` — multipart register and store version 1.
- `GET /api/buildings/{building_id}/knowledge/documents` — list metadata and versions.
- `GET /api/buildings/{building_id}/knowledge/documents/{document_id}` — document details.
- `POST /api/buildings/{building_id}/knowledge/documents/{document_id}/versions` — register a subsequent version.
- `POST /api/buildings/{building_id}/knowledge/documents/{document_id}/ingest` — explicitly extract and activate a successful version.
- `PATCH /api/buildings/{building_id}/knowledge/documents/{document_id}` — update metadata/scope.
- `POST /api/buildings/{building_id}/knowledge/documents/{document_id}/archive` — archive without deleting history.
- `POST /api/buildings/{building_id}/knowledge/retrieve` — lexical results with document/version/page/section/source provenance.
- `POST /api/buildings/{building_id}/knowledge/grounding-context` — deterministic informational context; not a generated answer.

The browser-supplied building identifier is not sufficient authorization. Cross-organization, cross-building, and document/building mismatches are rejected. Unauthorized knowledge access is written to the existing audit service. Audit metadata records action, identifiers, counts, and stable error codes only—not document contents, question text, credentials, or file paths. The existing audit service is bounded in-memory runtime audit; this phase does not add durable audit persistence.

## Knowledge UI

The Knowledge section is available to both roles: ADMIN can read/retrieve and OPERATOR can manage documents for an assigned building. It shows lifecycle badges, simulated provenance, version and chunk/page counts, expandable details, explicit ingestion/archive actions, and citations with source/version/page/section. It describes results as local lexical matches and displays that no generative model is configured. Error messages are human-readable with expandable safe diagnostics. Raw retrieved passages are rendered as escaped text.

## Safety, security, and limitations

- Existing JWT authentication, the exact ADMIN/OPERATOR roles, building authorization, audit mechanism, data-quality gates, SafetyConstraintService, command limits, fail-safe/manual override, and control provider are unchanged.
- Knowledge APIs do not import or invoke `ControlService`, recommendation workflow, or HVAC providers.
- Retrieved passages, including hostile instructions, remain quoted source data. The API labels them informational and never translates them into actions.
- Historical telemetry is not used to alter ZoneState.
- PostgreSQL-compatible Alembic tables are provided; production PostgreSQL deployment/migration is not claimed or performed here.
- Embeddings are not semantic and retrieval is not AI reasoning. No OCR, DOCX extraction, vector database, external answer model, automatic ingestion, or real building integration is included.
- UI improvements are limited to grouped navigation and the new Knowledge experience. Existing sections remain in place; no full dashboard redesign is implied.
