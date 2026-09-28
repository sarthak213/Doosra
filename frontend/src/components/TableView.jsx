import DataTable from "./kit/DataTable.jsx";

// Tables produced by the agent's tools, as shown in chat turns.
export default function TableView({ table }) {
  return <DataTable table={table} compact maxHeight={420} />;
}
