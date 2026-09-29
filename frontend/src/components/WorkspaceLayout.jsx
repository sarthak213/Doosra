import { useState } from "react";
import { Outlet, useParams } from "react-router-dom";
import AskSidebar from "./AskSidebar.jsx";

// Chats, projects and boards share one shell: the sidebar on the left, the
// current page on the right. Below 900px the sidebar slides over the page.
export default function WorkspaceLayout() {
  const { chatId } = useParams();
  const [open, setOpen] = useState(false);
  return (
    <div className="ask-shell">
      <AskSidebar activeChatId={chatId} open={open} onClose={() => setOpen(false)} />
      {open && <button type="button" className="sb-scrim" aria-label="Close chat list" onClick={() => setOpen(false)} />}
      <div className="ws-main">
        <button type="button" className="ghost-btn sb-toggle" onClick={() => setOpen(true)}>☰ Chats and projects</button>
        <Outlet />
      </div>
    </div>
  );
}
