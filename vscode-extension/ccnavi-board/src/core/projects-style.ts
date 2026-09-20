/**
 * プロジェクト管理画面の CSS。部品は Webview 側の React（`src/webview/projects/`）にあり、ここはその見た目。
 *
 * **部品を足したら、その CSS はここに足す。** クラス名で結び付いているだけなので、
 * 対応は人が保つ（`.project` は `Project.tsx`、`.menu` は `Menu.tsx`、帯と clone の欄は `App.tsx`）。
 * 拡張ホスト側に置く理由は `styles.ts` の頭に書いてある（CSP と nonce）。
 */
import { PAGE_STYLE } from "./styles.js";

export const PROJECTS_STYLE = `${PAGE_STYLE}
  section { margin-bottom: 18px; }
  .summary { font-weight: 600; }
  .summary .path { font-weight: 400; }
  .ok { color: var(--vscode-charts-green); }
  .warn-text { color: var(--vscode-editorWarning-foreground); }
  .clone-form input[type=text] { width: 100%; }
  .clone-form { display: flex; gap: 8px; align-items: flex-end; flex-wrap: wrap; }
  .clone-form label { display: flex; flex-direction: column; gap: 2px; font-size: .9em; color: var(--vscode-descriptionForeground); }
  .clone-form label.grow { flex: 1 1 320px; }
  .clone-form label:not(.grow) { flex: 0 1 200px; }
  .status { margin: 8px 0 0; padding: 6px 10px; border-radius: 4px; border: 1px solid var(--vscode-panel-border); overflow-wrap: anywhere; }
  .status.failed { border-color: var(--vscode-editorError-foreground); color: var(--vscode-editorError-foreground); }
  .projects { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
  .project {
    border: 1px solid var(--vscode-panel-border); border-radius: 6px; padding: 8px 10px;
    background: var(--vscode-editorWidget-background);
  }
  .project.has-problem { border-left: 3px solid var(--vscode-editorWarning-foreground); }
  .project-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; margin-bottom: 6px; }
  .project-head .name { font-weight: 600; font-size: 1.05em; overflow-wrap: anywhere; }
  .project-head .rel { overflow-wrap: anywhere; }
  .project-head .flags { display: flex; flex-wrap: wrap; gap: 4px; margin-left: auto; }
  /* 項目は 1 つ 240px を目安に、幅に合わせて段数が変わる。検証は長くなるので常に 1 段まるごと使う */
  .fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 6px 16px; margin: 0; }
  .field { min-width: 0; }
  .field.wide { grid-column: 1 / -1; }
  .field dt { font-size: .85em; color: var(--vscode-descriptionForeground); }
  .field dd { margin: 0; overflow-wrap: anywhere; }
  .field dd button.action { vertical-align: middle; }
  .ops { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--vscode-panel-border); }
  /* 行末のボタンは 2 つのメニューにまとめる。開いたメニューは外を押すか Esc で閉じる */
  .menu { position: relative; }
  .menu > summary {
    list-style: none; display: inline-flex; align-items: center; min-height: 24px; padding: 2px 12px; line-height: 1.3;
    background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground);
    border: 1px solid var(--vscode-button-border, rgba(128, 128, 128, .4)); border-radius: 3px;
    box-shadow: 0 1px 1px rgba(0, 0, 0, .25); cursor: pointer; user-select: none;
  }
  .menu > summary:hover, .menu[open] > summary { background: var(--vscode-button-secondaryHoverBackground); border-color: var(--vscode-focusBorder); }
  .menu > summary:focus-visible { outline: 1px solid var(--vscode-focusBorder); outline-offset: 1px; }
  .menu > summary::-webkit-details-marker { display: none; }
  .menu > .menu-items {
    position: absolute; z-index: 5; top: 100%; left: 0; margin-top: 2px; min-width: 100%;
    display: flex; flex-direction: column; gap: 2px; padding: 4px;
    background: var(--vscode-editorWidget-background); border: 1px solid var(--vscode-widget-border, var(--vscode-panel-border));
    border-radius: 3px; box-shadow: 0 2px 8px var(--vscode-widget-shadow, rgba(0, 0, 0, .3));
  }
  .menu > .menu-items > button.action { justify-content: flex-start; border-color: var(--vscode-contrastBorder, transparent); box-shadow: none; background: none; }
  .menu > .menu-items > button.action:hover:not(:disabled) { background: var(--vscode-list-hoverBackground); }
  /* HC では押せない項目も点線の枠で「在るが押せない」と分かるようにする */
  .menu > .menu-items > button.action:disabled { border-style: dashed; }
  .badge { font-size: .82em; padding: 0 6px; border-radius: 999px; border: 1px solid var(--vscode-panel-border); white-space: nowrap; }
  .badge.doing { color: var(--vscode-charts-yellow); border-color: var(--vscode-charts-yellow); }
  .badge.warn { color: var(--vscode-editorWarning-foreground); border-color: var(--vscode-editorWarning-foreground); }
  .badge.error { color: var(--vscode-editorError-foreground); border-color: var(--vscode-editorError-foreground); }
  .lint { list-style: none; margin: 0; padding: 0; font-size: .88em; }
  .lint li { overflow-wrap: anywhere; }
  .lint li.warn { color: var(--vscode-editorWarning-foreground); }
  .lint li.error { color: var(--vscode-editorError-foreground); }
  .self-rules { display: flex; flex-wrap: wrap; gap: 4px 8px; align-items: center; overflow-wrap: anywhere; }
  .stray-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
`;
