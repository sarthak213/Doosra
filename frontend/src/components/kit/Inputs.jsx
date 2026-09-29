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
// every keystroke), with optional suggestions in a themed list (a native
// <datalist> popup can't be styled).
export function CommitInput({ value, onCommit, placeholder, options, type = "text", ariaLabel }) {
  const [draft, setDraft] = useState(value ?? "");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  useEffect(() => setDraft(value ?? ""), [value]);
  const commitValue = (raw) => {
    const v = type === "number" ? (raw === "" ? null : Number(raw)) : String(raw).trim() || null;
    if (v !== (value ?? null)) onCommit(v);
  };
  const matches = useMemo(() => {
    if (!options?.length) return [];
    const q = String(draft).trim().toLowerCase();
    const hits = q ? options.filter((o) => o.toLowerCase().includes(q)) : options;
    return hits.slice(0, 8);
  }, [options, draft]);
  const pick = (o) => { setDraft(o); setOpen(false); commitValue(o); };
  const shown = open && matches.length > 0 && !(matches.length === 1 && matches[0] === draft);
  return (
    <div className="suggest">
      <input type={type} value={draft} placeholder={placeholder} aria-label={ariaLabel || placeholder}
        role={options ? "combobox" : undefined} aria-expanded={options ? shown : undefined} autoComplete="off"
        onChange={(e) => { setDraft(e.target.value); setOpen(true); setActive(-1); }}
        onFocus={() => setOpen(true)}
        onBlur={() => { setOpen(false); commitValue(draft); }}
        onKeyDown={(e) => {
          if (shown && e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, matches.length - 1)); }
          else if (shown && e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
          else if (e.key === "Enter") {
            if (shown && active >= 0) { e.preventDefault(); pick(matches[active]); } else e.currentTarget.blur();
          } else if (e.key === "Escape") setOpen(false);
        }} />
      {shown && (
        <ul className="autocomplete-list suggest-list" role="listbox">
          {matches.map((o, i) => (
            <li key={o} role="option" aria-selected={i === active} className={i === active ? "active" : ""}
              onMouseDown={(e) => { e.preventDefault(); pick(o); }}>{o}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Innings choices depend on the format: a Test has four innings; limited
// overs have two (setting and chasing).
const MULTI_DAY = new Set(["Test", "first-class"]);
const LIMITED = new Set(["ODI", "T20I", "T20", "List A"]);
function inningsOptions(format) {
  if (MULTI_DAY.has(format)) return ["", "1", "2", "3", "4"];
  if (LIMITED.has(format)) return ["", "1", "2"];
  return ["", "1", "2", "3", "4"];
}
function inningsNames(format) {
  if (LIMITED.has(format)) return { "": "all", 1: "1st (setting)", 2: "2nd (chasing)" };
  if (MULTI_DAY.has(format)) return { "": "all", 1: "1st", 2: "2nd", 3: "3rd", 4: "4th" };
  return { "": "all", 1: "1st (setting)", 2: "2nd (chasing)", 3: "3rd (multi-day)", 4: "4th (multi-day)" };
}

const FIELDS = {
  competition: { label: "Competition", options: "competitions", placeholder: "any" },
  format: { label: "Format", select: ["", "Test", "ODI", "T20I", "T20", "first-class", "List A", "international"] },
  gender: { label: "Gender", select: ["", "male", "female", "all"], names: { "": "auto" } },
  team: { label: "Team", options: "teams", placeholder: "any" },
  opposition: { label: "Opposition", options: "teams", placeholder: "any" },
  venue: { label: "Venue", options: "venues", placeholder: "any" },
  season: { label: "Season", placeholder: "e.g. 2024" },
  from_year: { label: "From", type: "number", placeholder: "year" },
  to_year: { label: "To", type: "number", placeholder: "year" },
  phase: { label: "Phase", select: ["", "powerplay", "middle", "death"], names: { "": "all" } },
  innings: { label: "Innings", select: (filters) => inningsOptions(filters?.format),
    names: (filters) => inningsNames(filters?.format) },
  // From the player's side: "won" = matches their team won. Batting and bowling.
  result: { label: "Match result", select: ["", "won", "lost", "drawn", "tied", "no result"],
    names: { "": "any", won: "team won", lost: "team lost" } },
  // How the batter came in -- batting only. Compare players in like-for-like roles (an opener with
  // openers, a finisher with finishers).
  position: { label: "Batting position", batting: true,
    select: ["", "1-2", "1-3", "4-7", "5-7", "8-11", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"],
    names: { "": "any", "1-2": "openers (1-2)", "1-3": "top order (1-3)", "4-7": "middle order (4-7)",
      "5-7": "finishers (5-7)", "8-11": "lower order (8-11)" } },
  entry_phase: { label: "Came in during", batting: true, select: ["", "powerplay", "middle", "death"], names: { "": "any" } },
  entry_wickets: { label: "Wickets down at entry", batting: true, select: ["", "0", "1", "2", "1-2", "3-4", "3+", "5+"],
    names: { "": "any" } },
};

const BATTING_ONLY = Object.keys(FIELDS).filter((k) => FIELDS[k].batting);

// The filters a view should send: bowling views drop the batting-only ones.
export function filtersFor(role, filters) {
  if (role !== "bowling" || !filters) return filters;
  return Object.fromEntries(Object.entries(filters).filter(([k]) => !BATTING_ONLY.includes(k)));
}

// The shared filter bar. `show` limits which filters appear; with role="bowling" the batting-only
// ones are hidden (and dropped from requests by filtersFor).
export function FilterBar({ filters, onChange, show = Object.keys(FIELDS), role }) {
  const options = useOptions();
  const set = (key, v) => {
    const next = { ...filters };
    if (v === null || v === "" || v === undefined) delete next[key];
    else next[key] = key === "innings" ? Number(v) : v;
    // Limited-overs formats have no 3rd or 4th innings.
    if (key === "format" && next.innings > 2 && LIMITED.has(next.format)) delete next.innings;
    onChange(next);
  };
  const active = Object.keys(filters || {}).length;
  const shown = role === "bowling" ? show.filter((k) => !BATTING_ONLY.includes(k)) : show;
  const parked = role === "bowling" && BATTING_ONLY.some((k) => filters?.[k] != null);
  return (
    <div className="filter-bar" role="group" aria-label="Filters">
      {shown.map((key) => {
        const f = FIELDS[key];
        const choices = typeof f.select === "function" ? f.select(filters) : f.select;
        const names = typeof f.names === "function" ? f.names(filters) : f.names;
        return (
          <label key={key} className="filter-field">
            <span>{f.label}</span>
            {f.select ? (
              <select value={filters?.[key] ?? ""} onChange={(e) => set(key, e.target.value)}>
                {choices.map((o) => (
                  <option key={o} value={o}>{names?.[o] ?? (o || "any")}</option>
                ))}
              </select>
            ) : (
              <CommitInput value={filters?.[key]} onCommit={(v) => set(key, v)} placeholder={f.placeholder}
                options={f.options ? options?.[f.options]?.slice(0, f.options === "competitions" ? 120 : undefined) : undefined}
                type={f.type} ariaLabel={f.label} />
            )}
          </label>
        );
      })}
      {active > 0 && (
        <button type="button" className="ghost-btn filter-clear" onClick={() => onChange({})}>Clear ({active})</button>
      )}
      {parked && <span className="note-meta filter-parked">Batting position and entry filters are set but don't apply to bowling.</span>}
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
