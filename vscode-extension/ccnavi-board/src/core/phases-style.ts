/**
 * フェーズ管理画面の CSS。部品は Webview 側の React（`src/webview/phases/`）にあり、ここはその見た目。
 *
 * **部品を足したら、その CSS はここに足す。** クラス名で結び付いているだけなので、対応は人が保つ
 * （`.phase` は `Phase.tsx`、帯と見出しは `App.tsx`）。一覧の骨組み（行・開閉・欄名）は設定 3 画面で
 * 同じ `LIST_STYLE` から持つ。拡張ホスト側に置く理由は `styles.ts` の頭に書いてある（CSP と nonce）。
 */
import { LIST_STYLE, PAGE_STYLE } from "./styles.js";

export const PHASES_STYLE = `${PAGE_STYLE}
  button.action.small { margin-left: auto; }
  input.duplicate { border-color: var(--vscode-editorError-foreground); }
  .block { border: 1px solid var(--vscode-panel-border); border-radius: 6px; padding: 8px 10px; margin-bottom: 10px; }
  .block h2 { display: flex; gap: 8px; align-items: center; }
${LIST_STYLE}
  /* 1 行 = 開閉、id、title、kind、review、scope */
  .phase .row-head { grid-template-columns: 18px minmax(110px, 160px) minmax(90px, 160px) max-content max-content minmax(0, 1fr); }
  .sum .tag.work { color: var(--vscode-descriptionForeground); }
  .sum .tag.feedback { color: var(--vscode-editorInfo-foreground); }
  .field > select.f-kind, .field > select.f-review { max-width: 420px; }
`;
