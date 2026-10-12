/**
 * テンプレート管理画面の入口。最初の中身は HTML に埋まっている（`<script type="application/json">`）。
 * 2 枚目からは拡張ホストが postMessage で渡し、React が要るところだけ描き直す。
 */
import { createRoot } from "react-dom/client";

import { DATA_ID, type TemplatesData } from "../../core/templates-view.js";
import { readInitial } from "../initial.js";
import { App } from "./App.js";

const root = document.getElementById("root");
if (root !== null) {
  const initial = readInitial<TemplatesData>(DATA_ID, "テンプレート管理画面", (error) => ({ kind: "error", error }));
  createRoot(root).render(<App initial={initial} />);
}
