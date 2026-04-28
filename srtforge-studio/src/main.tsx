import { createRoot } from "react-dom/client";

import "./styles/index.css";
import { App } from "./App";

// StrictMode intentionally NOT used: in dev it double-mounts components
// and double-fires effects, which interacts badly with WebView2 input
// handling and produced a "every keystroke types twice" symptom in the
// settings drawer. Tauri prod builds wouldn't ship StrictMode either.
createRoot(document.getElementById("root")!).render(<App />);
