import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  AlertTriangle,
  BookOpen,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleHelp,
  Clock3,
  FileText,
  FolderOpen,
  HardDrive,
  LoaderCircle,
  LogOut,
  Plus,
  RefreshCw,
  Settings,
  ShieldCheck,
  Trash2,
  Upload,
  X,
  Zap,
} from "lucide-react";
import "./styles.css";

let csrfToken = "";

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (csrfToken && options.method && options.method !== "GET") {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(path, { ...options, headers, credentials: "include" });
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const message = typeof body === "object" && body?.detail ? body.detail : `请求失败（${response.status}）`;
    throw new Error(message);
  }
  return body;
}

function formatBytes(bytes = 0) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(value) {
  if (!value) return "暂无记录";
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function statusMeta(status, phase) {
  if (status === "running" || status === "queued") {
    return { label: phase === "converting" ? "转换中" : phase === "publishing" ? "写入中" : "索引中", tone: "busy", icon: LoaderCircle };
  }
  if (status === "succeeded") return { label: "已同步", tone: "success", icon: CheckCircle2 };
  if (status === "failed") return { label: "同步失败", tone: "danger", icon: AlertTriangle };
  return { label: "未同步", tone: "muted", icon: Clock3 };
}

function StatusPill({ status, phase }) {
  const meta = statusMeta(status, phase);
  const Icon = meta.icon;
  return (
    <span className={`status-pill ${meta.tone}`}>
      <Icon size={14} className={meta.tone === "busy" ? "spin" : ""} />
      {meta.label}
    </span>
  );
}

function ErrorNotice({ message, onClose }) {
  if (!message) return null;
  return (
    <div className="error-notice" role="alert">
      <AlertTriangle size={17} />
      <span>{message}</span>
      {onClose && <button className="icon-button subtle" title="关闭" onClick={onClose}><X size={16} /></button>}
    </div>
  );
}

function Login({ onLogin }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
      csrfToken = result.csrfToken;
      onLogin(result.user);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-shell">
      <aside className="login-aside">
        <div className="aside-index">01 <span>/</span> 03</div>
        <div className="aside-rule" />
        <p className="eyebrow">INDEX ROOM</p>
        <h2>Documents in.<br /><em>Answers out.</em></h2>
        <div className="aside-stamp"><span>LOCAL</span><strong>SYNC</strong><span>CONTROL</span></div>
      </aside>
      <section className="login-panel">
        <div className="brand-mark"><BookOpen size={21} /><span>KB / MANAGER</span></div>
        <div className="login-content">
          <div className="login-copy">
            <h1>知识库管理</h1>
          </div>
          <form className="login-form" onSubmit={submit}>
            <label>管理员账号<input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" /></label>
            <label>密码<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" /></label>
            <ErrorNotice message={error} />
            <button className="primary-button wide" disabled={busy}>
              {busy ? <LoaderCircle className="spin" size={17} /> : <ShieldCheck size={17} />}
              {busy ? "正在验证" : "进入管理后台"}
            </button>
            <div className="login-footnote"><CircleHelp size={14} /> 首次使用请使用服务端配置的初始账号</div>
          </form>
        </div>
      </section>
    </main>
  );
}

function Modal({ title, children, onClose, eyebrow = "ACTION", className = "", closeDisabled = false }) {
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && !closeDisabled && onClose()}>
      <section className={`modal-panel ${className}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-header"><div><p className="eyebrow">{eyebrow}</p><h2>{title}</h2></div><button className="icon-button" title="关闭" disabled={closeDisabled} onClick={onClose}><X size={18} /></button></div>
        {children}
      </section>
    </div>
  );
}

function ConfirmDialog({ title, subject, description, note, confirmLabel, busy, error, onClose, onConfirm }) {
  return (
    <Modal title={title} eyebrow="DESTRUCTIVE ACTION" className="confirm-modal" closeDisabled={busy} onClose={onClose}>
      <div className="confirm-target">
        <div className="confirm-icon"><Trash2 size={20} /></div>
        <div><span>操作对象</span><strong>{subject}</strong></div>
      </div>
      <p className="confirm-description">{description}</p>
      <div className="confirm-note"><AlertTriangle size={16} /><span>{note}</span></div>
      <ErrorNotice message={error} />
      <div className="modal-actions">
        <button type="button" className="ghost-button" disabled={busy} onClick={onClose}>取消</button>
        <button type="button" className="danger-button" disabled={busy} onClick={onConfirm}>
          {busy ? <LoaderCircle className="spin" size={16} /> : <Trash2 size={16} />}
          {busy ? "正在删除" : confirmLabel}
        </button>
      </div>
    </Modal>
  );
}

function CreateKbModal({ onClose, onCreated }) {
  const [name, setName] = useState("");
  const [agents, setAgents] = useState([]);
  const [agentId, setAgentId] = useState("");
  const [path, setPath] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [agentsLoading, setAgentsLoading] = useState(true);

  useEffect(() => {
    let active = true;
    api("/api/openclaw/agents")
      .then((result) => {
        if (!active) return;
        setAgents(result.items);
        const first = result.items.find((agent) => !agent.used);
        if (first) {
          setAgentId(first.id);
          if (first.extraPaths?.length === 1) setPath(first.extraPaths[0]);
        }
      })
      .catch((err) => active && setError(err.message))
      .finally(() => active && setAgentsLoading(false));
    return () => { active = false; };
  }, []);

  const selectedAgent = agents.find((agent) => agent.id === agentId);
  const availableAgents = agents.filter((agent) => !agent.used);
  const configuredPaths = selectedAgent?.extraPaths || [];

  function selectAgent(event) {
    const nextId = event.target.value;
    const nextAgent = agents.find((agent) => agent.id === nextId);
    setAgentId(nextId);
    setPath(nextAgent?.extraPaths?.length === 1 ? nextAgent.extraPaths[0] : "");
  }

  async function submit(event) {
    event.preventDefault();
    if (!agentId || selectedAgent?.used) return setError("请选择一个未关联知识库的 OpenClaw Agent");
    setBusy(true);
    setError("");
    try {
      const result = await api("/api/knowledge-bases", {
        method: "POST",
        body: JSON.stringify({ name, agentId, path: path || undefined }),
      });
      onCreated(result);
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="新建知识库" onClose={onClose}>
      <form className="modal-form" onSubmit={submit}>
        <label>知识库名称<input autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：产品文档" /></label>
        <label>关联 Agent
          <select value={agentId} onChange={selectAgent} disabled={agentsLoading || !agents.length}>
            <option value="">{agentsLoading ? "正在读取 Agent..." : "选择已有 Agent"}</option>
            {agents.map((agent) => <option key={agent.id} value={agent.id} disabled={agent.used}>{agent.name} · {agent.id}{agent.used ? "（已关联）" : ""}</option>)}
          </select>
        </label>
        <label>知识库目录
          {configuredPaths.length > 1 ? (
            <select value={path} onChange={(event) => setPath(event.target.value)}>
              <option value="">选择 Agent 的记忆目录</option>
              {configuredPaths.map((configuredPath) => <option key={configuredPath} value={configuredPath}>{configuredPath}</option>)}
            </select>
          ) : (
            <input value={path} readOnly={configuredPaths.length === 1} onChange={(event) => setPath(event.target.value)} placeholder="/srv/openclaw/knowledge/products" />
          )}
        </label>
        <p className="field-hint">{selectedAgent?.pathSource === "default" ? "该 Agent 未单独配置记忆目录，当前使用 OpenClaw 全局默认目录。" : "已有 Agent 只有一个记忆目录时会自动使用；没有目录时请填写服务器上的绝对路径。"} 管理后台不会自动创建 Agent。</p>
        {!agentsLoading && !availableAgents.length && <p className="field-hint">没有可关联的 Agent，请先在 OpenClaw 中创建未关联的 Agent。</p>}
        <ErrorNotice message={error} />
        <div className="modal-actions"><button type="button" className="ghost-button" onClick={onClose}>取消</button><button className="primary-button" disabled={busy || agentsLoading || !agentId || selectedAgent?.used}>{busy ? <LoaderCircle className="spin" size={16} /> : <Plus size={16} />}{busy ? "正在配置" : "创建知识库"}</button></div>
      </form>
    </Modal>
  );
}

function UploadModal({ kb, onClose, onUploaded }) {
  const inputRef = useRef(null);
  const [files, setFiles] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function choose(event) {
    const selected = Array.from(event.target.files || []);
    setFiles(selected);
    setError("");
  }

  function onDrop(event) {
    event.preventDefault();
    const selected = Array.from(event.dataTransfer.files || []);
    setFiles(selected);
    setError("");
  }

  async function submit(event) {
    event.preventDefault();
    if (!files.length) return setError("请选择至少一个 DOCX 或 Markdown 文件");
    setBusy(true);
    setError("");
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    try {
      const result = await api(`/api/knowledge-bases/${kb.id}/documents`, { method: "POST", body: form });
      onUploaded(result);
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="导入文档" onClose={onClose}>
      <form className="modal-form" onSubmit={submit}>
        <div className="dropzone" onClick={() => inputRef.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={onDrop}>
          <input ref={inputRef} type="file" multiple accept=".docx,.md" onChange={choose} hidden />
          <div className="drop-icon"><Upload size={21} /></div>
          <strong>拖入文件，或点击选择</strong>
          <span>支持 DOCX / Markdown，可一次导入多个文件</span>
        </div>
        {files.length > 0 && <div className="selected-files">{files.map((file) => <div className="selected-file" key={`${file.name}-${file.size}`}><FileText size={15} /><span>{file.name}</span><small>{formatBytes(file.size)}</small></div>)}</div>}
        <ErrorNotice message={error} />
        <div className="modal-actions"><button type="button" className="ghost-button" onClick={onClose}>取消</button><button className="primary-button" disabled={busy}>{busy ? <LoaderCircle className="spin" size={16} /> : <Upload size={16} />}{busy ? "正在提交" : `导入 ${files.length || ""} 个文件`}</button></div>
      </form>
    </Modal>
  );
}

function Sidebar({ items, selected, onSelect, onCreate, onLogout, systemOpen, onSystem }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand"><div className="brand-icon"><BookOpen size={18} /></div><div><strong>KB / MANAGER</strong><span>memory control room</span></div></div>
      <div className="side-section-label">KNOWLEDGE BASES <span>{String(items.length).padStart(2, "0")}</span></div>
      <nav className="kb-nav">{items.map((item) => <button key={item.id} className={`kb-nav-item ${selected?.id === item.id ? "active" : ""}`} onClick={() => onSelect(item)}><span className="nav-dot" /><span className="nav-name">{item.name}</span>{item.isBusy ? <LoaderCircle size={14} className="spin" /> : <ChevronRight size={14} />}</button>)}</nav>
      <button className="new-kb-button" onClick={onCreate}><Plus size={16} /> 新建知识库</button>
      <div className="sidebar-bottom"><button className={`side-action ${systemOpen ? "active" : ""}`} onClick={onSystem}><Settings size={16} /> 系统状态</button><div className="user-card"><div className="avatar">A</div><div><strong>Administrator</strong><span>local access</span></div><button className="icon-button subtle" title="退出登录" onClick={onLogout}><LogOut size={15} /></button></div></div>
    </aside>
  );
}

function EmptyState({ onCreate }) {
  return <div className="empty-state"><div className="empty-icon"><FolderOpen size={28} /></div><p className="eyebrow">NO WORKSPACE SELECTED</p><h2>先选一个知识库</h2><p>新建知识库后，文档转换和 OpenClaw 索引会在这里统一管理。</p><button className="primary-button" onClick={onCreate}><Plus size={16} /> 新建知识库</button></div>;
}

function SystemPanel({ onClose }) {
  const [data, setData] = useState(null);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [passwordMessage, setPasswordMessage] = useState("");
  const [passwordError, setPasswordError] = useState("");
  const [passwordBusy, setPasswordBusy] = useState(false);
  useEffect(() => { api("/api/system/status").then(setData).catch(() => setData({ error: "无法读取系统状态" })); }, []);
  async function updatePassword(event) {
    event.preventDefault();
    setPasswordBusy(true); setPasswordMessage(""); setPasswordError("");
    try {
      await api("/api/auth/password", { method: "POST", body: JSON.stringify({ currentPassword, newPassword }) });
      setCurrentPassword(""); setNewPassword(""); setPasswordMessage("密码已更新");
    } catch (err) { setPasswordError(err.message); }
    finally { setPasswordBusy(false); }
  }
  return <section className="system-panel"><div className="section-heading"><div><p className="eyebrow">RUNTIME CHECK</p><h2>系统状态</h2></div><button className="icon-button" title="关闭" onClick={onClose}><X size={18} /></button></div>{data?.error ? <ErrorNotice message={data.error} /> : <div className="system-grid">{[["OpenClaw", data?.openclaw?.ok, data?.openclaw?.version || data?.openclaw?.error], ["MarkItDown", data?.markitdown?.ok, "in-process converter"], ["SQLite", data?.database?.ok, data?.database?.path], ["Worker", data?.worker?.running, data?.worker?.running ? "accepting jobs" : "stopped"]].map(([label, ok, detail]) => <div className="system-card" key={label}><div className={`system-icon ${ok ? "ok" : "bad"}`}>{ok ? <Check size={17} /> : <AlertTriangle size={17} />}</div><div><strong>{label}</strong><span>{detail || "not available"}</span></div></div>)}</div>}<form className="password-form" onSubmit={updatePassword}><div><p className="eyebrow">ACCOUNT SECURITY</p><h3>修改管理员密码</h3></div><label>当前密码<input type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} /></label><label>新密码<input type="password" minLength="8" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} /></label>{passwordError && <ErrorNotice message={passwordError} />}{passwordMessage && <div className="success-notice"><CheckCircle2 size={15} />{passwordMessage}</div>}<button className="primary-button" disabled={passwordBusy}>{passwordBusy ? <LoaderCircle className="spin" size={16} /> : <ShieldCheck size={16} />}{passwordBusy ? "正在更新" : "更新密码"}</button></form></section>;
}

function JobPanel({ job, onClose }) {
  if (!job) return null;
  const meta = statusMeta(job.status, job.phase);
  return <aside className="job-panel"><div className="job-panel-header"><div><p className="eyebrow">RUN DETAIL</p><h3>同步任务</h3></div><button className="icon-button" title="关闭任务详情" onClick={onClose}><X size={17} /></button></div><div className="job-summary"><StatusPill status={job.status} phase={job.phase} /><span>{job.mode === "force" ? "强制重建" : "自动同步"}</span></div><div className="job-timeline"><div className={job.phase === "converting" ? "current" : ""}><span>01</span><strong>转换文档</strong></div><div className={job.phase === "publishing" ? "current" : ""}><span>02</span><strong>写入目录</strong></div><div className={job.phase === "indexing" ? "current" : ""}><span>03</span><strong>执行索引</strong></div></div>{job.files?.length > 0 && <div className="job-files">{job.files.map((file) => <div key={file.source_name}><span>{file.source_name}</span><small className={file.status === "failed" ? "danger-text" : ""}>{file.status === "converted" ? "converted" : file.status}</small></div>)}</div>}{job.error && <div className="job-error"><AlertTriangle size={15} />{job.error}</div>}<div className="log-block"><div className="log-label">COMMAND OUTPUT</div><pre>{job.stdout || job.stderr || "等待任务输出..."}</pre></div><div className="job-time">创建于 {formatTime(job.createdAt)}{job.finishedAt && ` · 完成于 ${formatTime(job.finishedAt)}`}</div></aside>;
}

function Workspace({ kb, onRefresh, onUpload, onIndex, onDelete, busy, onShowJob }) {
  const [documents, setDocuments] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const completedJobId = ["succeeded", "failed"].includes(kb.latestJob?.status) ? kb.latestJob.id : "";

  async function loadDocuments() {
    setLoading(true);
    try {
      const result = await api(`/api/knowledge-bases/${kb.id}/documents`);
      setDocuments(result.items);
      setError("");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadDocuments(); }, [kb.id, completedJobId]);
  useEffect(() => {
    setDeleteTarget(null);
    setDeleteError("");
  }, [kb.id]);

  async function refreshAll() {
    await onRefresh();
    await loadDocuments();
  }

  async function remove() {
    if (!deleteTarget) return;
    setDeleteBusy(true);
    setDeleteError("");
    try {
      const result = await api(`/api/knowledge-bases/${kb.id}/documents/${deleteTarget.id}`, { method: "DELETE" });
      setDeleteTarget(null);
      onShowJob(result);
    } catch (err) {
      setDeleteError(err.message);
    } finally {
      setDeleteBusy(false);
    }
  }

  return (
    <>
      <main className="workspace">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">KNOWLEDGE BASE / {kb.slug}</p>
            <h1>{kb.name}</h1>
            <div className="path-line"><FolderOpen size={14} />{kb.path}<span className="separator">·</span>{kb.agentId}</div>
          </div>
          <div className="header-actions">
            <button className="ghost-button task-button" disabled={!kb.latestJob} onClick={() => onShowJob(kb.activeJob || kb.latestJob)}><Clock3 size={16} />最近任务</button>
            <button className="ghost-button refresh-button" disabled={busy} onClick={refreshAll}><RefreshCw size={16} className={busy ? "spin" : ""} />刷新</button>
            <button className="primary-button" disabled={busy} onClick={onUpload}><Upload size={16} />导入文档</button>
            <button className="icon-button danger-icon" disabled={busy} title="删除知识库" onClick={() => onDelete(kb)}><Trash2 size={17} /></button>
          </div>
        </header>
        <ErrorNotice message={error} onClose={() => setError("")} />
        {busy && <div className="busy-banner"><LoaderCircle size={16} className="spin" /><span>后台任务正在运行，文件操作暂时锁定</span><button onClick={() => onShowJob(kb.activeJob || kb.latestJob)}>查看任务 <ChevronRight size={14} /></button></div>}
        <section className="metric-row">
          <div className="metric"><span>文档数量</span><strong>{documents.length.toString().padStart(2, "0")}</strong><small>Markdown files</small></div>
          <div className="metric"><span>当前状态</span><strong className="metric-status"><StatusPill status={kb.latestJob?.status} phase={kb.latestJob?.phase} /></strong><small>{kb.latestJob ? formatTime(kb.latestJob.finishedAt || kb.latestJob.createdAt) : "尚未执行索引"}</small></div>
          <div className="metric"><span>存储目录</span><strong><HardDrive size={20} /></strong><small>external directory</small></div>
          <div className="metric accent"><span>检索 agent</span><strong>{kb.agentId.replace("kb-", "")}</strong><small>isolated memory scope</small></div>
        </section>
        <section className="documents-section">
          <div className="section-heading">
            <div><p className="eyebrow">DOCUMENT REGISTER</p><h2>文件目录 <span>{documents.length}</span></h2></div>
            <div className="section-tools"><button className="icon-button" title="刷新文件列表" onClick={loadDocuments}><RefreshCw size={16} /></button><button className="force-button" disabled={busy} onClick={() => onIndex(true)}><Zap size={15} />强制同步</button></div>
          </div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>文件名称</th><th>类型</th><th>大小</th><th>更新时间</th><th className="action-col">操作</th></tr></thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan="5" className="table-empty"><LoaderCircle className="spin" size={18} />正在读取目录</td></tr>
                ) : documents.length === 0 ? (
                  <tr><td colSpan="5" className="table-empty"><FileText size={22} /><span>目录中还没有 Markdown 文档</span><button className="text-button" disabled={busy} onClick={onUpload}>导入第一批文档</button></td></tr>
                ) : documents.map((document) => (
                  <tr key={document.id}>
                    <td><div className="file-name"><div className="file-icon"><FileText size={16} /></div><span>{document.name}</span></div></td>
                    <td><span className="file-type">{document.sourceExt === ".docx" ? "DOCX → MD" : "MARKDOWN"}</span></td>
                    <td>{formatBytes(document.sizeBytes)}</td>
                    <td>{formatTime(document.modifiedAt)}</td>
                    <td className="action-col"><button className="icon-button danger-icon" disabled={busy} title="删除文件" onClick={() => { setDeleteTarget(document); setDeleteError(""); }}><Trash2 size={16} /></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </main>
      {deleteTarget && (
        <ConfirmDialog
          title="删除文件？"
          subject={deleteTarget.name}
          description="目录中的 Markdown 文件将被永久删除，随后自动执行增量索引。"
          note="此操作无法从管理后台撤销。"
          confirmLabel="删除并同步"
          busy={deleteBusy}
          error={deleteError}
          onClose={() => { setDeleteTarget(null); setDeleteError(""); }}
          onConfirm={remove}
        />
      )}
    </>
  );
}

function App() {
  const [user, setUser] = useState(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [items, setItems] = useState([]);
  const [selected, setSelected] = useState(null);
  const [showCreate, setShowCreate] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const [showSystem, setShowSystem] = useState(false);
  const [job, setJob] = useState(null);
  const [error, setError] = useState("");
  const [deleteKbTarget, setDeleteKbTarget] = useState(null);
  const [deleteKbBusy, setDeleteKbBusy] = useState(false);
  const [deleteKbError, setDeleteKbError] = useState("");

  async function loadMe() {
    try {
      const result = await api("/api/auth/me");
      csrfToken = result.csrfToken;
      setUser(result.user);
    } catch {
      setUser(null);
    } finally {
      setAuthLoading(false);
    }
  }

  async function loadKbs(preferredId) {
    try {
      const result = await api("/api/knowledge-bases");
      setItems(result.items);
      const id = preferredId || selected?.id;
      setSelected(result.items.find((item) => item.id === id) || result.items[0] || null);
      setError("");
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => { loadMe(); }, []);
  useEffect(() => { if (user) loadKbs(); }, [user]);
  useEffect(() => {
    if (!user) return undefined;
    const timer = setInterval(() => loadKbs(), 1500);
    return () => clearInterval(timer);
  }, [user, selected?.id]);
  useEffect(() => {
    if (!job || ["succeeded", "failed"].includes(job.status)) return undefined;
    let active = true;
    const refreshJob = async () => {
      try {
        const result = await api(`/api/jobs/${job.id}`);
        if (!active) return;
        setJob(result);
        if (["succeeded", "failed"].includes(result.status)) loadKbs(result.knowledgeBaseId);
      } catch (err) {
        if (active) setError(err.message);
      }
    };
    const timer = setInterval(refreshJob, 1000);
    refreshJob();
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [job?.id, job?.status]);

  const busy = Boolean(items.some((item) => item.isBusy));
  const current = useMemo(() => items.find((item) => item.id === selected?.id) || selected, [items, selected]);

  async function logout() {
    try { await api("/api/auth/logout", { method: "POST" }); } catch { /* session can already be gone */ }
    csrfToken = "";
    setUser(null);
  }

  function created(kb) {
    setShowCreate(false);
    loadKbs(kb.id);
  }

  function newJob(result) {
    setJob(result);
    loadKbs(current?.id);
  }

  async function removeKnowledgeBase() {
    if (!deleteKbTarget) return;
    setDeleteKbBusy(true);
    setDeleteKbError("");
    try {
      await api(`/api/knowledge-bases/${deleteKbTarget.id}`, { method: "DELETE" });
      setItems((currentItems) => currentItems.filter((item) => item.id !== deleteKbTarget.id));
      setSelected((currentSelected) => currentSelected?.id === deleteKbTarget.id ? null : currentSelected);
      setJob(null);
      setDeleteKbTarget(null);
      setError("");
    } catch (err) {
      setDeleteKbError(err.message);
    } finally {
      setDeleteKbBusy(false);
    }
  }

  if (authLoading) return <div className="loading-screen"><LoaderCircle className="spin" size={22} />正在连接管理后台</div>;
  if (!user) return <Login onLogin={(nextUser) => { setUser(nextUser); }} />;

  return (
    <div className="app-shell">
      <Sidebar items={items} selected={current} onSelect={(item) => { setSelected(item); setShowSystem(false); }} onCreate={() => setShowCreate(true)} onLogout={logout} systemOpen={showSystem} onSystem={() => setShowSystem((value) => !value)} />
      <div className="main-stage">
        <div className="topline"><span><Activity size={14} /> CONTROL ROOM / LOCAL</span><span>{new Date().toLocaleDateString("zh-CN", { weekday: "long", month: "long", day: "numeric" })}</span></div>
        {error && <div className="stage-error"><ErrorNotice message={error} onClose={() => setError("")} /></div>}
        {showSystem ? (
          <SystemPanel onClose={() => setShowSystem(false)} />
        ) : current ? (
          <Workspace
            kb={current}
            busy={busy}
            onRefresh={() => loadKbs(current.id)}
            onUpload={() => setShowUpload(true)}
            onDelete={(kb) => { setDeleteKbTarget(kb); setDeleteKbError(""); }}
            onIndex={(force) => api(`/api/knowledge-bases/${current.id}/index`, { method: "POST", body: JSON.stringify({ force }) }).then(newJob).catch((err) => setError(err.message))}
            onShowJob={setJob}
          />
        ) : <EmptyState onCreate={() => setShowCreate(true)} />}
      </div>
      {job && <JobPanel job={job} onClose={() => setJob(null)} />}
      {showCreate && <CreateKbModal onClose={() => setShowCreate(false)} onCreated={created} />}
      {showUpload && current && <UploadModal kb={current} onClose={() => setShowUpload(false)} onUploaded={newJob} />}
      {deleteKbTarget && (
        <ConfirmDialog
          title="删除知识库？"
          subject={deleteKbTarget.name}
          description="此知识库的管理记录、文档清单和任务记录将被移除。"
          note={`服务器目录 ${deleteKbTarget.path} 及其中的文件会保留。`}
          confirmLabel="删除知识库"
          busy={deleteKbBusy}
          error={deleteKbError}
          onClose={() => { setDeleteKbTarget(null); setDeleteKbError(""); }}
          onConfirm={removeKnowledgeBase}
        />
      )}
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
