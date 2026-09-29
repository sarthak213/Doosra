import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { API_BASE, apiSend } from "../api.js";
import Wordmark from "../components/Wordmark.jsx";
import { formatBytes, useDesktop } from "../desktop/DesktopProvider.jsx";

const DATA_BYTES = 430e6;   // the compressed cricket database

// First run (and "Change AI" from Settings): pick the AI, then download the database and the model
// with progress, start the engine and check it answers. Everything resumes if it's interrupted.
export default function Setup() {
  const { status, refresh } = useDesktop();
  const navigate = useNavigate();
  const [engine, setEngine] = useState("builtin");
  const [model, setModel] = useState(null);
  const [useCopy, setUseCopy] = useState(true);
  const [lmModel, setLmModel] = useState("");
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const sourceRef = useRef(null);

  useEffect(() => {
    if (!status) return;
    setModel((m) => m || status.settings.model || status.hardware.recommended);
    if (status.setup?.state === "running") follow();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.hardware?.recommended]);
  useEffect(() => () => sourceRef.current?.close(), []);

  if (!status) return <main className="setup"><p className="muted">Checking this PC…</p></main>;
  const hw = status.hardware;
  const needsData = !status.data;
  const chosen = status.models.find((m) => m.id === model) || status.models[0];
  const installed = status.installed.includes(chosen.id);
  const hasCopy = status.lmstudio_copies.includes(chosen.id);
  const downloadBytes = (needsData ? DATA_BYTES : 0) + (engine === "builtin" && !installed && !(hasCopy && useCopy) ? chosen.size_bytes : 0);
  const tooSmall = hw.free_disk_gb * 1e9 < downloadBytes * 1.3 + 1.2e9;       // room for the unpacked database too

  function follow() {
    sourceRef.current?.close();
    const es = new EventSource(`${API_BASE}/api/desktop/setup/progress`);
    sourceRef.current = es;
    es.onmessage = (e) => {
      const snap = JSON.parse(e.data);
      setJob(snap);
      if (snap.state !== "running") { es.close(); refresh(); }
    };
    es.onerror = () => es.close();
  }

  async function start() {
    setError(null);
    try {
      setJob(await apiSend("/api/desktop/setup", {
        engine, model: engine === "builtin" ? chosen.id : null, use_lmstudio_copy: useCopy, lmstudio_model: lmModel || null }));
      follow();
    } catch (e) {
      setError(e.message);
    }
  }

  const running = job?.state === "running";
  if (job && job.state !== "idle") {
    return (
      <main className="setup">
        <div className="setup-card">
          <Wordmark />
          <h1>{job.state === "done" ? "Doosra is ready" : "Setting up Doosra"}</h1>
          <ol className="setup-steps">
            {job.steps.map((s) => (
              <li key={s.id} className={`setup-step ${s.state}`}>
                <span className="setup-step-mark" aria-hidden="true">
                  {{ done: "✓", skipped: "✓", error: "!", running: "…", waiting: "·" }[s.state]}
                </span>
                <div className="grow">
                  <div className="setup-step-head">
                    <strong>{s.label}</strong>
                    <span className="note-meta">{s.detail}</span>
                  </div>
                  {s.state === "running" && s.total > 0 && (
                    <div className="setup-bar" role="progressbar" aria-valuenow={Math.round((100 * s.done) / s.total)} aria-valuemin={0} aria-valuemax={100}>
                      <div style={{ width: `${(100 * s.done) / s.total}%` }} />
                      <span>{formatBytes(s.done)} of {formatBytes(s.total)} · {Math.round((100 * s.done) / s.total)}%</span>
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ol>
          {job.state === "error" && <p className="resolution-note" role="alert">{job.error}</p>}
          {job.state === "cancelled" && <p className="muted">Stopped. Downloads continue from where they stopped when you start again.</p>}
          <div className="setup-actions">
            {running && <button type="button" className="ghost-btn" onClick={() => apiSend("/api/desktop/setup/cancel")}>Stop</button>}
            {(job.state === "error" || job.state === "cancelled") && (
              <>
                <button type="button" className="primary-btn" onClick={start}>Try again</button>
                <button type="button" className="ghost-btn" onClick={() => setJob(null)}>Change choices</button>
              </>
            )}
            {job.state === "done" && (
              <button type="button" className="primary-btn" onClick={() => { refresh(); navigate("/ask"); }}>Open Doosra</button>
            )}
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="setup">
      <div className="setup-card">
        <Wordmark />
        <h1>Set up Doosra</h1>
        <p className="muted">
          Doosra runs its AI on this computer, so your questions never leave it. Pick a model, and it downloads what it
          needs once. You can stop and resume at any time.
        </p>

        <section className="setup-pc" aria-label="This computer">
          <div><span className="note-meta">Memory</span><strong>{hw.ram_gb} GB</strong></div>
          <div><span className="note-meta">Graphics</span><strong>{hw.gpus[0]?.name || "No usable GPU found (the CPU will be used)"}</strong></div>
          <div><span className="note-meta">Free space</span><strong>{hw.free_disk_gb} GB</strong></div>
        </section>

        <fieldset className="setup-choice">
          <legend>AI engine</legend>
          <label className={`setup-option${engine === "builtin" ? " on" : ""}`}>
            <input type="radio" name="engine" checked={engine === "builtin"} onChange={() => setEngine("builtin")} />
            <div>
              <strong>Built in</strong> <span className="setup-badge">recommended</span>
              <p className="note-meta">Doosra runs the model itself. Nothing else to install.</p>
            </div>
          </label>
          <label className={`setup-option${engine === "lmstudio" ? " on" : ""}${status.lmstudio.running ? "" : " disabled"}`}>
            <input type="radio" name="engine" disabled={!status.lmstudio.running} checked={engine === "lmstudio"} onChange={() => setEngine("lmstudio")} />
            <div>
              <strong>LM Studio</strong>
              <p className="note-meta">
                {status.lmstudio.running ? "Use the LM Studio already running on this PC." : "Not detected. Open LM Studio and start its server to use it instead."}
              </p>
            </div>
          </label>
        </fieldset>

        {engine === "builtin" ? (
          <fieldset className="setup-choice">
            <legend>Model</legend>
            {status.models.map((m) => (
              <label key={m.id} className={`setup-option${model === m.id ? " on" : ""}`}>
                <input type="radio" name="model" checked={model === m.id} onChange={() => setModel(m.id)} />
                <div>
                  <strong>{m.label}</strong>
                  {m.id === hw.recommended && <span className="setup-badge">best for this PC</span>}
                  {status.installed.includes(m.id) && <span className="setup-badge done">installed</span>}
                  <p className="note-meta">{m.note} {formatBytes(m.size_bytes)} download.</p>
                </div>
              </label>
            ))}
            {hasCopy && !installed && (
              <label className="setup-check">
                <input type="checkbox" checked={useCopy} onChange={(e) => setUseCopy(e.target.checked)} />
                LM Studio already has this model: use its copy instead of downloading it again (it's checked first).
              </label>
            )}
          </fieldset>
        ) : (
          <label className="filter-field">
            <span>LM Studio model</span>
            <select value={lmModel} onChange={(e) => setLmModel(e.target.value)}>
              <option value="">Pick automatically (a Qwen model if there is one)</option>
              {status.lmstudio.models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
        )}

        <p className="note-meta">
          {needsData ? "Also downloads the cricket database (about 430 MB; data from Cricsheet, ODC-By 1.0). " : ""}
          {downloadBytes ? `Total download about ${formatBytes(downloadBytes)}.` : "Nothing to download."}
        </p>
        {tooSmall && <p className="resolution-note" role="alert">There may not be enough free space for this. Free some space or pick the smaller model.</p>}
        {error && <p className="resolution-note" role="alert">{error}</p>}
        <div className="setup-actions">
          <button type="button" className="primary-btn" onClick={start}>{downloadBytes ? "Download and set up" : "Set up"}</button>
          {status.ready && <button type="button" className="ghost-btn" onClick={() => navigate(-1)}>Cancel</button>}
        </div>
      </div>
    </main>
  );
}
