/**
 * フロー編集画面の 1 枚の HTML を組み立てる。中身（帯・部品箱・図・欄）を作るのは Webview 側の
 * React（`src/webview/flow/`）で、ここが作るのはその入れ物だけ。フェーズ管理（`phases-render.ts`）と同じ作り。
 *
 * 束ねた画面のスクリプトと CSS は `<script nonce>` / `<style nonce>` に文字列として流し込み、
 * ファイルとしては読ませない（`localResourceRoots` は空のまま。ADR-0066）。
 * **この入れ物は 1 度しか入らない**（`retainedHost`、ADR-0062）。
 */
import { type Appearance, bodyTag } from "./appearance.js";
import { DATA_ID, embedData, type FlowData } from "./flow-view.js";
import { loadingMarkup } from "./loading-render.js";

export interface RenderOptions {
  readonly nonce: string;
  /** 束ねた画面のスクリプト（`out/webview/flow.js` の中身） */
  readonly script: string;
  /** 束ねた画面の CSS（`out/webview/flow.css` の中身） */
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
