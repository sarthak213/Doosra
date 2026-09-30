// Doosra win probability in JavaScript: the same models and features as predict.py (and the Doosra app),
// so the demo runs entirely in the browser. Works in Node too (module.exports), where a test compares it
// with the Python predictor.
(function (root) {
  const FEATURES = ["innings", "balls_left", "wickets_in_hand", "score", "target", "runs_needed", "required_rate",
    "venue_par", "female", "overs", "elo_diff"];

  function leaf(node, x) {
    while (node.leaf_value === undefined) {
      let v = x[node.split_feature];
      if (Number.isNaN(v) && node.missing_type === "None") v = 0; // LightGBM treats NaN as 0 here
      const left = Number.isNaN(v) ? node.default_left !== false : v <= node.threshold;
      node = left ? node.left_child : node.right_child;
    }
    return node.leaf_value;
  }

  class WinProbability {
    constructor(spec) {
      this.spec = spec;
      this.innings = {};
      for (const [k, v] of Object.entries(spec.innings)) {
        this.innings[k] = { names: v.features, trees: v.trees.tree_info.map((t) => t.tree_structure), linear: v.linear };
      }
    }

    // row: an array in FEATURES order (NaN where a feature doesn't apply)
    predictRow(row) {
      const m = this.innings[String(row[0])];
      const xs = m.names.map((n) => row[FEATURES.indexOf(n)]);
      let trees = 0;
      for (const t of m.trees) trees += leaf(t, xs);
      const { mean, scale, coef, intercept } = m.linear;
      let lin = intercept;
      xs.forEach((v, i) => { lin += coef[i] * ((Number.isNaN(v) ? mean[i] : v) - mean[i]) / scale[i]; });
      const p = 1 / (1 + Math.exp(-(trees + lin) / 2));
      return Math.min(0.999, Math.max(0.001, p));
    }

    // wickets = wickets fallen; venuePar = the ground's typical first-innings total (null if unknown);
    // eloDiff = batting side's Elo minus the bowling side's (0 = evenly matched)
    predict({ innings, score, wickets, ballsLeft, target = null, venuePar = null, female = false, eloDiff = 0, overs = null }) {
      overs = overs || (this.spec.group === "ODI" ? 50 : 20);
      const chase = innings === 2 && target !== null;
      const need = chase ? target - score : NaN;
      const req = chase ? (need * 6) / Math.max(ballsLeft, 1) : NaN;
      return this.predictRow([innings, ballsLeft, 10 - wickets, score, chase ? target : NaN, need, req,
        venuePar === null ? NaN : venuePar, female ? 1 : 0, overs, eloDiff]);
    }
  }

  const api = { WinProbability, FEATURES };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.DoosraWinProb = api;
})(typeof window !== "undefined" ? window : globalThis);
