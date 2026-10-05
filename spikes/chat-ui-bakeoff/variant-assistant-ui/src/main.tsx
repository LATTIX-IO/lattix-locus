import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { installProbe } from "@bakeoff/shared";
import { App } from "./App";
import "./index.css";

installProbe();
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
