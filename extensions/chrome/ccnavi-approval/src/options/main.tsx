/**
 * 設定画面の入口。リポジトリ（統合先の名前・直近 N 日・指定のブランチ）と PAT を登録する。
 *
 * 画面は React で組み、見た目は VS Code 拡張（ccnavi-board）と同じ共通の部品（`@ccnavi/shared` の
 * `styles/`）で揃える。CSS の入口は同じ置き場の `style.css`（ビルドが `options.css` に組む）。
 */
import { createRoot } from "react-dom/client";

import { App } from "./App.js";

const root = document.getElementById("root");
if (root !== null) {
  createRoot(root).render(<App />);
}
