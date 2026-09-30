import {
  Bar,
  BarChart,
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
import CsvButton from "./CsvButton.jsx";
import { AXIS_TEXT, GRID, INK, NEUTRAL_POINT, SERIES, TICK, TOOLTIP, formatValue, humanize } from "./theme.js";

// The plotting area. It fills whatever room its panel has (so a chart beside a taller neighbour
// isn't left floating in empty space) and never goes below `height`. The chart is drawn in an
// absolutely positioned layer, so it takes its size from the panel and never props the panel open.
function ChartArea({ height, children }) {
  return (
    <div className="chart-area" style={{ minHeight: height }}>
      <div className="chart-area-inner">
        <ResponsiveContainer width="100%" height="100%">{children}</ResponsiveContainer>
      </div>
    </div>
  );
}

// The row above a chart: its legend (if any) and a CSV button for the data it plots. `csv` is
// {name, data: () => ({columns, rows})}; false hides the button (the caller exports the data another way).
function ChartTop({ legend, csv }) {
  if (!legend && !csv) return null;
  return (
    <div className="chart-top">
      {legend || <span />}
      {csv && <CsvButton name={csv.name} data={csv.data} className="ghost-btn chart-csv" />}
    </div>
  );
}

function seriesCsv(name, data, x, series, xLabel) {
  return {
    name: name || series.map((s) => s.label).join(", ") || "doosra-chart",
    data: () => ({ columns: [xLabel || humanize(x), ...series.map((s) => s.label || humanize(s.key))],
                   rows: data.map((d) => [d[x], ...series.map((s) => d[s.key])]) }),
  };
}

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
export function LineChartKit({ data, x, series, references = [], height = 280, xLabel, yLabel, yDomain, csvName, csv = true }) {
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
      <ChartTop legend={series.length > 1 && <Legend items={series.map((s, i) => ({ label: s.label, color: s.color || SERIES[i] }))} />}
        csv={csv && seriesCsv(csvName, data, x, series, xLabel)} />
      <ChartArea height={height}>
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
      </ChartArea>
    </div>
  );
}

// Categories on one axis, one or more series as grouped bars (comparisons:
// players, teams, seasons). Bars are thin with rounded data-ends, a surface-
// coloured 2px stroke keeps neighbours apart, and long category lists turn
// horizontal so labels stay readable. One value axis only.
export function BarChartKit({ data, x, series, height = 280, xLabel, yLabel, horizontal, csvName, csv = true }) {
  if (!data?.length) return null;
  const sideways = horizontal ?? data.length > 8;
  const h = sideways ? Math.max(height, data.length * (series.length * 14 + 12) + 48) : height;
  const legend = series.length > 1;
  return (
    <div className="chart-block">
      <ChartTop legend={legend && <Legend items={series.map((s, i) => ({ label: s.label, color: s.color || SERIES[i] }))} />}
        csv={csv && seriesCsv(csvName, data, x, series, xLabel)} />
      <ChartArea height={h}>
        <BarChart data={data} layout={sideways ? "vertical" : "horizontal"} barCategoryGap="22%" barGap={2}
          margin={{ top: 10, right: 24, left: 4, bottom: xLabel ? 22 : 4 }}>
          <CartesianGrid stroke={GRID} horizontal={!sideways} vertical={sideways} />
          {sideways ? (
            <>
              <XAxis type="number" stroke={AXIS_TEXT} tick={TICK} tickLine={false} axisLine={false}
                label={yLabel ? { value: yLabel, position: "insideBottom", offset: -2, fill: AXIS_TEXT, fontSize: 11 } : undefined} />
              <YAxis type="category" dataKey={x} stroke={AXIS_TEXT} tick={TICK} tickLine={false} width={120} interval={0} />
            </>
          ) : (
            <>
              <XAxis dataKey={x} stroke={AXIS_TEXT} tick={TICK} tickLine={false} interval={0} minTickGap={8}
                label={xLabel ? { value: xLabel, position: "insideBottom", offset: -10, fill: AXIS_TEXT, fontSize: 11 } : undefined} />
              <YAxis stroke={AXIS_TEXT} tick={TICK} tickLine={false} axisLine={false} width={52}
                label={yLabel ? { value: yLabel, angle: -90, position: "insideLeft", fill: AXIS_TEXT, fontSize: 11 } : undefined} />
            </>
          )}
          <Tooltip {...TOOLTIP} cursor={{ fill: "rgba(241,232,214,0.06)" }}
            formatter={(v, name) => [formatValue(v, name), series.find((s) => s.key === name)?.label || humanize(name)]} />
          {series.map((s, i) => (
            <Bar key={s.key} dataKey={s.key} name={s.key} fill={s.color || SERIES[i]} stroke="#16281d" strokeWidth={2}
              radius={sideways ? [0, 4, 4, 0] : [4, 4, 0, 0]} maxBarSize={28} isAnimationActive={false} />
          ))}
        </BarChart>
      </ChartArea>
    </div>
  );
}

// Every qualified player as a point; medians split the plane into quadrants.
// Points are neutral; watchlisted players are brass, the selected one blue.
export function ScatterMatrix({ points, xKey, yKey, xLabel, yLabel, medians, highlighted = [], selected, onSelect,
  standouts = [], xBetterHigh = true, yBetterHigh = true, height = 460, medianLabel = "median",
  pointLabel = "Qualified players", csvName }) {
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
      <ChartTop legend={<Legend items={[{ label: pointLabel, color: NEUTRAL_POINT }, ...(hl.length ? [{ label: "Watchlist", color: SERIES[0] }] : []),
        ...(sel.length ? [{ label: selected, color: SERIES[1] }] : [])]} />}
        csv={{ name: csvName || `${yLabel} vs ${xLabel}`,
               data: () => ({ columns: ["Player", "Team", xLabel, yLabel, "Balls"],
                              rows: points.map((p) => [p.player, p.team, p[xKey], p[yKey], p.balls]) }) }} />
      <ChartArea height={height}>
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
      </ChartArea>
    </div>
  );
}

// Grid of cells shaded by one metric (single hue, brass): rows x columns.
// Cells with fewer than `minSample` (by subKey) aren't shaded -- a one-innings
// 75 shouldn't glow brighter than a 300-innings 52.
export function Heatmap({ rows, cols, cells, valueKey, labelKey, subKey, format, minSample = 5, csvName }) {
  const reliable = (c) => c && typeof c[valueKey] === "number" && (c[subKey] ?? 0) >= minSample;
  const vals = Object.values(cells).filter(reliable).map((c) => c[valueKey]);
  const max = Math.max(...vals, 1);
  const min = Math.min(...vals, 0);
  const shade = (v) => (typeof v !== "number" ? "transparent" : `rgba(176, 138, 58, ${0.12 + 0.78 * ((v - min) / (max - min || 1))})`);
  const csv = {
    name: csvName || `${humanize(valueKey)} by ${labelKey}`,
    data: () => ({ columns: [labelKey, ...cols],
                   rows: rows.map((r) => [r, ...cols.map((c) => cells[`${r}|${c}`]?.[valueKey] ?? "")]) }),
  };
  return (
    <>
    <ChartTop csv={csv} />
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
    </>
  );
}

// One row per metric, one bar per player (0-100 percentile).
export function PercentileBars({ metrics, players, values, labels, csvName }) {
  return (
    <div className="pct-bars">
      <ChartTop legend={players.length > 1 && <Legend items={players.map((p, i) => ({ label: p, color: SERIES[i] }))} />}
        csv={{ name: csvName || `Percentiles - ${players.join(", ")}`,
               data: () => ({ columns: ["Metric", "Player", "Value", "Percentile"],
                              rows: metrics.flatMap((m) => players.map((p) => {
                                const v = values[`${m}|${p}`];
                                return [labels?.[m] || humanize(m), p, v?.value ?? "", v?.percentile == null ? "" : Math.round(v.percentile)];
                              })) }) }} />
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

// Match Replay's win-probability worm: team1's chance of winning after every ball, both innings on one
// over axis. Wickets are dots on the line; key moments are numbered markers that match the list beside
// the chart. One series, one y scale (0-100%), 50% dashed; hover shows the ball and both sides' chances.
export function WinProbChart({ balls, overs, team1, team2, moments = [], height = 340, activeMoment, onMoment, csvName }) {
  if (!balls?.length) return null;
  const momentAt = new Map(moments.map((m, i) => [`${m.innings}-${m.seq}`, i + 1]));
  const data = balls.map((b) => ({
    ...b,
    x: (b.innings - 1) * overs + b.legal / 6,
    pct: Math.round(b.wp * 1000) / 10,
    moment: momentAt.get(`${b.innings}-${b.seq}`),
  }));
  const ticks = [];
  const step = overs >= 40 ? 10 : 5;
  for (let t = 0; t <= 2 * overs; t += step) ticks.push(t);
  const tickLabel = (v) => (v === overs ? "" : String(v > overs ? v - overs : v));
  const Dot = ({ cx, cy, payload }) => {
    if (cx == null || cy == null) return null;
    if (payload.moment) {
      const on = activeMoment === payload.moment;
      return (
        <g key={`m-${payload.innings}-${payload.seq}`} style={{ cursor: onMoment ? "pointer" : undefined }}
          onClick={() => onMoment?.(payload.moment)}>
          <circle cx={cx} cy={cy} r={on ? 11 : 9} fill={on ? SERIES[1] : "#1c3024"} stroke={SERIES[1]} strokeWidth={2} />
          <text x={cx} y={cy} dy={4} textAnchor="middle" fill={on ? "#0f1e16" : INK} fontSize={10.5}
            fontFamily="IBM Plex Mono" fontWeight={600}>{payload.moment}</text>
        </g>
      );
    }
    if (payload.wicket) {
      return <circle key={`w-${payload.innings}-${payload.seq}`} cx={cx} cy={cy} r={4} fill={INK} stroke="#16281d" strokeWidth={2} />;
    }
    return null;
  };
  const Tip = ({ active, payload }) => {
    if (!active || !payload?.length) return null;
    const b = payload[0].payload;
    return (
      <div style={TOOLTIP.contentStyle}>
        <div style={{ marginBottom: 4 }}>{b.batting_team} {b.score}/{b.wickets}</div>
        <div style={{ color: AXIS_TEXT, marginBottom: 6 }}>{b.text}</div>
        <div>{team1} {b.pct.toFixed(0)}% · {team2} {(100 - b.pct).toFixed(0)}%</div>
      </div>
    );
  };
  return (
    <div className="chart-block">
      <ChartTop legend={
        <ul className="chart-legend">
          <li><span className="legend-swatch" style={{ background: SERIES[0] }} aria-hidden="true" />{team1}'s chance of winning</li>
          <li><span className="legend-dot" aria-hidden="true" />wicket</li>
          {moments.length > 0 && <li><span className="legend-moment" aria-hidden="true">1</span>key moment</li>}
        </ul>}
        csv={{ name: csvName || `Win probability - ${team1} v ${team2}`,
               data: () => ({ columns: ["Innings", "Over", "Batting team", "Score", "Wickets", `${team1} win %`, `${team2} win %`, "Wicket", "Ball"],
                              rows: data.map((b) => [b.innings, `${Math.floor(b.legal / 6)}.${b.legal % 6}`, b.batting_team, b.score,
                                b.wickets, b.pct, Math.round((100 - b.pct) * 10) / 10, b.wicket ? "yes" : "", b.text]) }) }} />
      <ChartArea height={height}>
        <LineChart data={data} margin={{ top: 16, right: 24, left: 4, bottom: 14 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="x" type="number" domain={[0, 2 * overs]} ticks={ticks} tickFormatter={tickLabel} stroke={AXIS_TEXT}
            tick={TICK} tickLine={false}
            label={{ value: "Overs (first innings, then the chase)", position: "insideBottom", offset: -8, fill: AXIS_TEXT, fontSize: 11 }} />
          <YAxis domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} tickFormatter={(v) => `${v}%`} stroke={AXIS_TEXT} tick={TICK}
            tickLine={false} axisLine={false} width={48} />
          <ReferenceLine y={50} stroke="rgba(241,232,214,0.35)" strokeDasharray="4 4" />
          <ReferenceLine x={overs} stroke="rgba(241,232,214,0.35)"
            label={{ value: "innings break", position: "insideTopLeft", fill: AXIS_TEXT, fontSize: 11 }} />
          <ReferenceLine y={96} stroke="none" label={{ value: `▲ ${team1} ahead`, position: "insideLeft", fill: AXIS_TEXT, fontSize: 11 }} />
          <ReferenceLine y={4} stroke="none" label={{ value: `▼ ${team2} ahead`, position: "insideLeft", fill: AXIS_TEXT, fontSize: 11 }} />
          <Tooltip content={<Tip />} cursor={{ stroke: "rgba(241,232,214,0.25)" }} />
          <Line type="linear" dataKey="pct" stroke={SERIES[0]} strokeWidth={2} dot={<Dot />} activeDot={{ r: 5 }}
            isAnimationActive={false} />
        </LineChart>
      </ChartArea>
    </div>
  );
}
