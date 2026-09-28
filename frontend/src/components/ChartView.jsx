import {
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { humanize } from "./kit/theme.js";

const GRID_COLOR = "rgba(241, 232, 214, 0.1)";
const TEXT_COLOR = "#a79c87";
const BRASS = "#d1a954";

const TICK = { fill: TEXT_COLOR, fontFamily: "IBM Plex Mono", fontSize: 12 };
const TOOLTIP_STYLE = {
  background: "#16281d",
  border: "1px solid rgba(241,232,214,0.18)",
  fontFamily: "IBM Plex Mono",
  fontSize: 12,
};

// Normalizes both shapes the backend can send: {x, series:[{name, values}]}
// and the older single-series {x, y}.
function seriesOf(chartData) {
  if (Array.isArray(chartData.series) && chartData.series.length) return chartData.series;
  if (Array.isArray(chartData.y)) return [{ name: chartData.y_label || "value", values: chartData.y }];
  return [];
}

function SingleChart({ type, labels, series }) {
  const data = labels.map((label, i) => ({ name: String(label), value: series.values[i] }));
  const manyLabels = labels.length > 12;
  const xAxis = (
    <XAxis
      dataKey="name"
      stroke={TEXT_COLOR}
      tick={TICK}
      tickLine={false}
      interval={manyLabels ? "preserveStartEnd" : 0}
      angle={manyLabels ? -35 : 0}
      textAnchor={manyLabels ? "end" : "middle"}
      height={manyLabels ? 56 : 30}
    />
  );
  const yAxis = <YAxis stroke={TEXT_COLOR} tick={TICK} tickLine={false} axisLine={false} width={48} />;
  const tooltip = (
    <Tooltip
      contentStyle={TOOLTIP_STYLE}
      labelStyle={{ color: "#f1e8d6" }}
      formatter={(v) => [v, humanize(series.name)]}
      cursor={type === "line" ? { stroke: "rgba(241,232,214,0.25)" } : { fill: "rgba(241,232,214,0.05)" }}
    />
  );

  return (
    <ResponsiveContainer width="100%" height={240}>
      {type === "line" ? (
        <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 4 }}>
          <CartesianGrid stroke={GRID_COLOR} vertical={false} />
          {xAxis}
          {yAxis}
          {tooltip}
          <Line
            type="linear"
            dataKey="value"
            stroke={BRASS}
            strokeWidth={2}
            dot={{ r: 4, fill: BRASS, strokeWidth: 0 }}
            activeDot={{ r: 5 }}
            connectNulls
          />
        </LineChart>
      ) : (
        <BarChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 4 }}>
          <CartesianGrid stroke={GRID_COLOR} vertical={false} />
          {xAxis}
          {yAxis}
          {tooltip}
          <Bar dataKey="value" fill={BRASS} radius={[4, 4, 0, 0]} maxBarSize={48} />
        </BarChart>
      )}
    </ResponsiveContainer>
  );
}

export default function ChartView({ chartData }) {
  if (!chartData || !Array.isArray(chartData.x)) return null;
  const series = seriesOf(chartData);
  if (!series.length) return null;
  const type = chartData.type === "line" ? "line" : "bar";

  // Metrics on different scales (runs vs strike rate) never share an axis:
  // each series gets its own small chart, titled by the metric.
  return (
    <figure className="chart-view">
      {chartData.title && <figcaption className="chart-title">{chartData.title}</figcaption>}
      {series.map((s) => (
        <div className="chart-panel" key={s.name}>
          {(series.length > 1 || !chartData.title) && <p className="chart-series-name">{humanize(s.name)}</p>}
          <SingleChart type={type} labels={chartData.x} series={s} />
        </div>
      ))}
    </figure>
  );
}
