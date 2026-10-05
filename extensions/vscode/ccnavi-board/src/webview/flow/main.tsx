/**
 * フロー編集画面の入口。最初の中身は HTML に埋まっている（`<script type="application/json">`）。
 * 2 枚目からは拡張ホストが postMessage で渡す。入れ物は入れ直されない（入れ直すと打ちかけの編集が消える）。
 */
import { createRoot } from "react-dom/client";

import { DATA_ID, type FlowData } from "../../core/flow-view.js";
import { readInitial } from "../initial.js";
import { App } from "./App.js";

const root = document.getElementById("root");
if (root !== null) {
  const initial = readInitial<FlowData>(DATA_ID, "フロー編集画面", (error) => ({ kind: "error", error }));
  createRoot(root).render(<App initial={initial} />);
}
