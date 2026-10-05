import "@fontsource-variable/instrument-sans";
import "./styles.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Route, Routes } from "react-router";
import Home from "./pages/Home";
import AuditPage from "./pages/AuditPage";
import ShowcasePage from "./pages/ShowcasePage";

function App() {
  return (
    <div className="shell">
      <header className="masthead">
        <Link to="/" className="wordmark">
          Brand AI Readiness Audit
        </Link>
        <span className="masthead-note">Open source, no account needed</span>
      </header>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/audit/:id" element={<AuditPage />} />
        <Route path="/sample/:slug" element={<ShowcasePage />} />
        <Route path="*" element={<p>There's nothing at this address. <Link to="/">Start an audit</Link>.</p>} />
      </Routes>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
