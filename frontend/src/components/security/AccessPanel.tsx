import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../../services/api";

type UserRow = { user_id: string; email: string; role: "ADMIN" | "OPERATOR"; building_ids: string[] };

export function AccessPanel({ role }: { role: "ADMIN" | "OPERATOR" }) {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const reload = () => api.getAccessUsers().then(setUsers).catch(err => setError(err.message));
  useEffect(() => { reload(); }, []);
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setNotice("");
    const form = event.currentTarget;
    const values = new FormData(event.currentTarget);
    try {
      await api.createOperator(String(values.get("email")), String(values.get("password")));
      form.reset(); setNotice("Operator added to your assigned buildings."); reload();
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to create operator"); }
  }
  async function revoke(userId: string) {
    if (!window.confirm("Revoke this operator’s assigned building access? This action is audited.")) return;
    setError(""); setNotice("");
    try { await api.revokeOperator(userId); setNotice("Operator access revoked."); reload(); }
    catch (err) { setError(err instanceof Error ? err.message : "Unable to revoke access"); }
  }
  return <section className="card">
    <h2 className="card-title">BUILDING ACCESS</h2>
    <p className="security-note">Operators can manage access only within buildings assigned to their account. Account and building assignments are stored in the local configuration database.</p>
    {error && <p className="demo-error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {role === "OPERATOR" && <form className="access-form" onSubmit={create}>
      <label>Email<input name="email" type="email" autoComplete="off" required /></label>
      <label>Temporary password<input name="password" type="password" minLength={12} autoComplete="new-password" required /></label>
      <button className="btn btn-primary" type="submit">Add operator</button>
    </form>}
    <div className="access-list">{users.map(user => <div className="access-row" key={user.user_id}>
      <div><strong>{user.email}</strong><span>{user.role} · {user.building_ids.join(", ") || "organization-wide"}</span></div>
      {role === "OPERATOR" && user.role === "OPERATOR" && <button className="btn btn-secondary" onClick={() => revoke(user.user_id)}>Revoke</button>}
    </div>)}</div>
  </section>;
}
