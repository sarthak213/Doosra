import { useState } from "react";

function formatToolInput(input) {
  if (input && typeof input === "object" && "query" in input) {
    return input.query;
  }
  return JSON.stringify(input, null, 2);
}

function formatToolOutput(output) {
  if (output && typeof output === "object") {
    if (output.error) return `error: ${output.error}`;
    if (Array.isArray(output.rows) && Array.isArray(output.columns)) {
      const header = output.columns.join(" | ");
      const rows = output.rows
        .slice(0, 5)
        .map((r) => r.join(" | "))
        .join("\n");
      const more = output.row_count > 5 ? `\n… ${output.row_count - 5} more row(s)` : "";
      return `${header}\n${rows}${more}`;
    }
  }
  if (Array.isArray(output)) {
    return output.length ? output.join(", ") : "(no matches)";
  }
  return String(output);
}

function CopyableCode({ code }) {
  const [copied, setCopied] = useState(false);

  function copy() {
    navigator.clipboard
      .writeText(code)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {});
  }

  return (
    <div className="copyable-code">
      <pre className="step-code">{code}</pre>
      <button type="button" className="copy-btn" onClick={copy}>
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

function Step({ step }) {
  if (step.type === "thought") {
    return (
      <div className="step step-thought">
        <span className="step-dot" />
        <p>{step.content}</p>
      </div>
    );
  }

  if (step.type === "tool_call") {
    return (
      <div className="step step-tool">
        <span className="step-dot step-dot-brass" />
        <div>
          <p className="step-label">
            called <code>{step.tool}</code>
          </p>
          {step.tool === "run_sql" ? (
            <CopyableCode code={step.input.query} />
          ) : (
            <pre className="step-code">{formatToolInput(step.input)}</pre>
          )}
        </div>
      </div>
    );
  }

  if (step.type === "tool_result") {
    return (
      <div className="step step-tool-result">
        <span className="step-dot step-dot-brass-dim" />
        <div>
          <p className="step-label">result</p>
          <pre className="step-code step-code-dim">{formatToolOutput(step.output)}</pre>
        </div>
      </div>
    );
  }

  if (step.type === "self_correction") {
    return (
      <div className="step step-correction">
        <span className="step-dot step-dot-seam" />
        <p>{step.content}</p>
      </div>
    );
  }

  if (step.type === "error") {
    return (
      <div className="step step-error">
        <span className="step-dot step-dot-seam" />
        <p>{step.content}</p>
      </div>
    );
  }

  return null;
}

export default function ReasoningTrace({ steps, isStreaming }) {
  const [open, setOpen] = useState(() => isStreaming);

  if (steps.length === 0 && !isStreaming) return null;

  return (
    <details
      className="reasoning-trace"
      open={open}
      onToggle={(e) => setOpen(e.target.open)}
    >
      <summary>
        <span className="reasoning-caret" aria-hidden="true" />
        Reasoning · {steps.length} step{steps.length === 1 ? "" : "s"}
        {isStreaming && <span className="reasoning-live"> · live</span>}
      </summary>
      <div className="reasoning-steps">
        {steps.map((step, i) => (
          <Step step={step} key={i} />
        ))}
      </div>
    </details>
  );
}
