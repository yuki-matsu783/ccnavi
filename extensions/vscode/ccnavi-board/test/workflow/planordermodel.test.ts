/**
 * `--plan-order <親> --json` の答え（実行ファイルとの契約。`src/core/planordermodel.ts`）の読み取り。
 * 見本は Python 側が書き出した実例（`test/fixtures/plan-order.json`）。画面は判定せず、答えを読むだけ。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { parsePlanOrder, PLAN_ORDER_VERSION } from "../../src/core/planordermodel.js";
import { planOrderSample, planOrderText } from "../helpers/plan-order.js";

test("CB-T366 開いたときの答えを読む。計画ごとの図の中身（plans）と、ハッシュ・書けるか・子の一覧はそのまま", () => {
  const answer = planOrderSample("open");
  assert.equal(answer.version, PLAN_ORDER_VERSION);
  assert.equal(answer.ticket, "i0001");
  assert.equal(answer.source_sha, "<sha>");
  assert.deepEqual(
    answer.plans.map((plan) => [plan.part, plan.items.map((item) => item.number)]),
    [
      ["plan", [1, 2, 3, 4]],
      ["feedback", [5, 6]],
    ],
  );
  assert.equal(answer.plans[0].items[0].title, "調査");
  assert.deepEqual(answer.plans[0].after, { "2": [1], "3": [1], "4": [2, 3] });
  assert.equal(answer.writable, true);
  assert.deepEqual(answer.children, []);
  assert.deepEqual(answer.refused, []);
  assert.equal(answer.written, undefined, "読むだけの答えに written はありません");
});

test("CB-T367 振り直した答えの子の付け替え（旧識別子 → 新識別子、phase の旧 → 新）と、断った線・書けない理由・書いたかを読む", () => {
  const reverse = planOrderSample("reverse");
  assert.deepEqual(
    reverse.children.map((child) => [child.ticket, child.new_ticket, child.phase, child.new_phase]),
    [
      ["i0001-02-01", "i0001-03-01", 2, 3],
      ["i0001-03-01", "i0001-02-01", 3, 2],
    ],
  );
  assert.deepEqual(
    reverse.plans[0].items.map((item) => [item.number, item.from]),
    [
      [1, 1],
      [2, 3],
      [3, 2],
      [4, 4],
    ],
  );
  const cycle = planOrderSample("cycle");
  assert.deepEqual(cycle.refused.map((r) => r.kind), ["cycle"]);
  assert.match(cycle.refused[0].text, /循環/);
  assert.equal(cycle.writable, false);
  const loose = planOrderSample("loose");
  assert.equal(loose.writable, false);
  assert.deepEqual(loose.loose, [3]);
  assert.ok(loose.problems.length > 0);
  assert.equal(planOrderSample("written").written, true);
  const revision = planOrderSample("revision");
  assert.equal(revision.plans[0].items[0].locked, true);
  assert.deepEqual(revision.plans[0].current, { "2": [1], "3": [1], "4": [2, 3] });
});

test("CB-T368 読み込めない答えは理由を返す（JSON でない・版が違う・最上位がオブジェクトでない）。欄の型が崩れていれば空として読む", () => {
  const notJson = parsePlanOrder("ccnavi: i0099 の親の提案が承認待ちの置き場にありません");
  assert.equal(notJson.ok, false);
  assert.match(notJson.ok ? "" : notJson.error, /JSON/);
  const version = parsePlanOrder(JSON.stringify({ ...JSON.parse(planOrderText("open")), version: 2 }));
  assert.equal(version.ok, false);
  assert.match(version.ok ? "" : version.error, /版/);
  assert.equal(parsePlanOrder("[]").ok, false);
  const broken = parsePlanOrder(JSON.stringify({ version: 1, plans: "x", children: [{ ticket: 1 }], writable: "yes", refused: [{ kind: "cycle" }] }));
  assert.ok(broken.ok);
  if (broken.ok) {
    assert.deepEqual(broken.value.plans, []);
    assert.equal(broken.value.writable, false, "真偽でなければ書けない側");
    assert.equal(broken.value.children[0].ticket, "");
    assert.deepEqual(broken.value.refused.map((r) => [r.kind, r.number, r.text]), [["cycle", 0, ""]]);
  }
});
