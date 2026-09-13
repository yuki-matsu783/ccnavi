import { test } from "node:test";
import assert from "node:assert/strict";
import { BOARD_VERSION, parseBoardJson } from "../src/core/model.js";
import { fixture, fixtureText } from "./fixture.js";

test("CB-T01 フィクスチャ（実行ファイルの出力）を読める", () => {
  const board = fixture();
  assert.equal(board.version, BOARD_VERSION);
  assert.deepEqual(
    board.tickets.map((t) => t.ticket),
    ["i0001", "i0001-01", "i0001-02", "i0001-03"],
  );
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

test("CB-T110 layers[] からルールファイルの置き場を読み、無ければ空の並びにする", () => {
  const board = fixture();
  assert.deepEqual(board.layers.map((l) => l.name), ["common", "self"]);
  assert.equal(board.layers[1].rules.path, "<root>/.ccnavi/config/rules.yml");
  assert.equal(board.layers[1].rules.unreadable, "");
  const old = parseBoardJson(JSON.stringify({ version: BOARD_VERSION }));
  assert.ok(old.ok);
  assert.deepEqual(old.board.layers, []);
  const broken = parseBoardJson(JSON.stringify({ version: BOARD_VERSION, layers: [{ name: "lib" }, "x"] }));
  assert.ok(broken.ok);
  assert.deepEqual(broken.board.layers, [{ name: "lib", rules: { path: "", unreadable: "" } }]);
});

test("CB-T04 欠けた項目は既定値で埋め、全体を捨てない", () => {
  const parsed = parseBoardJson(
    JSON.stringify({
      version: BOARD_VERSION,
      tickets: [{ ticket: "x", copy: { status: "weird" }, proposal: { state: "nope" } }],
      parents: [{ ticket: "x", phases: [{ number: 1, marks: { requested: "not an object" } }] }],
    }),
  );
  assert.equal(parsed.ok, true);
  if (parsed.ok) {
    const t = parsed.board.tickets[0];
    assert.equal(t.copy.status, "none");
    assert.equal(t.proposal, null);
    assert.equal(t.human_review.required, false);
    assert.deepEqual(t.seen_in, []);
    const p = parsed.board.parents[0].phases[0];
    assert.equal(p.state, "planned");
    assert.deepEqual(p.marks, { requested: {} });
    assert.equal(p.label, "1");
  }
});
