import { createRoot } from "react-dom/client";
import App from "./App";
import { applyDocument } from "./i18n";
import "./style.css";
import "./workspace-design.css";
import "./layout-density.css";
import "./page-fit.css";
applyDocument();
createRoot(document.getElementById("root")!).render(<App />);
