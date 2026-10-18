/**
 * 取り込み待ち画面の入口。最初の中身は HTML に埋まっている（`<script type="application/json">`）。
 * 2 枚目からは拡張ホストが postMessage で渡し、React が要るところだけ描き直す。
 */
import { createRoot } from "react-dom/client";

import { DATA_ID, type HandoffData } from "../../core/handoff-view.js";
import { readInitial } from "../initial.js";
import { App } from "./App.js";

const root = document.getElementById("root");
if (root !== null) {
  const initial = readInitial<HandoffData>(DATA_ID, "取り込み待ち画面", (error) => ({ kind: "error", error }));
  createRoot(root).render(<App initial={initial} />);
}
