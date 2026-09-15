/**
 * 画面の見た目。既定は VS Code のテーマ変数に従う（`vscode`）。利用者が選べば、5 画面の地・枠・文字を
 * Claude の配色（ライト / ダーク）に置き換える。置き換えは body のクラスで切り替え、CSS は
 * `--vscode-*` の変数を上書きするだけなので、各画面の部品はテーマ変数を読むまま変わらない。
 *
 * ハイコントラストのテーマでは `--vscode-contrastBorder` などが定義されていて、それを使う枠や点線は
 * Claude の配色を選んでもそのまま効く。
 */

export const APPEARANCES = ["vscode", "claude-light", "claude-dark"] as const;
export type Appearance = (typeof APPEARANCES)[number];

export const DEFAULT_APPEARANCE: Appearance = "vscode";

export const APPEARANCE_LABELS: Readonly<Record<Appearance, string>> = {
  vscode: "VS Code のテーマに従う",
  "claude-light": "Claude ライト",
  "claude-dark": "Claude ダーク",
};

/** 設定の値を読む。知らない値と型違いは既定 */
export function parseAppearance(value: unknown): Appearance {
  return typeof value === "string" && (APPEARANCES as readonly string[]).includes(value) ? (value as Appearance) : DEFAULT_APPEARANCE;
}

/** body に付けるクラス。`vscode` なら無し */
export function appearanceClass(appearance: Appearance): string {
  return appearance === "vscode" ? "" : `ccnavi-${appearance}`;
}

/** 各画面の `<body>`。クラスが要るときだけ付ける（テストが `<body>` の文字列を見るので、無いときは素のまま） */
export function bodyTag(appearance: Appearance | undefined): string {
  const cls = appearanceClass(appearance ?? DEFAULT_APPEARANCE);
  return cls === "" ? "<body>" : `<body class="${cls}">`;
}

/** 拡張から画面へ送るメッセージ。開いている画面は再描画せずに切り替える */
export interface AppearanceMessage {
  readonly type: "appearance";
  readonly value: Appearance;
}

/**
 * Claude の配色。VS Code のテーマ変数を body のクラスの下で上書きする。
 * 意味の色（error / warning / charts の緑と黄）はテーマのまま。青系の強調だけ橙に寄せる。
 */
export const APPEARANCE_STYLE = `  body.ccnavi-claude-dark {
    --vscode-editor-background: #262624; --vscode-editor-foreground: #E8E6DF; --vscode-foreground: #E8E6DF; --vscode-descriptionForeground: #A8A69E;
    --vscode-panel-border: #3E3D39; --vscode-widget-border: #4A4844; --vscode-editorWidget-background: #30302E; --vscode-editorWidget-border: #4A4844;
    --vscode-list-hoverBackground: #35342F; --vscode-focusBorder: #D97757;
    --vscode-button-background: #D97757; --vscode-button-foreground: #ffffff; --vscode-button-hoverBackground: #C4633F;
    --vscode-button-secondaryBackground: #3A3936; --vscode-button-secondaryForeground: #E8E6DF; --vscode-button-secondaryHoverBackground: #45443F; --vscode-button-border: rgba(255, 255, 255, .06);
    --vscode-input-background: #1F1E1D; --vscode-input-foreground: #E8E6DF; --vscode-input-border: #4A4844; --vscode-input-placeholderForeground: #8A887F;
    --vscode-dropdown-background: #1F1E1D; --vscode-dropdown-foreground: #E8E6DF; --vscode-dropdown-border: #4A4844;
    --vscode-textCodeBlock-background: #1F1E1D; --vscode-charts-blue: #D97757; --vscode-editorInfo-foreground: #D97757;
  }
  body.ccnavi-claude-light {
    --vscode-editor-background: #FAF9F5; --vscode-editor-foreground: #141413; --vscode-foreground: #141413; --vscode-descriptionForeground: #6B6A64;
    --vscode-panel-border: #E0DED4; --vscode-widget-border: #D5D3C9; --vscode-editorWidget-background: #F0EEE6; --vscode-editorWidget-border: #D5D3C9;
    --vscode-list-hoverBackground: #EBE9E0; --vscode-focusBorder: #D97757;
    --vscode-button-background: #D97757; --vscode-button-foreground: #ffffff; --vscode-button-hoverBackground: #C4633F;
    --vscode-button-secondaryBackground: #EDEBE3; --vscode-button-secondaryForeground: #141413; --vscode-button-secondaryHoverBackground: #E3E1D7; --vscode-button-border: rgba(0, 0, 0, .08);
    --vscode-input-background: #FFFFFF; --vscode-input-foreground: #141413; --vscode-input-border: #D5D3C9; --vscode-input-placeholderForeground: #8A887F;
    --vscode-dropdown-background: #FFFFFF; --vscode-dropdown-foreground: #141413; --vscode-dropdown-border: #D5D3C9;
    --vscode-textCodeBlock-background: #F0EEE6; --vscode-charts-blue: #D97757; --vscode-editorInfo-foreground: #D97757;
    --vscode-editorWarning-foreground: #9A6700; --vscode-editorError-foreground: #C0392B; --vscode-charts-green: #3F7D2A; --vscode-charts-yellow: #9A6700;
  }
  body.ccnavi-claude-dark .summary .warn, body.ccnavi-claude-light .summary .warn,
  body.ccnavi-claude-dark .badge.copy-none, body.ccnavi-claude-light .badge.copy-none { color: #D97757; }
  body.ccnavi-claude-dark .card.pending, body.ccnavi-claude-light .card.pending { border-left-color: #D97757; }
  body.ccnavi-claude-dark .sum .sum-flag.on, body.ccnavi-claude-light .sum .sum-flag.on { background: #D97757; }`;

/**
 * 画面の中のスクリプトに埋める、切り替えの受け口。拡張が `{ type: "appearance", value }` を送ると
 * body のクラスだけを付け替える。HTML を作り直さないので、編集中の内容は消えない。
 * テンプレート文字列に埋めるので、バッククォートと \${ を使わない。
 */
export const APPEARANCE_SCRIPT = `  function applyAppearance(value) {
    for (const name of Array.from(document.body.classList)) {
      if (name.indexOf("ccnavi-claude-") === 0) { document.body.classList.remove(name); }
    }
    if (value === "claude-light" || value === "claude-dark") { document.body.classList.add("ccnavi-" + value); }
  }
  window.addEventListener("message", (event) => {
    const data = event.data || {};
    if (data.type === "appearance") { applyAppearance(data.value); }
  });`;
