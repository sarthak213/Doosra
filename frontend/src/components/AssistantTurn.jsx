import ReactMarkdown from "react-markdown";
import ReasoningTrace from "./ReasoningTrace.jsx";
import ChartView from "./ChartView.jsx";
import AddToBoard from "./AddToBoard.jsx";
import TableView from "./TableView.jsx";

// projectId: where a new board made from an answer goes. onExplain(kind, item): the Explain button under each chart or table.
export default function AssistantTurn({ turn, projectId, onExplain }) {
  const { steps, charts, tables, finalAnswer, chartData, tableData, error, isStreaming } = turn;
  const waitingForAnything = isStreaming && steps.length === 0;

  // plot_chart calls (mid-reasoning) plus a legacy chart_data on final_answer.
  const allCharts = chartData ? [...charts, chartData] : charts;
  const allTables = [...(tables || []), ...(tableData ? [tableData] : [])];

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
        <div className="answer-item" key={`c${i}`}>
          <ChartView chartData={chart} />
          {!isStreaming && (
            <div className="answer-actions">
              {onExplain && <button type="button" className="ghost-btn" onClick={() => onExplain("chart", chart)}>Explain</button>}
              <AddToBoard chart={chart} projectId={projectId} />
            </div>
          )}
        </div>
      ))}
      {allTables.map((table, i) => (
        <div className="answer-item" key={table.id || `t${i}`}>
          <TableView table={table} />
          {!isStreaming && (
            <div className="answer-actions">
              {onExplain && <button type="button" className="ghost-btn" onClick={() => onExplain("table", table)}>Explain</button>}
              <AddToBoard table={table} projectId={projectId} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
