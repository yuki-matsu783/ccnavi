/**
 * フェーズ管理画面の入口。最初の中身は HTML に埋まっている（`<script type="application/json">`）。
 * 2 枚目からは拡張ホストが postMessage で渡す。入れ物は拡張ホストからは入れ直されない（ADR-0062）
 * ので、ここが走るのはパネルを開いたとき。VS Code が画面を作り直す道（`Developer: Reload Webviews`、
 * 別ウィンドウへ移す）では同じ HTML からもう 1 度走るが、`ready` を送れば今の中身が届く。
 */
import { createRoot } from "react-dom/client";

import { DATA_ID, type PhasesData } from "../../core/phases-view.js";
import { readInitial } from "../initial.js";
import { App } from "./App.js";

const root = document.getElementById("root");
if (root !== null) {
  const initial = readInitial<PhasesData>(DATA_ID, "フェーズ管理画面", (error) => ({ kind: "error", error }));
  createRoot(root).render(<App initial={initial} />);
}
