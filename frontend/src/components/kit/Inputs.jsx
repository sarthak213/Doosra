import { useEffect, useMemo, useRef, useState } from "react";
import { apiGet } from "../../api.js";
import { useFetch } from "../../hooks/useFetch.js";

let optionsPromise = null;
export function useOptions() {
  const { data } = useFetch(() => (optionsPromise ||= apiGet("/api/options")), "options");
  return data;
}

let metricsPromise = null;
export function useMetrics() {
  const { data } = useFetch(() => (metricsPromise ||= apiGet("/api/metrics")), "metrics");
  return data?.metrics || [];
}

// A text input that commits on Enter or blur (so typing doesn't refetch on
// every keystroke), with optional suggestions.
function CommitInput({ value, onCommit, placeholder, list, type = "text", ariaLabel }) {
  const [draft, setDraft] = useState(value ?? "");
  useEffect(() => setDraft(value ?? ""), [value]);
  const commit = () => {
    const v = type === "number" ? (draft === "" ? null : Number(draft)) : draft.trim() || null;
    if (v !== (value ?? null)) onCommit(v);
  };
  return (
    <input type={type} value={draft} placeholder={placeholder} list={list} aria-label={ariaLabel || placeholder}
      onChange={(e) => setDraft(e.target.value)} onBlur={commit}
      onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }} />
  );
}

const FIELDS = {
  competition: { label: "Competition", list: "opt-competitions", placeholder: "any" },
  format: { label: "Format", select: ["", "Test", "ODI", "T20I", "T20", "first-class", "List A", "international"] },
  gender: { label: "Gender", select: ["", "male", "female", "all"], names: { "": "auto" } },
  team: { label: "Team", list: "opt-teams", placeholder: "any" },
  opposition: { label: "Opposition", list: "opt-teams", placeholder: "any" },
  venue: { label: "Venue", list: "opt-venues", placeholder: "any" },
  season: { label: "Season", placeholder: "e.g. 2024" },
  from_year: { label: "From", type: "number", placeholder: "year" },
  to_year: { label: "To", type: "number", placeholder: "year" },
  phase: { label: "Phase", select: ["", "powerplay", "middle", "death"], names: { "": "all" } },
  innings: { label: "Innings", select: ["", "1", "2"], names: { "": "both", 1: "1st (setting)", 2: "2nd (chasing)" } },
};

// The shared filter bar. `show` limits which filters appear.
export function FilterBar({ filters, onChange, show = Object.keys(FIELDS) }) {
  const options = useOptions();
  const set = (key, v) => {
    const next = { ...filters };
    if (v === null || v === "" || v === undefined) delete next[key];
    else next[key] = key === "innings" ? Number(v) : v;
    onChange(next);
  };
  const active = Object.keys(filters || {}).length;
  return (
    <div className="filter-bar" role="group" aria-label="Filters">
      {show.map((key) => {
        const f = FIELDS[key];
        return (
          <label key={key} className="filter-field">
            <span>{f.label}</span>
            {f.select ? (
              <select value={filters?.[key] ?? ""} onChange={(e) => set(key, e.target.value)}>
                {f.select.map((o) => (
                  <option key={o} value={o}>{f.names?.[o] ?? (o || "any")}</option>
                ))}
              </select>
            ) : (
              <CommitInput value={filters?.[key]} onCommit={(v) => set(key, v)} placeholder={f.placeholder} list={f.list}
                type={f.type} ariaLabel={f.label} />
            )}
          </label>
        );
      })}
      {active > 0 && (
        <button type="button" className="ghost-btn filter-clear" onClick={() => onChange({})}>Clear ({active})</button>
      )}
      <datalist id="opt-competitions">{options?.competitions?.slice(0, 120).map((c) => <option key={c} value={c} />)}</datalist>
      <datalist id="opt-teams">{options?.teams?.map((c) => <option key={c} value={c} />)}</datalist>
      <datalist id="opt-venues">{options?.venues?.map((c) => <option key={c} value={c} />)}</datalist>
    </div>
  );
}

// Registry-driven metric picker: single (select) or multiple (chips + menu).
export function MetricPicker({ role, value, onChange, multiple = false, label = "Metric" }) {
  const all = useMetrics();
  const metrics = useMemo(() => all.filter((m) => m.role === role), [all, role]);
  const families = useMemo(() => [...new Set(metrics.map((m) => m.family))], [metrics]);
  const byId = Object.fromEntries(metrics.map((m) => [m.id, m]));
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef(null);

  useEffect(() => {
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  if (!multiple) {
    return (
      <label className="filter-field">
        <span>{label}</span>
        <select value={value || ""} onChange={(e) => onChange(e.target.value)} title={byId[value]?.definition}>
          {families.map((f) => (
            <optgroup key={f} label={f}>
              {metrics.filter((m) => m.family === f).map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </optgroup>
          ))}
        </select>
      </label>
    );
  }

  const selected = value || [];
  const toggle = (id) => onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  const shown = metrics.filter((m) => !q || `${m.label} ${m.definition} ${m.family}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="metric-picker" ref={ref}>
      <span className="field-label">{label}</span>
      <div className="metric-chips">
        {selected.map((id) => (
          <button type="button" key={id} className="metric-chip" title={byId[id]?.definition} onClick={() => toggle(id)}>
            {byId[id]?.label || id} <span aria-hidden="true">×</span>
          </button>
        ))}
        <button type="button" className="ghost-btn" onClick={() => setOpen((o) => !o)} aria-expanded={open}>+ Add metric</button>
      </div>
      {open && (
        <div className="metric-menu">
          <input autoFocus placeholder="Search metrics (e.g. true, boundary, consistency)" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="metric-menu-list">
            {families.map((f) => {
              const items = shown.filter((m) => m.family === f);
              if (!items.length) return null;
              return (
                <div key={f}>
                  <p className="metric-family">{f}</p>
                  {items.map((m) => (
                    <label key={m.id} className="metric-option" title={m.definition}>
                      <input type="checkbox" checked={selected.includes(m.id)} onChange={() => toggle(m.id)} />
                      <span>
                        <strong>{m.label}</strong>
                        <small>{m.definition}</small>
                      </span>
                    </label>
                  ))}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// Player search with context (teams, span, matches) so namesakes are clear.
export function PlayerPicker({ onPick, placeholder = "Search a player…", autoFocus = false }) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState([]);
  const [active, setActive] = useState(0);

  useEffect(() => {
    if (q.trim().length < 2) {
      setItems([]);
      return undefined;
    }
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      apiGet("/api/search", { q, limit: 8 }, ctrl.signal).then((d) => { setItems(d.players || []); setActive(0); }).catch(() => {});
    }, 200);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [q]);

  const pick = (p) => { onPick(p.name); setQ(""); setItems([]); };
  return (
    <div className="player-picker">
      <input value={q} placeholder={placeholder} autoFocus={autoFocus} aria-label="Search players" role="combobox"
        aria-expanded={items.length > 0} onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, items.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
          else if (e.key === "Enter" && items[active]) { e.preventDefault(); pick(items[active]); }
          else if (e.key === "Escape") setItems([]);
        }} />
      {items.length > 0 && (
        <ul className="autocomplete-list player-results" role="listbox">
          {items.map((p, i) => (
            <li key={p.name} role="option" aria-selected={i === active} className={i === active ? "active" : ""}
              onMouseDown={(e) => { e.preventDefault(); pick(p); }}>
              <strong>{p.name}</strong>
              <span>{p.teams.slice(0, 2).join(", ")} · {p.span} · {p.matches} matches{p.gender === "female" ? " · women" : ""}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function RoleToggle({ value, onChange }) {
  return (
    <div className="segmented" role="radiogroup" aria-label="Discipline">
      {["batting", "bowling"].map((r) => (
        <button type="button" key={r} role="radio" aria-checked={value === r} className={value === r ? "on" : ""}
          onClick={() => onChange(r)}>
          {r}
        </button>
      ))}
    </div>
  );
}
