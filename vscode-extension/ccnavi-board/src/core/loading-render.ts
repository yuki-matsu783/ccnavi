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

/** 読み込み中の 1 枚。`title` は `<title>` と本文の両方に出す（HTML として逃がす） */
export function renderLoadingPage(title: string, options: LoadingOptions): string {
  const { nonce } = options;
  const text = escapeHtml(title);
  return `<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; style-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${text}</title>
<style nonce="${nonce}">
${options.style}
</style>
</head>
${bodyTag(options.appearance)}
<p class="empty" id="ccnavi-loading">${text}を読み込んでいる…</p>
</body>
</html>
`;
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
