/**
 * ボード画面の入口。最初の中身は HTML に埋まっている（`<script type="application/json">`）。
 * 2 枚目からは拡張ホストが postMessage で渡し、React が要るところだけ描き直す。
 */
import { createRoot } from "react-dom/client";

import { DATA_ID, type BoardData } from "../../core/board-view.js";
import { App } from "./App.js";

function initial(): BoardData {
  const element = document.getElementById(DATA_ID);
  const text = element?.textContent ?? "";
  if (text === "") {
    return { kind: "error", error: "画面に渡す中身が無い（拡張の不具合）。ボードを開き直す。" };
  }
  try {
    return JSON.parse(text) as BoardData;
  } catch (error) {
    return { kind: "error", error: `画面に渡す中身を読めなかった（拡張の不具合）: ${String(error)}` };
  }
}

const root = document.getElementById("root");
if (root !== null) {
  createRoot(root).render(<App initial={initial()} />);
}
