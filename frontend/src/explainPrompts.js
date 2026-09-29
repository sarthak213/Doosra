// The questions the Explain buttons ask. The numbers behind them are added by
// the server (board digests) or attached as the excerpt being asked about.
export const EXPLAIN_BOARD =
  "Explain this view: what stands out across these charts, what is skill and what may be luck or a small sample, and what I should look at next.";
export const EXPLAIN_CARD =
  "Explain this chart: what stands out, what is skill versus luck or a small sample, and what I should look at next.";
export const explainItem = (kind, title) =>
  `Explain the ${kind}${title ? ` "${title}"` : ""} above: what stands out, and what is skill versus luck or a small sample?`;
