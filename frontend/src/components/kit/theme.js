// Chart colours for the pitch-green dark surface (#16281d). The categorical
// order was validated with the dataviz palette checker (lightness band,
// chroma, colour-vision separation, contrast): brass, blue, orange, violet
// pass as adjacent series (lines, bars, up to 4). Scatter plots use neutral
// points with only the first two as accents -- that pair also passes
// all-pairs separation. Assign in this fixed order; never cycle.
export const SERIES = ["#b08a3a", "#3987e5", "#d95926", "#9085e9"];
export const NEUTRAL_POINT = "rgba(241, 232, 214, 0.28)";
export const GRID = "rgba(241, 232, 214, 0.1)";
export const AXIS_TEXT = "#a79c87";
export const INK = "#f1e8d6";
export const TICK = { fill: AXIS_TEXT, fontFamily: "IBM Plex Mono", fontSize: 11.5 };
export const TOOLTIP = {
  contentStyle: {
    background: "#1c3024",
    border: "1px solid rgba(241,232,214,0.18)",
    borderRadius: 6,
    fontFamily: "IBM Plex Mono",
    fontSize: 12,
    color: INK,
  },
  labelStyle: { color: INK, marginBottom: 4 },
  itemStyle: { color: INK },
};

const SPECIAL = { dot_pct: "Dot %", boundary_pct: "Boundary %", win_pct: "Win %", strike_rate: "Strike rate",
  true_sr: "True SR", first5_sr: "First-5 SR", innings_no: "Innings", no_result: "No result" };

export function humanize(key) {
  if (!key) return "";
  if (SPECIAL[key]) return SPECIAL[key];
  const s = String(key).replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// Rates always show two decimals (JSON turns 28.0 into 28); counts get separators.
const RATE = /(average|strike_rate|economy|_pct|per_|_sr|factor|true_|expected|percentile|similarity|cv)/;

export function formatValue(value, column = "") {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    if (RATE.test(column) || !Number.isInteger(value)) return value.toFixed(2);
    return value.toLocaleString("en-US");
  }
  return String(value);
}
