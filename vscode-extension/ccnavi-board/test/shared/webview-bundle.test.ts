/**
 * 束ねた画面（React）が、外へ出る口を持っていないこと。
 *
 * 入れ物の HTML は CSP（`default-src 'none'`）で守られていて、各画面のテストが「外の資源を
 * 指す綴りが無い」ことを見る。**束ねたものはその検査から外してある**（React の本番ビルドが
 * 自分の説明ページの URL を文字列で持っているため）。外した代わりに、ここで「本当に外へ出る
 * 呼び出し」だけを名前で見る。拡張の画面は実行ファイルの答えを見せるだけで、自分で外を見に
 * 行かない（CLAUDE.md の実行ファイルの境界、ADR-0035）。
 *
 * 見つかったら、その画面が何を読もうとしているかを確かめる。束ねたものは機械が書くので、
 * 目で読んで気づくことは期待できない。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { boardScript } from "../helpers/board.js";
import { projectsScript } from "../helpers/projects.js";
import { riskScript } from "../helpers/risk.js";
import { phasesScript } from "../helpers/phases.js";

/** 外へ出る口。名前で見るだけなので、綴りを変えて呼ぶ道までは塞げない */
const OUTSIDE = [/\bfetch\s*\(/, /XMLHttpRequest/, /\bWebSocket\b/, /sendBeacon/, /\bimportScripts\b/, /EventSource/, /new\s+Image\s*\(/];

test("CB-T157 束ねた画面は、外へ出る呼び出しを持たない", () => {
  for (const [name, script] of [
    ["board", boardScript()],
    ["projects", projectsScript()],
    ["risk", riskScript()],
    ["phases", phasesScript()],
  ] as const) {
    for (const pattern of OUTSIDE) {
      assert.doesNotMatch(script, pattern, `${name} の画面が ${String(pattern)} を持っている`);
    }
  }
});
