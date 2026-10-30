/**
 * 絞り込みの比べ方（`webview/shared/find.ts`）と、保存後に出す warn の行の取り出し（`core/lintmodel.ts`）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { warnLinesOf } from "../../src/core/lintmodel.js";
import { foldFind, moveTitle } from "../../src/webview/shared/find.js";
import { findText as ruleFind } from "../../src/webview/rules/text.js";
import { findText as phaseFind } from "../../src/webview/phases/text.js";
import { findText as riskFind } from "../../src/webview/risk/text.js";
import type { RuleForm } from "../../src/core/rules-view.js";
import type { PhaseForm } from "../../src/core/phases-view.js";
import type { FactorForm } from "../../src/core/risk-view.js";

// 各画面の `state.ts` は VS Code の API を読み込むので、テストでは同じ形の値を直に書く
const emptyRule = (): RuleForm => ({ origin: null, id: "", match: "", kind: "glob", pattern: "", message: "", additionalContext: "", additionalContextOnce: "", additionalContextFile: "", additionalContextOnceFile: "", every: "" });
const emptyPhase = (): PhaseForm => ({ origin: null, id: "", title: "", kind: "work", review: "mr", inherit: true, scope: [], deliverables: [], agent: "", when: "" });
const emptyFactor = (): FactorForm => ({ origin: null, id: "", points: "", kind: "lines_over", value: "", max: "", include: "", exclude: "", message: "" });

test("CB-T1238 絞り込みは NFKC で正規化して小文字にする。全角英数字・半角カナも同じに探せる", () => {
  assert.equal(foldFind("ＡＢＣ-ｇｉｔ"), "abc-git");
  assert.equal(foldFind("ﾃｽﾄ"), "テスト");
  assert.ok(ruleFind({ ...emptyRule(), id: "Git-Push" }).includes(foldFind("ＧＩＴ")));
  assert.ok(phaseFind({ ...emptyPhase(), id: "design" }).includes(foldFind("ＤＥＳＩＧＮ")));
  assert.ok(riskFind({ ...emptyFactor(), id: "Big-Change" }).includes(foldFind("ＢＩＧ")));
});

test("CB-T1239 絞り込み中の上下ボタンの title は理由を言う。そうでなければ向きだけ", () => {
  assert.equal(moveTitle("上へ", false), "上へ");
  assert.equal(moveTitle("下へ", false), "下へ");
  assert.match(moveTitle("上へ", true), /絞り込み中は並べ替えできません/);
});

test("CB-T1240 --lint の報告から warn の行だけを取り出し、一時ファイルのパスを対象のパスに直す", () => {
  const report = [
    "ccnavi: 設定を検証する",
    "  ルール: /tmp/x/rules.yml",
    "warn: no-rm: /tmp/x/rules.yml の説明が空",
    "info: deps: 補足",
    "warn: (file): 重なっています",
    "error 0 件、warn 2 件、info 1 件",
  ].join("\n");
  assert.deepEqual(warnLinesOf(report, "/tmp/x/rules.yml", ".ccnavi/config/common/rules.yml"), [
    "warn: no-rm: .ccnavi/config/common/rules.yml の説明が空",
    "warn: (file): 重なっています",
  ]);
  assert.deepEqual(warnLinesOf("error 0 件、warn 0 件、info 0 件", "/tmp/x", "y"), []);
});
