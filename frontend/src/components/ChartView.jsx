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

const GRID_COLOR = "rgba(241, 232, 214, 0.1)";
const TEXT_COLOR = "#a79c87";
const BRASS = "#d1a954";

export default function ChartView({ chartData }) {
  if (!chartData || !Array.isArray(chartData.x) || !Array.isArray(chartData.y)) {
    return null;
  }

  const data = chartData.x.map((label, i) => ({
    name: String(label),
    value: chartData.y[i],
  }));

  const isLine = chartData.type === "line";

  return (
    <div className="chart-view">
      <ResponsiveContainer width="100%" height={260}>
        {isLine ? (
          <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
            <CartesianGrid stroke={GRID_COLOR} vertical={false} />
            <XAxis
              dataKey="name"
              stroke={TEXT_COLOR}
              tick={{ fill: TEXT_COLOR, fontFamily: "IBM Plex Mono", fontSize: 12 }}
              tickLine={false}
            />
            <YAxis
              stroke={TEXT_COLOR}
              tick={{ fill: TEXT_COLOR, fontFamily: "IBM Plex Mono", fontSize: 12 }}
              tickLine={false}
              axisLine={false}
              label={
                chartData.y_label
                  ? { value: chartData.y_label, angle: -90, position: "insideLeft", fill: TEXT_COLOR }
                  : undefined
              }
            />
            <Tooltip
              contentStyle={{
                background: "#16281d",
                border: "1px solid rgba(241,232,214,0.18)",
                fontFamily: "IBM Plex Mono",
                fontSize: 12,
              }}
              labelStyle={{ color: "#f1e8d6" }}
            />
            <Line type="monotone" dataKey="value" stroke={BRASS} strokeWidth={2} dot={{ r: 3, fill: BRASS }} />
          </LineChart>
        ) : (
          <BarChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
            <CartesianGrid stroke={GRID_COLOR} vertical={false} />
            <XAxis
              dataKey="name"
              stroke={TEXT_COLOR}
              tick={{ fill: TEXT_COLOR, fontFamily: "IBM Plex Mono", fontSize: 12 }}
              tickLine={false}
            />
            <YAxis
              stroke={TEXT_COLOR}
              tick={{ fill: TEXT_COLOR, fontFamily: "IBM Plex Mono", fontSize: 12 }}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              contentStyle={{
                background: "#16281d",
                border: "1px solid rgba(241,232,214,0.18)",
                fontFamily: "IBM Plex Mono",
                fontSize: 12,
              }}
              labelStyle={{ color: "#f1e8d6" }}
              cursor={{ fill: "rgba(241,232,214,0.05)" }}
            />
            <Bar dataKey="value" fill={BRASS} radius={[3, 3, 0, 0]} />
          </BarChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
