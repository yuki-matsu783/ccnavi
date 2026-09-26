import { test } from "node:test";
import assert from "node:assert/strict";
import { BOARD_VERSION, parseBoardJson } from "../../src/core/model.js";
import { fixture, fixtureText } from "../helpers/fixture.js";

test("CB-T01 フィクスチャ（実行ファイルの出力）を読める", () => {
  const board = fixture();
  assert.equal(board.version, BOARD_VERSION);
  assert.deepEqual(
    board.tickets.map((t) => t.ticket),
    ["i0001", "i0001-01", "i0001-02", "i0001-03", "i0001-04", "i0001-05"],
  );
  assert.equal(board.settings.approved, ".ccnavi/approved");
  const by = new Map(board.tickets.map((t) => [t.ticket, t]));
  // 提案の側にあるのは承認待ちとレビュー待ちだけ。承認済みチケットの側は proposal が null
  assert.deepEqual(
    board.tickets.map((t) => [t.ticket, t.proposal?.state ?? null, t.copy.status]),
    [
      ["i0001", null, "open"],
      ["i0001-01", null, "closed"],
      ["i0001-02", null, "open"],
      ["i0001-03", "todo", "none"],
      ["i0001-04", "review", "review"],
      ["i0001-05", null, "closed"],
    ],
  );
  assert.notEqual(by.get("i0001-05")!.cancelled_at, "");
  assert.deepEqual(board.parents[0].phases[1].states, { "i0001-02": "doing", "i0001-04": "review", "i0001-05": "cancelled" });
  assert.deepEqual(board.pending_approval, ["i0001-03"]);
  assert.equal(board.parents.length, 1);
  assert.equal(board.parents[0].phases.length, 2);
});

test("CB-T02 版が違えば読まない", () => {
  const text = fixtureText().replace(`"version": ${BOARD_VERSION}`, `"version": ${BOARD_VERSION + 1}`);
  const parsed = parseBoardJson(text);
  assert.equal(parsed.ok, false);
  if (!parsed.ok) {
    assert.match(parsed.error, /版が違う/);
  }
});

test("CB-T03 JSON でなければ理由を返す", () => {
  const parsed = parseBoardJson("not json");
  assert.equal(parsed.ok, false);
  if (!parsed.ok) {
    assert.match(parsed.error, /JSON として読めない/);
  }
  const notObject = parseBoardJson("[1]");
  assert.equal(notObject.ok, false);
});

test("CB-T110 layers[] からルールファイルの置き場を読み、欠けた JSON でも全体を捨てずに空で補う", () => {
  const board = fixture();
  assert.deepEqual(board.layers.map((l) => l.name), ["common", "self"]);
  assert.equal(board.layers[1].rules.path, "<root>/.ccnavi/config/rules.yml");
  assert.equal(board.layers[1].rules.unreadable, "");
  assert.equal(board.layers[1].phasesFile.path, "<root>/.ccnavi/config/phases.yml");
  assert.equal(board.layers[0].phasesFile.path, "<root>/phases.yml");
  // 実行ファイルは常に layers を出す。欠けていれば（壊れた JSON）CB-T04 と同じく既定値の空で補う
  const missing = parseBoardJson(JSON.stringify({ version: BOARD_VERSION }));
  assert.ok(missing.ok);
  assert.deepEqual(missing.board.layers, []);
  const broken = parseBoardJson(JSON.stringify({ version: BOARD_VERSION, layers: [{ name: "lib" }, "x"] }));
  assert.ok(broken.ok);
  assert.deepEqual(broken.board.layers, [
    { name: "lib", rules: { path: "", unreadable: "" }, phasesFile: { path: "", unreadable: "" } },
  ]);
});

test("CB-T04 欠けた項目は既定値で埋め、全体を捨てない", () => {
  const parsed = parseBoardJson(
    JSON.stringify({
      version: BOARD_VERSION,
      tickets: [
        { ticket: "x", copy: { status: "weird" }, proposal: { state: "nope" } },
        // 旧の置き場の状態（doing / done / cancelled）は提案の状態としては読まない（ADR-0055）
        { ticket: "y", copy: { status: "review" }, proposal: { state: "doing" } },
      ],
      parents: [{ ticket: "x", phases: [{ number: 1, marks: { requested: "not an object" } }] }],
    }),
  );
  assert.equal(parsed.ok, true);
  if (parsed.ok) {
    const t = parsed.board.tickets[0];
    assert.equal(t.copy.status, "none");
    assert.equal(t.proposal, null);
    assert.equal(parsed.board.tickets[1].copy.status, "review");
    assert.equal(parsed.board.tickets[1].proposal, null);
    assert.equal(t.human_review.required, false);
    assert.deepEqual(t.seen_in, []);
    const p = parsed.board.parents[0].phases[0];
    assert.equal(p.state, "planned");
    assert.deepEqual(p.marks, { requested: {} });
    assert.equal(p.label, "1");
    // 欠けたレビュー待ちは「待ちではない」として扱う。マーカーから組み直さない
    assert.equal(p.review_waiting, false);
  }
});

test("CB-T140 blocked は欄が無ければ空。古い実行ファイルの出力でも落ちない", () => {
  // 欄が無いのは、この欄より前の実行ファイルの出力。空なら止まっていないと読む。
  const base = JSON.parse(fixtureText()) as Record<string, unknown>;
  const tickets = (base.tickets as Record<string, unknown>[]).map((t) => ({ ...t }));
  tickets[0].blocked = "親 i0001 の承認済みチケットが作業中に無い（未承認か、閉じている）";
  delete tickets[1].blocked;

  const parsed = parseBoardJson(JSON.stringify({ ...base, tickets }));
  assert.equal(parsed.ok, true);
  if (!parsed.ok) {
    return;
  }
  assert.match(parsed.board.tickets[0].blocked, /作業中に無い/);
  assert.equal(parsed.board.tickets[1].blocked, "");
});
