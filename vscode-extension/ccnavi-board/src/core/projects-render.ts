/**
 * プロジェクト管理画面の 1 枚の HTML を組み立てる。中身（帯・カード・メニュー・clone の欄）を作るのは
 * Webview 側の React（`src/webview/projects/`）で、ここが作るのはその入れ物だけ。
 *
 * 外部資源に依存しない 1 枚にする方針は変えていない。束ねた画面のスクリプトは
 * `<script nonce>` に文字列として流し込み、ファイルとしては読ませない（`localResourceRoots` は空のまま）。
 * 最初に見せる中身は `<script type="application/json">` に埋める。2 枚目からは HTML を作り直さず、
 * 拡張ホストが `postMessage` で渡す（projects-panel）。
 *
 * CSS は画面の側の持ち物（`projects-style.ts`。5 画面共通のぶんは `styles.ts`）で、ここは
 * それを `<style nonce>` に流し込むだけ。
 */
import { type Appearance, bodyTag } from "./appearance.js";
import { PROJECTS_STYLE } from "./projects-style.js";
import { DATA_ID, embedData, type ProjectsData } from "./projects-view.js";

export interface RenderOptions {
  readonly nonce: string;
  /** 束ねた画面のスクリプト（`out/webview/projects.js` の中身）。拡張は起動時に 1 度読む */
  readonly script: string;
  /** 見た目。無ければ VS Code のテーマに従う */
  readonly appearance?: Appearance;
}

/**
 * 入れ物の HTML。`data` はそのまま画面の最初の 1 枚になる。
 *
 * nonce は呼ぶたびに変える。同じ文字列を `webview.html` に入れても VS Code は何もしないので、
 * 作り直したいときに作り直せなくなる。
 */
export function renderProjectsPage(data: ProjectsData, options: RenderOptions): string {
  const { nonce } = options;
  return `<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ccnavi プロジェクト管理</title>
<style nonce="${nonce}">
${PROJECTS_STYLE}
</style>
</head>
${bodyTag(options.appearance)}
<div id="root"></div>
<script type="application/json" id="${DATA_ID}">${embedData(data)}</script>
<script nonce="${nonce}">
${options.script}
</script>
</body>
</html>
`;
}
