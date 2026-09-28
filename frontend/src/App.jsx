import { Navigate, Route, Routes } from "react-router-dom";
import Footer from "./components/Footer.jsx";
import Masthead from "./components/Masthead.jsx";
import CopilotDrawer from "./copilot/CopilotDrawer.jsx";
import { CopilotProvider, useCopilot } from "./copilot/CopilotProvider.jsx";
import AskView from "./views/AskView.jsx";
import CompareStudio from "./views/CompareStudio.jsx";
import Home from "./views/Home.jsx";
import PlayerHub from "./views/PlayerHub.jsx";
import PlayerMatrix from "./views/PlayerMatrix.jsx";
import QueryBuilder from "./views/QueryBuilder.jsx";
import "./App.css";
import "./views.css";

function Shell() {
  const { open } = useCopilot();
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
          <Route path="/ask" element={<AskView />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        <Footer />
      </div>
      <CopilotDrawer />
    </div>
  );
}

export default function App() {
  return (
    <CopilotProvider>
      <Shell />
    </CopilotProvider>
  );
}
