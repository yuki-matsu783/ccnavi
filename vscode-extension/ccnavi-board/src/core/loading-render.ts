/**
 * 中身を読み込んでいる間だけ見せる 1 枚の HTML。5 画面が共通で使う。
 *
 * 画面を開くと、中身を読む前にタブを作ってこれを入れる。実行ファイルへの問い合わせ（ボード・
 * プロジェクト管理・層の置き場）は数秒かかることがあり、読み終えてからタブを作ると、押しても
 * 何も起きないように見えて押し直され、同じ種類のタブが 2 枚開く道にもなっていた。
 *
 * **これは段取り（`screen-host.ts`）を通さない。** パネルが `webview.html` に直に入れる。段取りは
 * 入れ物をまだ 1 枚も入れていないつもりのままなので、読み終えて最初に渡す中身は必ず入れ物ごと
 * （`rebuilt`）になり、この 1 枚と入れ替わる。スクリプトは持たず、`ready` も送らない。
 *
 * CSS はその画面の束ね（`out/webview/<名前>.css`）をそのまま流し込む。地の色と見た目の切り替え
 * （body のクラス）が、読み終えた後の画面と同じになる。
 */
import { type Appearance, bodyTag } from "./appearance.js";

export interface LoadingOptions {
  readonly nonce: string;
  /** その画面の束ねた CSS */
  readonly style: string;
  /** 見た目。無ければ VS Code のテーマに従う */
  readonly appearance?: Appearance;
}

/** 読み込み中の一言。`what` は読むもの（「チケット」「web のルール」）。5 画面と切り替え中の表示で綴りを揃える */
export function loadingText(what: string): string {
  return `${what}を読み込み中…`;
}

/**
 * 読み込み中の一言を段落にしたもの（HTML として逃がす）。この 1 枚の本文のほか、各画面の入れ物の
 * `<div id="root">` にも入れる。入れ物を入れてから束ねた画面が組み上がるまでの間、白いままにしないため。
 * 組み上がると React が中身を入れ替えるので、残らない
 */
export function loadingMarkup(what: string): string {
  return `<p class="empty" id="ccnavi-loading">${escapeHtml(loadingText(what))}</p>`;
}

/** 読み込み中の 1 枚。`title` は `<title>` に、`what` は本文の一言に出す（どちらも HTML として逃がす） */
export function renderLoadingPage(title: string, what: string, options: LoadingOptions): string {
  const { nonce } = options;
  return `<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; style-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${escapeHtml(title)}</title>
<style nonce="${nonce}">
${options.style}
</style>
</head>
${bodyTag(options.appearance)}
${loadingMarkup(what)}
</body>
</html>
`;
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
