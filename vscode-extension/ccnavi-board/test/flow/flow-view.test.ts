/**
 * フロー編集画面の契約（`core/flow-view.ts`）と、ボードの JSON の `flow` の欄の読み方。
 * 画面から届くメッセージの形の確かめ、錠を実行ファイルの答えから取り込むこと、カードのボタンの言葉を見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildBoard, flowCardOf } from "../../src/core/board.js";
import { templateFlow } from "../../src/core/flow-doc.js";
import { asFlowMessage, flowButtonLabel, flowRequestPrompt, flowTargetOf, flowTicketOf, lockFromFailure, requestLabel } from "../../src/core/flow-view.js";
import { parseBoardJson, type BoardJson } from "../../src/core/model.js";
import { fixture, fixtureText } from "../helpers/fixture.js";

test("CB-T225 画面から届くメッセージは形を確かめ、崩れたもの（読めない保存の中身など）は捨てる", () => {
  const doc = templateFlow("i0001-01-01", "調査");
  assert.deepEqual(asFlowMessage({ type: "ready" }), { type: "ready" });
  assert.deepEqual(asFlowMessage({ type: "openFile", extra: 1 }), { type: "openFile" });
  assert.deepEqual(asFlowMessage({ type: "tourDone" }), { type: "tourDone" });
  assert.deepEqual(asFlowMessage({ type: "reload", dirty: true }), { type: "reload", dirty: true });
  // 未保存かが読めなければ「変更なし」ではなく、真のときだけ真（確認を飛ばす側にしないのは拡張ホストの問い）
  assert.deepEqual(asFlowMessage({ type: "reload", dirty: "yes" }), { type: "reload", dirty: false });
  assert.deepEqual(asFlowMessage({ type: "dirty", dirty: false }), { type: "dirty", dirty: false });
  assert.equal(asFlowMessage({ type: "dirty" }), undefined);
  assert.deepEqual(asFlowMessage({ type: "save", doc }), { type: "save", doc });
  // 保存の中身が描けない形なら、保存そのものを捨てる（書かない）
  assert.equal(asFlowMessage({ type: "save" }), undefined);
  assert.equal(asFlowMessage({ type: "save", doc: { name: "x" } }), undefined);
  assert.equal(asFlowMessage({ type: "save", doc: { nodes: [{ type: "start" }] } }), undefined);
  // 描ける形なら受ける。id の重なりのような正しさは、保存の前に実行ファイル（--lint --flow）が言う
  const twin = { nodes: [{ id: "a" }, { id: "a" }] };
  assert.deepEqual(asFlowMessage({ type: "save", doc: twin }), { type: "save", doc: twin });
  // 知らない操作・オブジェクトでないもの
  assert.equal(asFlowMessage({ type: "delete" }), undefined);
  // 取り込み（外のワークフローを読む操作）は無い
  assert.equal(asFlowMessage({ type: "import", dirty: true }), undefined);
  assert.equal(asFlowMessage("save"), undefined);
  assert.equal(asFlowMessage(null), undefined);
});

test("CB-T226 ボードの「フロー」ボタンの識別子は、識別子に使える綴りだけ受ける", () => {
  assert.equal(flowTicketOf({ ticket: "i0001-01-01" }), "i0001-01-01");
  assert.equal(flowTicketOf({ ticket: "web.i0002-01-03" }), "web.i0002-01-03");
  for (const bad of ["", " i0001-01-01", "../i0001-01-01", "i0001/01", "i0001\\01", "-x"]) {
    assert.equal(flowTicketOf({ ticket: bad }), undefined, bad);
  }
  assert.equal(flowTicketOf({ ticket: 1 }), undefined);
  assert.equal(flowTicketOf({}), undefined);
});

test("CB-T227 カードのボタンの言葉は、在るか・着手中か（実行ファイルの答え）で 作成 / 編集 / 閲覧（着手中）", () => {
  const base = { path: "/ws/.ccnavi/approved/flows/x.yml", rel: ".ccnavi/approved/flows/x.yml", tree: "/ws", linked: false, draft: null };
  assert.equal(flowButtonLabel({ ...base, exists: false, locked: false }), "フロー: 作成");
  assert.equal(flowButtonLabel({ ...base, exists: true, locked: false }), "フロー: 編集");
  assert.equal(flowButtonLabel({ ...base, exists: true, locked: true }), "フロー: 閲覧（着手中）");
  // 無いまま着手した子も、読むだけ
  assert.equal(flowButtonLabel({ ...base, exists: false, locked: true }), "フロー: 閲覧（着手中）");
});

test("CB-T228 錠は実行ファイルの flow.locked の写し。親・無い子・欄の無い子は引けない", () => {
  const board = fixture();
  // 見本の i0001-02-02 は着手中（DENY_TICKET_FLOW_LOCKED で止まる）、i0001-01-01 は閉じていてファイルが在る
  const locked = flowTargetOf(board, "i0001-02-02");
  assert.ok(locked.ok);
  assert.equal(locked.target.lock.locked, true);
  assert.match(locked.target.lock.reason, /DENY_TICKET_FLOW_LOCKED/);
  assert.match(locked.target.lock.reason, /finish で終わるか cancel で取り消されると外れます/);
  assert.equal(locked.target.parent, "i0001");
  assert.match(locked.target.flow.rel, /^\.ccnavi\/approved\/flows\/i0001-02-02\.yml$/);
  const open = flowTargetOf(board, "i0001-01-01");
  assert.ok(open.ok);
  assert.deepEqual(open.target.lock, { locked: false, reason: "" });
  const parent = flowTargetOf(board, "i0001");
  assert.equal(parent.ok, false);
  assert.match(parent.ok ? "" : parent.error, /親チケット/);
  const missing = flowTargetOf(board, "i9999-01-01");
  assert.equal(missing.ok, false);
  // 取り消しの子でファイルが無ければ、実行ファイルは flow を null で返す。開く先が無い
  const cancelled = flowTargetOf(board, "i0001-02-05");
  assert.equal(cancelled.ok, false);
  assert.match(cancelled.ok ? "" : cancelled.error, /完了・取り消しの子でファイルが無い/);
  // 画面は started_at を見て組み直さない。答えが locked: false と言えば、着手済みに見えても開く
  const trusted: BoardJson = {
    ...board,
    tickets: board.tickets.map((t) => (t.ticket === "i0001-02-02" && t.flow !== null ? { ...t, flow: { ...t.flow, locked: false } } : t)),
  };
  const told = flowTargetOf(trusted, "i0001-02-02");
  assert.ok(told.ok);
  assert.equal(told.target.lock.locked, false);
  // 確かめられないときは閉じる側
  assert.equal(lockFromFailure("返らない").locked, true);
});

test("CB-T229 ボードの JSON の flow は子だけが持ち、locked が欠けたら閉じる側に読む", () => {
  const board = fixture();
  assert.equal(board.tickets.find((t) => t.ticket === "i0001")?.flow, null);
  const child = board.tickets.find((t) => t.ticket === "i0001-01-01")?.flow;
  assert.ok(child !== null && child !== undefined);
  assert.equal(child.exists, true);
  assert.equal(board.tickets.find((t) => t.ticket === "i0001-02-05")?.flow, null);
  assert.equal(child.linked, false);
  assert.equal(child.tree, "<root>/.claude/worktrees/i0001");
  // locked の欠けた答え（古い実行ファイルか不正な出力）は、止まっているものとして読む
  const raw = JSON.parse(fixtureText()) as { tickets: { ticket: string; flow: Record<string, unknown> | null }[] };
  for (const t of raw.tickets) {
    if (t.flow !== null) {
      delete t.flow.locked;
    }
  }
  const parsed = parseBoardJson(JSON.stringify(raw));
  assert.ok(parsed.ok);
  assert.ok(parsed.board.tickets.filter((t) => t.flow !== null).every((t) => t.flow?.locked === true));
  // カードにも反映され、親のカードには無い。ボタンの開く先は子のカードだけ
  const built = buildBoard(board);
  assert.ok(flowCardOf(built, "i0001-01-01") !== undefined);
  assert.equal(flowCardOf(built, "i0001"), undefined);
  assert.equal(flowCardOf(built, "i9999-01-01"), undefined);
  assert.equal(flowCardOf(built, "i0001-02-05"), undefined);
});

test("CB-T241 閉じた子（完了・取り消し）でファイルが無ければ、古い実行ファイルの答えでもカードにフローを載せない", () => {
  const board = fixture();
  // 古い実行ファイルは閉じた子にも exists: false の flow を返していた
  const flowOf = (ticket: string, exists: boolean) => ({
    path: `<root>/.claude/worktrees/i0001/.ccnavi/approved/flows/${ticket}.yml`,
    rel: `.ccnavi/approved/flows/${ticket}.yml`,
    tree: "<root>/.claude/worktrees/i0001",
    exists,
    linked: false,
    locked: false,
    draft: null,
  });
  const old: BoardJson = {
    ...board,
    tickets: board.tickets.map((t) => (t.ticket === "i0001-01-01" || t.ticket === "i0001-02-05" ? { ...t, flow: flowOf(t.ticket, false) } : t)),
  };
  const built = buildBoard(old);
  const card = (id: string) => built.columns.flatMap((column) => column.cards).find((c) => c.id === id);
  assert.equal(card("i0001-01-01")?.column, "done");
  assert.equal(card("i0001-01-01")?.flow, null);
  assert.equal(card("i0001-02-05")?.column, "cancelled");
  assert.equal(card("i0001-02-05")?.flow, null);
  assert.equal(flowCardOf(built, "i0001-01-01"), undefined);
  // 閉じた子でもファイルが在れば残す（中身を見られる）。開いている子はファイルが無くても載る（作成）
  assert.equal(flowCardOf(buildBoard(board), "i0001-01-01")?.flow?.exists, true);
  assert.equal(card("i0001-02-04")?.flow?.exists, false);
  assert.equal(card("i0001-02-03")?.flow?.exists, false);
});

test("CB-T230 置き場かその途中がリンクなら、着手前でも読むだけ。ツリーの欄が無い古い答えも書かない側", () => {
  const board = fixture();
  const edit = (change: (flow: NonNullable<BoardJson["tickets"][number]["flow"]>) => object): BoardJson => ({
    ...board,
    tickets: board.tickets.map((t) => (t.ticket === "i0001-01-01" && t.flow !== null ? { ...t, flow: { ...t.flow, ...change(t.flow) } } : t)),
  });
  const linked = flowTargetOf(edit(() => ({ linked: true })), "i0001-01-01");
  assert.ok(linked.ok);
  assert.equal(linked.target.lock.locked, true);
  assert.match(linked.target.lock.reason, /シンボリックリンク/);
  const old = flowTargetOf(edit(() => ({ tree: "" })), "i0001-01-01");
  assert.ok(old.ok);
  assert.equal(old.target.lock.locked, true);
  // linked の欠けた答えは、リンクかを確かめられないので書かない側に読む
  const raw = JSON.parse(fixtureText()) as { tickets: { flow: Record<string, unknown> | null }[] };
  for (const t of raw.tickets) {
    if (t.flow !== null) {
      delete t.flow.linked;
    }
  }
  const parsed = parseBoardJson(JSON.stringify(raw));
  assert.ok(parsed.ok);
  assert.ok(parsed.board.tickets.filter((t) => t.flow !== null).every((t) => t.flow?.linked === true));
});

test("CB-T293 下書きの置き場は実行ファイルの flow.draft の写し。欠けた欄はリンクの側、無い答えは null", () => {
  const board = fixture();
  const child = board.tickets.find((t) => t.ticket === "i0001-02-03")?.flow;
  assert.ok(child !== null && child !== undefined);
  assert.deepEqual(child.draft, {
    path: "<root>/.claude/worktrees/i0001/wip/proposals/flows/i0001-02-03.yml",
    rel: "wip/proposals/flows/i0001-02-03.yml",
    exists: false,
    linked: false,
  });
  const raw = JSON.parse(fixtureText()) as { tickets: { ticket: string; flow: Record<string, unknown> | null }[] };
  for (const t of raw.tickets) {
    if (t.flow !== null && t.ticket === "i0001-02-03") {
      delete (t.flow.draft as Record<string, unknown>).linked;
    }
    if (t.flow !== null && t.ticket === "i0001-02-04") {
      delete t.flow.draft;
    }
  }
  const parsed = parseBoardJson(JSON.stringify(raw));
  assert.ok(parsed.ok);
  assert.equal(parsed.board.tickets.find((t) => t.ticket === "i0001-02-03")?.flow?.draft?.linked, true);
  assert.equal(parsed.board.tickets.find((t) => t.ticket === "i0001-02-04")?.flow?.draft, null);
  // 着手の前かは、ボタンを出すかだけに使う（錠は flow.locked のまま）
  const before = flowTargetOf(board, "i0001-02-03");
  assert.ok(before.ok);
  assert.equal(before.target.beforeStart, true);
  const started = flowTargetOf(board, "i0001-02-02");
  assert.ok(started.ok);
  assert.equal(started.target.beforeStart, false);
  const closed = flowTargetOf(board, "i0001-01-01");
  assert.ok(closed.ok);
  assert.equal(closed.target.beforeStart, false);
});

test("CB-T294 依頼のボタンは着手の前で錠が無いときだけ。フローが無ければ作成、在れば直し、下書きが在れば頼み直す", () => {
  const open = { beforeStart: true, locked: false, flowExists: false, draftExists: false };
  assert.equal(requestLabel(open), "エージェントにフローの作成を頼む");
  assert.equal(requestLabel({ ...open, flowExists: true }), "エージェントにフローの直しを頼む");
  assert.equal(requestLabel({ ...open, draftExists: true }), "エージェントにフローを頼み直す");
  assert.equal(requestLabel({ ...open, flowExists: true, draftExists: true }), "エージェントにフローを頼み直す");
  // 着手中・閉じた子（着手の前でない）・錠の確認中やリンク（錠が掛かっている）には出さない
  assert.equal(requestLabel({ ...open, beforeStart: false }), undefined);
  assert.equal(requestLabel({ ...open, locked: true }), undefined);
});

test("CB-T295 依頼の文は子・書く置き場・いまのフロー・確かめ方・承認済みの領域に書かないことを言う", () => {
  const base = {
    ticket: "i0001-02-03",
    title: "設計",
    parent: "i0001",
    draftPath: "/ws/.claude/worktrees/i0001/wip/proposals/flows/i0001-02-03.yml",
    redo: false,
    lintCommand: "sh /ws/.ccnavi/scripts/ccnavi-launcher.sh --lint --flow /ws/.claude/worktrees/i0001/wip/proposals/flows/i0001-02-03.yml",
  };
  const made = flowRequestPrompt(base);
  assert.match(made, /子チケット i0001-02-03（設計） のフローの作成を頼んだ（親 i0001）/);
  assert.match(made, /下書きを書く置き場: \/ws\/\.claude\/worktrees\/i0001\/wip\/proposals\/flows\/i0001-02-03\.yml/);
  assert.match(made, /ccnavi-launcher\.sh --lint --flow \/ws\/.*i0001-02-03\.yml` で確かめ、error が無くなってから/);
  assert.match(made, /\.ccnavi\/approved\/flows\/）には書かない/);
  assert.doesNotMatch(made, /いまのフロー/);
  assert.doesNotMatch(made, /前の下書き/);
  const fix = flowRequestPrompt({ ...base, flowPath: "/ws/.claude/worktrees/i0001/.ccnavi/approved/flows/i0001-02-03.yml", redo: true });
  assert.match(fix, /フローの直しを頼んだ/);
  assert.match(fix, /いまのフロー: \/ws\/.*\.ccnavi\/approved\/flows\/i0001-02-03\.yml/);
  assert.match(fix, /前の下書きが残っている/);
});

test("CB-T296 画面からの依頼・提案の操作と、保存に添える取り込んだ下書きの指紋は形を確かめてから受ける", () => {
  for (const type of ["openProposal", "request", "requestCopy", "requestOpen"]) {
    assert.deepEqual(asFlowMessage({ type }), { type });
  }
  const doc = templateFlow("i0001-01-01", "調査");
  const hash = "a".repeat(64);
  assert.deepEqual(asFlowMessage({ type: "save", doc, imported: hash }), { type: "save", doc, imported: hash });
  // 指紋の形でなければ、取り込みは無かったものとして受ける（下書きを消さない側）
  for (const bad of ["", "x".repeat(64), "A".repeat(64), "a".repeat(63), 1, null]) {
    assert.deepEqual(asFlowMessage({ type: "save", doc, imported: bad }), { type: "save", doc });
  }
});
