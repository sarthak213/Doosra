import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiGet, apiSend } from "../api.js";
import Panel from "../components/kit/Panel.jsx";
import { formatBytes, useDesktop } from "../desktop/DesktopProvider.jsx";

const MODES = [["", "Automatic (GPU if it works)"], ["vulkan", "GPU"], ["vulkan-nocoopmat", "GPU, compatibility mode"], ["cpu", "CPU only"]];
const CONTEXTS = [[8192, "8K (least memory)"], [16384, "16K (recommended)"], [32768, "32K (long chats, more memory)"]];
const MODE_LABEL = { vulkan: "on the GPU", "vulkan-nocoopmat": "on the GPU (compatibility mode)", cpu: "on the CPU" };

// The desktop app's settings: the AI engine and model, downloaded models, the cricket data.
export default function Settings() {
  const { status, refresh } = useDesktop();
  const navigate = useNavigate();
  const [message, setMessage] = useState(null);
  const [dataCheck, setDataCheck] = useState(null);
  const [busy, setBusy] = useState(false);
  if (!status) return <div className="view"><p className="muted">Loading…</p></div>;
  const s = status.settings;
  const eng = status.engine;
  const current = status.models.find((m) => m.id === s.model);

  const run = async (fn, ok) => {
    setBusy(true);
    try { await fn(); setMessage(ok); } catch (e) { setMessage(e.message); } finally { setBusy(false); refresh(); }
  };
  const saveEngine = (patch) => run(async () => {
    await apiSend("/api/desktop/settings", patch, "PUT");
    await apiSend("/api/desktop/engine/restart");
  }, "Saved. The AI engine is restarting with the new settings.");

  return (
    <div className="view settings-view">
      <div className="view-head">
        <div>
          <h1>Settings</h1>
          <p className="muted">Doosra {status.version}. Everything it keeps is in {status.home}.</p>
        </div>
      </div>
      {message && <p className="resolution-note" role="status">{message}</p>}

      <Panel title="AI" subtitle="The model that answers your questions, running on this computer.">
        <dl className="settings-list">
          <div><dt>Engine</dt><dd>{s.engine === "lmstudio" ? `LM Studio (${s.lmstudio_model})` : "Built in (llama.cpp)"}</dd></div>
          {s.engine === "builtin" && <div><dt>Model</dt><dd>{current ? `${current.label} (${current.quant})` : "none"}</dd></div>}
          {s.engine === "builtin" && (
            <div><dt>Status</dt><dd>{eng.running ? `Running ${MODE_LABEL[eng.mode] || ""}` : eng.error ? `Not running: ${eng.error}` : "Starting…"}</dd></div>
          )}
        </dl>
        <div className="setup-actions">
          <button type="button" className="primary-btn" onClick={() => navigate("/setup")}>Change AI engine or model</button>
        </div>
        {s.engine === "builtin" && (
          <div className="controls-row">
            <label className="filter-field"><span>Run on</span>
              <select value={s.engine_mode || ""} disabled={busy} onChange={(e) => saveEngine({ engine_mode: e.target.value || null })}>
                {MODES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
            <label className="filter-field"><span>Memory for a conversation</span>
              <select value={s.ctx_size} disabled={busy} onChange={(e) => saveEngine({ ctx_size: Number(e.target.value) })}>
                {CONTEXTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
          </div>
        )}
      </Panel>

      <Panel title="Downloaded models" subtitle="Delete a model you no longer use to free space.">
        {status.installed.length === 0 && <p className="empty-note">No models downloaded.</p>}
        <ul className="notes">
          {status.models.filter((m) => status.installed.includes(m.id)).map((m) => (
            <li className="note" key={m.id}>
              <div className="note-head">
                <span className="note-title grow">{m.label} ({m.quant})</span>
                <span className="note-meta">{formatBytes(m.size_bytes)}{m.id === s.model && s.model_path ? " · LM Studio's copy" : ""}</span>
                <button type="button" className="ghost-btn danger" disabled={busy || (m.id === s.model && s.engine === "builtin")}
                  title={m.id === s.model ? "In use" : undefined}
                  onClick={() => window.confirm(`Delete ${m.label}? You can download it again later.`) &&
                    run(() => apiSend(`/api/desktop/models/${m.id}`, undefined, "DELETE"), `${m.label} deleted.`)}>
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title="Cricket data" subtitle="Ball-by-ball data from Cricsheet (ODC-By 1.0), rebuilt every week. Doosra checks for a newer build once a day.">
        <dl className="settings-list">
          <div><dt>Installed</dt><dd>{status.data ? `Built ${status.data.built_at?.slice(0, 10)}, matches up to ${status.data.latest_match}` : "Not installed"}</dd></div>
          {dataCheck && (
            <div><dt>Latest</dt><dd>{dataCheck.newer ? `A newer build (${dataCheck.published.slice(0, 10)}, matches up to ${dataCheck.latest_match}) will be installed at the next daily check.` : "You have the latest build."}</dd></div>
          )}
        </dl>
        <div className="setup-actions">
          <button type="button" className="ghost-btn" disabled={busy}
            onClick={() => run(async () => setDataCheck(await apiGet("/api/desktop/data/check")), null)}>Check for newer data</button>
          <button type="button" className="ghost-btn" onClick={() => apiSend("/api/desktop/open-folder").catch((e) => setMessage(e.message))}>Open data folder</button>
        </div>
      </Panel>
    </div>
  );
}
