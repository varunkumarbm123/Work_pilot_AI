"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

const NAV_ITEMS = [
  ["overview", "Overview", "▦"],
  ["knowledge", "Knowledge library", "▤"],
  ["data", "Business data", "▥"],
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

function Panel({ title, eyebrow, action, children }) {
  return (
    <section className="panel">
      {(title || action) && (
        <div className="panel-heading">
          <div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}{title && <h2>{title}</h2>}</div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

function PageIntro({ eyebrow, title, description }) {
  return <div className="page-intro"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{description}</p></div></div>;
}

function ErrorNote({ error, onRetry }) {
  if (!error) return null;
  return <div className="error-note" role="alert"><span>{error}</span>{onRetry && <button className="text-button" onClick={onRetry}>Retry</button>}</div>;
}

function Field({ label, children }) {
  return <label className="field"><span className="field-label">{label}</span>{children}</label>;
}

function Empty({ children }) {
  return <div className="empty-state">{children}</div>;
}

export default function WorkPilot() {
  const [page, setPage] = useState("overview");
  const [health, setHealth] = useState({ state: "checking" });
  const [healthAttempt, setHealthAttempt] = useState(0);

  const api = useCallback(async (path, options = {}) => {
    if (!API_BASE) throw new Error("Set NEXT_PUBLIC_API_BASE_URL to connect the backend.");
    const headers = { ...(options.headers || {}) };
    if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
    const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
    const type = response.headers.get("content-type") || "";
    const result = type.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) {
      const message = typeof result === "object" ? result.detail || result.message : result;
      throw new Error(message || `Request failed with status ${response.status}.`);
    }
    return result;
  }, []);

  useEffect(() => {
    let active = true;
    let timer;
    let attempt = 0;
    const ping = async () => {
      if (!API_BASE) {
        setHealth({ state: "missing", message: "Set NEXT_PUBLIC_API_BASE_URL to connect the backend." });
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
          timer = setTimeout(ping, Math.min(1500 * (2 ** attempt++), 12000));
        }
      }
    };
    ping();
    return () => { active = false; clearTimeout(timer); };
  }, [healthAttempt]);

  const activePage = useMemo(() => {
    if (page === "knowledge") return <KnowledgeLibrary api={api} />;
    if (page === "data") return <BusinessData api={api} />;
    if (page === "settings") return <SettingsPage api={api} />;
    return <Overview api={api} onNavigate={setPage} />;
  }, [api, page]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark">W</div><div><strong>workpilot</strong><span>business workspace</span></div></div>
        <div className="workspace-label">WORKSPACE</div>
        <nav className="navigation" aria-label="Main navigation">
          {NAV_ITEMS.map(([id, label, icon]) => (
            <button key={id} onClick={() => setPage(id)} className={classNames("nav-link", page === id && "active")}>
              <span className="nav-icon">{icon}</span><span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="connection-indicator">
            <span className={classNames("connection-dot", health.state === "ready" && "online")} />
            <span>{health.state === "ready" ? "Backend connected" : health.state === "missing" ? "Backend not configured" : "Connecting to backend"}</span>
          </div>
          <div className="session-caption">Business data workspace</div>
        </div>
      </aside>
      <main className="main-column">
        <header className="topbar">
          <div className="mobile-brand"><span className="brand-mark">W</span><strong>workpilot</strong></div>
          <div className="topbar-title">{NAV_ITEMS.find(([id]) => id === page)?.[1]}</div>
          <div className="topbar-right">
            <span className={classNames("health-chip", health.state === "ready" ? "health-good" : "health-wait")}>
              <span className="connection-dot" />{health.state === "ready" ? "Live" : health.state === "missing" ? "Setup needed" : "Connecting"}
            </span>
            {health.state !== "ready" && <button className="icon-button" title="Check server again" onClick={() => setHealthAttempt((value) => value + 1)}>↻</button>}
          </div>
        </header>
        {health.state === "waking" && <div className="wake-banner"><span className="spinner" /> Connecting to the backend… {health.message}</div>}
        {health.state === "missing" && <div className="wake-banner">{health.message}</div>}
        <div className="page-content">{activePage}</div>
      </main>
    </div>
  );
}

function Overview({ api, onNavigate }) {
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setSummary(await api("/api/dashboard")); setError(""); }
    catch (issue) { setError(issue.message); }
  }, [api]);
  useEffect(() => { load(); }, [load]);
  return (
    <>
      <PageIntro eyebrow="BUSINESS WORKSPACE" title="Your business, organized." description="Keep reference documents, sales data, and customer enquiries together in one workspace." />
      <ErrorNote error={error} onRetry={load} />
      {summary && <>
        <div className="stats-grid">
          <Metric label="Knowledge documents" value={summary.document_count} foot="Reference files stored" />
          <Metric label="Sales records" value={summary.sales.row_count} foot={`${summary.sales.datasets.length} datasets`} />
          <Metric label="Customer enquiries" value={summary.enquiry_count} foot="Recent records available" />
          <Metric label="Sales totals" value={Object.keys(summary.sales.numeric_totals).length} foot="Numeric fields summarized" />
        </div>
        <div className="two-column">
          <Panel title="Business data" eyebrow="SALES SUMMARY">
            {!summary.sales.row_count ? <Empty>No sales records yet. Import a CSV to see summary totals.</Empty> : <>
              <div className="signal-row"><span>Datasets</span><strong>{summary.sales.datasets.join(", ")}</strong></div>
              {Object.entries(summary.sales.numeric_totals).map(([field, value]) => <div className="signal-row" key={field}><span>Total · {field}</span><strong>{value}</strong></div>)}
            </>}
            <button className="text-button" onClick={() => onNavigate("data")}>Manage business data →</button>
          </Panel>
          <Panel title="Workspace tools" eyebrow="GET STARTED">
            <div className="quick-links">
              <button onClick={() => onNavigate("knowledge")}><span className="quick-icon">▤</span><span><strong>Knowledge library</strong><small>Store policies, guides, and reference files</small></span><b>→</b></button>
              <button onClick={() => onNavigate("data")}><span className="quick-icon">▥</span><span><strong>Sales and enquiries</strong><small>Import a spreadsheet or add customer records</small></span><b>→</b></button>
              <button onClick={() => onNavigate("settings")}><span className="quick-icon">⚙</span><span><strong>Workspace settings</strong><small>Configure document storage behavior</small></span><b>→</b></button>
            </div>
          </Panel>
        </div>
      </>}
    </>
  );
}

function Metric({ label, value, foot }) {
  return <div className="metric-card"><span>{label}</span><strong>{value}</strong><small>{foot}</small></div>;
}

function KnowledgeLibrary({ api }) {
  const [documents, setDocuments] = useState([]);
  const [file, setFile] = useState(null);
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async () => {
    try { setDocuments(await api("/api/knowledge")); setError(""); }
    catch (issue) { setError(issue.message); }
  }, [api]);
  useEffect(() => { load(); }, [load]);

  const upload = async (event) => {
    event.preventDefault();
    if (!file) return;
    setBusy(true); setError(""); setNotice("");
    const data = new FormData(); data.append("file", file);
    try {
      const result = await api("/api/knowledge/upload", { method: "POST", body: data });
      setNotice(`${result.name} added to the knowledge library.`); setFile(null); await load();
    } catch (issue) { setError(issue.message); }
    finally { setBusy(false); }
  };
  const paste = async (event) => {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    try {
      const result = await api("/api/knowledge/paste", { method: "POST", body: JSON.stringify({ name, content }) });
      setNotice(`${result.name} added to the knowledge library.`); setName(""); setContent(""); await load();
    } catch (issue) { setError(issue.message); }
    finally { setBusy(false); }
  };
  return <>
    <PageIntro eyebrow="KNOWLEDGE LIBRARY" title="Keep reference material together." description="Upload policies, guides, and other business reference documents. Text and PDF content is stored in your workspace." />
    <ErrorNote error={error} onRetry={load} />{notice && <div className="success-note">{notice}</div>}
    <div className="two-column">
      <Panel title="Upload a document" eyebrow="PDF, MARKDOWN OR TEXT">
        <form className="stack-form" onSubmit={upload}>
          <Field label="Choose a file"><input type="file" accept=".txt,.md,.pdf" onChange={(event) => setFile(event.target.files?.[0] || null)} required /></Field>
          <div className="muted-copy">Files must be 10 MB or smaller. Text, Markdown, and text-based PDFs are supported.</div>
          <button className="button button-primary" disabled={busy || !file}>{busy ? "Uploading…" : "Upload document"}</button>
        </form>
        <div className="divider-label">OR ADD TEXT</div>
        <form className="stack-form" onSubmit={paste}>
          <Field label="Document name"><input value={name} onChange={(event) => setName(event.target.value)} required /></Field>
          <Field label="Document content"><textarea value={content} onChange={(event) => setContent(event.target.value)} rows={7} required /></Field>
          <button className="button button-secondary" disabled={busy || !content.trim()}>{busy ? "Saving…" : "Save document"}</button>
        </form>
      </Panel>
      <Panel title="Stored documents" eyebrow={`${documents.length} DOCUMENTS`}>
        {!documents.length && <Empty>No documents yet. Upload or paste a file to add it here.</Empty>}
        <div className="document-list">{documents.map((document) => (
          <div className="document-row" key={document.id}>
            <span className="document-icon">▤</span>
            <div className="document-copy"><strong>{document.name}</strong><small>{document.chunk_count} sections · {formatTime(document.created_at)}</small></div>
            {document.sample && <span className="sample-tag">Sample</span>}
          </div>
        ))}</div>
      </Panel>
    </div>
  </>;
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
    try {
      const [sales, inbox] = await Promise.all([api("/api/sales/summary"), api("/api/enquiries")]);
      setSummary(sales); setEnquiries(inbox); setError("");
    } catch (issue) { setError(issue.message); }
  }, [api]);
  useEffect(() => { load(); }, [load]);

  const upload = async (event) => {
    event.preventDefault(); if (!file) return; setBusy(true); setError(""); setNotice("");
    const data = new FormData(); data.append("file", file);
    try {
      const result = await api("/api/sales/upload", { method: "POST", body: data });
      setNotice(`${result.row_count} rows imported from ${result.dataset}.`); setFile(null); await load();
    } catch (issue) { setError(issue.message); }
    finally { setBusy(false); }
  };
  const addRows = async (event) => {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    try {
      const rows = JSON.parse(rowJson);
      if (!Array.isArray(rows) || rows.some((row) => !row || Array.isArray(row) || typeof row !== "object")) {
        throw new Error("Enter a JSON array of row objects.");
      }
      if (!dataset.trim()) throw new Error("Enter a dataset name.");
      const result = await api("/api/sales/rows", { method: "POST", body: JSON.stringify({ dataset, rows }) });
      setNotice(`${result.row_count} rows saved to ${result.dataset}.`); setRowJson(""); await load();
    } catch (issue) { setError(issue.message); }
    finally { setBusy(false); }
  };
  const addEnquiry = async (event) => {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    try {
      await api("/api/enquiries", { method: "POST", body: JSON.stringify(enquiry) });
      setNotice("Enquiry added to the workspace.");
      setEnquiry({ customer_name: "", customer_email: "", subject: "", message: "" });
      await load();
    } catch (issue) { setError(issue.message); }
    finally { setBusy(false); }
  };
  return <>
    <PageIntro eyebrow="BUSINESS DATA" title="Sales records and customer enquiries." description="Import sales spreadsheets, review calculated totals, and keep customer enquiries in one place." />
    <ErrorNote error={error} onRetry={load} />{notice && <div className="success-note">{notice}</div>}
    <div className="two-column">
      <Panel title="Sales records" eyebrow="CSV OR ROW DATA">
        <form className="stack-form" onSubmit={upload}>
          <Field label="Upload CSV"><input type="file" accept=".csv,text/csv" onChange={(event) => setFile(event.target.files?.[0] || null)} required /></Field>
          <button className="button button-primary" disabled={busy || !file}>{busy ? "Importing…" : "Import CSV"}</button>
        </form>
        <div className="divider-label">OR ADD ROWS</div>
        <form className="stack-form" onSubmit={addRows}>
          <Field label="Dataset name"><input value={dataset} onChange={(event) => setDataset(event.target.value)} required /></Field>
          <Field label="Rows as JSON"><textarea value={rowJson} onChange={(event) => setRowJson(event.target.value)} rows={5} placeholder={'[{"product":"Example","revenue":100}]'} required /></Field>
          <div className="muted-copy">Provide a JSON array of row objects.</div>
          <button className="button button-secondary" disabled={busy || !rowJson.trim()}>Add data rows</button>
        </form>
        {summary && <div className="data-summary">
          <div className="signal-row"><span>Rows available</span><strong>{summary.row_count}</strong></div>
          <div className="signal-row"><span>Datasets</span><strong>{summary.datasets.join(", ") || "—"}</strong></div>
          {Object.entries(summary.numeric_totals).map(([field, value]) => <div className="signal-row" key={field}><span>Total · {field}</span><strong>{value}</strong></div>)}
          {summary.row_limit_reached && <div className="muted-copy">Summary is capped at 2,000 rows.</div>}
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
        <div className="enquiry-list">{enquiries.map((item) => (
          <article className="enquiry-row" key={item.id}>
            <div className="enquiry-top"><strong>{item.subject}</strong>{item.source === "sample" && <span className="sample-tag">Sample</span>}</div>
            <p>{item.message}</p><small>{item.customer_name || item.customer_email || "Customer"} · {formatTime(item.created_at)}</small>
          </article>
        ))}</div>
        {!enquiries.length && <Empty>No enquiries yet.</Empty>}
      </Panel>
    </div>
  </>;
}

function SettingsPage({ api }) {
  const [settings, setSettings] = useState([]);
  const [values, setValues] = useState({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState("");
  const load = useCallback(async () => {
    try {
      const result = await api("/api/settings");
      setSettings(result);
      setValues(Object.fromEntries(result.map((setting) => [setting.key, JSON.stringify(setting.value)])));
      setError("");
    } catch (issue) { setError(issue.message); }
  }, [api]);
  useEffect(() => { load(); }, [load]);
  const save = async (setting) => {
    setSaving(setting.key); setError(""); setNotice("");
    try {
      const value = JSON.parse(values[setting.key]);
      await api(`/api/settings/${setting.key}`, { method: "PUT", body: JSON.stringify({ value }) });
      setNotice(`${setting.key} saved.`);
      await load();
    } catch (issue) { setError(issue instanceof SyntaxError ? "Enter a valid JSON value." : issue.message); }
    finally { setSaving(""); }
  };
  return <>
    <PageIntro eyebrow="WORKSPACE SETTINGS" title="Configure document storage." description="Review and update settings used by the business workspace." />
    <ErrorNote error={error} onRetry={load} />{notice && <div className="success-note">{notice}</div>}
    {!settings.length && !error && <div className="loading-line"><span className="spinner" /> Loading settings…</div>}
    <div className="settings-list">{settings.map((setting) => (
      <article className="setting-card" key={setting.key}>
        <div className="setting-info"><strong>{setting.key.replaceAll("_", " ")}</strong><small>{setting.description}</small><code>{setting.key}</code></div>
        <div className="setting-editor">
          <textarea aria-label={`Value for ${setting.key}`} value={values[setting.key] ?? ""} onChange={(event) => setValues({ ...values, [setting.key]: event.target.value })} />
          <button className="button button-secondary button-small" disabled={saving === setting.key} onClick={() => save(setting)}>{saving === setting.key ? "Saving…" : "Save setting"}</button>
        </div>
      </article>
    ))}</div>
  </>;
}
