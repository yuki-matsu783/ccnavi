/**
 * 絞り込みの比べ方（`webview/shared/find.ts`）と、保存後に出すwarnの行の取り出し（`core/lintmodel.ts`）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { MAX_WARN_LINES, errorLinesOf, truncateLines, warnLinesOf } from "../../src/core/lintmodel.js";
import { foldFind, moveTitle } from "../../src/webview/shared/find.js";
import { findText as ruleFind } from "../../src/webview/rules/text.js";
import { findText as phaseFind } from "../../src/webview/phases/text.js";
import { findText as riskFind } from "../../src/webview/risk/text.js";
import type { RuleForm } from "../../src/core/rules-view.js";
import type { PhaseForm } from "../../src/core/phases-view.js";
import type { FactorForm } from "../../src/core/risk-view.js";

// 各画面の`state.ts`はVS CodeのAPIを読み込むので、テストでは同じ形の値を直に書く
const emptyRule = (): RuleForm => ({ origin: null, id: "", match: "", kind: "glob", pattern: "", message: "", additionalContext: "", additionalContextOnce: "", additionalContextFile: "", additionalContextOnceFile: "", every: "" });
const emptyPhase = (): PhaseForm => ({ origin: null, id: "", title: "", kind: "work", review: true, inherit: true, scope: [], deliverables: [], agent: "", skill: "", when: "" });
const emptyFactor = (): FactorForm => ({ origin: null, id: "", points: "", kind: "lines_over", value: "", max: "", include: "", exclude: "", message: "" });

test("CB-T1238 絞り込みはNFKCで正規化して小文字にする。全角英数字・半角カナも同じに探せる", () => {
  assert.equal(foldFind("ＡＢＣ-ｇｉｔ"), "abc-git");
  assert.equal(foldFind("ﾃｽﾄ"), "テスト");
  assert.ok(ruleFind({ ...emptyRule(), id: "Git-Push" }).includes(foldFind("ＧＩＴ")));
  assert.ok(phaseFind({ ...emptyPhase(), id: "design" }).includes(foldFind("ＤＥＳＩＧＮ")));
  assert.ok(riskFind({ ...emptyFactor(), id: "Big-Change" }).includes(foldFind("ＢＩＧ")));
});

test("CB-T1239 絞り込み中の上下ボタンのtitleは理由を言う。そうでなければ向きだけ", () => {
  assert.equal(moveTitle("上へ", false), "上へ");
  assert.equal(moveTitle("下へ", false), "下へ");
  assert.match(moveTitle("上へ", true), /絞り込み中は並べ替えできません/);
});

test("CB-T1240 --lintの報告からwarnの行だけを取り出し、一時ファイルのパスを対象のパスに直す", () => {
  const report = [
    "ccnavi: 設定を検証する",
    "  ルール: /tmp/x/rules.yml",
    "warn: no-rm: /tmp/x/rules.ymlの説明が空",
    "info: deps: 補足",
    "warn: (file): 重なっています",
    "error 0件、warn 2件、info 1件",
  ].join("\n");
  assert.deepEqual(warnLinesOf(report, "/tmp/x/rules.yml", ".ccnavi/config/common/rules.yml"), [
    "warn: no-rm: .ccnavi/config/common/rules.ymlの説明が空",
    "warn: (file): 重なっています",
  ]);
  assert.deepEqual(warnLinesOf("error 0件、warn 0件、info 0件", "/tmp/x", "y"), []);
});

test("CB-T1241 warnの帯は先頭MAX_WARN_LINES件までで、超えた分は「ほかN件」の1行にする。以下ならそのまま", () => {
  const warns = (n: number) => Array.from({ length: n }, (_, i) => `warn: w${i}: 本文`).join("\n");
  const atLimit = warnLinesOf(warns(MAX_WARN_LINES), "/tmp/x", "y");
  assert.equal(atLimit.length, MAX_WARN_LINES);
  assert.ok(atLimit.every((line) => line.startsWith("warn: ")));
  const over = warnLinesOf(warns(MAX_WARN_LINES + 3), "/tmp/x", "y");
  assert.equal(over.length, MAX_WARN_LINES + 1);
  assert.equal(over[0], "warn: w0: 本文");
  assert.equal(over[MAX_WARN_LINES], "ほか3件。`ccnavi --lint`で全部見られます");
});

test("CB-T1244 保存拒否のerror行は日本語の行頭にし、今保存するファイルの行を先頭に出して件数で切り詰める", () => {
  const others = Array.from({ length: 9 }, (_, i) => `error: (ticket): T${i}: 無関係`);
  const report = [...others, "error: r1: /tmp/x/rules.ymlの条件が不正", "warn: w: 警告は含めない", "error 10件、warn 1件、info 0件"].join("\n");
  const lines = errorLinesOf(report, "/tmp/x/rules.yml", ".ccnavi/rules.yml");
  assert.equal(lines.length, MAX_WARN_LINES + 1);
  assert.equal(lines[0], "エラー: r1: .ccnavi/rules.ymlの条件が不正");
  assert.ok(lines.slice(1, MAX_WARN_LINES).every((l) => l.startsWith("エラー: ")));
  assert.match(lines[MAX_WARN_LINES], /^ほか5件/);
});

test("CB-T1262 「ほかN件」の案内は呼ぶ側が渡す。渡さなければ「ほかN件」だけ", () => {
  const lines = Array.from({ length: MAX_WARN_LINES + 2 }, (_, i) => `l${i}`);
  assert.equal(truncateLines(lines, "")[MAX_WARN_LINES], "ほか2件");
  assert.equal(truncateLines(lines, "。続きはここ")[MAX_WARN_LINES], "ほか2件。続きはここ");
});
