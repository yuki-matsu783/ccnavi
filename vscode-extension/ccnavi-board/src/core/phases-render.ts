/**
 * フェーズ管理画面の 1 枚の HTML を組み立てる。中身（帯・注意・種類の一覧）を作るのは
 * Webview 側の React（`src/webview/phases/`）で、ここが作るのはその入れ物だけ。
 *
 * 外部資源に依存しない 1 枚にする方針は変えていない。束ねた画面のスクリプトは
 * `<script nonce>` に文字列として流し込み、ファイルとしては読ませない（`localResourceRoots` は空のまま）。
 * 最初に見せる中身は `<script type="application/json">` に埋める。
 *
 * **この入れ物は 1 度しか入らない**（`retainedHost`、ADR-0062）。画面は編集の途中を持つので、
 * 入れ直すと打ちかけの内容が消える。2 枚目からは拡張ホストが `postMessage` で渡す（phases-panel）。
 *
 * CSS も画面の側の持ち物で、部品と同じ置き場にある（`src/webview/phases/*.css`。5 画面共通のぶんは
 * `src/webview/styles/`）。それを束ねた 1 本（`out/webview/phases.css`）を、ここが `<style nonce>` に
 * 流し込む。挿すのがここなのは、Webview の CSP が nonce を持つ `<style>` しか通さず、nonce を作るのが
 * 入れ物を組む側だから（ADR-0066）。
 */
import { type Appearance, bodyTag } from "./appearance.js";
import { loadingMarkup } from "./loading-render.js";
import { DATA_ID, embedData, type PhasesData } from "./phases-view.js";

export interface RenderOptions {
  readonly nonce: string;
  /** 束ねた画面のスクリプト（`out/webview/phases.js` の中身）。拡張は起動時に 1 度読む */
  readonly script: string;
  /** 束ねた画面の CSS（`out/webview/phases.css` の中身）。拡張は起動時に 1 度読む */
  readonly style: string;
  /** 見た目。無ければ VS Code のテーマに従う */
  readonly appearance?: Appearance;
}

/** 入れ物の HTML。`data` はそのまま画面の最初の 1 枚になる */
export function renderPhasesPage(data: PhasesData, options: RenderOptions): string {
  const { nonce } = options;
  return `<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ccnavi フェーズ管理</title>
<style nonce="${nonce}">
${options.style}
</style>
</head>
${bodyTag(options.appearance)}
<div id="root">${loadingMarkup("フェーズ")}</div>
<script type="application/json" id="${DATA_ID}">${embedData(data)}</script>
<script nonce="${nonce}">
${options.script}
</script>
</body>
</html>
`;
}
