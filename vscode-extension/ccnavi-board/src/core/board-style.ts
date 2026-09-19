/**
 * ボード画面の CSS。部品は Webview 側の React（`src/webview/board/`）にあり、ここはその見た目。
 *
 * **部品を足したら、その CSS はここに足す。** クラス名で結び付いているだけなので、
 * 対応は人が保つ（`.card` は `Card.tsx`、`.approval-*` は `Approval.tsx`、列とツールバーは `App.tsx`）。
 * 拡張ホスト側に置く理由は `styles.ts` の頭に書いてある（CSP と nonce）。
 */
import { PAGE_STYLE } from "./styles.js";

export const BOARD_STYLE = `${PAGE_STYLE}
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
