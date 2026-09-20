/**
 * リスク管理画面の CSS。部品は Webview 側の React（`src/webview/risk/`）にあり、ここはその見た目。
 *
 * **部品を足したら、その CSS はここに足す。** クラス名で結び付いているだけなので、対応は人が保つ
 * （`.factor` は `Factor.tsx`、閾値の欄と帯は `App.tsx`）。一覧の骨組み（行・開閉・欄名）は
 * 設定 3 画面で同じ `LIST_STYLE` から持つ。拡張ホスト側に置く理由は `styles.ts` の頭に書いてある（CSP と nonce）。
 */
import { LIST_STYLE, PAGE_STYLE } from "./styles.js";

export const RISK_STYLE = `${PAGE_STYLE}
  button.action.small { margin-left: auto; }
  .block { border: 1px solid var(--vscode-panel-border); border-radius: 6px; padding: 8px 10px; margin-bottom: 10px; }
  .block h2 { display: flex; gap: 8px; align-items: center; }
  .levels { display: flex; flex-wrap: wrap; gap: 8px 20px; align-items: center; }
  .levels .field { display: flex; flex-direction: row; gap: 6px; align-items: center; }
  .levels .field > .cap { text-align: left; }
  .levels .field > input { width: 90px; }
${LIST_STYLE}
  /* 1 行 = 開閉、id、points、当て方と値と文面 */
  .factor .row-head { grid-template-columns: 18px minmax(110px, 160px) 60px minmax(0, 1fr); }
  .sum .sum-points { text-align: right; font-variant-numeric: tabular-nums; }
  .field > select.f-kind { max-width: 300px; }
`;
