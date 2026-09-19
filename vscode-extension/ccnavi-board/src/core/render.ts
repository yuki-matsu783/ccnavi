/**
 * ボード画面の 1 枚の HTML を組み立てる。中身（列・カード・承認のオーバーレイ）を作るのは
 * Webview 側の React（`src/webview/board/`）で、ここが作るのはその入れ物だけ。
 *
 * 外部資源に依存しない 1 枚にする方針は変えていない。束ねた画面のスクリプトは
 * `<script nonce>` に文字列として流し込み、ファイルとしては読ませない（`localResourceRoots` は空のまま）。
 * 最初に見せる中身は `<script type="application/json">` に埋める。2 枚目からは HTML を作り直さず、
 * 拡張ホストが `postMessage` で渡す（board-panel）。
 *
 * 色は VS Code のテーマ変数だけを使う。5 つの画面で同じ骨組みの CSS（PAGE_STYLE・BUTTON_STYLE・
 * LIST_STYLE）はここが持ち、残り 4 画面もここから読む。
 */
import { APPEARANCE_STYLE, type Appearance, bodyTag } from "./appearance.js";
import { DATA_ID, embedData, type BoardData } from "./board-view.js";

export interface RenderOptions {
  readonly nonce: string;
  /** 束ねた画面のスクリプト（`out/webview/board.js` の中身）。拡張は起動時に 1 度読む */
  readonly script: string;
  /** 見た目。無ければ VS Code のテーマに従う */
  readonly appearance?: Appearance;
}

/**
 * 入れ物の HTML。`data` はそのまま画面の最初の 1 枚になる。
 *
 * nonce は呼ぶたびに変える。同じ文字列を `webview.html` に入れても VS Code は何もしないので、
 * 作り直したいときに作り直せなくなる。
 */
export function renderBoardPage(data: BoardData, options: RenderOptions): string {
  const { nonce } = options;
  return `<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ccnavi ボード</title>
<style nonce="${nonce}">
${STYLE}
</style>
</head>
${bodyTag(options.appearance)}
<div id="root"></div>
<script type="application/json" id="${DATA_ID}">${embedData(data)}</script>
<script nonce="${nonce}">
${options.script}
</script>
</body>
</html>
`;
}

/** HTML の特殊文字を実体参照にする。& を最初に変換して二重変換を避ける */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

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

const STYLE = `${PAGE_STYLE}
  .toolbar { padding-bottom: 12px; }
  .summary .counts { color: var(--vscode-descriptionForeground); }
  .filter { display: flex; gap: 6px; align-items: center; color: var(--vscode-descriptionForeground); }
  .filter.attention { gap: 4px; cursor: pointer; }
  .filter.attention input { margin: 0; cursor: pointer; }
  a.mr-link { color: var(--vscode-textLink-foreground); text-decoration: none; }
  a.mr-link:hover, a.mr-link:focus-visible { text-decoration: underline; color: var(--vscode-textLink-activeForeground); }
  .phase-status a.mr-link { margin-left: 6px; }
  .board-empty { padding: 4px; color: var(--vscode-descriptionForeground); }
  /* 列は空きに合わせて伸び縮みする。1 列 220px を割るところまで狭まったら横スクロールに逃がす。
     右端の取っ手をドラッグした列は幅が px で固定され（.sized）、ダブルクリックで元の伸び縮みに戻る。
     畳んだ列は見出し 1 行ぶんの幅に縮む。縦書きにはしない */
  .board { display: flex; gap: 12px; align-items: flex-start; overflow-x: auto; }
  .column {
    position: relative;
    flex: 1 1 0; min-width: 220px;
    border: 1px solid var(--vscode-panel-border); border-radius: 6px; padding: 8px;
  }
  .column.sized { flex: 0 0 auto; }
  .resizer { position: absolute; top: 0; bottom: 0; right: -7px; width: 12px; cursor: col-resize; z-index: 1; }
  .resizer::after {
    content: ""; position: absolute; top: 8px; bottom: 8px; left: 5px; width: 2px;
    border-radius: 1px; background: var(--vscode-focusBorder); opacity: 0;
  }
  .resizer:hover::after, .column.resizing .resizer::after { opacity: 1; }
  body.resizing, body.resizing * { cursor: col-resize; user-select: none; }
  .column h2 { margin: 0 0 8px; font-size: 1em; display: flex; justify-content: space-between; align-items: center; gap: 6px; }
  .column .count { color: var(--vscode-descriptionForeground); }
  button.fold {
    display: flex; align-items: center; gap: 4px; min-width: 0;
    background: none; border: none; padding: 0; margin: 0; cursor: pointer;
    font: inherit; font-weight: inherit; color: inherit; text-align: left;
  }
  button.fold:hover .label { text-decoration: underline; }
  button.fold:focus-visible { outline: 1px solid var(--vscode-focusBorder); outline-offset: 2px; }
  .fold-mark {
    display: inline-block; width: 0; height: 0;
    border-left: 5px solid transparent; border-right: 5px solid transparent; border-top: 6px solid currentColor;
  }
  .column.folded .fold-mark {
    border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-left: 6px solid currentColor; border-right: 0;
  }
  /* ドラッグで固定した px 幅（インラインの style）より畳んだ状態を優先する。広げたときは固定幅に戻る */
  .column.folded { flex: 0 0 auto; min-width: 0; width: auto !important; }
  .column.folded h2 { margin: 0; white-space: nowrap; }
  .column.folded .cards, .column.folded .empty, .column.folded .resizer { display: none; }
  .empty { margin: 0; color: var(--vscode-descriptionForeground); font-size: .92em; }
  .cards { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  .card {
    position: relative;
    border: 1px solid var(--vscode-panel-border); border-radius: 5px; padding: 8px;
    background: var(--vscode-editorWidget-background); cursor: pointer;
  }
  .card.child { margin-left: 12px; }
  .card:hover, .card:focus {
    outline: 1px solid var(--vscode-focusBorder);
    background: var(--vscode-list-hoverBackground);
  }
  /* HC のホバーの点線。疑似要素に描き、上の outline（実線。焦点の輪でもある）には触らない。他のテーマでは透明 */
  .card:hover::after { content: ""; position: absolute; inset: 0; border-radius: 5px; pointer-events: none; border: 1px dashed var(--vscode-contrastActiveBorder, transparent); }
  .card.has-issue { border-left: 3px solid var(--vscode-editorWarning-foreground); }
  .card.pending { border-left: 3px solid var(--vscode-charts-blue); }
  .card.review-hold { border-left: 3px solid var(--vscode-editorError-foreground); }
  .card.hidden { display: none; }
  .card-head { display: flex; gap: 6px; align-items: baseline; flex-wrap: wrap; }
  .num { font-weight: 600; font-variant-numeric: tabular-nums; }
  .title { overflow-wrap: anywhere; }
  .where { margin-left: auto; font-size: .85em; color: var(--vscode-descriptionForeground); white-space: nowrap; }
  .stage { margin-top: 2px; font-size: .9em; color: var(--vscode-descriptionForeground); }
  /* 札は人が動く必要がある状態だけ。属性は枠無しの薄い文字で 1 行に並べる */
  .badges { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
  .badge {
    font-size: .82em; padding: 0 6px; border-radius: 999px;
    border: 1px solid currentColor; color: var(--vscode-descriptionForeground);
  }
  .badge.copy-none { color: var(--vscode-charts-blue); }
  .badge.hold, .badge.seen, .badge.risk-high, .badge.risk-critical { color: var(--vscode-editorError-foreground); }
  .badge.blocked { color: var(--vscode-editorError-foreground); font-weight: 600; }
  .badge.mark-requested { color: var(--vscode-charts-yellow); }
  .badge.worktree.none { color: var(--vscode-editorWarning-foreground); }
  .facts { display: flex; flex-wrap: wrap; gap: 2px 10px; margin-top: 5px; font-size: .85em; color: var(--vscode-descriptionForeground); }
  .fact { white-space: nowrap; max-width: 100%; overflow: hidden; text-overflow: ellipsis; }
  .fact.copy-open::before, .fact.copy-review::before, .fact.copy-closed::before, .fact.mark-reviewed::before { content: "✓ "; }
  .fact.sha { font-family: var(--vscode-editor-font-family); }
  /* 親のフェーズ一覧。1 段階 1 行。左の丸が段階で、右が状態。
     状態は要約（.phase-brief）と全文（.phase-full）を両方持ち、フェーズ一覧の幅（カードの内寸）で
     どちらを見せるかを決める。480px 未満（1920px の画面で 4 列のときも含む）は要約だけを見せ、
     他の列を畳むかドラッグで広げて 480px 以上になると全文に替わる。狭いとき全文は display: none
     ではなく画面の外に置き、読み上げには残す。行の title にもあるので、マウスでも読める。
     右の列を auto にすると状態の 1 行分の幅が行を占め、段階名の列が 0 になって省略記号ごと
     消えるので、状態の列は 55% で止める */
  .phases { list-style: none; margin: 8px 0 0; padding: 6px 0 0; border-top: 1px solid var(--vscode-panel-border); font-size: .85em; display: flex; flex-direction: column; gap: 3px; container-type: inline-size; }
  .phase { display: grid; grid-template-columns: 12px minmax(0, 1fr) fit-content(55%); gap: 6px; align-items: baseline; color: var(--vscode-descriptionForeground); }
  .phase-dot { width: 8px; height: 8px; border-radius: 50%; border: 1.5px solid var(--vscode-descriptionForeground); align-self: center; }
  .phase-ended .phase-dot { background: var(--vscode-charts-green); border-color: var(--vscode-charts-green); }
  .phase-active .phase-dot { border: 2.5px solid var(--vscode-charts-blue); }
  .phase-name { min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .phase .phase-label { font-weight: 600; color: var(--vscode-editor-foreground); }
  .phase-tickets::before { content: "·"; margin: 0 5px; }
  .phase-status { position: relative; min-width: 0; text-align: right; overflow-wrap: anywhere; justify-self: end; }
  .phase-full { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
  @container (min-width: 480px) {
    .phase-brief { display: none; }
    .phase-full { position: static; width: auto; height: auto; overflow: visible; clip-path: none; white-space: normal; }
  }
  .phase-active .phase-status { color: var(--vscode-charts-blue); }
  .phase.review-hold .phase-label, .phase.review-hold .phase-status { color: var(--vscode-editorError-foreground); }
  .phase button.action { margin-left: 6px; min-height: 20px; padding: 0 8px; font-size: .95em; }
  .issues {
    list-style: none; margin: 6px 0 0; padding: 0;
    font-size: .82em; color: var(--vscode-editorWarning-foreground);
  }
  .issues li { overflow-wrap: anywhere; }
  .card-actions { display: flex; gap: 6px; margin-top: 8px; }
  .approval-backdrop {
    position: fixed; inset: 0; z-index: 10;
    background: color-mix(in srgb, var(--vscode-editor-background) 70%, transparent);
    display: flex; align-items: center; justify-content: center; padding: 16px;
  }
  .approval {
    width: min(920px, 100%); max-height: 100%; overflow: auto; box-sizing: border-box;
    padding: 14px 16px; border-radius: 6px;
    background: var(--vscode-editorWidget-background, var(--vscode-editor-background));
    border: 1px solid var(--vscode-editorWidget-border, var(--vscode-panel-border));
    box-shadow: 0 4px 16px rgba(0, 0, 0, .35);
  }
  .approval h2 { margin: 0 0 8px; font-size: 1.1em; }
  .approval h3 { margin: 12px 0 4px; font-size: .95em; color: var(--vscode-descriptionForeground); }
  .approval-batch { border-collapse: collapse; width: 100%; font-size: .9em; margin-bottom: 8px; }
  .approval-batch th, .approval-batch td { text-align: left; padding: 2px 8px 2px 0; border-bottom: 1px solid var(--vscode-panel-border); vertical-align: top; }
  .approval-id { font-family: var(--vscode-editor-font-family); }
  .approval-text {
    margin: 0; padding: 8px; max-height: 50vh; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere;
    font-family: var(--vscode-editor-font-family); font-size: var(--vscode-editor-font-size);
    background: var(--vscode-textCodeBlock-background); border: 1px solid var(--vscode-panel-border); border-radius: 4px;
  }
  .approval-rejected, .approval-problems { margin: 0; padding-left: 18px; font-size: .88em; color: var(--vscode-editorWarning-foreground); }
  .approval-rejected li, .approval-problems li { overflow-wrap: anywhere; }
  .approval-note { margin: 4px 0 8px; }
  .approval-note.warn { color: var(--vscode-editorWarning-foreground); }
  .approval-note.error { color: var(--vscode-editorError-foreground); white-space: pre-wrap; overflow-wrap: anywhere; }
  .approval-actions { display: flex; gap: 8px; margin-top: 12px; justify-content: flex-end; }`;
