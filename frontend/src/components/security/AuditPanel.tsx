import { useEffect, useState } from "react";
import { api } from "../../services/api";

export function AuditPanel() {
  const [records, setRecords] = useState<Awaited<ReturnType<typeof api.getAuditRecords>>>([]);
  const [error, setError] = useState("");
  useEffect(() => { api.getAuditRecords().then(setRecords).catch(err => setError(err.message)); }, []);
  return <section className="card">
    <h2 className="card-title">SECURITY AUDIT</h2>
    <p className="security-note">Bounded in-memory history. Credentials and tokens are excluded.</p>
    {error && <p className="demo-error" role="alert">{error}</p>}
    <div className="access-list">{records.slice().reverse().map((record, index) => <div className="access-row" key={`${record.timestamp}-${index}`}>
      <div><strong>{record.action} · {record.resource}</strong><span>{record.timestamp} · {record.role ?? "anonymous"} · {record.success ? "SUCCESS" : "FAILED"}</span></div>
      <span>{record.building_id ?? "—"}</span>
    </div>)}{records.length === 0 && <p>No audit activity has been recorded in this process.</p>}</div>
  </section>;
}
