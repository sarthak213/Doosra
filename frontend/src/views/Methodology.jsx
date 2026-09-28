import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { apiGet } from "../api.js";
import { useCopilotContext } from "../copilot/CopilotProvider.jsx";
import { LineChartKit, ScatterMatrix } from "../components/kit/Charts.jsx";
import DataTable from "../components/kit/DataTable.jsx";
import { FilterBar, RoleToggle } from "../components/kit/Inputs.jsx";
import Panel, { ErrorNote, Loading, summarize } from "../components/kit/Panel.jsx";
import { SERIES } from "../components/kit/theme.js";
import { useFetch } from "../hooks/useFetch.js";
import { stateUrl, useViewState } from "../hooks/useViewState.js";

// The FIBS methodology page: a long-form article whose numbers,
// charts and findings are all read live from the study tables that every
// data build recomputes (backend/analytics/fibs.py).

const DEFAULTS = { format: "T20", gender: "male", role: "bowling", metric: "dot", unlucky: false, luckBy: null,
  filters: { competition: "IPL", season: "latest" } };
const FORMATS = [["T20", "T20"], ["ODI", "One-day"], ["Test", "Multi-day"]];
// Outcomes the bowler (or batter) controls vs those shared with fielders and chance.
const SHARED = new Set(["ct_field", "run_out", "inplay_runs"]);
const MINOR = new Set(["hit_wicket", "ct_bowler"]);
// Which direction is good, per role, for the scatter's axis hints.
const LOWER_BETTER = {
  bowling: new Set(["runs", "fib_runs", "boundary", "four", "six", "wide", "noball", "inplay", "inplay_runs"]),
  batting: new Set(["outs", "fib_outs", "dot", "bowled", "lbw", "ct_keeper", "ct_field", "ct_bowler", "stumped", "run_out"]),
};
const CURVE_METRICS = { bowling: ["dot", "runs", "boundary", "wickets"], batting: ["dot", "runs", "boundary", "outs"] };
const SHORT = { dot: "Dots", boundary: "Boundaries", wickets: "Wickets", outs: "Dismissals" };

const records = (t) => (t?.rows || []).map((r) => Object.fromEntries(t.columns.map((c, i) => [c, r[i]])));
const fmt = (v, d = 0) => (v == null ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d }));
const pct = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);

function Segmented({ value, options, onChange, label }) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label}>
      {options.map(([v, l]) => (
        <button type="button" key={v} role="radio" aria-checked={value === v} className={value === v ? "on" : ""}
          onClick={() => onChange(v)}>{l}</button>
      ))}
    </div>
  );
}

// Reliability of each outcome over a typical season, as horizontal bars
// (single hue: this is magnitude). Outcomes with no measurable skill get a
// note instead of a bar.
function ReliabilityBars({ rows, unit }) {
  const items = rows.filter((r) => !MINOR.has(r.metric)).sort((a, b) => (a.k_balls ?? 1e12) - (b.k_balls ?? 1e12));
  return (
    <div className="rel-bars">
      {items.map((r) => (
        <div className={`rel-row${SHARED.has(r.metric) ? " shared" : ""}`} key={r.metric}
          title={`${r.label}: K = ${fmt(r.k_balls)} ${unit}; split-half r ${fmt(r.split_half_r, 2)}; year-to-year r ${fmt(r.yoy_r, 2)}`}>
          <div className="rel-label">{r.label}{SHARED.has(r.metric) && <span className="rel-tag">fielding / luck</span>}</div>
          <div className="rel-track">
            {r.reliability_typical != null
              ? <div className="rel-fill" style={{ width: `${r.reliability_typical * 100}%` }} />
              : null}
            <span className="rel-text">
              {r.k_balls != null ? `${pct(r.reliability_typical)} signal · K ${fmt(r.k_balls)}` : "no measurable skill"}
            </span>
          </div>
        </div>
      ))}
      <p className="pct-scale" aria-hidden="true"><span>0%</span><span>share of a typical season that is skill</span><span>100%</span></p>
    </div>
  );
}

// Error bars for next-season prediction: how much each predictor cuts the
// error of just guessing the league average.
function PredictionBars({ rows }) {
  const base = rows.find((r) => r.predictor === "league average");
  const items = rows.filter((r) => r !== base).map((r) => ({ ...r, gain: (r.gain_pct ?? 0) / 100 }))
    .sort((a, b) => b.gain - a.gain);
  const max = Math.max(...items.map((r) => r.gain ?? 0), 0.01);
  return (
    <div className="rel-bars">
      {items.map((r, i) => (
        <div className="rel-row" key={r.predictor} title={`error ${fmt(r.rmse, 2)} ${r.unit}; r = ${fmt(r.r, 2)}`}>
          <div className="rel-label">{r.predictor}{i === 0 && <span className="rel-tag best">best</span>}</div>
          <div className="rel-track">
            <div className="rel-fill" style={{ width: `${Math.max(0, (r.gain ?? 0) / max) * 100}%` }} />
            <span className="rel-text">{fmt(r.gain * 100, 2)}% less error than the league average</span>
          </div>
        </div>
      ))}
      {base && <p className="table-notes-inline">Baseline (league average): error {fmt(base.rmse, 2)} {base.unit}. Out-of-sample, two folds by year; {fmt(base.n_pairs)} season pairs.</p>}
    </div>
  );
}

export default function Methodology() {
  const navigate = useNavigate();
  const [st, set] = useViewState(DEFAULTS);
  const role = st.role;
  const unit = role === "bowling" ? "deliveries" : "balls faced";

  const report = useFetch((s) => apiGet("/api/fibs/report", { format: st.format, gender: st.gender, role }, s),
    `${st.format}|${st.gender}|${role}`);
  const pairs = useFetch((s) => apiGet("/api/fibs/pairs", { metric: st.metric, format: st.format, gender: st.gender, role }, s),
    `${st.metric}|${st.format}|${st.gender}|${role}`);
  const luck = useFetch((s) => apiGet("/api/fibs/luck", { role, unlucky: st.unlucky, by: st.luckBy, ...st.filters }, s),
    `${role}|${st.unlucky}|${st.luckBy}|${JSON.stringify(st.filters)}`);

  const rows = useMemo(() => records(report.data), [report.data]);
  const by = useMemo(() => Object.fromEntries(rows.map((r) => [r.metric, r])), [rows]);
  const outcomes = rows.filter((r) => !["runs", "fib_runs", "wickets", "fib_wickets", "outs", "fib_outs"].includes(r.metric));
  const pred = useMemo(() => records(report.data?.prediction), [report.data]);
  const headline = by[role === "bowling" ? "wickets" : "outs"];
  const runs = by.runs;
  const dot = by.dot;

  const curve = useMemo(() => {
    const ks = CURVE_METRICS[role].map((m) => by[m]).filter((r) => r?.k_balls);
    if (!ks.length) return { data: [], series: [] };
    const top = st.format === "T20" ? 3000 : st.format === "ODI" ? 4000 : 8000;
    const step = top / 60;
    const data = Array.from({ length: 61 }, (_, i) => {
      const n = Math.round(i * step);
      return Object.fromEntries([["balls", n], ...ks.map((r) => [r.metric, n === 0 ? 0 : (100 * n) / (n + r.k_balls)])]);
    });
    const short = (r) => SHORT[r.metric] || (role === "bowling" ? "Economy" : "Scoring rate");
    return { data, series: ks.map((r, i) => ({ key: r.metric, label: short(r), color: SERIES[i] })) };
  }, [by, role, st.format]);

  const points = useMemo(() => records(pairs.data).map((r) => ({ ...r, team: `${r.yr} → ${r.yr + 1}` })), [pairs.data]);
  const pairMetric = by[st.metric];

  useCopilotContext({
    view: "methodology: FIBS", settings: st,
    visible: { findings: report.data?.findings, stability: rows.slice(0, 24), prediction: pred, luck: summarize(luck.data, 10) },
  });

  const luckOptions = role === "bowling" ? [["wicket_luck", "Wicket luck"], ["runs_luck", "Runs luck"]]
    : [["dismissal_luck", "Dismissal luck"], ["runs_luck", "Runs luck"]];

  return (
    <div className="view method">
      <header className="method-head">
        <p className="method-kicker">Methodology</p>
        <h1>FIBS</h1>
        <p className="method-expansion">Fielding-Independent Bowling Statistics</p>
        <p className="method-dek">
          Which parts of a cricketer's record are repeatable skill, and which are the fielders, the gaps and chance?
          Measured live on every match in the database, and recomputed with every data release.
        </p>
      </header>

      <div className="controls-row">
        <Segmented label="Format" value={st.format} options={FORMATS} onChange={(v) => set({ format: v })} />
        <Segmented label="Gender" value={st.gender} options={[["male", "Men"], ["female", "Women"]]} onChange={(v) => set({ gender: v })} />
        <RoleToggle value={role} onChange={(r) => set({ role: r, metric: "dot", luckBy: null })} />
      </div>

      {report.loading && <Loading label="Reading the study…" />}
      <ErrorNote error={report.error} />

      {report.data && (
        <Panel title="The short answer" className="method-summary"
          explain={{ data: { findings: report.data.findings }, question: "Explain these FIBS findings in plain English for a cricket fan." }}>
          <ul className="findings">
            {report.data.findings.map((f) => <li key={f}>{f}</li>)}
          </ul>
        </Panel>
      )}

      <article className="method-body">
        <section>
          <h2>1. From DIPS to FIBS</h2>
          <p>
            In 2001 the baseball analyst Voros McCracken noticed something odd: how often a pitcher's balls in play
            turned into hits barely carried over from one season to the next, while strikeouts, walks and home runs
            did. Pitchers, it seemed, had little control over what happened once the ball was hit; that part of their
            record was largely fielding and luck. His <em>Defense-Independent Pitching Statistics</em> (DIPS) split
            the two, and changed how baseball judges pitchers.
          </p>
          <p>
            Cricket has never had a settled equivalent. When a bowler's figures look brilliant or dreadful, how much
            is the bowler, and how much is the catches that stuck, the edges that flew between slips and the singles
            that should have been dots?
          </p>
          <p>
            <strong>FIBS -- Fielding-Independent Bowling Statistics</strong> -- is Doosra's attempt at an analogous
            framework for cricket. It borrows DIPS's question and its tests, but not its answer: every outcome is
            measured on the data, for bowlers and (as the mirror image) for batters, and this page reports whatever
            the data says -- including where cricket turns out not to be baseball.
          </p>
        </section>

        <section>
          <h2>2. How a wicket happens</h2>
          <p>
            Every ball is broken into the things that can happen on it. Some are the {role === "bowling" ? "bowler's" : "batter's"} own
            doing; others involve a fielder or where the ball happens to land:
          </p>
          <div className="taxonomy">
            <div>
              <h3>{role === "bowling" ? "The bowler's own" : "The batter's own"}</h3>
              <ul>{outcomes.filter((r) => !SHARED.has(r.metric)).map((r) => (
                <li key={r.metric}>{r.label} <span className="muted">{fmt(r.league_rate, 2)} {r.unit}</span></li>
              ))}</ul>
            </div>
            <div>
              <h3>Shared with the fielders and chance</h3>
              <ul>{outcomes.filter((r) => SHARED.has(r.metric)).map((r) => (
                <li key={r.metric}>{r.label} <span className="muted">{fmt(r.league_rate, 2)} {r.unit}</span></li>
              ))}</ul>
            </div>
          </div>
          <p className="method-aside">
            Rates are for {st.gender === "male" ? "men's" : "women's"} {FORMATS.find((f) => f[0] === st.format)[1].toLowerCase()} cricket.
            "Runs per scoring shot in play" is cricket's version of baseball's batting average on balls in play:
            the runs from shots that beat the field without reaching the rope. "Caught by the keeper" needs to know
            who kept wicket, which the scorecards don't record: Doosra takes the XI member who made a stumping in the
            match, otherwise the one with the most career stumpings. Sides with no stumper on record have every catch
            counted as in the field.
          </p>
        </section>

        <section>
          <h2>3. Signal versus noise</h2>
          <p>
            Every rate is measured <em>above expected</em>: against what an average {role === "bowling" ? "bowler" : "batter"} would
            have produced in exactly the same situations (format, year, innings, over and wickets down), so a death
            bowler isn't punished for bowling at the death. Each player-season's matches are then split in two at
            random. Luck in one half has nothing to do with luck in the other, so how closely the halves agree across
            hundreds of players measures how much of the spread between players is real.
          </p>
          <p>
            That gives each outcome a single number, <strong>K</strong>: the number of {unit} at which a rate is half
            skill and half noise. Over <em>n</em> {unit}, the share that is skill is <code>n / (n + K)</code>.
            {dot?.k_balls && runs?.k_balls && headline?.k_balls && (
              <> Dot balls settle quickly (K ≈ {fmt(dot.k_balls)}); {runs.label.toLowerCase()} takes about {fmt(runs.k_balls)};
                {" "}{headline.label.toLowerCase()} need around {fmt(headline.k_balls)}. So over a typical season
                ({fmt(headline.typical_balls)} {unit}) only about {pct(headline.reliability_typical)} of the differences
                between players' {role === "bowling" ? "wicket" : "dismissal"} rates is skill; the rest is luck.</>
            )}
          </p>
        </section>
      </article>

      {rows.length > 0 && (
        <div className="grid-2">
          <Panel title="How much of a typical season is skill" subtitle={`By outcome, sorted by K (${unit}). Hover for correlations.`}
            explain={{ data: rows.map(({ metric, label, k_balls, reliability_typical, yoy_r }) => ({ metric, label, k_balls, reliability_typical, yoy_r })),
              question: "Which of these outcomes are mostly skill and which mostly noise? What does that mean for judging players?" }}>
            <ReliabilityBars rows={outcomes} unit={unit} />
          </Panel>
          <Panel title="Reliability as the sample grows" subtitle={`Share that is skill after n ${unit}; the dashed line is half.`}>
            {curve.data.length > 0
              ? <LineChartKit data={curve.data} x="balls" series={curve.series} xLabel={unit} yDomain={[0, 100]}
                  references={[{ y: 50, label: "half skill" }]} height={300} />
              : <p className="empty-note">Not enough data in this format to estimate.</p>}
          </Panel>
        </div>
      )}

      <article className="method-body">
        <section>
          <h2>4. Year to year</h2>
          <p>
            The simplest test of skill: does a player who is above average at something one season stay above average
            the next? Each point is a player's season against their following season, both above expected. A cloud
            along the diagonal is a skill; a round blob is noise.
            {pairMetric?.yoy_r != null && <> For <strong>{pairMetric.label.toLowerCase()}</strong> the correlation is
              {" "}<strong>r = {fmt(pairMetric.yoy_r, 2)}</strong> across {fmt(pairMetric.n_pairs)} season pairs.</>}
          </p>
        </section>
      </article>

      <Panel title="This season against next" subtitle={pairs.data?.notes?.[0]}
        actions={
          <select aria-label="Metric" value={st.metric} onChange={(e) => set({ metric: e.target.value })}>
            {rows.filter((r) => !MINOR.has(r.metric)).map((r) => <option key={r.metric} value={r.metric}>{r.label}</option>)}
          </select>
        }
        explain={{ data: { metric: pairMetric, sample: records(pairs.data).slice(0, 20) }, question: "What does this year-to-year scatter say about whether this is a skill?" }}>
        {pairs.loading ? <Loading /> : (
          <ScatterMatrix points={points} xKey="this_season" yKey="next_season" height={400}
            xLabel={`This season (${pairs.data?.unit || ""})`} yLabel="Next season" medians={{ x: 0, y: 0 }}
            medianLabel="average" pointLabel="Player-season pairs"
            xBetterHigh={!LOWER_BETTER[role].has(st.metric)} yBetterHigh={!LOWER_BETTER[role].has(st.metric)}
            onSelect={(p) => navigate(`/players/${encodeURIComponent(p)}`)} />
        )}
        <ErrorNote error={pairs.error} />
      </Panel>

      {role === "bowling" && pred.length > 0 && (
        <>
          <article className="method-body">
            <section>
              <h2>5. Does it predict?</h2>
              <p>
                The real test, borrowed from DIPS. If catches in the field and runs off shots in play were mostly luck, stripping
                them out (baseball-style, replacing them with what an average bowler got) should predict next season
                <em> better</em> than the raw figures. Four ways of predicting next season from this one are compared
                out of sample, as a cut in error against simply guessing the league average:
              </p>
              <ul>
                <li><strong>this season</strong> -- the raw figure;</li>
                <li><strong>regressed</strong> -- the raw figure shrunk toward average by its K;</li>
                <li><strong>DIPS-style</strong> -- baseball's approach: the fielding-affected outcomes replaced by the league average;</li>
                <li><strong>luck-adjusted</strong> -- only the fielding-affected parts shrunk by their own K.</li>
              </ul>
            </section>
          </article>
          <div className="grid-2">
            <Panel title="Next season's economy" explain={{ data: pred.filter((r) => r.target === "runs"), question: "Which predictor of next season's economy wins, and what does that say about FIBS?" }}>
              <PredictionBars rows={pred.filter((r) => r.target === "runs")} />
            </Panel>
            <Panel title="Next season's wicket rate" explain={{ data: pred.filter((r) => r.target === "wickets"), question: "Which predictor of next season's wicket rate wins, and why?" }}>
              <PredictionBars rows={pred.filter((r) => r.target === "wickets")} />
            </Panel>
          </div>
        </>
      )}

      <article className="method-body">
        <section>
          <h2>{role === "bowling" ? "6" : "5"}. What Doosra does with it</h2>
          <p>
            The study's verdict shapes the metrics. Catches in the field and runs off shots in play turn out to be
            partly the {role === "bowling" ? "bowler's" : "batter's"} own doing, so Doosra doesn't throw them away. It
            keeps each player's own rate for them, but shrinks it toward the situation average by the K measured
            above. A long career keeps almost all of its own rate; a short one is pulled most of the way to average.
            What that leaves over is <strong>luck</strong>.
          </p>
          <ul className="formulas">
            <li><code>regressed rate = (observed + K × expected rate) / (balls + K)</code></li>
            <li><code>{role === "bowling" ? "FIB wickets = wickets − catches in the field + balls × regressed outfield-catch rate"
              : "FIB dismissals = dismissals − caught in the field + balls × regressed caught-in-the-field rate"}</code></li>
            <li><code>{role === "bowling" ? "FIB runs = runs − runs off shots in play + shots × regressed runs per shot"
              : "FIB runs = runs − runs off shots in play + shots × regressed runs per shot"}</code></li>
            <li><code>{role === "bowling" ? "wicket luck = wickets − FIB wickets;  runs luck = FIB runs − runs conceded"
              : "dismissal luck = FIB dismissals − dismissals;  runs luck = runs − FIB runs"}</code></li>
          </ul>
          <p>
            These appear as <em>FIB economy / average / wickets</em>, <em>wicket luck</em>, <em>runs luck</em> and the
            <em> regressed</em> figures in the Player Hub, the Query Builder and the Matrix, and the copilot can use
            them. Positive luck means the record flatters the player.
          </p>
        </section>
      </article>

      <Panel title={st.unlucky ? "Unluckiest" : "Luckiest"} subtitle="Luck matters most over a season or a tournament; over a career it mostly evens out."
        actions={
          <>
            <Segmented label="Direction" value={st.unlucky ? "u" : "l"} options={[["l", "Luckiest"], ["u", "Unluckiest"]]}
              onChange={(v) => set({ unlucky: v === "u" })} />
            <select aria-label="Luck measure" value={st.luckBy || luckOptions[0][0]} onChange={(e) => set({ luckBy: e.target.value })}>
              {luckOptions.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </>
        }
        explain={{ data: summarize(luck.data), question: "Who has been lucky or unlucky here, and how much did it change their figures?" }}>
        <FilterBar filters={st.filters} onChange={(f) => set({ filters: f })} show={["competition", "season", "format", "gender", "team"]} />
        {luck.loading ? <Loading /> : (
          <DataTable table={luck.data} maxHeight={420}
            onRowClick={(row) => navigate(stateUrl(`/players/${encodeURIComponent(row[1])}`, { role, filters: st.filters }))} />
        )}
        <ErrorNote error={luck.error} />
      </Panel>

      <article className="method-body">
        <section>
          <h2>Assumptions and limits</h2>
          <ul>
            <li>"Expected" means an average player in the same ball state: format, year, gender, innings, over and
              wickets down. It does not adjust for the opposition, the pitch or the ground.</li>
            <li>There is no ball-tracking data, and dropped catches and misfields aren't recorded, so "luck" is
              everything the measured skill doesn't explain -- including skills nobody can see in a scorecard.</li>
            <li>The keeper is inferred (see above). Run-outs of the non-striker aren't in a batter's per-ball outcomes.</li>
            <li>A season must reach {role === "bowling" ? "120 / 300 / 600 deliveries" : "60 / 150 / 300 balls faced"} (T20 /
              one-day / multi-day) to count, and splitting a season in two assumes talent is steady within it.</li>
            <li>K comes from the players who qualify, and applies to the format and gender it was measured in. Where a
              women's sample is too small to estimate K, the metrics borrow the men's K for the same format.</li>
          </ul>
        </section>
        <section>
          <h2>Data</h2>
          <p>
            Ball-by-ball data from <a href="https://cricsheet.org" target="_blank" rel="noreferrer">Cricsheet</a>, under
            the <a href="https://opendatacommons.org/licenses/by/1-0/" target="_blank" rel="noreferrer">ODC-By 1.0</a> licence.
            The study reruns with every weekly data build, so the numbers on this page move as new matches arrive.
          </p>
        </section>
      </article>
    </div>
  );
}
