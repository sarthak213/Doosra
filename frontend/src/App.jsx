import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthProvider.jsx";
import Footer from "./components/Footer.jsx";
import Masthead from "./components/Masthead.jsx";
import WorkspaceLayout from "./components/WorkspaceLayout.jsx";
import CopilotDrawer from "./copilot/CopilotDrawer.jsx";
import { CopilotProvider, useCopilot } from "./copilot/CopilotProvider.jsx";
import AskView from "./views/AskView.jsx";
import BoardView from "./views/BoardView.jsx";
import CompareStudio from "./views/CompareStudio.jsx";
import DataCoverage from "./views/DataCoverage.jsx";
import Home from "./views/Home.jsx";
import Invites from "./views/Invites.jsx";
import Login from "./views/Login.jsx";
import Methodology from "./views/Methodology.jsx";
import PlayerHub from "./views/PlayerHub.jsx";
import PlayerMatrix from "./views/PlayerMatrix.jsx";
import ProjectPage from "./views/ProjectPage.jsx";
import QueryBuilder from "./views/QueryBuilder.jsx";
import "./App.css";
import "./views.css";

function Shell() {
  const { open } = useCopilot();
  const { loading, mode, user } = useAuth();
  if (loading) return null;
  if (mode !== "none" && !user) return <Login />; // hosted, and nobody is signed in
  return (
    <div className={`app-shell${open ? " copilot-open" : ""}`}>
      <Masthead />
      <div className="app-body">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/players" element={<PlayerHub />} />
          <Route path="/players/:name" element={<PlayerHub />} />
          <Route path="/compare" element={<CompareStudio />} />
          <Route path="/query" element={<QueryBuilder />} />
          <Route path="/matrix" element={<PlayerMatrix />} />
          <Route element={<WorkspaceLayout />}>
            <Route path="/ask/:chatId?" element={<AskView />} />
            <Route path="/projects/:projectId" element={<ProjectPage />} />
            <Route path="/boards/:boardId" element={<BoardView />} />
          </Route>
          <Route path="/methodology/fibs" element={<Methodology />} />
          <Route path="/data" element={<DataCoverage />} />
          <Route path="/admin/invites" element={<Invites />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
      <Footer />
      <CopilotDrawer />
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <CopilotProvider>
        <Shell />
      </CopilotProvider>
    </AuthProvider>
  );
}
