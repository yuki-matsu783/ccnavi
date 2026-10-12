/**
 * 計画編集タブの入口。最初の中身は HTML に埋まっている（`<script type="application/json">`）。
 * 2 枚目からは拡張ホストが postMessage で渡す。入れ物は入れ直されない（入れ直すと未保存の線が消える）。
 */
import { createRoot } from "react-dom/client";

import { DATA_ID, type WorkflowData } from "../../core/workflow-view.js";
import { readInitial } from "../initial.js";
import { App } from "./App.js";

const root = document.getElementById("root");
if (root !== null) {
  const initial = readInitial<WorkflowData>(DATA_ID, "計画編集タブ", (error) => ({ kind: "error", error }));
  createRoot(root).render(<App initial={initial} />);
}
