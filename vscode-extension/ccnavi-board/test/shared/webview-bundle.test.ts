/**
 * 束ねた画面（React）と、その CSS が、外へ出る口を持っていないこと。
 *
 * 入れ物の HTML は CSP（`default-src 'none'`）で守られていて、各画面のテストが「外の資源を
 * 指す綴りが無い」ことを見る。**束ねたものはその検査から外してある**（React の本番ビルドが
 * 自分の説明ページの URL を文字列で持っているため）。外した代わりに、ここで「本当に外へ出る
 * 呼び出し」だけを名前で見る。拡張の画面は実行ファイルの答えを見せるだけで、自分で外を見に
 * 行かない（docs/claude/exe-boundary.md、ADR-0035）。
 *
 * CSS も同じで、束ねた 1 本が `<style nonce>` に入る（ADR-0066）。CSS から外へ出る口は
 * `url()` と `@import`（束ねに入らなかったもの）なので、そこを見る。
 *
 * 見つかったら、その画面が何を読もうとしているかを確かめる。束ねたものは機械が書くので、
 * 目で読んで気づくことは期待できない。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { screenNames, screenScript, screenStyle } from "../helpers/bundle.js";

/** 外へ出る口。名前で見るだけなので、綴りを変えて呼ぶ道までは塞げない */
const OUTSIDE = [/\bfetch\s*\(/, /XMLHttpRequest/, /\bWebSocket\b/, /sendBeacon/, /\bimportScripts\b/, /EventSource/, /new\s+Image\s*\(/];

test("CB-T157 束ねた画面は、外へ出る呼び出しを持たない", () => {
  const names = screenNames();
  assert.ok(names.length >= 5, `画面を数えられていない（${names.join(" ")}）`);
  for (const name of names) {
    const script = screenScript(name);
    for (const pattern of OUTSIDE) {
      assert.doesNotMatch(script, pattern, `${name} の画面が ${String(pattern)} を持っている`);
    }
  }
});

test("CB-T167 束ねた CSS は、外の資源を読まない（url() と残った @import が無い）", () => {
  for (const name of screenNames()) {
    const style = screenStyle(name);
    // 画像やフォントを読む口。VS Code のテーマ変数（var(--vscode-*)）だけで組む方針
    assert.doesNotMatch(style, /url\s*\(/, `${name} の CSS が url() を持っている`);
    // 束ねに入らなかった @import は、配ったあとに外へ読みに行く（CSP が止めるが、見た目は抜ける）
    assert.doesNotMatch(style, /@import/, `${name} の CSS に束ねられなかった @import が残っている`);
    assert.doesNotMatch(style, /https?:\/\//, `${name} の CSS が外の綴りを持っている`);
  }
});

/**
 * 束ねた画面が、CSP に止められるやり方でスタイルを当てていないこと。
 *
 * 画面の CSP は `style-src 'nonce-…'` だけで `'unsafe-inline'` が無い。この下では
 * **`style` 属性（`setAttribute("style", …)` を含む）は黙って無効になる**。例外は投げず、
 * コンソールに出るだけなので、配ってから「幅が効かない」「図が動かない」で気づくことになる。
 * 通るのは CSSOM（`el.style.x = …` / `setProperty`）のほうで、React の style プロップも
 * React Flow の viewport の transform もそちらを通る（Chromium で実測。ADR-0070）。
 *
 * これは**外から来た部品の版が上がったときに気づくための検査**で、いまの版がそうだという
 * 確認ではない。増えたら、その部品が何をしているかを見てから通す。
 *
 * 見るのは `style` 属性を立てるやり方だけ。`el.style.cssText = …` は**通る**（Chromium で実測。
 * CSSOM なので属性の禁止に当たらない）ので、ここでは見ない。`dangerouslySetInnerHTML` も見ない。
 * react-dom が属性の対応表に綴りを持っているだけで、5 画面とも当たってしまう。
 *
 * 属性名を実行時に組み立てる書き方（`el.setAttribute(name, value)`）は、綴りでは捕まらない。
 * 名前で見るだけの検査で、そこまでは塞げない。
 */
const CSP_BLOCKED = [/setAttribute\(\s*["']style["']/];

test("CB-T190 束ねた画面は、CSP に止められるやり方でスタイルを当てない", () => {
  for (const name of screenNames()) {
    const script = screenScript(name);
    for (const pattern of CSP_BLOCKED) {
      assert.doesNotMatch(script, pattern, `${name} の画面が ${String(pattern)} を使っている（style-src に 'unsafe-inline' が無いので黙って効かない）`);
    }
  }
});
