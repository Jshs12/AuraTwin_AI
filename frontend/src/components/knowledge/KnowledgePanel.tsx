import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../../services/api";
import type { KnowledgeDocument, KnowledgeResult } from "../../services/api";
import { knowledgeStatusTone, readableKnowledgeLabel } from "../../utils/knowledgePresentation";

const categories = ["HVAC_MANUAL", "EQUIPMENT_MANUAL", "OPERATING_POLICY", "MAINTENANCE_PROCEDURE", "BUILDING_GUIDE", "COMFORT_POLICY", "SAFETY_POLICY", "OTHER"];

export function KnowledgePanel({ buildingId, canManage }: { buildingId?: string; canManage: boolean }) {
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [diagnostic, setDiagnostic] = useState("");
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<KnowledgeResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [busyDocument, setBusyDocument] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    if (!buildingId) { setDocuments([]); return; }
    setLoading(true); setError(""); setDiagnostic("");
    try { setDocuments(await api.getKnowledgeDocuments(buildingId)); }
    catch (cause) { setDocuments([]); setError("Building knowledge could not be loaded. Check your access or try again."); setDiagnostic(cause instanceof Error ? cause.message : "Request failed."); }
    finally { setLoading(false); }
  }, [buildingId]);
  useEffect(() => { void load(); }, [load]);

  const submitDocument = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!buildingId || !fileInput.current?.files?.[0]) return;
    const data = new FormData(event.currentTarget);
    const file = fileInput.current.files[0];
    setError(""); setDiagnostic(""); setNotice("");
    try {
      const doc = await api.registerKnowledgeDocument(buildingId, {
        name: String(data.get("name")), category: String(data.get("category")), file,
        description: String(data.get("description") || ""),
        source_reference: String(data.get("source_reference") || ""),
        simulated: data.get("simulated") === "on",
      });
      setNotice(`“${doc.name}” registered as version 1. Ingest it to make it retrievable.`);
      form.reset();
      await load();
    } catch (cause) { setError("The document could not be registered. Review the file and document details."); setDiagnostic(cause instanceof Error ? cause.message : "Request failed."); }
  };

  const ingest = async (document: KnowledgeDocument) => {
    if (!buildingId) return;
    setBusyDocument(document.document_id); setError(""); setDiagnostic(""); setNotice("");
    try {
      const result = await api.ingestKnowledgeDocument(buildingId, document.document_id);
      if (result.ingestion_status === "READY") setNotice(`Version ${result.version} is ready · ${result.chunks} chunks${result.pages ? ` · ${result.pages} pages` : ""}.`);
      else { setError("Ingestion failed. This version is not searchable."); setDiagnostic(result.ingestion_error_code || "No extraction details were returned."); }
      await load();
    } catch (cause) { setError("Ingestion could not be completed. This version is not searchable."); setDiagnostic(cause instanceof Error ? cause.message : "Request failed."); }
    finally { setBusyDocument(""); }
  };

  const search = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!buildingId || !query.trim()) return;
    setSearching(true); setError(""); setDiagnostic(""); setResults([]);
    try { setResults((await api.retrieveKnowledge(buildingId, query.trim())).results); }
    catch (cause) { setError("Knowledge search is unavailable. Check your building access and try again."); setDiagnostic(cause instanceof Error ? cause.message : "Request failed."); }
    finally { setSearching(false); }
  };

  const archive = async (document: KnowledgeDocument) => {
    if (!buildingId || !window.confirm(`Archive “${document.name}”? It will no longer appear in retrieval.`)) return;
    setBusyDocument(document.document_id); setError(""); setDiagnostic(""); setNotice("");
    try { await api.archiveKnowledgeDocument(buildingId, document.document_id); setNotice("Document archived. It is excluded from retrieval."); await load(); }
    catch (cause) { setError("The document could not be archived."); setDiagnostic(cause instanceof Error ? cause.message : "Request failed."); }
    finally { setBusyDocument(""); }
  };

  if (!buildingId) return <section className="knowledge-empty card"><h2>Select a building</h2><p>Choose an authorized building to review its knowledge documents.</p></section>;
  return <div className="knowledge-layout">
    <header className="knowledge-heading">
      <div><p className="eyebrow">BUILDING KNOWLEDGE · INFORMATIONAL</p><h1>Knowledge</h1>
        <p>Building documents and source-grounded passages. Retrieved text cannot issue equipment commands.</p></div>
      <span className="badge primary">BUILDING SCOPED</span>
    </header>
    {error && <div className="knowledge-alert" role="alert"><strong>{error}</strong>{diagnostic && <details><summary>Technical details</summary><code>{diagnostic}</code></details>}</div>}
    {notice && <div className="knowledge-notice" role="status">{notice}</div>}
    <section className="knowledge-card card" aria-labelledby="knowledge-documents-title">
      <div className="knowledge-section-heading"><div><h2 id="knowledge-documents-title">Documents</h2><p>Only active READY versions can be retrieved.</p></div><span className="badge neutral">{documents.filter(item => item.management_status === "ACTIVE").length} ACTIVE</span></div>
      {loading ? <div className="knowledge-skeleton" aria-label="Loading documents"><span /><span /><span /></div>
        : documents.length ? <div className="knowledge-documents">{documents.map(document => {
          const active = document.versions.find(version => version.is_active && version.ingestion_status === "READY");
          const latest = document.versions.at(-1);
          const status = document.management_status === "ARCHIVED" ? "ARCHIVED" : latest?.ingestion_status ?? "REGISTERED";
          return <article className="knowledge-document" key={document.document_id}>
            <div className="knowledge-document-main"><div className="knowledge-doc-icon" aria-hidden="true">DOC</div>
              <div className="knowledge-doc-copy"><div className="knowledge-doc-title"><h3>{document.name}</h3><span className={`badge ${knowledgeStatusTone(status)}`}>{readableKnowledgeLabel(status)}</span>{document.simulated && <span className="badge warning">SIMULATED</span>}</div>
                <p>{readableKnowledgeLabel(document.category)} · {document.versions.length} version{document.versions.length === 1 ? "" : "s"}{active ? ` · v${active.version} active` : ""}</p>
                <details className="knowledge-details"><summary>Document details</summary><dl>
                  <div><dt>File</dt><dd>{latest?.file_name ?? "—"}</dd></div><div><dt>Format</dt><dd>{latest?.file_format.toUpperCase() ?? "—"}</dd></div>
                  <div><dt>Scope</dt><dd>{document.zone_id ? "Zone scoped" : document.floor_id ? "Floor scoped" : "Building scoped"}</dd></div>
                  <div><dt>Source</dt><dd>{document.source_reference || "Not provided"}</dd></div>
                  {document.description && <div><dt>Description</dt><dd>{document.description}</dd></div>}
                  {latest && <><div><dt>Provenance</dt><dd>{latest.provenance}</dd></div><div><dt>Pages / chunks</dt><dd>{latest.pages ?? "Not available"} / {latest.chunks}</dd></div><div><dt>Content hash</dt><dd><code>{latest.content_hash.slice(0, 12)}…</code></dd></div>{latest.ingestion_error_code && <div><dt>Ingestion issue</dt><dd>{readableKnowledgeLabel(latest.ingestion_error_code)}</dd></div>}</>}
                </dl></details>
              </div>
            </div>
            {canManage && document.management_status === "ACTIVE" && <div className="knowledge-document-actions">
              {latest?.ingestion_status !== "READY" || !active ? <button className="button primary" disabled={busyDocument === document.document_id || !latest} onClick={() => void ingest(document)}>{busyDocument === document.document_id ? "Working…" : latest?.ingestion_status === "FAILED" ? "Retry ingestion" : "Ingest"}</button> : null}
              <button className="button" disabled={busyDocument === document.document_id} onClick={() => void archive(document)}>Archive</button>
            </div>}
          </article>;
        })}</div> : <div className="knowledge-empty"><strong>No building documents yet</strong><p>Register an approved manual or policy to begin building a source library.</p></div>}
      {canManage && <form className="knowledge-upload" onSubmit={event => void submitDocument(event)}>
        <div className="knowledge-section-heading"><div><h2>Add a document</h2><p>PDF, UTF-8 TXT, and Markdown are supported. Maximum file size: 10 MiB.</p></div></div>
        <div className="knowledge-form-grid"><label>Document name<input name="name" required minLength={1} maxLength={240} placeholder="Cooling policy" /></label>
          <label>Category<select name="category" defaultValue="HVAC_MANUAL">{categories.map(category => <option key={category} value={category}>{readableKnowledgeLabel(category)}</option>)}</select></label>
          <label className="knowledge-file">File<input ref={fileInput} name="file" type="file" accept=".pdf,.txt,.md,.markdown,application/pdf,text/plain,text/markdown" required /></label>
          <label>Source reference (optional)<input name="source_reference" maxLength={500} placeholder="Published reference or internal document label" /></label>
          <label className="knowledge-wide">Description<input name="description" maxLength={2000} placeholder="What this document covers" /></label>
          <label className="knowledge-simulated"><input name="simulated" type="checkbox" /> Demo/simulated source document</label>
        </div>
        <button className="button primary" type="submit">Register document</button>
      </form>}
    </section>
    <section className="knowledge-card card" aria-labelledby="knowledge-retrieval-title">
      <div className="knowledge-section-heading"><div><h2 id="knowledge-retrieval-title">Ask building knowledge</h2><p>Searches only this authorized building. Results are local lexical matches, not AI answers.</p></div><span className="badge neutral">NO GENERATIVE MODEL</span></div>
      <form className="knowledge-search" onSubmit={event => void search(event)}><label htmlFor="knowledge-query">Question or topic</label>
        <div><input id="knowledge-query" value={query} onChange={event => setQuery(event.target.value)} maxLength={1000} minLength={2} placeholder="What does the cooling policy say about occupied classrooms?" required />
          <button className="button primary" disabled={searching || !query.trim()}>{searching ? "Searching…" : "Find sources"}</button></div>
      </form>
      {!!query.trim() && <div className="knowledge-answer"><div className="knowledge-answer-label"><span className="badge primary">GROUNDED CONTEXT</span><span className="muted">Informational only · no control authority</span></div>
        <p>No answer generator is configured. Search results below are source passages for operator review.</p></div>}
      {results.length > 0 ? <div className="knowledge-results" aria-live="polite">{results.map(result => <article key={result.chunk_id} className="knowledge-result">
        <div className="knowledge-result-heading"><strong>{result.document_name}</strong><span>v{result.version}{result.page ? ` · page ${result.page}` : ""}{result.section ? ` · ${result.section}` : ""}</span></div>
        <blockquote>{result.text}</blockquote><div className="knowledge-citation"><span>{readableKnowledgeLabel(result.provenance)}{result.simulated ? " · SIMULATED" : ""} · {result.source || "Source reference not provided"}</span><span>Lexical match {Math.round((result.score ?? 0) * 100)}%</span></div>
      </article>)}</div> : query.trim() && !searching && <p className="knowledge-empty" role="status">No matching passages were found in active READY documents.</p>}
    </section>
  </div>;
}
