import { humanize } from "./ChartView.jsx";

// Rates always show two decimals (JSON turns 28.0 into 28, which would sit
// oddly next to 27.20); counts get thousands separators.
const RATE_COLUMNS = /(average|strike_rate|economy|_pct|per_)/;

function formatCell(value, column) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    if (RATE_COLUMNS.test(column) || !Number.isInteger(value)) return value.toFixed(2);
    return value.toLocaleString("en-US");
  }
  return String(value);
}

// Renders a table the agent's tools produced ({title, columns, rows,
// filters, notes}) -- or the older bare {columns, rows} shape.
export default function TableView({ table }) {
  if (!table || !Array.isArray(table.columns) || !Array.isArray(table.rows) || !table.rows.length) {
    return null;
  }
  const filters = table.filters ? Object.entries(table.filters) : [];
  const notes = Array.isArray(table.notes) ? table.notes : [];

  return (
    <figure className="table-view">
      {(table.title || table.id) && (
        <figcaption className="table-title">
          {table.id && <span className="table-id">{table.id}</span>}
          {table.title}
        </figcaption>
      )}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {table.columns.map((col) => (
                <th key={col} scope="col">
                  {humanize(col)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, i) => (
              <tr key={i}>
                {row.map((cell, j) => (
                  <td key={j} className={typeof cell === "number" ? "num" : undefined}>
                    {formatCell(cell, table.columns[j])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(filters.length > 0 || notes.length > 0) && (
        <div className="table-meta">
          {filters.length > 0 && (
            <ul className="filter-chips" aria-label="Filters applied">
              {filters.map(([k, v]) => (
                <li key={k}>
                  <span>{humanize(k)}</span> {String(v)}
                </li>
              ))}
            </ul>
          )}
          {notes.length > 0 && (
            <ul className="table-notes">
              {notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </figure>
  );
}
