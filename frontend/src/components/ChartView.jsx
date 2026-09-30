import { BarChartKit, LineChartKit } from "./kit/Charts.jsx";
import CsvButton from "./kit/CsvButton.jsx";
import { humanize } from "./kit/theme.js";

// Normalizes both shapes the backend can send: {x, series:[{name, values}]}
// and the older single-series {x, y}.
function seriesOf(chartData) {
  if (Array.isArray(chartData.series) && chartData.series.length) return chartData.series;
  if (Array.isArray(chartData.y)) return [{ name: chartData.y_label || "value", values: chartData.y }];
  return [];
}

function SingleChart({ type, labels, series }) {
  const data = labels.map((label, i) => ({ name: String(label), value: series.values[i] }));
  const kit = [{ key: "value", label: humanize(series.name) }];
  return type === "line"
    ? <LineChartKit data={data} x="name" series={kit} height={240} csv={false} />
    : <BarChartKit data={data} x="name" series={kit} height={240} csv={false} />;
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
      <figcaption className="chart-title">
        <span>{chartData.title}</span>
        <CsvButton name={chartData.title || "doosra-chart"} data={() => ({
          columns: [humanize(chartData.x_label || "label"), ...series.map((s) => humanize(s.name))],
          rows: chartData.x.map((x, i) => [x, ...series.map((s) => s.values[i])]),
        })} />
      </figcaption>
      {series.map((s) => (
        <div className="chart-panel" key={s.name}>
          {(series.length > 1 || !chartData.title) && <p className="chart-series-name">{humanize(s.name)}</p>}
          <SingleChart type={type} labels={chartData.x} series={s} />
        </div>
      ))}
    </figure>
  );
}
