/**
 * 画面の見た目。既定は VS Code のテーマ変数に従う（`vscode`）。利用者が選べば、5 画面の地・枠・文字を
 * Claude の配色（ライト / ダーク）に置き換える。置き換えは body のクラスで切り替え、CSS は
 * `--vscode-*` の変数を上書きするだけなので、各画面の部品はテーマ変数を読むまま変わらない。
 *
 * ハイコントラストのテーマでは `--vscode-contrastBorder` などが定義されていて、それを使う枠や点線は
 * Claude の配色を選んでもそのまま効く。
 *
 * 配色そのもの（変数の上書き）は画面の側の `src/webview/styles/appearance.css` にあり、ここが持つのは
 * 設定の値・body に付けるクラス・送るメッセージの形だけ。付け替えるのは `src/webview/appearance.ts`。
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
