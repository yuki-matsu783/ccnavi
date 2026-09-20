/**
 * ルール設定画面の CSS。部品は Webview 側の React（`src/webview/rules/`）にあり、ここはその見た目。
 *
 * **部品を足したら、その CSS はここに足す。** クラス名で結び付いているだけなので、対応は人が保つ
 * （`.rule` は `Rule.tsx`、タブと帯は `App.tsx`、判定の結果は `Judge.tsx`、hook の表は `Hooks.tsx`）。
 * 一覧の骨組み（行・開閉・欄名）は設定 3 画面で同じ `LIST_STYLE` から持つ。拡張ホスト側に置く理由は
 * `styles.ts` の頭に書いてある（CSP と nonce）。
 */
import { LIST_STYLE, PAGE_STYLE } from "./styles.js";

export const RULES_STYLE = `${PAGE_STYLE}
  .tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--vscode-panel-border); margin-bottom: 10px; }
  .tab {
    background: none; border: none; border-bottom: 2px solid transparent; color: var(--vscode-descriptionForeground);
    padding: 6px 12px; cursor: pointer; font: inherit;
  }
  .tab.active { color: var(--vscode-editor-foreground); border-bottom-color: var(--vscode-focusBorder); }
  .pane { display: none; }
  .pane.active { display: block; }
  button.action.small { margin-left: auto; }
${LIST_STYLE}
  .rule-section { margin-bottom: 14px; }
  .rule-section h2 { margin: 0 0 4px; font-size: 1em; display: flex; gap: 8px; align-items: center; }
  .section-name { font-weight: 700; padding: 0 8px; border-radius: 999px; border: 1px solid currentColor; font-size: .85em; font-family: var(--vscode-editor-font-family); }
  .section-name.deny { color: var(--vscode-editorError-foreground); }
  .section-name.ask { color: var(--vscode-editorWarning-foreground); }
  .section-name.allow { color: var(--vscode-charts-green); }
  .section-label, .count { color: var(--vscode-descriptionForeground); font-weight: 400; }
  .rule-section.folded .list { display: none; }
  /* 絞り込み中は畳んだタイプの中も見せる（矢印も開いた向きにする） */
  .finding .rule-section.folded .list { display: block; }
  /* 1 行 = 開閉、id、match、パターンと文面、渡す回の刻み、コンテキストの有無 */
  .rule .row-head { grid-template-columns: 18px minmax(110px, 170px) minmax(90px, 190px) minmax(0, 1fr) max-content 14px; }
  /* 刻みの札。刻みが無い行でも空の枠を置く（置かないと右端の●が 1 列ずれる） */
  .sum .sum-every { color: var(--vscode-descriptionForeground); white-space: nowrap; }
  .field > input.f-every { max-width: 110px; }
  .rule.hit .row-head { box-shadow: inset 3px 0 0 var(--vscode-focusBorder); }
  select.f-section { max-width: 120px; }
  /* 押すと札が出る欄。欄そのものは普通のテキスト入力で、手でも書ける。 */
  .picker { position: relative; }
  .picker > input { width: 100%; box-sizing: border-box; max-width: 360px; }
  .picker > .menu { display: none; }
  .picker.open > .menu {
    display: flex; flex-direction: column; gap: 2px; position: absolute; z-index: 5; top: 100%; left: 0; min-width: 100%;
    background: var(--vscode-editorWidget-background); border: 1px solid var(--vscode-widget-border, var(--vscode-panel-border));
    border-radius: 3px; padding: 5px 7px; box-shadow: 0 2px 8px var(--vscode-widget-shadow, transparent);
  }
  .picker .tool { display: flex; gap: 5px; align-items: center; white-space: nowrap; cursor: pointer; }
  .picker .tool input { margin: 0; }
  .with-button { display: flex; gap: 6px; align-items: center; }
  .with-button > input { flex: 1; min-width: 120px; }
  .with-button > button { margin-left: 0; white-space: nowrap; }
  .rule .stale { grid-column: 1 / -1; margin: 0; font-size: .9em; color: var(--vscode-editorError-foreground); overflow-wrap: anywhere; }
  .rule .stale code { margin: 0 6px; }
  .rule .pattern { font-family: var(--vscode-editor-font-family); }
  .judge-form { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; margin-bottom: 10px; }
  .judge-form label { display: flex; gap: 6px; align-items: center; color: var(--vscode-descriptionForeground); }
  .judge-form .grow { flex: 1 1 320px; }
  .judge-form .grow input { flex: 1; }
  .result { border: 1px solid var(--vscode-panel-border); border-radius: 6px; padding: 8px 10px; margin-bottom: 12px; }
  .verdict { font-weight: 700; padding: 0 8px; border-radius: 999px; border: 1px solid currentColor; }
  .verdict.deny { color: var(--vscode-editorError-foreground); }
  .verdict.ask { color: var(--vscode-editorWarning-foreground); }
  .verdict.allow { color: var(--vscode-charts-green); }
  .verdict.skip, .verdict.none { color: var(--vscode-descriptionForeground); }
  .kv { margin: 4px 0; display: grid; grid-template-columns: max-content 1fr; gap: 2px 12px; }
  .kv dt { color: var(--vscode-descriptionForeground); }
  .kv dd { margin: 0; overflow-wrap: anywhere; }
  pre.response { margin: 4px 0 0; padding: 6px 8px; white-space: pre-wrap; overflow-wrap: anywhere; background: var(--vscode-textCodeBlock-background); border-radius: 4px; }
  table { border-collapse: collapse; width: 100%; font-size: .95em; }
  th, td { text-align: left; padding: 3px 8px; border-bottom: 1px solid var(--vscode-panel-border); vertical-align: top; }
  th { color: var(--vscode-descriptionForeground); font-weight: 600; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  td.cmd { overflow-wrap: anywhere; }
  tr.ng td { color: var(--vscode-editorError-foreground); }
  tr.skipped td { color: var(--vscode-editorWarning-foreground); }
  .samples-head { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
  .counts { display: flex; gap: 14px; margin-bottom: 6px; }
  .counts .ng { color: var(--vscode-editorError-foreground); font-weight: 600; }
`;
