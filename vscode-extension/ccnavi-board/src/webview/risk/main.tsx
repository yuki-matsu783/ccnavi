/**
 * リスク管理画面の入口。最初の中身は HTML に埋まっている（`<script type="application/json">`）。
 * 2 枚目からは拡張ホストが postMessage で渡す。入れ物は入れ直されない（ADR-0062）ので、
 * ここが走るのはパネルを開いた 1 度だけ。
 */
import { createRoot } from "react-dom/client";

import { DATA_ID, type RiskData } from "../../core/risk-view.js";
import { readInitial } from "../initial.js";
import { App } from "./App.js";

const root = document.getElementById("root");
if (root !== null) {
  const initial = readInitial<RiskData>(DATA_ID, "リスク管理画面", (error) => ({ kind: "error", error }));
  createRoot(root).render(<App initial={initial} />);
}
