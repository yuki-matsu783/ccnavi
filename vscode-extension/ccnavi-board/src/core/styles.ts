/**
 * 5 つの画面で同じ骨組みの CSS。画面ごとの CSS はそれぞれが持ち、ここから読んで前に足す。
 *
 * 色は VS Code のテーマ変数だけを使う（`--vscode-*`）。Claude の配色は body のクラスの下で
 * 同じ変数を上書きするだけなので（`appearance.ts`）、部品はテーマ変数を読むまま変わらない。
 *
 * CSS が拡張ホスト側にあるのは、Webview の CSP が nonce を持つ `<style>` しか通さないため。
 * 画面（React）が後から `<style>` を挿すには nonce を画面へ渡すことになるので、入れ物を組む側が持つ。
 */
import { APPEARANCE_STYLE } from "./appearance.js";

/**
 * 5 つの画面（ボード・ルール設定・リスク管理・フェーズ管理・プロジェクト管理）で同じ見た目のボタン。
 * 縁と薄い影で「押せる」と分かるようにし、押した瞬間に 1px 沈む。primary は VS Code の主ボタンの色。
 */
export const BUTTON_STYLE = `  button.action {
    display: inline-flex; align-items: center; justify-content: center; gap: 4px;
    min-height: 24px; padding: 2px 12px; line-height: 1.3; white-space: nowrap;
    background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground);
    border: 1px solid var(--vscode-button-border, rgba(128, 128, 128, .4)); border-radius: 3px;
    box-shadow: 0 1px 1px rgba(0, 0, 0, .25);
    cursor: pointer; font: inherit;
  }
  button.action:hover { background: var(--vscode-button-secondaryHoverBackground); border-color: var(--vscode-focusBorder); }
  button.action:active { transform: translateY(1px); box-shadow: none; }
  button.action:focus-visible { outline: 1px solid var(--vscode-focusBorder); outline-offset: 1px; }
  button.action.primary { background: var(--vscode-button-background); color: var(--vscode-button-foreground); border-color: transparent; }
  button.action.primary:hover { background: var(--vscode-button-hoverBackground); }
  button.action.small { min-height: 20px; padding: 0 8px; font-size: .9em; }
  button.action:disabled { opacity: .5; cursor: not-allowed; transform: none; box-shadow: none; border-color: transparent; }
  /* 待っている間の回り記号。busy が付いたボタンにだけ出す。動きを減らす設定では回さず、出したままにする */
  button.action .spin { display: none; width: .85em; height: .85em; border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%; }
  /* 非活性の .5 のままだと、見せたい回り記号が一番薄くなる。opacity は子で戻せない（親でまとめて掛かる）ので、busy の間だけボタンごと濃くする。押せない見た目は残る */
  button.action.busy:disabled { opacity: .8; }
  button.action.busy .spin { display: inline-block; animation: ccnavi-spin .8s linear infinite; }
  @keyframes ccnavi-spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { button.action.busy .spin { animation: none; } }`;

/**
 * 5 つの画面で同じ骨組み。本文・見出し上のツールバー（左にパス、右にボタン）・帯・注意・欄・脚注。
 * 画面ごとの部品（ボードの列、設定 3 画面の一覧、プロジェクトのカード）は各画面が足す。
 */
export const PAGE_STYLE = `  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 12px;
    background: var(--vscode-editor-background);
    color: var(--vscode-editor-foreground);
    font-family: var(--vscode-font-family);
    font-size: var(--vscode-font-size);
  }
  code { font-family: var(--vscode-editor-font-family); font-size: .95em; }
  .hidden { display: none !important; }
  .mono { font-family: var(--vscode-editor-font-family); }
  .small { font-size: .85em; }
  .dim { color: var(--vscode-descriptionForeground); }
  .toolbar { display: flex; flex-wrap: wrap; gap: 12px 24px; align-items: center; padding: 0 4px 10px; }
  .summary { display: flex; flex-wrap: wrap; gap: 4px 14px; align-items: baseline; }
  .summary .warn { color: var(--vscode-editorWarning-foreground); font-weight: 600; }
  .path { color: var(--vscode-descriptionForeground); overflow-wrap: anywhere; }
  .dirty { color: var(--vscode-editorWarning-foreground); font-weight: 600; }
  .controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-left: auto; }
  .banner {
    margin: 0 0 10px; padding: 6px 10px; border-radius: 4px;
    border: 1px solid var(--vscode-panel-border);
    display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
  }
  .banner.warn { border-color: var(--vscode-editorWarning-foreground); color: var(--vscode-editorWarning-foreground); }
  .banner.error { border-color: var(--vscode-editorError-foreground); color: var(--vscode-editorError-foreground); }
  .banner.missing { border-color: var(--vscode-editorInfo-foreground); }
  .banner.missing > span { flex: 1 1 320px; }
  .lock {
    margin: 0 0 10px; padding: 6px 10px; border-radius: 4px;
    border: 1px solid var(--vscode-editorError-foreground); color: var(--vscode-editorError-foreground);
  }
  .problems {
    margin: 0 0 12px; padding: 8px 8px 8px 24px;
    border: 1px solid var(--vscode-editorWarning-foreground); border-radius: 4px;
    color: var(--vscode-editorWarning-foreground);
  }
  h2 { margin: 0 0 8px; font-size: 1em; }
  .count { color: var(--vscode-descriptionForeground); font-weight: 400; }
  .hint { margin: 0 0 10px; color: var(--vscode-descriptionForeground); font-size: .92em; }
  .empty { margin: 0; color: var(--vscode-descriptionForeground); font-size: .92em; }
  details.help { margin: 0 0 8px; font-size: .92em; }
  details.help > summary { cursor: pointer; color: var(--vscode-descriptionForeground); list-style: none; }
  details.help > summary::-webkit-details-marker { display: none; }
  details.help > summary::before { content: "▸ "; }
  details.help[open] > summary::before { content: "▾ "; }
  details.help > .hint { margin: 4px 0 0; }
${BUTTON_STYLE}
  input[type=text], input[type=search], textarea, select {
    background: var(--vscode-input-background); color: var(--vscode-input-foreground);
    border: 1px solid var(--vscode-input-border, var(--vscode-panel-border)); border-radius: 2px;
    min-height: 24px; padding: 2px 6px; font: inherit;
  }
  input[type=text]:focus, input[type=search]:focus, textarea:focus, select:focus { outline: 1px solid var(--vscode-focusBorder); outline-offset: -1px; }
  select { background: var(--vscode-dropdown-background); color: var(--vscode-dropdown-foreground); border-color: var(--vscode-dropdown-border); }
  input:disabled, select:disabled, textarea:disabled { opacity: .6; }
  .foot { margin-top: 12px; font-size: .85em; color: var(--vscode-descriptionForeground); min-height: 1.2em; overflow-wrap: anywhere; }
  .foot.error { color: var(--vscode-editorError-foreground); }
  pre.load-error { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0 12px; }
  /* ハイコントラストのテーマでは背景が変わらないので、VS Code の作法どおり点線の縁でホバーを見せ、
     無効なボタンは枠を消さず点線にする。contrast の変数は HC でしか定義されないので、他のテーマでは効かない */
  button.action:hover:not(:disabled):not(:focus-visible) { outline: 1px dashed var(--vscode-contrastActiveBorder, transparent); outline-offset: -1px; }
  button.action:disabled { border-color: var(--vscode-contrastBorder, transparent); border-style: dashed; }
${APPEARANCE_STYLE}`;

/**
 * 設定 3 画面（ルール設定・リスク管理・フェーズ管理）の一覧。1 件 1 行で、押した行だけ下に欄が開く。
 * 行の見出し（.row-head）の列の幅は画面ごとに決める。欄名は欄の左に置き、欄と欄名は親の格子に並ぶ
 * （.field は display: contents）。出番の少ない欄は details.more に畳み、値があるときだけ開いて出す。
 */
export const LIST_STYLE = `  .list { list-style: none; margin: 0; padding: 0; border: 1px solid var(--vscode-panel-border); border-radius: 5px; }
  .list:empty { display: none; }
  .row { border-top: 1px solid var(--vscode-panel-border); }
  .row:first-child { border-top: 0; }
  .row:first-child > .row-head { border-radius: 4px 4px 0 0; }
  .row:last-child > .row-body, .row:last-child:not(.open) > .row-head { border-radius: 0 0 4px 4px; }
  /* 絞り込みで隠す。開いている行は打っている途中で消えないよう隠さない */
  .row.hidden-by-find:not(.open) { display: none; }
  .row-head { display: grid; gap: 10px; align-items: center; padding: 5px 8px; cursor: pointer; }
  .row-head:hover { background: var(--vscode-list-hoverBackground); outline: 1px dashed var(--vscode-contrastActiveBorder, transparent); outline-offset: -1px; }
  .row.open .row-head { background: var(--vscode-editorWidget-background); }
  /* 開いている行は左に縁を付ける。HC は contrastActiveBorder、他は focusBorder */
  .row.open > .row-head, .row.open > .row-body { box-shadow: inset 3px 0 0 var(--vscode-contrastActiveBorder, var(--vscode-focusBorder)); }
  .row.open > .row-body { border-top: 1px dashed var(--vscode-contrastBorder, transparent); }
  .twist {
    background: none; border: none; color: var(--vscode-descriptionForeground);
    font: inherit; padding: 0 2px; cursor: pointer; line-height: 1;
  }
  .sum { display: contents; }
  .sum .sum-id { font-weight: 600; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sum .dim { color: var(--vscode-descriptionForeground); }
  .sum .mono { font-family: var(--vscode-editor-font-family); font-size: .92em; }
  .sum .clip { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sum .sum-note { color: var(--vscode-descriptionForeground); margin-left: 10px; }
  .sum .sum-flag { width: 8px; height: 8px; border-radius: 50%; justify-self: center; }
  .sum .sum-flag.on { background: var(--vscode-charts-blue); }
  .sum .tag { font-size: .85em; padding: 0 7px; border-radius: 999px; border: 1px solid currentColor; font-family: var(--vscode-editor-font-family); justify-self: start; }
  .row-body {
    display: none; padding: 4px 10px 10px 36px; gap: 6px 12px; align-items: center;
    grid-template-columns: 150px minmax(0, 1fr); background: var(--vscode-editorWidget-background);
  }
  .row.open .row-body { display: grid; }
  .row-body .buttons { grid-column: 1 / -1; display: flex; gap: 4px; justify-content: flex-end; padding-top: 4px; }
  .row-body .buttons button { margin-left: 0; }
  .row-body .wide { grid-column: 1 / -1; }
  .row-body textarea { width: 100%; min-height: 2.6em; resize: vertical; font-family: inherit; }
  .field { display: contents; }
  .field > .cap { color: var(--vscode-descriptionForeground); text-align: right; font-size: .95em; }
  .field > input, .field > select, .field > textarea, .field > .inline, .field > .picker, .field > .with-button { width: 100%; box-sizing: border-box; margin: 0; min-width: 0; }
  .field > input.narrow, .field > select.narrow { max-width: 260px; }
  .field > input.num { max-width: 90px; }
  .inline { display: flex; gap: 6px; align-items: center; }
  .inline > input { flex: 1; min-width: 120px; }
  .inline > input.num { flex: 0 0 90px; min-width: 0; }
  .inline > .cap { color: var(--vscode-descriptionForeground); }
  details.more { grid-column: 1 / -1; }
  details.more > summary { cursor: pointer; color: var(--vscode-descriptionForeground); list-style: none; padding: 2px 0; }
  details.more > summary::-webkit-details-marker { display: none; }
  details.more > summary::before { content: "▸ "; }
  details.more[open] > summary::before { content: "▾ "; }
  details.more > summary b { font-weight: 500; color: var(--vscode-editor-foreground); }
  details.more > .sub { display: grid; grid-template-columns: 150px minmax(0, 1fr); gap: 6px 12px; align-items: center; padding-top: 4px; }
  .find { display: flex; gap: 12px; align-items: center; margin: 0 0 10px; flex-wrap: wrap; }
  .find input { width: 320px; max-width: 100%; }
  .find .hint { margin: 0; }`;
