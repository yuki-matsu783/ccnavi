/**
 * 束ねた画面（React）と、その CSS が、外へ出る口を持っていないこと。
 *
 * 入れ物の HTML は CSP（`default-src 'none'`）で守られていて、各画面のテストが「外の資源を
 * 指す綴りが無い」ことを見る。**束ねたものはその検査から外してある**（React の本番ビルドが
 * 自分の説明ページの URL を文字列で持っているため）。外した代わりに、ここで「本当に外へ出る
 * 呼び出し」だけを名前で見る。拡張の画面は実行ファイルの答えを見せるだけで、自分で外を見に
 * 行かない（CLAUDE.md の実行ファイルの境界、ADR-0035）。
 *
 * CSS も同じで、束ねた 1 本が `<style nonce>` に入る（ADR-0066）。CSS から外へ出る口は
 * `url()` と `@import`（束ねに入らなかったもの）なので、そこを見る。
 *
 * 見つかったら、その画面が何を読もうとしているかを確かめる。束ねたものは機械が書くので、
 * 目で読んで気づくことは期待できない。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { screenScript, screenStyle } from "../helpers/bundle.js";

/** 画面の名前。足したら、ここに 1 行足す（束ねの出口の綴りがそのまま名前） */
const SCREENS = ["board", "projects", "risk", "phases", "rules"] as const;

/** 外へ出る口。名前で見るだけなので、綴りを変えて呼ぶ道までは塞げない */
const OUTSIDE = [/\bfetch\s*\(/, /XMLHttpRequest/, /\bWebSocket\b/, /sendBeacon/, /\bimportScripts\b/, /EventSource/, /new\s+Image\s*\(/];

test("CB-T157 束ねた画面は、外へ出る呼び出しを持たない", () => {
  for (const name of SCREENS) {
    const script = screenScript(name);
    for (const pattern of OUTSIDE) {
      assert.doesNotMatch(script, pattern, `${name} の画面が ${String(pattern)} を持っている`);
    }
  }
});

test("CB-T167 束ねた CSS は、外の資源を読まない（url() と残った @import が無い）", () => {
  for (const name of SCREENS) {
    const style = screenStyle(name);
    // 画像やフォントを読む口。VS Code のテーマ変数（var(--vscode-*)）だけで組む方針
    assert.doesNotMatch(style, /url\s*\(/, `${name} の CSS が url() を持っている`);
    // 束ねに入らなかった @import は、配ったあとに外へ読みに行く（CSP が止めるが、見た目は抜ける）
    assert.doesNotMatch(style, /@import/, `${name} の CSS に束ねられなかった @import が残っている`);
    assert.doesNotMatch(style, /https?:\/\//, `${name} の CSS が外の綴りを持っている`);
  }
});
