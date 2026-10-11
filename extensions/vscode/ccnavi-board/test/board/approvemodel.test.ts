import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { APPROVE_VERSION, parseApprovePreview, parseApproveResult, partialMessage } from "../../src/core/approvemodel.js";

/** Python 側のテスト（tests/ticket/test_approve_json.py）が書き出した、実行ファイルの出力そのもの */
function fixtureText(name: string): string {
  return fs.readFileSync(path.join(__dirname, "..", "..", "..", "test", "fixtures", name), "utf8");
}

test("CB-T104 承認の preview を読む（一覧・範囲の超過・本文・対象外・読めない提案）", () => {
  const parsed = parseApprovePreview(fixtureText("approve-preview.json"));
  assert.ok(parsed.ok);
  if (!parsed.ok) {
    return;
  }
  const preview = parsed.value;
  assert.equal(preview.version, APPROVE_VERSION);
  assert.deepEqual(
    preview.batch.map((b) => [b.ticket, b.parent, b.phase, b.revision]),
    [
      ["i0001", null, null, false],
      ["i0001-01-01", "i0001", 1, false],
      ["i0001-01-02", "i0001", 1, false],
    ],
  );
  // 定義の範囲を超える子は承認を止めず、一覧に載って超過を持つ（判定で止まる）。
  assert.deepEqual(preview.batch[0].overflow, []);
  assert.deepEqual(preview.batch[1].overflow, []);
  assert.equal(preview.batch[2].overflow.length, 1);
  assert.ok(preview.batch[2].overflow[0].includes("超えている"));
  assert.ok(preview.text.startsWith("チケットの承認リクエスト: 3 件"));
  assert.ok(preview.text.includes("編集対象としているが"));
  // 本文のダイジェスト。承認するときに --digest で返す。値はワークツリーの絶対パスに依るので、
  // フィクスチャでは伏せてある。
  assert.equal(preview.digest, "<digest>");
  // 対象にしないのは形の正しくない子（計画に無い番号）だけ。
  assert.equal(preview.rejected.length, 1);
  assert.equal(preview.rejected[0].ticket, "i0001-05-05");
  assert.ok(preview.rejected[0].problems[0].includes("計画に無い"));
  assert.deepEqual(preview.problems, []);
});

test("CB-T328 承認の preview の plans を読む（計画を持つ親の図の中身。実行ファイルが組んだ番号・先行・終端に当たらない項・すぐ始まる項・延期の引き受け手をそのまま）", () => {
  const parsed = parseApprovePreview(fixtureText("approve-preview.json"));
  assert.ok(parsed.ok);
  if (!parsed.ok) {
    return;
  }
  const plans = parsed.value.plans;
  assert.equal(plans.length, 1);
  const plan = plans[0];
  assert.equal(plan.ticket, "i0001");
  assert.equal(plan.part, "plan");
  assert.match(plan.source_sha, /^[0-9a-f]{64}$/);
  assert.deepEqual(
    plan.items.map((item) => [item.number, item.from, item.type, item.title, item.review, item.deferred, item.review_at, item.locked]),
    [
      [1, 1, "research", "調査", "none", false, null, false],
      [2, 2, "design", "設計", "mr", false, null, false],
    ],
  );
  assert.deepEqual(plan.after, { "2": [1] });
  assert.deepEqual(plan.proposed, { "2": [1] });
  assert.equal(plan.current, null);
  assert.deepEqual(plan.loose, []);
  assert.deepEqual(plan.ready, [1]);
  assert.deepEqual(plan.problems, []);

  // 欄が無い（前の版の実行ファイル）なら空。画面は図を出さない
  const raw = JSON.parse(fixtureText("approve-preview.json")) as Record<string, unknown>;
  delete raw.plans;
  const old = parseApprovePreview(JSON.stringify(raw));
  assert.ok(old.ok && old.value.plans.length === 0);

  // 形の崩れた項目は読み飛ばし、数でない番号と文字でない理由は落とす（図を描くのに要るものが無ければ描かない）
  const broken = parseApprovePreview(
    JSON.stringify({
      ...raw,
      plans: [
        "x",
        {
          ticket: "i0002",
          part: "feedback",
          items: [{ number: 3, title: "設計の見直し", deferred: true, review_at: 4 }, { number: "4" }, null],
          after: { "4": [3, "x"], "5": "3" },
          current: { "4": [3] },
          loose: [3, "4"],
          ready: [3],
          problems: ["最後の項がほかの項を待っていない", 5],
        },
      ],
    }),
  );
  assert.ok(broken.ok);
  if (!broken.ok) {
    return;
  }
  assert.equal(broken.value.plans.length, 1);
  const fb = broken.value.plans[0];
  assert.equal(fb.part, "feedback");
  assert.deepEqual(fb.items.map((item) => [item.number, item.from, item.title, item.deferred, item.review_at, item.locked]), [[3, 3, "設計の見直し", true, 4, false]]);
  assert.deepEqual(fb.after, { "4": [3] });
  assert.deepEqual(fb.proposed, {});
  assert.deepEqual(fb.current, { "4": [3] });
  assert.deepEqual(fb.loose, [3]);
  assert.deepEqual(fb.problems, ["最後の項がほかの項を待っていない"]);
});

test("CB-T104b 超過の欄が無い古い答えは、空の配列として読む", () => {
  const parsed = parseApprovePreview(
    JSON.stringify({
      version: APPROVE_VERSION,
      batch: [{ ticket: "i0001", title: "親", parent: null, phase: null, revision: false, tree: "", path: "" }],
    }),
  );
  assert.ok(parsed.ok);
  assert.ok(parsed.ok && parsed.value.batch[0].overflow.length === 0);
});

test("CB-T105 承認の答えを読む（承認した / 一覧が違った）", () => {
  const done = parseApproveResult(fixtureText("approve-yes.json"));
  assert.ok(done.ok);
  if (done.ok) {
    assert.deepEqual(done.value.approved, ["i0001", "i0001-01-01"]);
    assert.equal(done.value.copies.length, 2);
    assert.ok(done.value.prompt.includes("i0001-01-01"));
    assert.ok(done.value.prompt.includes("ccnavi-ticket.sh start"));
    assert.ok(done.value.lines.length > 0);
  }
  const changed = parseApproveResult(fixtureText("approve-mismatch.json"));
  assert.ok(!changed.ok);
  assert.ok("mismatch" in changed);
  if ("mismatch" in changed) {
    assert.deepEqual(changed.mismatch.expected, ["i0001-01-09"]);
    assert.deepEqual(changed.mismatch.current, []);
    assert.deepEqual(changed.mismatch.digest, { expected: "<digest>", current: "<digest>" });
  }
});

test("CB-T106 版が違う・JSON でない答えは読まない", () => {
  const other = parseApprovePreview(JSON.stringify({ version: APPROVE_VERSION + 1, batch: [] }));
  assert.ok(!other.ok);
  assert.ok(!other.ok && other.error.includes("版が違います"));
  const broken = parseApproveResult("承認した。\n");
  assert.ok(!broken.ok);
  assert.ok("error" in broken);
});

test("CB-T158 途中で止まった承認を読む（置いたぶんを拾い、成功にはしない）", () => {
  const stopped = parseApproveResult(
    JSON.stringify({
      version: APPROVE_VERSION,
      partial: { placed: ["i0001"], ticket: "i0001-01-01", reason: "書けない (…)", lines: ["  i0001 のフェーズ 1 のマーカーを消した"] },
    }),
  );
  assert.ok(!stopped.ok, "置いたぶんがあっても成功にはしない");
  assert.ok("partial" in stopped);
  if ("partial" in stopped) {
    assert.deepEqual(stopped.partial.placed, ["i0001"]);
    assert.equal(stopped.partial.ticket, "i0001-01-01");
    assert.ok(stopped.partial.reason.includes("書けない"));
    assert.deepEqual(stopped.partial.lines, ["  i0001 のフェーズ 1 のマーカーを消した"]);
  }
  // 1 件も置かれなかった形
  const none = parseApproveResult(
    JSON.stringify({ version: APPROVE_VERSION, partial: { placed: [], ticket: "i0001", reason: "書けない", lines: [] } }),
  );
  assert.ok(!none.ok && "partial" in none && none.partial.placed.length === 0);
});

test("CB-T159 途中で止まったことを伝える文（置いた件数・後始末で止まった場合・進捗の行）", () => {
  const stopped = partialMessage({
    placed: ["i0001"],
    ticket: "i0001-01-01",
    reason: "書けない (…)",
    lines: ["  i0001 のフェーズ 1 のマーカーを消した"],
  });
  assert.ok(stopped.includes("i0001-01-01 で止まりました"));
  assert.ok(stopped.includes("i0001 の 1 件は承認済みチケットに入っています"));
  assert.ok(stopped.includes("コミットと push はターミナルに送っていません"));
  assert.ok(stopped.includes("マーカーを消した"), "端末に出ていた行も渡す");

  // 1 件も置かれなかったときは「一部だけ置かれた」と言わない
  const none = partialMessage({ placed: [], ticket: "i0001", reason: "書けない", lines: [] });
  assert.ok(none.includes("承認済みになったチケットはありません"));
  assert.ok(!none.includes("入っています"));

  // 書けたあとの後始末（マーカーを置く）で落ちたときは、言い方を変える
  const after = partialMessage({
    placed: ["i0001"],
    ticket: "i0001",
    reason: "マーカーを置けない: 書けない",
    lines: [],
  });
  assert.ok(after.includes("i0001 の後始末で止まりました"));
  assert.ok(!after.includes("i0001 で止まりました"));

  // 「が」と識別子の間は空白 1 つ。止まった識別子が分からない（空）ときは空白を重ねない
  assert.ok(stopped.startsWith("ccnavi --agree --yes が i0001-01-01 で止まりました: 書けない (…)。"), stopped);
  assert.ok(after.startsWith("ccnavi --agree --yes が i0001 の後始末で止まりました: "), after);
  const unknown = partialMessage({ placed: [], ticket: "", reason: "書けない", lines: [] });
  assert.ok(unknown.startsWith("ccnavi --agree --yes が止まりました: 書けない。"), unknown);
  // 継ぎ目だけを見る。識別子があれば「が」のあとに空白ちょうど 1 つ、無ければ「が」の直後に「止まりました」
  assert.match(stopped, /--yes が [^\s]/, stopped);
  assert.match(unknown, /--yes が止まりました/, unknown);
});
