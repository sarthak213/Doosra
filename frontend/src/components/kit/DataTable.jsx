import { useMemo, useState } from "react";
import { formatValue, humanize } from "./theme.js";

function toCsv(columns, rows) {
  const esc = (v) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns.map(esc).join(","), ...rows.map((r) => r.map(esc).join(","))].join("\n");
}

function download(filename, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  a.click();
  URL.revokeObjectURL(url);
}

// A result envelope ({title, columns, rows, filters, notes, highlights}) as a
// sortable table with CSV export. `labels` optionally maps column -> header.
export default function DataTable({ table, labels, onRowClick, highlight = [], compact = false, maxHeight = 460 }) {
  const [sort, setSort] = useState(null); // {col, dir}
  const cols = table?.columns || [];
  const rows = useMemo(() => {
    const base = table?.rows || [];
    if (!sort) return base;
    const i = cols.indexOf(sort.col);
    return [...base].sort((a, b) => {
      const x = a[i];
      const y = b[i];
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      const cmp = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true });
      return sort.dir === "asc" ? cmp : -cmp;
    });
  }, [table, sort, cols]);

  // A column is numeric when every value in it is a number (blanks aside).
  // Numeric columns are right-aligned -- header included -- so digits line
  // up and the header sits directly over its numbers.
  const numericCols = useMemo(() => cols.map((_, i) => {
    const vals = (table?.rows || []).map((r) => r[i]).filter((v) => v !== null && v !== undefined);
    return vals.length > 0 && vals.every((v) => typeof v === "number");
  }), [table, cols]);

  if (!table || !cols.length || !table.rows?.length) return null;
  const filters = table.filters ? Object.entries(table.filters) : [];
  const notes = Array.isArray(table.notes) ? table.notes : [];
  const playerCol = cols.indexOf("player");

  function clickHeader(col) {
    setSort((s) => (s?.col === col ? { col, dir: s.dir === "desc" ? "asc" : "desc" } : { col, dir: "desc" }));
  }

  return (
    <figure className={`table-view${compact ? " table-compact" : ""}`}>
      {(table.title || table.id) && (
        <figcaption className="table-title">
          <span>
            {table.id && <span className="table-id">{table.id}</span>}
            {table.title}
          </span>
          <button type="button" className="ghost-btn" onClick={() => download(`${(table.title || "doosra").slice(0, 60)}.csv`, toCsv(cols, rows))}>
            CSV
          </button>
        </figcaption>
      )}
      <div className="table-scroll" style={{ maxHeight }}>
        <table>
          <thead>
            <tr>
              {cols.map((col, i) => (
                <th key={col} scope="col" className={numericCols[i] ? "num" : undefined} aria-sort={sort?.col === col ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
                  <button type="button" className="th-btn" onClick={() => clickHeader(col)}>
                    {labels?.[i] && typeof labels[i] === "string" ? labels[i] : humanize(col)}
                    {sort?.col === col && <span aria-hidden="true">{sort.dir === "asc" ? " ▲" : " ▼"}</span>}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => {
              const name = playerCol >= 0 ? row[playerCol] : null;
              return (
                <tr
                  key={i}
                  className={`${onRowClick ? "clickable " : ""}${name && highlight.includes(name) ? "row-highlight" : ""}`}
                  onClick={onRowClick ? () => onRowClick(row, cols) : undefined}
                >
                  {row.map((cell, j) => (
                    <td key={j} className={numericCols[j] ? "num" : undefined}>
                      {formatValue(cell, cols[j])}
                    </td>
                  ))}
                </tr>
              );
            })}
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
