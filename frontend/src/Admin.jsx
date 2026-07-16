import { useState, useEffect, useMemo } from "react";
import {
  adminStats,
  adminAudit,
  adminUsers,
  adminSetUserRole,
  adminSetUserActive,
  adminCreateUser,
  adminTemplates,
  adminCreateTemplate,
  adminUpdateTemplate,
  adminDeleteTemplate,
  adminGenerateTemplate,
  adminEncounters,
} from "./api.js";

// ---------------------------------------------------------------------------
// Small, reusable client-side sort + per-column filter for the admin tables.
// Admin datasets are small (a roster, a template list, an audit tail), so we
// filter/sort in the browser instead of round-tripping the API. A table passes
// a `columns` config ({key, label, get}); `get` extracts the plain value used
// for BOTH the text filter and the sort compare — cell *rendering* stays in each
// table's <tbody>, so interactive cells (role selects, active toggles) are
// untouched. We only reorder/hide the data feeding those rows.
function useSortFilter(rows, columns) {
  const [filters, setFilters] = useState({});
  const [sortKey, setSortKey] = useState(null);
  const [sortDir, setSortDir] = useState(1); // 1 = ascending, -1 = descending

  const setFilter = (key, val) => setFilters((f) => ({ ...f, [key]: val }));

  // Three-state header click: unsorted → ascending → descending → unsorted.
  function toggleSort(key) {
    if (sortKey !== key) {
      setSortKey(key);
      setSortDir(1);
      return;
    }
    if (sortDir === 1) {
      setSortDir(-1);
      return;
    }
    setSortKey(null);
    setSortDir(1);
  }

  const view = useMemo(() => {
    let out = rows;
    // Filter: every active column filter must match (substring, case-insensitive).
    for (const c of columns) {
      const q = (filters[c.key] || "").trim().toLowerCase();
      if (q) out = out.filter((r) => String(c.get(r) ?? "").toLowerCase().includes(q));
    }
    // Sort: numeric when both cells are numbers (e.g. version counts), else a
    // locale string compare. ISO date strings (created_at, YYYY-MM-DD DOB) sort
    // chronologically under a string compare, so they need no special-casing.
    if (sortKey) {
      const c = columns.find((x) => x.key === sortKey);
      out = [...out].sort((a, b) => {
        const av = c.get(a);
        const bv = c.get(b);
        const an = Number(av);
        const bn = Number(bv);
        const numeric =
          av !== "" && bv !== "" && av != null && bv != null &&
          !Number.isNaN(an) && !Number.isNaN(bn);
        const cmp = numeric ? an - bn : String(av ?? "").localeCompare(String(bv ?? ""));
        return cmp * sortDir;
      });
    }
    return out;
  }, [rows, columns, filters, sortKey, sortDir]);

  return { view, filters, setFilter, sortKey, sortDir, toggleSort };
}

// The two header rows every admin table shares: a clickable label row (sort
// arrow on the active column) and a per-column filter-input row. `trailing`
// names extra, non-data columns (e.g. "Actions") that don't sort or filter.
function TableHead({ columns, ctl, trailing = [] }) {
  return (
    <thead>
      <tr>
        {columns.map((c) => (
          <th
            key={c.key}
            className="sortable"
            onClick={() => ctl.toggleSort(c.key)}
            title="Click to sort"
          >
            {c.label}
            <span className="sort-arrow">
              {ctl.sortKey === c.key ? (ctl.sortDir === 1 ? " ▲" : " ▼") : ""}
            </span>
          </th>
        ))}
        {trailing.map((t) => (
          <th key={t}>{t}</th>
        ))}
      </tr>
      <tr className="filter-row">
        {columns.map((c) => (
          <th key={c.key}>
            <input
              className="col-filter"
              value={ctl.filters[c.key] || ""}
              placeholder="filter…"
              onChange={(e) => ctl.setFilter(c.key, e.target.value)}
            />
          </th>
        ))}
        {trailing.map((t) => (
          <th key={t} />
        ))}
      </tr>
    </thead>
  );
}

// Admin dashboard: system-wide stats, the audit log, the user roster, plus
// management of note templates, providers, and cross-provider encounter oversight.
// Only reachable when the logged-in user is an admin (App gates the nav + view);
// the backend enforces it too (require_admin → 403 for providers). `me` is the
// current admin, so we can disable self-mutating controls (role/active) on their
// own row.
export default function Admin({ me }) {
  const [stats, setStats] = useState(null);
  const [audit, setAudit] = useState([]);
  const [users, setUsers] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [encounters, setEncounters] = useState([]);
  const [error, setError] = useState("");

  // Add-user form.
  const [newUser, setNewUser] = useState({
    email: "",
    full_name: "",
    password: "",
    role: "provider",
  });

  // New-template form + inline edit state (which template is open, and its fields).
  const [newTpl, setNewTpl] = useState({ name: "", prompt_body: "" });
  const [editId, setEditId] = useState(null);
  const [editTpl, setEditTpl] = useState({ name: "", prompt_body: "", is_active: true });

  // AI template generator: a short description → Claude drafts name + prompt_body
  // into the new-template form for review. `genBusy` disables the button + shows
  // progress; nothing is saved until the admin submits the create form.
  const [genDesc, setGenDesc] = useState("");
  const [genBusy, setGenBusy] = useState(false);

  // Encounter-oversight filters (server-side: provider + date range).
  const [filters, setFilters] = useState({ provider_id: "", start: "", end: "" });

  // Column configs (label + value extractor) for the four admin tables, feeding
  // the shared client-side sort/filter. Memoized so their identity is stable
  // across renders (they're a dependency of useSortFilter's useMemo).
  const userCols = useMemo(
    () => [
      { key: "full_name", label: "Name", get: (u) => u.full_name || "" },
      { key: "email", label: "Email", get: (u) => u.email || "" },
      { key: "role", label: "Role", get: (u) => u.role || "" },
      { key: "status", label: "Status", get: (u) => (u.is_active ? "Active" : "Inactive") },
    ],
    []
  );
  const tplCols = useMemo(
    () => [
      { key: "name", label: "Name", get: (t) => t.name || "" },
      { key: "active", label: "Active", get: (t) => (t.is_active ? "Active" : "Inactive") },
    ],
    []
  );
  const encCols = useMemo(
    () => [
      { key: "provider", label: "Provider", get: (e) => e.provider_name || e.provider_email || "" },
      {
        key: "patient",
        label: "Patient",
        get: (e) => `${e.patient_first_name || ""} ${e.patient_last_name || ""}`.trim(),
      },
      { key: "dob", label: "DOB", get: (e) => e.patient_dob || "" },
      { key: "created", label: "Created", get: (e) => e.created_at || "" },
      { key: "status", label: "Status", get: (e) => e.status || "" },
      { key: "versions", label: "Versions", get: (e) => e.version_count },
    ],
    []
  );
  const auditCols = useMemo(
    () => [
      { key: "when", label: "When", get: (e) => e.created_at || "" },
      { key: "actor", label: "Actor", get: (e) => e.actor_email || "" },
      { key: "action", label: "Action", get: (e) => e.action || "" },
      {
        key: "entity",
        label: "Entity",
        get: (e) => `${e.entity_type || ""}${e.entity_id != null ? ` #${e.entity_id}` : ""}`,
      },
    ],
    []
  );

  const usersView = useSortFilter(users, userCols);
  const tplView = useSortFilter(templates, tplCols);
  const encView = useSortFilter(encounters, encCols);
  const auditView = useSortFilter(audit, auditCols);

  async function changeRole(userId, role) {
    setError("");
    try {
      const updated = await adminSetUserRole(userId, role);
      setUsers((list) => list.map((u) => (u.id === userId ? updated : u)));
    } catch (err) {
      setError(err.message); // e.g. "You cannot remove your own admin role"
    }
  }

  async function toggleActive(user) {
    setError("");
    try {
      const updated = await adminSetUserActive(user.id, !user.is_active);
      // Merge so we keep name/email/role even if the endpoint returns a partial row.
      setUsers((list) => list.map((u) => (u.id === user.id ? { ...u, ...updated } : u)));
    } catch (err) {
      setError(err.message);
    }
  }

  async function addUser(e) {
    e.preventDefault();
    setError("");
    try {
      const created = await adminCreateUser(newUser);
      setUsers((list) => [...list, created]);
      setNewUser({ email: "", full_name: "", password: "", role: "provider" });
    } catch (err) {
      setError(err.message); // e.g. "email already registered"
    }
  }

  async function createTemplate(e) {
    e.preventDefault();
    setError("");
    if (!newTpl.name.trim() || !newTpl.prompt_body.trim()) return;
    try {
      const created = await adminCreateTemplate({ ...newTpl, is_active: true });
      setTemplates((list) => [...list, created]);
      setNewTpl({ name: "", prompt_body: "" });
    } catch (err) {
      setError(err.message);
    }
  }

  async function generateTemplate(e) {
    e.preventDefault();
    setError("");
    const desc = genDesc.trim();
    if (desc.length < 3 || genBusy) return;
    setGenBusy(true);
    try {
      const draft = await adminGenerateTemplate(desc);
      // Drop the AI's draft straight into the create form for review + edit.
      setNewTpl({ name: draft.name, prompt_body: draft.prompt_body });
    } catch (err) {
      setError(err.message);
    } finally {
      setGenBusy(false);
    }
  }

  function startEdit(t) {
    setEditId(t.id);
    setEditTpl({
      name: t.name,
      prompt_body: t.prompt_body || "",
      is_active: t.is_active,
    });
  }
  function cancelEdit() {
    setEditId(null);
  }
  async function saveEdit(id) {
    setError("");
    try {
      const updated = await adminUpdateTemplate(id, editTpl);
      setTemplates((list) => list.map((t) => (t.id === id ? updated : t)));
      setEditId(null);
    } catch (err) {
      setError(err.message);
    }
  }
  async function removeTemplate(t) {
    if (!window.confirm(`Delete template "${t.name}"? This can't be undone.`)) return;
    setError("");
    try {
      await adminDeleteTemplate(t.id);
      setTemplates((list) => list.filter((x) => x.id !== t.id));
    } catch (err) {
      setError(err.message);
    }
  }

  async function loadEncounters(f = filters) {
    setError("");
    try {
      setEncounters(await adminEncounters(f));
    } catch (err) {
      setError(err.message);
    }
  }
  function applyFilters(e) {
    e.preventDefault();
    loadEncounters();
  }
  function clearFilters() {
    const empty = { provider_id: "", start: "", end: "" };
    setFilters(empty);
    loadEncounters(empty);
  }

  useEffect(() => {
    (async () => {
      try {
        const [s, a, u, t, enc] = await Promise.all([
          adminStats(),
          adminAudit(100),
          adminUsers(),
          adminTemplates(),
          adminEncounters(),
        ]);
        setStats(s);
        setAudit(a);
        setUsers(u);
        setTemplates(t);
        setEncounters(enc);
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

      {/* Provider management: add users, toggle their active status, set role. */}
      <section className="card">
        <h2>Providers &amp; users</h2>
        <form className="admin-form" onSubmit={addUser}>
          <label>
            Email
            <input
              type="email"
              value={newUser.email}
              onChange={(e) => setNewUser((n) => ({ ...n, email: e.target.value }))}
              required
            />
          </label>
          <label>
            Full name
            <input
              value={newUser.full_name}
              onChange={(e) => setNewUser((n) => ({ ...n, full_name: e.target.value }))}
              required
            />
          </label>
          <label>
            Password
            <input
              type="password"
              value={newUser.password}
              onChange={(e) => setNewUser((n) => ({ ...n, password: e.target.value }))}
              autoComplete="new-password"
              required
            />
          </label>
          <label>
            Role
            <select
              value={newUser.role}
              onChange={(e) => setNewUser((n) => ({ ...n, role: e.target.value }))}
            >
              <option value="provider">provider</option>
              <option value="admin">admin</option>
            </select>
          </label>
          <button type="submit">Add user</button>
        </form>

        <div className="table-scroll">
        <table className="admin-table">
          <TableHead columns={userCols} ctl={usersView} />
          <tbody>
            {usersView.view.map((u) => {
              const isSelf = me && u.id === me.id;
              return (
                <tr key={u.id}>
                  <td>{u.full_name || "—"}</td>
                  <td>{u.email}</td>
                  <td>
                    <select
                      className={`role-select ${u.role}`}
                      value={u.role}
                      disabled={isSelf}
                      title={isSelf ? "You can't change your own role" : "Assign role"}
                      onChange={(e) => changeRole(u.id, e.target.value)}
                    >
                      <option value="provider">provider</option>
                      <option value="admin">admin</option>
                    </select>
                  </td>
                  <td>
                    <button
                      className={`active-toggle ${u.is_active ? "on" : "off"}`}
                      disabled={isSelf}
                      title={
                        isSelf
                          ? "You can't deactivate your own account"
                          : u.is_active
                          ? "Click to deactivate"
                          : "Click to reactivate"
                      }
                      onClick={() => toggleActive(u)}
                    >
                      {u.is_active ? "Active" : "Inactive"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
      </section>

      {/* Templates manager: create, edit (name/body/active), delete. The backend
          re-reads a template per generation, so edits take effect immediately. */}
      <section className="card">
        <h2>Note templates</h2>

        {/* AI template generator: describe the encounter type, Claude drafts a
            full template into the form below for review before saving. */}
        <form className="ai-gen" onSubmit={generateTemplate}>
          <label className="full">
            Draft a template with AI
            <div className="ai-gen-row">
              <input
                value={genDesc}
                onChange={(e) => setGenDesc(e.target.value)}
                placeholder="Describe the encounter type, e.g. “orthopedic follow-up” or “pediatric urgent care”"
                disabled={genBusy}
              />
              <button type="submit" disabled={genBusy || genDesc.trim().length < 3}>
                {genBusy ? "Drafting…" : "✨ Generate"}
              </button>
            </div>
          </label>
          <p className="hint">
            Claude drafts a name and prompt into the form below — review and edit it,
            then Save. Nothing is saved until you do.
          </p>
        </form>

        <form className="admin-form" onSubmit={createTemplate}>
          <label>
            Name
            <input
              value={newTpl.name}
              onChange={(e) => setNewTpl((n) => ({ ...n, name: e.target.value }))}
              required
            />
          </label>
          <label className="full">
            Prompt body
            <textarea
              className="tpl-body"
              value={newTpl.prompt_body}
              onChange={(e) => setNewTpl((n) => ({ ...n, prompt_body: e.target.value }))}
              placeholder="The system/prompt text that steers this template's SOAP output…"
              required
            />
          </label>
          <button type="submit">Add template</button>
        </form>

        <table className="admin-table">
          <TableHead columns={tplCols} ctl={tplView} trailing={["Actions"]} />
          <tbody>
            {tplView.view.map((t) =>
              editId === t.id ? (
                <tr key={t.id}>
                  <td colSpan={3}>
                    <div className="admin-form">
                      <label>
                        Name
                        <input
                          value={editTpl.name}
                          onChange={(e) =>
                            setEditTpl((n) => ({ ...n, name: e.target.value }))
                          }
                        />
                      </label>
                      <label className="full">
                        Prompt body
                        <textarea
                          className="tpl-body"
                          value={editTpl.prompt_body}
                          onChange={(e) =>
                            setEditTpl((n) => ({ ...n, prompt_body: e.target.value }))
                          }
                        />
                      </label>
                      <label>
                        <input
                          type="checkbox"
                          style={{ width: "auto", display: "inline", marginRight: 6 }}
                          checked={editTpl.is_active}
                          onChange={(e) =>
                            setEditTpl((n) => ({ ...n, is_active: e.target.checked }))
                          }
                        />
                        Active
                      </label>
                      <div className="tpl-actions">
                        <button onClick={() => saveEdit(t.id)}>Save</button>
                        <button className="ghost" onClick={cancelEdit}>
                          Cancel
                        </button>
                      </div>
                    </div>
                  </td>
                </tr>
              ) : (
                <tr key={t.id}>
                  <td>{t.name}</td>
                  <td>
                    <span className={`active-toggle ${t.is_active ? "on" : "off"}`}>
                      {t.is_active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td>
                    <div className="tpl-actions">
                      <button className="ghost" onClick={() => startEdit(t)}>
                        Edit
                      </button>
                      <button className="danger" onClick={() => removeTemplate(t)}>
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              )
            )}
            {tplView.view.length === 0 && (
              <tr>
                <td colSpan={3}>
                  <span className="hint">
                    {templates.length === 0 ? "No templates yet." : "No templates match these filters."}
                  </span>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      {/* Encounter oversight: every provider's encounters, filterable. */}
      <section className="card">
        <h2>Encounter oversight</h2>
        <form className="filter-bar" onSubmit={applyFilters}>
          <label>
            Provider
            <select
              value={filters.provider_id}
              onChange={(e) =>
                setFilters((f) => ({ ...f, provider_id: e.target.value }))
              }
            >
              <option value="">All providers</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.full_name || u.email}
                </option>
              ))}
            </select>
          </label>
          <label>
            From
            <input
              type="date"
              value={filters.start}
              onChange={(e) => setFilters((f) => ({ ...f, start: e.target.value }))}
            />
          </label>
          <label>
            To
            <input
              type="date"
              value={filters.end}
              onChange={(e) => setFilters((f) => ({ ...f, end: e.target.value }))}
            />
          </label>
          <button type="submit">Apply</button>
          <button type="button" className="ghost" onClick={clearFilters}>
            Clear
          </button>
        </form>

        <div className="table-scroll">
        <table className="admin-table">
          <TableHead columns={encCols} ctl={encView} />
          <tbody>
            {encView.view.map((enc) => (
              <tr key={enc.id}>
                <td>{enc.provider_name || enc.provider_email || "—"}</td>
                <td>
                  {enc.patient_first_name} {enc.patient_last_name}
                </td>
                <td>{enc.patient_dob}</td>
                <td>{new Date(enc.created_at).toLocaleString()}</td>
                <td>
                  <span className="action-tag">{enc.status}</span>
                </td>
                <td>{enc.version_count}</td>
              </tr>
            ))}
            {encView.view.length === 0 && (
              <tr>
                <td colSpan={6}>
                  <span className="hint">No encounters match these filters.</span>
                </td>
              </tr>
            )}
          </tbody>
        </table>
        </div>
      </section>

      <section className="card">
        <h2>Audit log</h2>
        <p className="hint">Most recent {audit.length} events, newest first.</p>
        <div className="table-scroll">
        <table className="admin-table">
          <TableHead columns={auditCols} ctl={auditView} />
          <tbody>
            {auditView.view.map((e) => (
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
        </div>
      </section>
    </div>
  );
}
