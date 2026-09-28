import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { AXIS_TEXT, GRID, INK, NEUTRAL_POINT, SERIES, TICK, TOOLTIP, formatValue, humanize } from "./theme.js";

function Legend({ items }) {
  return (
    <ul className="chart-legend">
      {items.map((it) => (
        <li key={it.label}>
          <span className="legend-swatch" style={{ background: it.color }} aria-hidden="true" />
          {it.label}
        </li>
      ))}
    </ul>
  );
}

// Several series over one x axis (form, career arcs). One y scale only:
// callers pass series that share units. A legend appears for 2+ series, and
// each line also carries a direct label at its last point.
export function LineChartKit({ data, x, series, references = [], height = 280, xLabel, yLabel, yDomain }) {
  if (!data?.length) return null;
  // End labels are placed in render order; one that would overlap an
  // already-placed label is nudged down so names never collide.
  // Recharts may call a label renderer more than once per render, so the
  // placement is remembered per series.
  const placed = new Map();
  const labelY = (key, x, y) => {
    if (placed.has(key)) return placed.get(key).y;
    let out = y;
    const clash = () => [...placed.values()].some((p) => Math.abs(p.x - x) < 110 && Math.abs(p.y - out) < 13);
    while (clash()) out += 13;
    placed.set(key, { x, y: out });
    return out;
  };
  const lastIndex = Object.fromEntries(
    series.map((s) => {
      let idx = -1;
      data.forEach((d, i) => {
        if (d[s.key] !== null && d[s.key] !== undefined) idx = i;
      });
      return [s.key, idx];
    })
  );
  return (
    <div className="chart-block">
      {series.length > 1 && <Legend items={series.map((s, i) => ({ label: s.label, color: s.color || SERIES[i] }))} />}
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 10, right: series.length > 1 ? 96 : 24, left: 4, bottom: 4 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey={x} stroke={AXIS_TEXT} tick={TICK} tickLine={false} minTickGap={24}
            label={xLabel ? { value: xLabel, position: "insideBottom", offset: -2, fill: AXIS_TEXT, fontSize: 11 } : undefined} />
          <YAxis stroke={AXIS_TEXT} tick={TICK} tickLine={false} axisLine={false} width={52} domain={yDomain || ["auto", "auto"]}
            label={yLabel ? { value: yLabel, angle: -90, position: "insideLeft", fill: AXIS_TEXT, fontSize: 11 } : undefined} />
          <Tooltip {...TOOLTIP} formatter={(v, name) => [formatValue(v, name), series.find((s) => s.key === name)?.label || humanize(name)]}
            cursor={{ stroke: "rgba(241,232,214,0.25)" }} />
          {references.map((r) => (
            <ReferenceLine key={r.label} y={r.y} stroke="rgba(241,232,214,0.35)" strokeDasharray="4 4"
              label={{ value: r.label, position: "insideTopRight", fill: AXIS_TEXT, fontSize: 11 }} />
          ))}
          {series.map((s, i) => (
            <Line key={s.key} type="linear" dataKey={s.key} stroke={s.color || SERIES[i]} strokeWidth={2}
              dot={data.length <= 40 ? { r: 3, strokeWidth: 0, fill: s.color || SERIES[i] } : false}
              activeDot={{ r: 5 }} connectNulls isAnimationActive={false}
              label={series.length > 1 ? (props) => (props.index === lastIndex[s.key] ? (
                <text x={props.x + 6} y={labelY(s.key, props.x, props.y)} dy={4} fill={INK} fontSize={11} fontFamily="IBM Plex Sans">{s.label}</text>
              ) : null) : undefined} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// Every qualified player as a point; medians split the plane into quadrants.
// Points are neutral; watchlisted players are brass, the selected one blue.
export function ScatterMatrix({ points, xKey, yKey, xLabel, yLabel, medians, highlighted = [], selected, onSelect,
  standouts = [], xBetterHigh = true, yBetterHigh = true, height = 460, medianLabel = "median",
  pointLabel = "Qualified players" }) {
  if (!points?.length) return null;
  // Standouts (best on both axes) stay neutral but get a name label.
  const named = points.filter((p) => standouts.includes(p.player) && !highlighted.includes(p.player) && p.player !== selected);
  const base = points.filter((p) => !highlighted.includes(p.player) && p.player !== selected && !standouts.includes(p.player));
  const hl = points.filter((p) => highlighted.includes(p.player) && p.player !== selected);
  const sel = points.filter((p) => p.player === selected);
  const tip = ({ active, payload }) => {
    if (!active || !payload?.length) return null;
    const p = payload[0].payload;
    return (
      <div style={TOOLTIP.contentStyle} className="scatter-tip">
        <strong>{p.player}</strong>
        <div>{p.team}</div>
        <div>{xLabel}: {formatValue(p[xKey], xKey)}</div>
        <div>{yLabel}: {formatValue(p[yKey], yKey)}</div>
        <div>Balls: {formatValue(p.balls)}</div>
      </div>
    );
  };
  return (
    <div className="chart-block">
      <Legend items={[{ label: pointLabel, color: NEUTRAL_POINT }, ...(hl.length ? [{ label: "Watchlist", color: SERIES[0] }] : []),
        ...(sel.length ? [{ label: selected, color: SERIES[1] }] : [])]} />
      <ResponsiveContainer width="100%" height={height}>
        <ScatterChart margin={{ top: 16, right: 96, bottom: 28, left: 8 }}>
          <CartesianGrid stroke={GRID} />
          <XAxis type="number" dataKey={xKey} name={xLabel} stroke={AXIS_TEXT} tick={TICK} domain={["auto", "auto"]}
            label={{ value: `${xLabel} ${xBetterHigh ? "→ better" : "← better"}`, position: "insideBottom", offset: -16, fill: AXIS_TEXT, fontSize: 12 }} />
          <YAxis type="number" dataKey={yKey} name={yLabel} stroke={AXIS_TEXT} tick={TICK} width={56} domain={["auto", "auto"]}
            label={{ value: `${yLabel} ${yBetterHigh ? "↑ better" : "↓ better"}`, angle: -90, position: "insideLeft", fill: AXIS_TEXT, fontSize: 12 }} />
          <ZAxis range={[42, 42]} />
          <Tooltip content={tip} cursor={{ strokeDasharray: "3 3", stroke: "rgba(241,232,214,0.25)" }} />
          {medians?.x != null && <ReferenceLine x={medians.x} stroke="rgba(241,232,214,0.3)" strokeDasharray="4 4" />}
          {medians?.y != null && <ReferenceLine y={medians.y} stroke="rgba(241,232,214,0.3)" strokeDasharray="4 4"
            label={{ value: medianLabel, position: "insideTopLeft", fill: AXIS_TEXT, fontSize: 11 }} />}
          <Scatter data={base} fill={NEUTRAL_POINT} onClick={(p) => onSelect?.(p.player)} isAnimationActive={false} />
          <Scatter data={named} fill="rgba(241, 232, 214, 0.7)" onClick={(p) => onSelect?.(p.player)} isAnimationActive={false}
            label={{ dataKey: "player", position: "right", fill: AXIS_TEXT, fontSize: 11, offset: 8 }} />
          <Scatter data={hl} fill={SERIES[0]} onClick={(p) => onSelect?.(p.player)} isAnimationActive={false}
            label={{ dataKey: "player", position: "top", fill: INK, fontSize: 11 }} />
          <Scatter data={sel} fill={SERIES[1]} onClick={(p) => onSelect?.(p.player)} isAnimationActive={false}
            label={{ dataKey: "player", position: "top", fill: INK, fontSize: 12 }} />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}

// Grid of cells shaded by one metric (single hue, brass): rows x columns.
// Cells with fewer than `minSample` (by subKey) aren't shaded -- a one-innings
// 75 shouldn't glow brighter than a 300-innings 52.
export function Heatmap({ rows, cols, cells, valueKey, labelKey, subKey, format, minSample = 5 }) {
  const reliable = (c) => c && typeof c[valueKey] === "number" && (c[subKey] ?? 0) >= minSample;
  const vals = Object.values(cells).filter(reliable).map((c) => c[valueKey]);
  const max = Math.max(...vals, 1);
  const min = Math.min(...vals, 0);
  const shade = (v) => (typeof v !== "number" ? "transparent" : `rgba(176, 138, 58, ${0.12 + 0.78 * ((v - min) / (max - min || 1))})`);
  return (
    <div className="heatmap" style={{ gridTemplateColumns: `minmax(120px, auto) repeat(${cols.length}, minmax(64px, 1fr))` }}>
      <div className="heatmap-corner">{labelKey}</div>
      {cols.map((c) => (
        <div key={c} className="heatmap-col">{c}</div>
      ))}
      {rows.map((r) => (
        <div className="heatmap-row" key={r} style={{ display: "contents" }}>
          <div className="heatmap-rowhead">{r}</div>
          {cols.map((c) => {
            const cell = cells[`${r}|${c}`];
            return (
              <div key={c} className={`heatmap-cell${cell && !reliable(cell) ? " thin" : ""}`}
                style={{ background: reliable(cell) ? shade(cell[valueKey]) : "transparent" }}
                title={cell ? format(cell) + (reliable(cell) ? "" : ` (under ${minSample} innings -- not shaded)`) : "no innings"}>
                {cell ? (
                  <>
                    <strong>{formatValue(cell[valueKey], valueKey)}</strong>
                    <span>{cell[subKey]} inns</span>
                  </>
                ) : (
                  <span className="muted">—</span>
                )}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

// One row per metric, one bar per player (0-100 percentile).
export function PercentileBars({ metrics, players, values, labels }) {
  return (
    <div className="pct-bars">
      {players.length > 1 && <Legend items={players.map((p, i) => ({ label: p, color: SERIES[i] }))} />}
      {metrics.map((m) => (
        <div className="pct-row" key={m}>
          <div className="pct-label">{labels?.[m] || humanize(m)}</div>
          <div className="pct-tracks">
            {players.map((p, i) => {
              const v = values[`${m}|${p}`];
              const pct = v?.percentile;
              return (
                <div className="pct-track" key={p} title={`${p}: ${formatValue(v?.value, m)} (${pct == null ? "n/a" : Math.round(pct)}th percentile)`}>
                  <div className="pct-fill" style={{ width: `${pct ?? 0}%`, background: SERIES[i] }} />
                  <span className="pct-text">
                    {formatValue(v?.value, m)} · {pct == null ? "—" : `${Math.round(pct)}`}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ))}
      <p className="pct-scale" aria-hidden="true">
        <span>0</span>
        <span>50th percentile</span>
        <span>100</span>
      </p>
    </div>
  );
}
