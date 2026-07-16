import { useState, useEffect } from "react";
import {
  adminStats,
  adminAudit,
  adminUsers,
  adminSetUserRole,
} from "./api.js";

// Admin dashboard: system-wide stats, the audit log, and the user roster. Only
// reachable when the logged-in user is an admin (App gates the nav + view); the
// backend enforces it too (require_admin → 403 for providers). `me` is the current
// admin, so we can disable the role control on their own row (can't self-demote).
export default function Admin({ me }) {
  const [stats, setStats] = useState(null);
  const [audit, setAudit] = useState([]);
  const [users, setUsers] = useState([]);
  const [error, setError] = useState("");

  async function changeRole(userId, role) {
    setError("");
    try {
      const updated = await adminSetUserRole(userId, role);
      setUsers((list) => list.map((u) => (u.id === userId ? updated : u)));
    } catch (err) {
      setError(err.message); // e.g. "You cannot remove your own admin role"
    }
  }

  useEffect(() => {
    (async () => {
      try {
        const [s, a, u] = await Promise.all([
          adminStats(),
          adminAudit(100),
          adminUsers(),
        ]);
        setStats(s);
        setAudit(a);
        setUsers(u);
      } catch (err) {
        setError(err.message);
      }
    })();
  }, []);

  const STAT_CARDS = stats
    ? [
        { label: "Users", value: stats.users },
        { label: "Providers", value: stats.providers },
        { label: "Admins", value: stats.admins },
        { label: "Patients", value: stats.patients },
        { label: "Encounters", value: stats.encounters },
        { label: "Note versions", value: stats.note_versions },
      ]
    : [];

  return (
    <div className="admin">
      <section className="card">
        <h2>Dashboard</h2>
        {error && <p className="error">{error}</p>}
        <div className="stat-grid">
          {STAT_CARDS.map((c) => (
            <div key={c.label} className="stat">
              <div className="stat-value">{c.value}</div>
              <div className="stat-label">{c.label}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="card">
        <h2>Users</h2>
        <table className="admin-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Email</th>
              <th>Role</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>
                  {u.first_name} {u.last_name}
                </td>
                <td>{u.email}</td>
                <td>
                  <select
                    className={`role-select ${u.role}`}
                    value={u.role}
                    disabled={me && u.id === me.id}
                    title={
                      me && u.id === me.id
                        ? "You can't change your own role"
                        : "Assign role"
                    }
                    onChange={(e) => changeRole(u.id, e.target.value)}
                  >
                    <option value="provider">provider</option>
                    <option value="admin">admin</option>
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h2>Audit log</h2>
        <p className="hint">Most recent {audit.length} events, newest first.</p>
        <table className="admin-table">
          <thead>
            <tr>
              <th>When</th>
              <th>Actor</th>
              <th>Action</th>
              <th>Entity</th>
            </tr>
          </thead>
          <tbody>
            {audit.map((e) => (
              <tr key={e.id}>
                <td>{new Date(e.created_at).toLocaleString()}</td>
                <td>{e.actor_email || "—"}</td>
                <td>
                  <span className="action-tag">{e.action}</span>
                </td>
                <td>
                  {e.entity_type}
                  {e.entity_id != null ? ` #${e.entity_id}` : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
