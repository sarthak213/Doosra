import ReactMarkdown from "react-markdown";
import ReasoningTrace from "./ReasoningTrace.jsx";
import ChartView from "./ChartView.jsx";
import TableView from "./TableView.jsx";

export default function AssistantTurn({ turn }) {
  const { steps, charts, finalAnswer, chartData, tableData, error, isStreaming } = turn;
  const waitingForAnything = isStreaming && steps.length === 0;

  // plot_chart calls (mid-reasoning) plus a legacy chart_data on final_answer,
  // deduplicated by reference isn't necessary since they're structurally
  // distinct paths -- just render everything in the order it arrived.
  const allCharts = chartData ? [...charts, chartData] : charts;

  return (
    <div className="assistant-turn">
      {waitingForAnything && (
        <div className="thinking-indicator">
          <span className="thinking-dot" />
          <span className="thinking-dot" />
          <span className="thinking-dot" />
        </div>
      )}

      <ReasoningTrace steps={steps} isStreaming={isStreaming} />

      {finalAnswer && (
        <div className="final-answer">
          <ReactMarkdown>{finalAnswer}</ReactMarkdown>
        </div>
      )}

      {error && <p className="final-error">{error}</p>}

      {allCharts.map((chart, i) => (
        <ChartView chartData={chart} key={i} />
      ))}
      <TableView tableData={tableData} />
    </div>
  );
}
