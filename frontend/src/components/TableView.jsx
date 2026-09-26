export default function TableView({ tableData }) {
  if (!tableData || !Array.isArray(tableData.columns) || !Array.isArray(tableData.rows)) {
    return null;
  }

  return (
    <div className="table-view">
      <table>
        <thead>
          <tr>
            {tableData.columns.map((col) => (
              <th key={col}>{col}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tableData.rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j}>{cell === null ? "—" : String(cell)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
