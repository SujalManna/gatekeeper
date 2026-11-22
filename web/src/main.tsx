import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { applyMaterialTheme } from "./material";
import "./styles.css";

applyMaterialTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
