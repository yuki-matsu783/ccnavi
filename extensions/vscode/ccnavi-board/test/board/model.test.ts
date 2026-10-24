import { test } from "node:test";
import assert from "node:assert/strict";
import { BOARD_VERSION, parseBoardJson } from "../../src/core/model.js";
import { fixture, fixtureText } from "../helpers/fixture.js";

test("CB-T1 フィクスチャ（実行ファイルの出力）を読める", () => {
  const board = fixture();
  assert.equal(board.version, BOARD_VERSION);
  assert.deepEqual(
    board.tickets.map((t) => t.ticket),
    ["i0001", "i0001-01-01", "i0001-02-02", "i0001-02-03", "i0001-02-04", "i0001-02-05"],
  );
  assert.equal(board.settings.approved, ".ccnavi/approved");
  const by = new Map(board.tickets.map((t) => [t.ticket, t]));
  // 提案の側にあるのは承認待ちとレビュー待ちだけ。承認済みチケットの側は proposal が null
  assert.deepEqual(
    board.tickets.map((t) => [t.ticket, t.proposal?.state ?? null, t.copy.status]),
    [
      ["i0001", null, "open"],
      ["i0001-01-01", null, "closed"],
      ["i0001-02-02", null, "open"],
      ["i0001-02-03", "todo", "none"],
      ["i0001-02-04", "review", "review"],
      ["i0001-02-05", null, "closed"],
    ],
  );
  assert.notEqual(by.get("i0001-02-05")!.cancelled_at, "");
  assert.deepEqual(board.parents[0].phases[1].states, { "i0001-02-02": "doing", "i0001-02-04": "review", "i0001-02-05": "cancelled" });
  assert.deepEqual(board.pending_approval, ["i0001-02-03"]);
  assert.equal(board.parents.length, 1);
  assert.equal(board.parents[0].phases.length, 2);
});

test("CB-T2 版が違えば読まない", () => {
  const text = fixtureText().replace(`"version": ${BOARD_VERSION}`, `"version": ${BOARD_VERSION + 1}`);
  const parsed = parseBoardJson(text);
  assert.equal(parsed.ok, false);
  if (!parsed.ok) {
    assert.match(parsed.error, /版が違います/);
  }
});

test("CB-T3 JSON でなければ理由を返す", () => {
  const parsed = parseBoardJson("not json");
  assert.equal(parsed.ok, false);
  if (!parsed.ok) {
    assert.match(parsed.error, /JSON として解釈できません/);
  }
  const notObject = parseBoardJson("[1]");
  assert.equal(notObject.ok, false);
});

test("CB-T110 layers[] からルールファイルの置き場を読み、欠けた JSON でも全体を破棄せずに空で補う", () => {
  const board = fixture();
  assert.deepEqual(board.layers.map((l) => l.name), ["common", "self"]);
  assert.equal(board.layers[1].rules.path, "<root>/.ccnavi/config/project/rules.yml");
  assert.equal(board.layers[1].rules.unreadable, "");
  assert.equal(board.layers[1].phasesFile.path, "<root>/.ccnavi/config/project/phases.yml");
  assert.equal(board.layers[1].risk.path, "<root>/.ccnavi/config/project/risks.yml");
  assert.equal(board.layers[0].phasesFile.path, "<root>/.ccnavi/config/common/phases.yml");
  // 実行ファイルは常に layers を出す。欠けていれば（不正な JSON）CB-T4 と同じく既定値の空で補う
  const missing = parseBoardJson(JSON.stringify({ version: BOARD_VERSION }));
  assert.ok(missing.ok);
  assert.deepEqual(missing.board.layers, []);
  const broken = parseBoardJson(JSON.stringify({ version: BOARD_VERSION, layers: [{ name: "lib" }, "x"] }));
  assert.ok(broken.ok);
  assert.deepEqual(broken.board.layers, [
    { name: "lib", rules: { path: "", unreadable: "" }, risk: { path: "", unreadable: "" }, phasesFile: { path: "", unreadable: "" } },
  ]);
});

test("CB-T318 sums[] を読む。ワークスペースの統合結果（共通 + 自身）が先で、欠けた欄は空で補い、古い実行ファイル（sums がありません）では空", () => {
  const board = fixture();
  assert.deepEqual(board.sums.map((s) => s.name), ["self"]);
  const self = board.sums[0];
  assert.deepEqual(self.layers, ["common", "self"]);
  assert.equal(self.rules.path, "<root>/.ccnavi/config/project/rules.yml");
  assert.equal(self.rules.missing, true);
  assert.deepEqual(self.rules.deny.map((r) => [r.id, r.source, r.match, r.kind]), [["guard-approved", "common", "Write|Edit|NotebookEdit", "glob"]]);
  assert.deepEqual(self.risk.levels, { medium: 20, high: 40, critical: 70 });
  assert.equal(self.risk.fallback, "");
  // フェーズ定義は共通 + 自身の設定の和。この見本の共通の設定には定義が無いので、自身の設定の定義だけが載る
  // 定義は順序を持たない（順序は親の計画のステップの after）。先行関係（order）の欄は実行ファイルも渡さず、拡張も読まない
  assert.ok(!("order" in self.phases), JSON.stringify(self.phases));
  assert.deepEqual(self.phases.types.map((t) => t.id).slice(0, 2), ["research", "design"]);
  assert.ok(self.phases.types.every((t) => t.source === "self"));
  const old = parseBoardJson(JSON.stringify({ version: BOARD_VERSION }));
  assert.ok(old.ok);
  assert.deepEqual(old.board.sums, []);
  const broken = parseBoardJson(JSON.stringify({ version: BOARD_VERSION, sums: [{ name: "lib" }, "x"] }));
  assert.ok(broken.ok);
  assert.equal(broken.board.sums.length, 1);
  assert.deepEqual(broken.board.sums[0].layers, []);
  assert.deepEqual(broken.board.sums[0].rules.deny, []);
  assert.equal(broken.board.sums[0].rules.missing, false);
  assert.deepEqual(broken.board.sums[0].risk.levels, { medium: null, high: null, critical: null });
  assert.deepEqual(broken.board.sums[0].phases.types, []);
});

test("CB-T4 欠けた項目は既定値で埋め、全体を破棄しない", () => {
  const parsed = parseBoardJson(
    JSON.stringify({
      version: BOARD_VERSION,
      tickets: [
        { ticket: "x", copy: { status: "weird" }, proposal: { state: "nope" } },
        // 提案の状態は todo / review だけ。旧の状態（doing / done / cancelled）は読まない
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

test("CB-T140 blocked は欄がなければ空。古い実行ファイルの出力でも失敗しない", () => {
  // 欄が無いのは、この欄より前の実行ファイルの出力。空なら止まっていないと読む。
  const base = JSON.parse(fixtureText()) as Record<string, unknown>;
  const tickets = (base.tickets as Record<string, unknown>[]).map((t) => ({ ...t }));
  tickets[0].blocked = "親 i0001 の承認済みチケットが作業中にない（未承認か、閉じている）";
  delete tickets[1].blocked;

  const parsed = parseBoardJson(JSON.stringify({ ...base, tickets }));
  assert.equal(parsed.ok, true);
  if (!parsed.ok) {
    return;
  }
  assert.match(parsed.board.tickets[0].blocked, /作業中にない/);
  assert.equal(parsed.board.tickets[1].blocked, "");
});

test("CB-T262 predecessors_unmet は実行ファイルの答えをそのまま使う。欄がなければ空（満たしている扱い）", () => {
  const base = JSON.parse(fixtureText()) as Record<string, unknown>;
  const tickets = (base.tickets as Record<string, unknown>[]).map((t) => ({ ...t }));
  tickets[3].predecessors_unmet = [{ ticket: "i0001-02-02", state: "doing", label: "作業中（doing/）" }, "壊れた行"];
  delete tickets[1].predecessors_unmet;
  const parsed = parseBoardJson(JSON.stringify({ ...base, tickets }));
  assert.equal(parsed.ok, true);
  if (!parsed.ok) {
    return;
  }
  assert.deepEqual(parsed.board.tickets[3].predecessors_unmet, [{ ticket: "i0001-02-02", state: "doing", label: "作業中（doing/）" }]);
  assert.deepEqual(parsed.board.tickets[1].predecessors_unmet, []);
});

test("CB-T303 archived は実行ファイルの退避の欄をコピーする。欄がなければ空、オブジェクトでない行は除き、欠けた欄は既定値で埋める", () => {
  const base = JSON.parse(fixtureText()) as Record<string, unknown>;
  const parsed = parseBoardJson(
    JSON.stringify({
      ...base,
      archived: [
        { ticket: "old", title: "古い親", path: "/ws/logs/archive/self/done/old.md" },
        "壊れた行",
      ],
    }),
  );
  assert.equal(parsed.ok, true);
  if (!parsed.ok) {
    return;
  }
  assert.equal(parsed.board.archived?.length, 1);
  const old = parsed.board.archived![0];
  assert.equal(old.ticket, "old");
  assert.equal(old.parent, "");
  assert.equal(old.phase, null);
  assert.equal(old.path, "/ws/logs/archive/self/done/old.md");
  // この欄を出さない古い実行ファイル
  const { archived: _dropped, ...older } = base;
  const parsedOlder = parseBoardJson(JSON.stringify(older));
  assert.equal(parsedOlder.ok, true);
  if (parsedOlder.ok) {
    assert.deepEqual(parsedOlder.board.archived, []);
  }
});

test("CB-T385 提案の has_plan（計画を持つか）を読む。ない・真偽でない値は偽（古い実行ファイルの答え）", () => {
  const parsed = parseBoardJson(
    JSON.stringify({
      version: BOARD_VERSION,
      tickets: [
        { ticket: "a", copy: { status: "none" }, proposal: { state: "todo", has_plan: true } },
        { ticket: "b", copy: { status: "none" }, proposal: { state: "todo" } },
        { ticket: "c", copy: { status: "none" }, proposal: { state: "todo", has_plan: "yes" } },
      ],
      parents: [],
    }),
  );
  assert.equal(parsed.ok, true);
  if (parsed.ok) {
    assert.deepEqual(
      parsed.board.tickets.map((t) => t.proposal?.has_plan),
      [true, false, false],
    );
  }
});

test("CB-T570 チケットの template_draft（完了した計画付きの親から下書きを作れるか）を読む。ない・真偽でない値は偽（古い実行ファイルの答え）", () => {
  const parsed = parseBoardJson(
    JSON.stringify({
      version: BOARD_VERSION,
      tickets: [
        { ticket: "a", parent: "", copy: { status: "closed" }, template_draft: true },
        { ticket: "b", parent: "", copy: { status: "closed" } },
        { ticket: "c", parent: "", copy: { status: "closed" }, template_draft: "yes" },
      ],
      parents: [],
    }),
  );
  assert.equal(parsed.ok, true);
  if (parsed.ok) {
    assert.deepEqual(
      parsed.board.tickets.map((t) => t.template_draft),
      [true, false, false],
    );
  }
});
