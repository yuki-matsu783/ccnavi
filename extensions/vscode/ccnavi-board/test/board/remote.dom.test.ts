/**
 * 親カードの出どころ（issue・MR・ブランチ名）とリモートとの差（未送信・遅れ）、
 * 親のブランチ名 -> 識別子の変換。
 *
 * 値は実行ファイルの JSON（`remote`・`mr`・`issue`・`branch`）のとおりで、拡張は計算し直さない。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

import { buildBoard, type Board } from "../../src/core/board.js";
import type { BoardJson, RemoteJson, TicketJson } from "../../src/core/model.js";
import { branchToId } from "../../src/core/ticket-id.js";
import { fixture } from "../helpers/fixture.js";
import { openBoard } from "../helpers/board.js";
import { remoteItems } from "../../src/webview/board/text.js";

/** 見本のボードの親 i0001 に、出どころとリモートとの差を載せる */
function withRemote(remote: RemoteJson | null, over: Partial<TicketJson> = {}): BoardJson {
  const board = fixture();
  return {
    ...board,
    tickets: board.tickets.map((t) => (t.ticket === "i0001" ? { ...t, remote, ...over } : t)),
  };
}

function parentCard(board: Board) {
  const card = board.columns.flatMap((c) => c.cards).find((c) => c.id === "i0001");
  assert.ok(card !== undefined);
  return card;
}

test("CB-T578 親のカードに issue・MR・ブランチ名とリモートとの差が載る。MR は親チケットの mr: から", () => {
  const remote: RemoteJson = { branch: "feature/12-login", ahead: 2, behind: 1 };
  const card = parentCard(buildBoard(withRemote(remote, { issue: 12, mr: 7, branch: "feature/12-login" })));
  assert.equal(card.issueNumber, 12);
  assert.equal(card.parentMr, 7);
  assert.equal(card.branch, "feature/12-login");
  assert.deepEqual(card.remote, remote);
  // 親チケットに mr: が無ければ MR は出さない
  assert.equal(parentCard(buildBoard(withRemote(remote, { mr: null }))).parentMr, null);
  // 子には載せない
  const child = buildBoard(withRemote(remote)).columns.flatMap((c) => c.cards).find((c) => c.id === "i0001-01-01");
  assert.ok(child !== undefined);
  assert.equal(child.remote, null);
  assert.equal(child.parentMr, null);
  assert.equal(child.branch, "");
});

test("CB-T579 リモートとの差の言い方。未送信・遅れ。読めない差は不明", () => {
  const board = (remote: RemoteJson | null) => parentCard(buildBoard(withRemote(remote)));
  assert.deepEqual(remoteItems(board({ branch: "a", ahead: 2, behind: 1 })), ["未送信 2 件", "遅れ 1 件"]);
  assert.deepEqual(remoteItems(board({ branch: "a", ahead: 0, behind: 0 })), ["未送信 なし", "遅れ なし"]);
  assert.deepEqual(remoteItems(board({ branch: "a", ahead: null, behind: null })), ["未送信 不明", "遅れ 不明"]);
  // ワークツリーが無い親（remote が無い）は出さない
  assert.deepEqual(remoteItems(board(null)), []);
});

test("CB-D363 親のカードは、未送信を注意の行に短く出し、出どころ（issue・MR・ブランチ）とリモートとの差の全部は詳細に畳む", async () => {
  const remote: RemoteJson = { branch: "feature/12-login", ahead: 2, behind: 1 };
  const json = withRemote(remote, { issue: 12, mr: 7, branch: "feature/12-login" });
  const page = await openBoard(json);
  try {
    const parent = page.one('li.card[data-id="i0001"]');
    // 未送信（ahead が 1 以上）は注意の行に。遅れはここには出さない
    assert.deepEqual(Array.from(parent.querySelectorAll(".notes .badge.unsent")).map((e) => e.textContent), ["未送信 2 件"]);
    assert.equal(parent.querySelectorAll(".origin").length, 0, "出どころの行は常時は出さない");
    // 詳細は畳んである（開くまで中身を出さない）
    assert.equal(parent.querySelectorAll("details.card-details").length, 1);
    assert.equal(parent.querySelectorAll(".detail-rows").length, 0);
  } finally {
    await page.close();
  }
  // 詳細を開いた状態（カードごとに覚えている）で開く
  const opened = await openBoard(json, { state: { details: [JSON.stringify(["", "i0001"])] } });
  try {
    const parent = opened.one('li.card[data-id="i0001"]');
    const row = (key: string): string => parent.querySelector(`.detail-row[data-row="${key}"] dd`)?.textContent ?? "";
    assert.equal(row("issue"), "#12");
    assert.equal(row("parent-mr"), "!7");
    assert.equal(row("branch"), "feature/12-login");
    assert.equal(row("remote"), "未送信 2 件遅れ 1 件");
    // 子のカードには出さない
    const child = opened.one('li.card[data-id="i0001-01-01"]');
    assert.equal(child.querySelectorAll(".notes .badge.unsent").length, 0);
  } finally {
    await opened.close();
  }
  // 未送信が 0 件なら注意には出ない（普通のことは出さない）
  const clean = await openBoard(withRemote({ branch: "a", ahead: 0, behind: 3 }, {}));
  try {
    assert.equal(clean.all(".badge.unsent").length, 0);
  } finally {
    await clean.close();
  }
});

test("CB-T581 ブランチ名の / を - に置き換えた識別子を作る。Python・sh と共通の表（tests/fixtures/branch-to-id.json）で一致する", () => {
  const table = path.join(__dirname, "..", "..", "..", "..", "..", "..", "tests", "fixtures", "branch-to-id.json");
  const cases = (JSON.parse(fs.readFileSync(table, "utf8")) as { convert: { branch: string; id: string }[] }).convert;
  assert.ok(cases.length > 0);
  for (const c of cases) {
    assert.equal(branchToId(c.branch), c.id, c.branch);
  }
});
