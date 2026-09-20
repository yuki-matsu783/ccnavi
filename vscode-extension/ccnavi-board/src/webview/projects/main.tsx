/**
 * プロジェクト管理画面の入口。最初の中身は HTML に埋まっている（`<script type="application/json">`）。
 * 2 枚目からは拡張ホストが postMessage で渡し、React が要るところだけ描き直す。
 */
import { createRoot } from "react-dom/client";

import { DATA_ID, type ProjectsData } from "../../core/projects-view.js";
import { readInitial } from "../initial.js";
import { App } from "./App.js";

const root = document.getElementById("root");
if (root !== null) {
  const initial = readInitial<ProjectsData>(DATA_ID, (detail) => ({ kind: "error", error: `${detail}（拡張の不具合）。プロジェクト管理を開き直す。` }));
  createRoot(root).render(<App initial={initial} />);
}
