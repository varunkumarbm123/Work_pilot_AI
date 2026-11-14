"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

const NAV_ITEMS = [
  ["home", "Control Hub", "⌂"],
  ["dashboard", "Dashboard", "▦"],
  ["agents", "Agent Builder", "◈"],
  ["knowledge", "Knowledge Hub", "▤"],
  ["data", "Business data", "▥"],
  ["approvals", "Approval Inbox", "✓"],
  ["activity", "Activity Log", "↗"],
  ["sent", "Sent Items", "↗"],
  ["settings", "Settings", "⚙"],
];

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/+$/, "");

function classNames(...values) {
  return values.filter(Boolean).join(" ");
}

function formatTime(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function formatDuration(seconds) {
  if (seconds == null) return "In progress";
  return seconds < 60 ? `${seconds.toFixed(1)} sec` : `${(seconds / 60).toFixed(1)} min`;
}

function statusLabel(status = "unknown") {
  if (status === "waiting_capacity") return "waiting for capacity";
  return status.replaceAll("_", " ");
}

function Panel({ title, eyebrow, action, children, className = "" }) {
  return (
    <section className={classNames("panel", className)}>
      {(title || action) && (
        <div className="panel-heading">
          <div>
            {eyebrow && <div className="eyebrow">{eyebrow}</div>}
            {title && <h2>{title}</h2>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

function Status({ value }) {
  return <span className={classNames("status-pill", `status-${value || "unknown"}`)}>{statusLabel(value)}</span>;
}

function ErrorNote({ error, onRetry }) {
  if (!error) return null;
  return (
    <div className="error-note" role="alert">
      <span>{error}</span>
      {onRetry && <button className="text-button" onClick={onRetry}>Retry</button>}
    </div>
  );
}

function Empty({ children }) {
  return <div className="empty-state">{children}</div>;
}

export default function WorkPilot() {
  const [page, setPage] = useState("home");
  const [sessionId, setSessionId] = useState("");
  const [health, setHealth] = useState({ state: "checking" });
  const [healthAttempt, setHealthAttempt] = useState(0);
  const [approvalCount, setApprovalCount] = useState(0);

  const api = useCallback(async (path, options = {}) => {
    if (!API_BASE) throw new Error("Set NEXT_PUBLIC_API_BASE_URL to the deployed backend URL.");
    const headers = { ...(options.headers || {}) };
    if (sessionId) headers["X-Session-ID"] = sessionId;
    if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
    const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
    const contentType = response.headers.get("content-type") || "";
    const result = contentType.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) {
      const message = typeof result === "object" ? result.detail || result.message : result;
      throw new Error(message || `Request failed with status ${response.status}.`);
    }
    return result;
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return undefined;
    let active = true;
    const refresh = async () => {
      try {
        const pending = await api("/api/approvals");
        if (active) setApprovalCount(pending.length);
      } catch (error) {
        console.error("Could not refresh the approval count.", error);
      }
    };
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => { active = false; clearInterval(timer); };
  }, [api, sessionId]);

  useEffect(() => {
    let active = true;
    let timer;
    let attempt = 0;
    const saved = window.localStorage.getItem("workpilot-session");
    const id = saved || window.crypto.randomUUID();
    if (!saved) window.localStorage.setItem("workpilot-session", id);
    setSessionId(id);
    const ping = async () => {
      if (!API_BASE) {
        setHealth({ state: "missing", message: "Set NEXT_PUBLIC_API_BASE_URL to connect this interface." });
        return;
      }
      try {
        const response = await fetch(`${API_BASE}/health`);
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || `Server returned ${response.status}.`);
        if (active) setHealth({ state: "ready", body });
      } catch (error) {
        if (!active) return;
        setHealth({ state: "waking", message: error.message });
        if (attempt < 8) {
          const delay = Math.min(1500 * (2 ** attempt), 12000);
          attempt += 1;
          timer = setTimeout(ping, delay);
        }
      }
    };
    ping();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [healthAttempt]);

  const activePage = useMemo(() => {
    const shared = { api, sessionId, onNavigate: setPage };
    if (page === "home") return <ControlHub {...shared} />;
    if (page === "dashboard") return <Dashboard {...shared} />;
    if (page === "agents") return <AgentBuilder {...shared} />;
    if (page === "knowledge") return <KnowledgeHub {...shared} />;
    if (page === "data") return <BusinessData {...shared} />;
    if (page === "approvals") return <ApprovalInbox {...shared} />;
    if (page === "activity") return <ActivityLog {...shared} />;
    if (page === "sent") return <SentItems {...shared} />;
    return <SettingsPage {...shared} />;
  }, [api, page, sessionId]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">W</div>
          <div><strong>workpilot</strong><span>AI workforce</span></div>
        </div>
        <div className="workspace-label">WORKSPACE</div>
        <nav className="navigation" aria-label="Main navigation">
          {NAV_ITEMS.map(([id, label, icon]) => (
            <button key={id} onClick={() => setPage(id)} className={classNames("nav-link", page === id && "active")}>
              <span className="nav-icon">{icon}</span><span>{label}</span>
              {id === "approvals" && approvalCount > 0 && <span className="approval-count">{approvalCount}</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="connection-indicator">
            <span className={classNames("connection-dot", health.state === "ready" ? "online" : "")} />
            <span>{health.state === "ready" ? "Backend connected" : health.state === "missing" ? "Backend not configured" : "Connecting to backend"}</span>
          </div>
          <div className="session-caption">No account required · demo session</div>
        </div>
      </aside>

      <main className="main-column">
        <header className="topbar">
          <div className="mobile-brand"><span className="brand-mark">W</span><strong>workpilot</strong></div>
          <div className="topbar-title">{NAV_ITEMS.find(([id]) => id === page)?.[1]}</div>
          <div className="topbar-right">
            <span className={classNames("health-chip", health.state === "ready" ? "health-good" : "health-wait")}>
              <span className="connection-dot" />
              {health.state === "ready" ? "Live" : health.state === "missing" ? "Setup needed" : "Waking server"}
            </span>
            {health.state === "waking" && <button className="icon-button" title="Check server again" onClick={() => setHealthAttempt((value) => value + 1)}>↻</button>}
          </div>
        </header>
        {health.state === "waking" && (
          <div className="wake-banner"><span className="spinner" /> Waking up the server… {health.message}</div>
        )}
        {health.state === "missing" && <div className="wake-banner">{health.message}</div>}
        {health.state === "ready" && health.body && !health.body.ai_configured && (
          <div className="wake-banner">The database is connected, but the server-side model provider is not configured.</div>
        )}
        <div className="page-content">{activePage}</div>
      </main>
    </div>
  );
}

function PageIntro({ eyebrow, title, description, action }) {
  return (
    <div className="page-intro">
      <div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{description}</p></div>
      {action}
    </div>
  );
}

function TaskCard({ task, api, onNavigate }) {
  const [current, setCurrent] = useState(task);
  const [taskError, setTaskError] = useState("");
  useEffect(() => setCurrent(task), [task]);
  useEffect(() => {
    if (!["planned", "running"].includes(current.status) || !API_BASE) return undefined;
    const stream = new EventSource(`${API_BASE}/api/tasks/${current.id}/events/stream`);
    stream.addEventListener("update", (event) => {
      try {
        const update = JSON.parse(event.data);
        if (update.task) setCurrent(update.task);
      } catch (error) {
        console.error("Could not read the task update.", error);
      }
    });
    return () => stream.close();
  }, [current.id, current.status]);

  return (
    <article className="task-card">
      <div className="task-card-top">
        <div><div className="task-date">{formatTime(current.created_at)}</div><h3>{current.instruction}</h3></div>
        <Status value={current.status} />
      </div>
      {current.plan?.length > 0 && (
        <div className="plan-list">
          {current.plan.map((step, index) => (
            <div className="plan-line" key={`${current.id}-${index}`}>
              <span className="plan-index">{index + 1}</span><span>{step.description}</span>
              <span className="plan-agent">{step.agent_name}</span>
            </div>
          ))}
        </div>
      )}
      {current.steps?.map((step) => (
        <div className="step-card" key={step.id}>
          <div className="step-heading">
            <div><strong>{step.agent_name}</strong><span>{step.description}</span></div>
            <Status value={step.status} />
          </div>
          {step.output && <p className="step-output">{step.output}</p>}
          {!!step.sources?.length && <div className="citation-row">Sources: {step.sources.map((source) => `${source.document} · ${source.chunk_id}`).join("  |  ")}</div>}
          <div className="step-meta">Duration: {formatDuration(step.duration_seconds)}</div>
        </div>
      ))}
      {current.status === "failed" && (
        <button className="button button-secondary button-small" onClick={async () => {
          try { setTaskError(""); await api(`/api/tasks/${current.id}/retry`, { method: "POST" }); setCurrent({ ...current, status: "running" }); }
          catch (error) { setTaskError(error.message); }
        }}>Retry task</button>
      )}
      <ErrorNote error={taskError} />
      {current.status === "awaiting_approval" && (
        <button className="text-button" onClick={() => onNavigate("approvals")}>Review draft in Approval Inbox →</button>
      )}
    </article>
  );
}

function ControlHub({ api, onNavigate }) {
  const [instruction, setInstruction] = useState("");
  const [examples, setExamples] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [exampleIndex, setExampleIndex] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const [recent, sampleInstructions] = await Promise.all([api("/api/tasks?limit=8"), api("/api/examples")]);
      setTasks(recent);
      setExamples(sampleInstructions);
      setError("");
    } catch (issue) { setError(issue.message); }
    finally { setLoading(false); }
  }, [api]);
  useEffect(() => { refresh(); const timer = setInterval(refresh, 4000); return () => clearInterval(timer); }, [refresh]);

  const run = async (event) => {
    event.preventDefault();
    if (!instruction.trim()) { setError("Enter an instruction before starting a task."); return; }
    setBusy(true); setError(""); setNotice("");
    try {
      const task = await api("/api/tasks", { method: "POST", body: JSON.stringify({ instruction }) });
      setInstruction("");
      setNotice("Instruction accepted. The orchestrator is building a plan.");
      setTasks((items) => [task, ...items.filter((item) => item.id !== task.id)]);
      await refresh();
    } catch (issue) { setError(issue.message); }
    finally { setBusy(false); }
  };

  return (
    <>
      <PageIntro eyebrow="YOUR AI WORKFORCE" title="Good work starts with one instruction." description="Describe the outcome you need. WorkPilot plans the work, assigns your agents, and brings you the result for review." />
      <div className="home-grid">
        <div className="home-main">
          <Panel title="What should your team work on?" eyebrow="CONTROL HUB" className="composer-panel">
            <form onSubmit={run}>
              <label className="field-label" htmlFor="instruction">Your instruction</label>
              <textarea id="instruction" className="instruction-input" value={instruction} onChange={(event) => setInstruction(event.target.value)} maxLength={10000} required aria-label="Work instruction" />
              <div className="composer-footer">
                <button type="button" className="button button-quiet" onClick={() => {
                  if (!examples.length) { setError("No example instructions are stored yet."); return; }
                  setInstruction(examples[exampleIndex % examples.length]);
                  setExampleIndex((index) => index + 1);
                  }}>↻ Load sample instruction</button>
                <button className="button button-primary" disabled={busy || !instruction.trim()}>{busy ? "Starting…" : "Run instruction"} <span>→</span></button>
              </div>
            </form>
            <ErrorNote error={error} onRetry={refresh} />
            {notice && <div className="success-note">{notice}</div>}
          </Panel>

          <Panel title="Recent work" eyebrow="LIVE TASKS" action={<button className="text-button" onClick={() => onNavigate("activity")}>View activity →</button>}>
            {loading && <div className="loading-line"><span className="spinner" /> Loading tasks…</div>}
            {!loading && !tasks.length && <Empty>Your recent tasks will appear here after you run an instruction.</Empty>}
            <div className="task-list">{tasks.map((task) => <TaskCard key={task.id} task={task} api={api} onNavigate={onNavigate} />)}</div>
          </Panel>
        </div>
        <aside className="home-aside">
          <Panel title="Your review loop" eyebrow="THREE SIMPLE STEPS">
            <ol className="onboarding-list">
              <li><span>1</span><div><strong>Run an instruction</strong><small>Give your agents a clear outcome.</small></div></li>
              <li><span>2</span><div><strong>Review the draft</strong><small>Approve, edit, or reject it in the inbox.</small></div></li>
              <li><span>3</span><div><strong>Check the activity log</strong><small>See the plan, tools, evidence, and decisions.</small></div></li>
            </ol>
          </Panel>
          <Panel title="Your workspace" eyebrow="READY TO WORK">
            <div className="quick-links">
              <button onClick={() => onNavigate("agents")}><span className="quick-icon">◈</span><span><strong>Manage agents</strong><small>Update roles and tools</small></span><b>→</b></button>
              <button onClick={() => onNavigate("knowledge")}><span className="quick-icon">▤</span><span><strong>Add business knowledge</strong><small>Upload policies and guides</small></span><b>→</b></button>
              <button onClick={() => onNavigate("data")}><span className="quick-icon">▥</span><span><strong>Connect sales data</strong><small>Upload a CSV or add rows</small></span><b>→</b></button>
            </div>
          </Panel>
        </aside>
      </div>
    </>
  );
}

function Dashboard({ api, onNavigate }) {
  const [dashboard, setDashboard] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [agents, setAgents] = useState([]);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const [metrics, recent, profiles] = await Promise.all([api("/api/dashboard"), api("/api/tasks?limit=6"), api("/api/agents")]);
      setDashboard(metrics); setTasks(recent); setAgents(profiles); setError("");
    } catch (issue) { setError(issue.message); }
  }, [api]);
  useEffect(() => { load(); const timer = setInterval(load, 5000); return () => clearInterval(timer); }, [load]);
  const statusCounts = dashboard?.task_status_counts || {};
  const total = dashboard?.total_tasks || 0;
  return (
    <>
      <PageIntro eyebrow="WORKSPACE OVERVIEW" title="Your work, at a glance." description="Live task and agent states are calculated from the activity recorded in your workspace." />
      <ErrorNote error={error} onRetry={load} />
      {!dashboard && !error && <div className="loading-line"><span className="spinner" /> Loading dashboard events…</div>}
      {dashboard && <>
        <div className="stats-grid">
          <Metric label="Tasks logged" value={total} foot="From workspace events" />
          <Metric label="Completed successfully" value={`${Math.round(dashboard.success_rate * 100)}%`} foot={`${statusCounts.done || 0} completed`} />
          <Metric label="Awaiting review" value={statusCounts.awaiting_approval || 0} foot="Human approval required" />
          <Metric label="Average completion" value={dashboard.average_duration_seconds == null ? "—" : formatDuration(dashboard.average_duration_seconds)} foot="From task transition events" />
        </div>
        <div className="dashboard-grid">
          <Panel title="Task status" eyebrow="RECENT EVENT COUNTS">
            {!Object.keys(statusCounts).length && <Empty>No task activity has been recorded yet.</Empty>}
            {Object.entries(statusCounts).map(([status, count]) => (
              <div className="bar-row" key={status}><span>{statusLabel(status)}</span><div className="bar-track"><div className={classNames("bar-fill", `bar-${status}`)} style={{ width: `${total ? Math.max(4, (count / total) * 100) : 0}%` }} /></div><strong>{count}</strong></div>
            ))}
          </Panel>
          <Panel title="Review signals" eyebrow="APPROVAL EVENTS">
            <div className="signal-row"><span>Reviewed items</span><strong>{dashboard.approval_count}</strong></div>
            <div className="signal-row"><span>Edit rate</span><strong>{Math.round(dashboard.edit_rate * 100)}%</strong></div>
            <div className="muted-copy">Calculated from approval and edit events.</div>
          </Panel>
        </div>
        <Panel title="Agent activity" eyebrow="STATUS FROM EXECUTION EVENTS">
          {!Object.keys(dashboard.agent_status_counts).length && <Empty>Agent activity appears after a task starts.</Empty>}
          <div className="agent-status-grid">{Object.entries(dashboard.agent_status_counts).map(([agentId, statuses]) => (
            <div className="agent-status-card" key={agentId}><strong>{agents.find((agent) => agent.id === agentId)?.name || agentId}</strong><div>{Object.entries(statuses).map(([status, count]) => <span key={status}>{statusLabel(status)} · {count}</span>)}</div></div>
          ))}</div>
        </Panel>
        <Panel title="Latest tasks" eyebrow="LIVE">
          {!tasks.length && <Empty>No tasks have been run yet.</Empty>}
          <div className="task-list">{tasks.map((task) => <TaskCard key={task.id} task={task} api={api} onNavigate={onNavigate} />)}</div>
        </Panel>
      </>}
    </>
  );
}

function Metric({ label, value, foot }) {
  return <div className="metric-card"><span>{label}</span><strong>{value}</strong><small>{foot}</small></div>;
}

function AgentBuilder({ api }) {
  const emptyForm = { name: "", role: "", instructions: "", allowed_tools: [], knowledge_access: true };
  const [agents, setAgents] = useState([]);
  const [tools, setTools] = useState([]);
  const [form, setForm] = useState(emptyForm);
  const [editing, setEditing] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { const [profiles, available] = await Promise.all([api("/api/agents"), api("/api/tools")]); setAgents(profiles); setTools(available); setError(""); }
    catch (issue) { setError(issue.message); }
  }, [api]);
  useEffect(() => { load(); }, [load]);
  const change = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  const save = async (event) => {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    try {
      await api(editing ? `/api/agents/${editing}` : "/api/agents", { method: editing ? "PUT" : "POST", body: JSON.stringify(form) });
      setNotice(editing ? "Agent profile updated." : "Agent profile created.");
      setForm(emptyForm); setEditing(""); await load();
    } catch (issue) { setError(issue.message); }
    finally { setBusy(false); }
  };
  const edit = (agent) => { setEditing(agent.id); setForm({ name: agent.name, role: agent.role, instructions: agent.instructions, allowed_tools: agent.allowed_tools, knowledge_access: agent.knowledge_access }); setNotice(""); };
  const remove = async (agent) => {
    if (!window.confirm(`Delete ${agent.name}?`)) return;
    try { await api(`/api/agents/${agent.id}`, { method: "DELETE" }); setNotice("Agent deleted."); if (editing === agent.id) { setEditing(""); setForm(emptyForm); } await load(); }
    catch (issue) { setError(issue.message); }
  };
  return (
    <>
      <PageIntro eyebrow="NO-CODE AGENT BUILDER" title="Shape your AI workforce." description="Give each agent a role, clear instructions, and only the tools it should use." />
      <ErrorNote error={error} onRetry={load} />{notice && <div className="success-note">{notice}</div>}
      <div className="two-column">
        <Panel title={editing ? "Edit agent" : "Create an agent"} eyebrow="AGENT PROFILE">
          <form className="stack-form" onSubmit={save}>
            <Field label="Name"><input value={form.name} onChange={(event) => change("name", event.target.value)} maxLength={160} required /></Field>
            <Field label="Role"><textarea value={form.role} onChange={(event) => change("role", event.target.value)} rows={3} maxLength={4000} required /></Field>
            <Field label="Instructions"><textarea value={form.instructions} onChange={(event) => change("instructions", event.target.value)} rows={5} maxLength={8000} required /></Field>
            <fieldset className="tool-options"><legend>Allowed tools</legend>
              {tools.map((tool) => <label key={tool.id} className="check-row"><input type="checkbox" checked={form.allowed_tools.includes(tool.id)} onChange={(event) => change("allowed_tools", event.target.checked ? [...form.allowed_tools, tool.id] : form.allowed_tools.filter((item) => item !== tool.id))} />{tool.label}</label>)}
            </fieldset>
            <label className="check-row"><input type="checkbox" checked={form.knowledge_access} onChange={(event) => change("knowledge_access", event.target.checked)} />Can access business knowledge</label>
            <div className="button-row"><button className="button button-primary" disabled={busy}>{busy ? "Saving…" : editing ? "Save changes" : "Create agent"}</button>{editing && <button type="button" className="button button-quiet" onClick={() => { setEditing(""); setForm(emptyForm); }}>Cancel</button>}</div>
          </form>
        </Panel>
        <Panel title={`Agents (${agents.length})`} eyebrow="STORED IN YOUR WORKSPACE">
          {!agents.length && <Empty>Create an agent profile to start building your workforce.</Empty>}
          <div className="agent-list">{agents.map((agent) => (
            <article className="agent-card" key={agent.id}>
              <div className="agent-card-top"><div className="agent-avatar">{agent.name.slice(0, 1).toUpperCase()}</div><div><h3>{agent.name} {agent.sample && <span className="sample-tag">Sample</span>}</h3><span>{agent.role}</span></div></div>
              <p>{agent.instructions}</p>
              <div className="tag-row">{agent.allowed_tools.map((tool) => <span className="tag" key={tool}>{tool.replaceAll("_", " ")}</span>)}{agent.knowledge_access && <span className="tag tag-soft">Knowledge access</span>}</div>
              <div className="agent-actions"><button className="text-button" onClick={() => edit(agent)}>Edit</button><button className="text-button danger-text" onClick={() => remove(agent)}>Delete</button></div>
            </article>
          ))}</div>
        </Panel>
      </div>
    </>
  );
}

function Field({ label, children }) {
  return <label className="field"><span className="field-label">{label}</span>{children}</label>;
}

function KnowledgeHub({ api }) {
  const [documents, setDocuments] = useState([]);
  const [file, setFile] = useState(null);
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async () => { try { setDocuments(await api("/api/knowledge")); setError(""); } catch (issue) { setError(issue.message); } }, [api]);
  useEffect(() => { load(); }, [load]);
  const upload = async (event) => {
    event.preventDefault(); if (!file) return;
    setBusy(true); setError(""); setNotice("");
    const data = new FormData(); data.append("file", file);
    try { const result = await api("/api/knowledge/upload", { method: "POST", body: data }); setNotice(`${result.name} embedded as ${result.chunk_count} knowledge chunks.`); setFile(null); await load(); }
    catch (issue) { setError(issue.message); }
    finally { setBusy(false); }
  };
  const paste = async (event) => {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    try { const result = await api("/api/knowledge/paste", { method: "POST", body: JSON.stringify({ name, content }) }); setNotice(`${result.name} embedded as ${result.chunk_count} knowledge chunks.`); setName(""); setContent(""); await load(); }
    catch (issue) { setError(issue.message); }
    finally { setBusy(false); }
  };
  return (
    <>
      <PageIntro eyebrow="BUSINESS KNOWLEDGE HUB" title="Give agents trusted context." description="Upload policies and guides. WorkPilot embeds each document and retrieves relevant chunks with citations at runtime." />
      <ErrorNote error={error} onRetry={load} />{notice && <div className="success-note">{notice}</div>}
      <div className="two-column">
        <Panel title="Upload a document" eyebrow="PDF, MARKDOWN OR TEXT">
          <form className="stack-form" onSubmit={upload}>
            <Field label="Choose a file"><input type="file" accept=".txt,.md,.pdf" onChange={(event) => setFile(event.target.files?.[0] || null)} required /></Field>
            <div className="muted-copy">Text and Markdown files are read as UTF-8. PDFs are text-extracted before embedding.</div>
            <button className="button button-primary" disabled={busy || !file}>{busy ? "Embedding…" : "Upload to Knowledge Hub"}</button>
          </form>
          <div className="divider-label">OR PASTE TEXT</div>
          <form className="stack-form" onSubmit={paste}>
            <Field label="Document name"><input value={name} onChange={(event) => setName(event.target.value)} required /></Field>
            <Field label="Document content"><textarea value={content} onChange={(event) => setContent(event.target.value)} rows={7} required /></Field>
            <button className="button button-secondary" disabled={busy || !content.trim()}>{busy ? "Embedding…" : "Save pasted document"}</button>
          </form>
        </Panel>
        <Panel title="Indexed documents" eyebrow={`${documents.length} DOCUMENTS`}>
          {!documents.length && <Empty>Upload or paste a policy to make it available for retrieval.</Empty>}
          <div className="document-list">{documents.map((document) => (
            <div className="document-row" key={document.id}>
              <span className="document-icon">▤</span><div className="document-copy"><strong>{document.name}</strong><small>{document.chunk_count} chunks · {formatTime(document.created_at)}</small></div>
              {document.sample && <span className="sample-tag">Sample</span>}
            </div>
          ))}</div>
        </Panel>
      </div>
    </>
  );
}

function BusinessData({ api }) {
  const [file, setFile] = useState(null);
  const [dataset, setDataset] = useState("");
  const [rowJson, setRowJson] = useState("");
  const [summary, setSummary] = useState(null);
  const [enquiries, setEnquiries] = useState([]);
  const [enquiry, setEnquiry] = useState({ customer_name: "", customer_email: "", subject: "", message: "" });
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { const [sales, inbox] = await Promise.all([api("/api/sales/summary"), api("/api/enquiries")]); setSummary(sales); setEnquiries(inbox); setError(""); }
    catch (issue) { setError(issue.message); }
  }, [api]);
  useEffect(() => { load(); }, [load]);
  const upload = async (event) => {
    event.preventDefault(); if (!file) return; setBusy(true); setError(""); setNotice("");
    const data = new FormData(); data.append("file", file);
    try { const result = await api("/api/sales/upload", { method: "POST", body: data }); setNotice(`${result.row_count} rows imported from ${result.dataset}.`); setFile(null); await load(); }
    catch (issue) { setError(issue.message); } finally { setBusy(false); }
  };
  const addRows = async (event) => {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    try {
      const rows = JSON.parse(rowJson);
      if (!Array.isArray(rows)) throw new Error("Enter a JSON array of row objects.");
      if (!dataset.trim()) throw new Error("Enter a dataset name.");
      const result = await api("/api/sales/rows", { method: "POST", body: JSON.stringify({ dataset, rows }) });
      setNotice(`${result.row_count} rows saved to ${result.dataset}.`); setRowJson(""); await load();
    } catch (issue) { setError(issue.message); } finally { setBusy(false); }
  };
  const addEnquiry = async (event) => {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    try { await api("/api/enquiries", { method: "POST", body: JSON.stringify(enquiry) }); setNotice("Enquiry added to the workspace."); setEnquiry({ customer_name: "", customer_email: "", subject: "", message: "" }); await load(); }
    catch (issue) { setError(issue.message); } finally { setBusy(false); }
  };
  return (
    <>
      <PageIntro eyebrow="BUSINESS DATA TOOLS" title="Connect sales and customer context." description="Agents read sales figures from database summaries and search customer enquiries through controlled tools." />
      <ErrorNote error={error} onRetry={load} />{notice && <div className="success-note">{notice}</div>}
      <div className="two-column">
        <Panel title="Sales data" eyebrow="READ-ONLY DATABASE TOOL">
          <form className="inline-form" onSubmit={upload}>
            <Field label="Upload CSV"><input type="file" accept=".csv,text/csv" onChange={(event) => setFile(event.target.files?.[0] || null)} required /></Field>
            <button className="button button-primary" disabled={busy || !file}>{busy ? "Importing…" : "Import CSV"}</button>
          </form>
          <form className="stack-form data-entry-form" onSubmit={addRows}>
            <Field label="Dataset name"><input value={dataset} onChange={(event) => setDataset(event.target.value)} required /></Field>
            <Field label="Add rows as JSON"><textarea value={rowJson} onChange={(event) => setRowJson(event.target.value)} rows={4} required /></Field>
            <div className="muted-copy">Use an array of objects with the same columns as your sales data.</div>
            <button className="button button-secondary" disabled={busy || !rowJson.trim()}>Add data rows</button>
          </form>
          {summary && <div className="data-summary">
            <div className="signal-row"><span>Rows available to agents</span><strong>{summary.row_count}</strong></div>
            <div className="signal-row"><span>Datasets</span><strong>{summary.datasets.join(", ") || "—"}</strong></div>
            {Object.entries(summary.numeric_totals).map(([field, value]) => <div className="signal-row" key={field}><span>Total · {field}</span><strong>{value}</strong></div>)}
            {!!summary.row_limit_reached && <div className="muted-copy">The read tool reached its configured row safety limit.</div>}
          </div>}
        </Panel>
        <Panel title="Customer enquiries" eyebrow={`${enquiries.length} STORED`}>
          <form className="stack-form" onSubmit={addEnquiry}>
            <Field label="Customer name"><input value={enquiry.customer_name} onChange={(event) => setEnquiry({ ...enquiry, customer_name: event.target.value })} /></Field>
            <Field label="Customer email"><input type="email" value={enquiry.customer_email} onChange={(event) => setEnquiry({ ...enquiry, customer_email: event.target.value })} /></Field>
            <Field label="Subject"><input value={enquiry.subject} onChange={(event) => setEnquiry({ ...enquiry, subject: event.target.value })} required /></Field>
            <Field label="Enquiry"><textarea value={enquiry.message} onChange={(event) => setEnquiry({ ...enquiry, message: event.target.value })} rows={3} required /></Field>
            <button className="button button-secondary" disabled={busy}>Add enquiry</button>
          </form>
          <div className="enquiry-list">{enquiries.map((item) => <article className="enquiry-row" key={item.id}>
            <div className="enquiry-top"><strong>{item.subject}</strong>{item.source === "sample" && <span className="sample-tag">Sample</span>}</div>
            <p>{item.message}</p><small>{item.customer_name || item.customer_email || "Customer"} · {formatTime(item.created_at)}</small>
          </article>)}</div>
        </Panel>
      </div>
    </>
  );
}

function ApprovalInbox({ api }) {
  const [items, setItems] = useState([]);
  const [drafts, setDrafts] = useState({});
  const [reasons, setReasons] = useState({});
  const [recipients, setRecipients] = useState({});
  const [connector, setConnector] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busyId, setBusyId] = useState("");
  const load = useCallback(async () => {
    try { const [pending, active] = await Promise.all([api("/api/approvals"), api("/api/connector")]); setItems(pending); setConnector(active.adapter); setError(""); }
    catch (issue) { setError(issue.message); }
  }, [api]);
  useEffect(() => { load(); const timer = setInterval(load, 4000); return () => clearInterval(timer); }, [load]);
  const review = async (task, action) => {
    const step = task.steps.find((item) => item.status === "awaiting_approval");
    if (!step) { setError("This task no longer has a reviewable draft."); return; }
    setBusyId(task.id); setError(""); setNotice("");
    try {
      const result = await api(`/api/approvals/${task.id}`, {
        method: "POST",
        body: JSON.stringify({
          action,
          content: drafts[task.id] || step.output,
          reason: reasons[task.id] || "",
          recipient: recipients[task.id] || "",
        }),
      });
      setNotice(action === "reject" ? "Draft rejected. The assigned agent is regenerating it with your feedback." : `Approved and published through ${result.adapter}.`);
      await load();
    } catch (issue) { setError(issue.message); }
    finally { setBusyId(""); }
  };
  return (
    <>
      <PageIntro eyebrow="HUMAN APPROVAL GATE" title="Nothing sensitive goes out without you." description="Review the validated draft, edit it before approval, or reject it with feedback to trigger a new agent run." action={<span className="connector-chip">Active connector · {connector || "checking"}</span>} />
      <ErrorNote error={error} onRetry={load} />{notice && <div className="success-note">{notice}</div>}
      {!items.length && !error && <Panel><Empty>Your approval inbox is clear. Drafts appear here when an agent finishes its work.</Empty></Panel>}
      <div className="approval-list">{items.map((task) => {
        const step = task.steps.find((item) => item.status === "awaiting_approval");
        if (!step) return null;
        const validation = task.shared_context?.[`step_${step.order_index}`]?.validation;
        return <Panel key={task.id} className="approval-card" title={step.agent_name} eyebrow={`TASK · ${formatTime(task.created_at)}`}>
          <h3 className="approval-instruction">{task.instruction}</h3>
          <div className="validation-banner"><strong>Validator review</strong><span>{validation?.valid === false ? "Needs human attention" : "Draft is ready for human review"}</span></div>
          {!!validation?.reasons?.length && <ul className="validation-reasons">{validation.reasons.map((reason, index) => <li key={`${index}-${reason}`}>{reason}</li>)}</ul>}
          <Field label="Draft"><textarea className="draft-editor" value={drafts[task.id] ?? step.output} onChange={(event) => setDrafts({ ...drafts, [task.id]: event.target.value })} rows={10} /></Field>
              {!!step.sources?.length && <div className="citation-panel"><strong>Retrieved sources</strong>{step.sources.map((source) => <div key={source.chunk_id}>{source.document} <code>{source.chunk_id}</code><p>{source.content}</p></div>)}</div>}
          <Field label="Recipient email (required by SMTP connector)"><input type="email" value={recipients[task.id] || ""} onChange={(event) => setRecipients({ ...recipients, [task.id]: event.target.value })} /></Field>
          <Field label="Reviewer note or rejection reason"><textarea value={reasons[task.id] || ""} onChange={(event) => setReasons({ ...reasons, [task.id]: event.target.value })} rows={2} /></Field>
          <div className="button-row review-actions">
            <button className="button button-primary" disabled={busyId === task.id} onClick={() => review(task, "approve")}>Approve and publish</button>
            <button className="button button-secondary" disabled={busyId === task.id} onClick={() => review(task, "edit_approve")}>Edit and approve</button>
            <button className="button button-danger" disabled={busyId === task.id || !(reasons[task.id] || "").trim()} onClick={() => review(task, "reject")}>Reject and regenerate</button>
          </div>
        </Panel>;
      })}</div>
    </>
  );
}

function ActivityLog({ api }) {
  const [taskId, setTaskId] = useState("");
  const [events, setEvents] = useState([]);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setEvents(await api(`/api/events?limit=100${taskId ? `&task_id=${encodeURIComponent(taskId)}` : ""}`)); setError(""); }
    catch (issue) { setError(issue.message); }
  }, [api, taskId]);
  useEffect(() => { load(); const timer = setInterval(load, 5000); return () => clearInterval(timer); }, [load]);
  return (
    <>
      <PageIntro eyebrow="AUDITABLE WORK" title="Every step leaves a trace." description="See instructions, model prompts, plans, retrievals, data-tool results, validation decisions, and human reviews." />
      <Panel title="Filter activity" eyebrow="EVENT STREAM">
        <div className="filter-row"><input value={taskId} onChange={(event) => setTaskId(event.target.value)} aria-label="Task identifier filter" /><button className="button button-secondary" onClick={load}>Refresh events</button></div>
      </Panel>
      <ErrorNote error={error} onRetry={load} />
      <Panel title="Recorded events" eyebrow={`${events.length} LATEST EVENTS`}>
        {!events.length && !error && <Empty>Workspace events will appear here after your first interaction.</Empty>}
        <div className="timeline">{events.map((event) => <article className="timeline-event" key={event.id}>
          <span className="timeline-dot" /><div className="timeline-content">
            <div className="timeline-top"><strong>{event.message}</strong><time>{formatTime(event.created_at)}</time></div>
            <div className="event-meta">{event.event_type}{event.model && ` · ${event.model}`}{event.task_id && ` · task ${event.task_id.slice(0, 8)}`}</div>
            {!!Object.keys(event.details || {}).length && <details><summary>Inspect event details and prompt</summary><pre>{JSON.stringify(event.details, null, 2)}</pre></details>}
          </div>
        </article>)}</div>
      </Panel>
    </>
  );
}

function SentItems({ api }) {
  const [items, setItems] = useState([]);
  const [connector, setConnector] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { const [sent, active] = await Promise.all([api("/api/sent-items"), api("/api/connector")]); setItems(sent); setConnector(active.adapter); setError(""); }
    catch (issue) { setError(issue.message); }
  }, [api]);
  useEffect(() => { load(); }, [load]);
  return (
    <>
      <PageIntro eyebrow="PUBLISHED WORK" title="Sent Items." description="Approved outputs are recorded by the active connector." action={<span className="connector-chip">Active · {connector || "checking"}</span>} />
      <ErrorNote error={error} onRetry={load} />
      <Panel title="Approved and published" eyebrow={`${items.length} ITEMS`}>
        {!items.length && !error && <Empty>Approved replies and reports will appear here.</Empty>}
        <div className="sent-list">{items.map((item) => <article className="sent-card" key={item.id}>
          <div className="sent-header"><span className="sample-tag">{item.adapter}</span><time>{formatTime(item.created_at)}</time></div>
          <p>{item.content}</p><span className="task-reference">Task {item.task_id.slice(0, 8)}</span>
        </article>)}</div>
      </Panel>
    </>
  );
}

function SettingsPage({ api }) {
  const [settings, setSettings] = useState([]);
  const [health, setHealth] = useState(null);
  const [values, setValues] = useState({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState("");
  const load = useCallback(async () => {
    try {
      const [result, status] = await Promise.all([api("/api/settings"), api("/health")]);
      setSettings(result);
      setHealth(status);
      setValues(Object.fromEntries(result.map((entry) => [entry.key, JSON.stringify(entry.value, null, 2)])));
      setError("");
    } catch (issue) { setError(issue.message); }
  }, [api]);
  useEffect(() => { load(); }, [load]);
  const save = async (setting) => {
    setSaving(setting.key); setError(""); setNotice("");
    try {
      const value = JSON.parse(values[setting.key]);
      await api(`/api/settings/${setting.key}`, { method: "PUT", body: JSON.stringify({ value }) });
      setNotice(`Saved ${setting.key}.`); await load();
    } catch (issue) { setError(issue.message); }
    finally { setSaving(""); }
  };
  return (
    <>
      <PageIntro eyebrow="RUNTIME CONFIGURATION" title="Tune how WorkPilot works." description="These values are stored in PostgreSQL and used by orchestration, retrieval, validation, retries, rate limiting, and cost tracking." />
      <ErrorNote error={error} onRetry={load} />{notice && <div className="success-note">{notice}</div>}
      {health && !health.cost_tracking_configured && <div className="wake-banner">Provider token prices are unset. Request and rate caps remain active, but USD cost estimates require all three token-price settings.</div>}
      {!settings.length && !error && <div className="loading-line"><span className="spinner" /> Loading settings…</div>}
      <div className="settings-list">{settings.map((setting) => <article className="setting-card" key={setting.key}>
        <div className="setting-info"><strong>{setting.key.replaceAll("_", " ")}</strong><small>{setting.description}</small><code>{setting.key}</code></div>
        <div className="setting-editor"><textarea value={values[setting.key] || ""} onChange={(event) => setValues({ ...values, [setting.key]: event.target.value })} rows={Array.isArray(setting.value) || typeof setting.value === "object" ? 4 : 2} aria-label={`Value for ${setting.key}`} /><button className="button button-secondary button-small" disabled={saving === setting.key} onClick={() => save(setting)}>{saving === setting.key ? "Saving…" : "Save setting"}</button></div>
      </article>)}</div>
      <div className="notice-box"><strong>Provider credentials stay on the backend.</strong><span>Set the provider key, API base URL, SMTP details, and frontend origin as deployment environment variables. They are never returned by this page.</span></div>
    </>
  );
}
