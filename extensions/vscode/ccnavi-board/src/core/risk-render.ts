/**
 * リスク管理画面の1枚のHTMLを組み立てる。中身（帯・境目の点の欄・項目の行）を作るのは
 * Webview側のReact（`src/webview/risk/`）で、ここが作るのはその入れ物だけ。
 *
 * 外部資源に依存しない1枚にする方針は変えていない。バンドルした画面のスクリプトは
 * `<script nonce>`に文字列として流し込み、ファイルとしては読ませない（`localResourceRoots`は空のまま）。
 * 最初に見せる中身は`<script type="application/json">`に埋める。
 *
 * この入れ物は1度しか入らない（`retainedHost`）。画面は編集の途中を持つので、
 * 入れ直すと打ちかけの内容が消える。2枚目からは拡張ホストが`postMessage`で渡す（risk-panel）。
 *
 * CSSも画面の側の持ち物で、部品と同じ置き場にある（`src/webview/risk/*.css`。6画面共通のぶんは
 * `extensions/shared/styles/`と配色の`src/webview/styles/appearance.css`）。それをバンドルした1本（`out/webview/risk.css`）を、ここが`<style nonce>`に
 * 流し込む。挿すのがここなのは、WebviewのCSPがnonceを持つ`<style>`しか通さず、nonceを作るのが
 * 入れ物を組む側だから。
 */
import { type Appearance, bodyTag } from "./appearance.js";
import { loadingMarkup } from "./loading-render.js";
import { DATA_ID, embedData, type RiskData } from "./risk-view.js";

export interface RenderOptions {
  readonly nonce: string;
  /** バンドルした画面のスクリプト（`out/webview/risk.js`の中身）。拡張は起動時に1度読む */
  readonly script: string;
  /** バンドルした画面のCSS（`out/webview/risk.css`の中身）。拡張は起動時に1度読む */
  readonly style: string;
  /** 見た目。無ければVS Codeのテーマに従う */
  readonly appearance?: Appearance;
}

/** 入れ物のHTML。`data`はそのまま画面の最初の1枚になる */
export function renderRiskPage(data: RiskData, options: RenderOptions): string {
  const { nonce } = options;
  return `<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ccnaviリスク管理</title>
<style nonce="${nonce}">
${options.style}
</style>
</head>
${bodyTag(options.appearance)}
<div id="root">${loadingMarkup("リスク")}</div>
<script type="application/json" id="${DATA_ID}">${embedData(data)}</script>
<script nonce="${nonce}">
${options.script}
</script>
</body>
</html>
`;
}
