/**
 * ボード画面の入口。最初の中身は HTML に埋まっている（`<script type="application/json">`）。
 * 2 枚目からは拡張ホストが postMessage で渡し、React が要るところだけ描き直す。
 */
import { createRoot } from "react-dom/client";

import { DATA_ID, type BoardData } from "../../core/board-view.js";
import { readInitial } from "../initial.js";
import { App } from "./App.js";

const root = document.getElementById("root");
if (root !== null) {
  const initial = readInitial<BoardData>(DATA_ID, "ボード", (error) => ({ kind: "error", error }));
  createRoot(root).render(<App initial={initial} />);
}
