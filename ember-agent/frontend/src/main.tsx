import { createRoot } from "react-dom/client";
import { App } from "./App";
import { MemoryGraphPage } from "./MemoryGraph";
import "./style.css";

const root = document.getElementById("root");
if (!root) throw new Error("Ember root is missing.");
createRoot(root).render(
  location.pathname === "/memory-graph" ? <MemoryGraphPage /> : <App />,
);
