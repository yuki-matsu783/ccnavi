/**
 * 取り込み待ち画面の 1 枚の HTML を組み立てる。中身（帯・対象の一覧・取り込みの結果）を作るのは
 * Webview 側の React（`src/webview/handoff/`）で、ここが作るのはその入れ物だけ。
 *
 * 外部資源に依存しない 1 枚にする方針は他の画面と同じ。バンドルした画面のスクリプトは
 * `<script nonce>` に文字列として流し込み、ファイルとしては読ませない（`localResourceRoots` は空のまま）。
 * 最初に見せる中身は `<script type="application/json">` に埋める。
 *
 * この入れ物は 1 度しか入らない（`retainedHost`）。画面は選択の途中を持つので、入れ直すと選びかけた内容が消える。
 * 2 枚目からは拡張ホストが `postMessage` で渡す（handoff-panel）。
 *
 * CSS も画面の側の持ち物で、部品と同じ置き場にある（`src/webview/handoff/*.css`）。それをバンドルした 1 本
 * （`out/webview/handoff.css`）を、ここが `<style nonce>` に流し込む。
 */
import { type Appearance, bodyTag } from "./appearance.js";
import { DATA_ID, embedData, type HandoffData } from "./handoff-view.js";
import { loadingMarkup } from "./loading-render.js";

export interface RenderOptions {
  readonly nonce: string;
  /** バンドルした画面のスクリプト（`out/webview/handoff.js` の中身）。拡張は起動時に 1 度読む */
  readonly script: string;
  /** バンドルした画面の CSS（`out/webview/handoff.css` の中身）。拡張は起動時に 1 度読む */
  readonly style: string;
  /** 見た目。無ければ VS Code のテーマに従う */
  readonly appearance?: Appearance;
}

/** 入れ物の HTML。`data` はそのまま画面の最初の 1 枚になる */
export function renderHandoffPage(data: HandoffData, options: RenderOptions): string {
  const { nonce } = options;
  return `<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ccnavi 取り込み待ち</title>
<style nonce="${nonce}">
${options.style}
</style>
</head>
${bodyTag(options.appearance)}
<div id="root">${loadingMarkup("取り込み待ち")}</div>
<script type="application/json" id="${DATA_ID}">${embedData(data)}</script>
<script nonce="${nonce}">
${options.script}
</script>
</body>
</html>
`;
}
