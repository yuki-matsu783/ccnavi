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

/**
 * 見た目の送り先。画面に中身を渡す段取り（`ScreenHost`）の `post` だけを使う形。
 * `ScreenHost<D>` はそのまま渡せる（`post` は `unknown` を取り、`boolean` を返す）。
 */
export interface AppearanceSink {
  /** 生きている画面にだけ届く。届いたら真、落ちるので送らなかったら偽 */
  post(message: AppearanceMessage): boolean;
}

/**
 * 見た目を画面へ送る。**段取りを通す**ので、組み上がっていない画面と捨てられた画面には送らない
 * （偽が返る）。落ちたぶんを持ち越す必要は無い。どちらの道でも、後からいまの値が渡るため。
 *
 * - 入れ物ごと入れ直す道（`rebuilt`）では、組む側が `appearance` を HTML に埋める（`bodyTag`）
 * - 画面が組み上がった（`ready`）ところで、呼ぶ側が送り直す
 *
 * 直に `webview.postMessage` を叩くと、この 2 つのどちらも通らない画面へ送ることになり、
 * 「送ったつもりで落ちている」が段取りの外に残る。
 */
export function sendAppearance(sink: AppearanceSink, value: Appearance): boolean {
  return sink.post({ type: "appearance", value });
}
