/**
 * フロー編集画面の 1 枚の HTML を組み立てる。中身（帯・部品箱・図・欄）を作るのは Webview 側の
 * React（`src/webview/flow/`）で、ここが作るのはその入れ物だけ。フェーズ管理（`phases-render.ts`）と同じ作り。
 *
 * バンドルした画面のスクリプトと CSS は `<script nonce>` / `<style nonce>` に文字列として流し込み、
 * ファイルとしては読ませない（`localResourceRoots` は空のまま）。
 * **この入れ物は 1 度しか入らない**（`retainedHost`）。画面は編集の途中を持つので、入れ直すと打ちかけの内容が消える。
 */
import { type Appearance, bodyTag } from "./appearance.js";
import { DATA_ID, embedData, type FlowData } from "./flow-view.js";
import { loadingMarkup } from "./loading-render.js";

export interface RenderOptions {
  readonly nonce: string;
  /** バンドルした画面のスクリプト（`out/webview/flow.js` の中身） */
  readonly script: string;
  /** バンドルした画面の CSS（`out/webview/flow.css` の中身） */
  readonly style: string;
  readonly appearance?: Appearance;
}

export function renderFlowPage(data: FlowData, options: RenderOptions): string {
  const { nonce } = options;
  return `<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ccnavi フロー編集</title>
<style nonce="${nonce}">
${options.style}
</style>
</head>
${bodyTag(options.appearance)}
<div id="root">${loadingMarkup("フロー")}</div>
<script type="application/json" id="${DATA_ID}">${embedData(data)}</script>
<script nonce="${nonce}">
${options.script}
</script>
</body>
</html>
`;
}
