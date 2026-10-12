/**
 * 計画編集タブの 1 枚の HTML を組み立てる。中身（帯・図・理由・保存の確かめ）を作るのは Webview 側の
 * React（`src/webview/workflow/`）で、ここが作るのはその入れ物だけ。フロー編集（`flow-render.ts`）と同じ作り。
 *
 * バンドルした画面のスクリプトと CSS は `<script nonce>` / `<style nonce>` に文字列として流し込み、
 * ファイルとしては読ませない（`localResourceRoots` は空のまま）。
 * **この入れ物は 1 度しか入らない**（`retainedHost`）。画面は線の引き直しの途中を持つので、入れ直すと未保存の線が消える。
 */
import { type Appearance, bodyTag } from "./appearance.js";
import { DATA_ID, embedData, type WorkflowData } from "./workflow-view.js";
import { loadingMarkup } from "./loading-render.js";

export interface RenderOptions {
  readonly nonce: string;
  /** バンドルした画面のスクリプト（`out/webview/workflow.js` の中身） */
  readonly script: string;
  /** バンドルした画面の CSS（`out/webview/workflow.css` の中身） */
  readonly style: string;
  readonly appearance?: Appearance;
}

export function renderWorkflowPage(data: WorkflowData, options: RenderOptions): string {
  const { nonce } = options;
  return `<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ccnavi 計画編集</title>
<style nonce="${nonce}">
${options.style}
</style>
</head>
${bodyTag(options.appearance)}
<div id="root">${loadingMarkup("計画編集")}</div>
<script type="application/json" id="${DATA_ID}">${embedData(data)}</script>
<script nonce="${nonce}">
${options.script}
</script>
</body>
</html>
`;
}
