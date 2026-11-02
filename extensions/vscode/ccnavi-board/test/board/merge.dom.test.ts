/**
 * マージ状況の帯と、ツールバーの「MRの状況を確認」（Reactの画面をhappy-domで動かす）。
 * 中身（`MergeView`）は拡張ホストが持つ。ここは渡されたものを正しく出し、押した操作を正しく返すかを見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { buildBoard } from "../../src/core/board.js";
import type { MergeView, MergeViewRow } from "../../src/core/merge.js";
import { boardPage, openBoard } from "../helpers/board.js";
import { boardOf, ticketJson } from "../helpers/cards.js";
import { flatStyle } from "../helpers/bundle.js";
import type { DomPage } from "../helpers/dom.js";
import type { ArchivedTicketJson } from "../../src/core/model.js";
import type { HTMLButtonElement } from "happy-dom" with { "resolution-mode": "import" };

function row(parent: string, over: Partial<MergeViewRow> = {}): MergeViewRow {
  return { parent, project: "", mr: 5, state: "merged", draft: false, url: `https://example.com/pull/5`, reason: "", detail: "", sent: false, busy: false, sendError: "", abandon: "", ...over };
}

function view(rows: readonly MergeViewRow[], over: Partial<MergeView> = {}): MergeView {
  return { checking: false, checkedAt: "2026-10-01 10:00:00", error: "", rows, ...over };
}

function archived(ticket: string, title: string, project = ""): ArchivedTicketJson {
  return { ticket, parent: "", phase: null, title, project, path: `/ws/logs/archive/x/done/${ticket}.md`, started_at: "", completed_at: "t", cancelled_at: "", cancel_reason: "" };
}

const json = (): ReturnType<typeof boardOf> => boardOf([ticketJson("live")], [], { archived: [archived("i1", "古い親"), archived("i2", "別のプロジェクトの親", "web")], projects: ["web"] });

function text(page: DomPage, selector: string): string {
  return page.one(selector).textContent.replace(/\s+/g, " ").trim();
}

test("CB-D720 「MRの状況を確認」はツールバーに通常の色で置く。押すとmergeCheckを送り、確認中は「確認中…」で押せず、二重に送らない", async () => {
  const page = await openBoard(json());
  try {
    const button = page.one<HTMLButtonElement>('.controls button[data-action="merge-check"]');
    assert.equal(button.textContent.trim(), "MRの状況を確認");
    assert.ok(button.classList.contains("action") && !button.classList.contains("primary"));
    assert.equal(page.all(".merge-band").length, 0, "一度も確認していなければ帯を出さない");
    page.click(button);
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "mergeCheck" });
    // 拡張ホストの答えを待つ間も、確認中で押せない
    const waiting = page.one<HTMLButtonElement>('button[data-action="merge-check"]');
    assert.equal(waiting.disabled, true);
    assert.equal(waiting.textContent.trim(), "確認中…");
    assert.equal(waiting.getAttribute("aria-busy"), "true");
    const sent = page.posted.filter((m) => m.type === "mergeCheck").length;
    page.click(waiting);
    await page.settle();
    assert.equal(page.posted.filter((m) => m.type === "mergeCheck").length, sent, "確認中にもう一度押しても送らない");
    // 拡張ホストが確認中を伝える
    await page.send({ type: "data", data: { kind: "board", board: buildBoard(json()), merge: view([row("i1", { state: "checking" })], { checking: true, checkedAt: "" }) } });
    assert.equal(page.one<HTMLButtonElement>('button[data-action="merge-check"]').disabled, true);
    assert.equal(text(page, ".merge-state"), "確認中…");
    assert.equal(page.one(".merge-band").getAttribute("aria-busy"), "true");
    // 終わったら押せる
    await page.send({ type: "data", data: { kind: "board", board: buildBoard(json()), merge: view([row("i1")]) } });
    const idle = page.one<HTMLButtonElement>('button[data-action="merge-check"]');
    assert.equal(idle.disabled, false);
    assert.equal(idle.textContent.trim(), "MRの状況を確認");
  } finally {
    await page.close();
  }
});

test("CB-D721 帯の行: 親・タイトル・MR !N・状態。タイトルはボードのアーカイブの同じ親（プロジェクトとIDが一致）から取り、無ければIDだけ", async () => {
  const rows = [row("i1"), row("i2", { project: "web", mr: 6 }), row("i2", { project: "", mr: 7 }), row("nowhere", { mr: null })];
  // 同じIDが2行あるとkeyが重なるので、別のIDでプロジェクトの取り違えを確かめる
  const page = await openBoard(json(), { merge: view([row("i1"), row("i2", { project: "web", mr: 6 }), row("nowhere", { mr: null })]) });
  try {
    const line = (parent: string): string => text(page, `.merge-row[data-parent="${parent}"] .merge-who`);
    assert.equal(line("i1"), "i1 古い親 MR !5");
    assert.equal(line("i2"), "i2 別のプロジェクトの親 MR !6");
    assert.equal(line("nowhere"), "nowhere", "タイトルが無ければIDだけ（MRの番号も無ければ出さない）");
  } finally {
    await page.close();
  }
  // プロジェクトが違えば別の親: webのi2のタイトルを、ワークスペースのi2に付けない
  const other = await openBoard(json(), { merge: view([row("i2", { project: "" })]) });
  try {
    assert.equal(text(other, '.merge-row[data-parent="i2"] .merge-who'), "i2 MR !5");
  } finally {
    await other.close();
  }
  void rows;
});

test("CB-D720a 帯にマージ待ちの行があるときは、チケットが0件でも「チケットなし」を出さない", async () => {
  const empty = await openBoard(boardOf([], []), { merge: view([]) });
  try {
    assert.equal(empty.all("p.board-empty").length, 1);
  } finally {
    await empty.close();
  }
  const waiting = await openBoard(boardOf([], []), { merge: view([row("i1")]) });
  try {
    assert.equal(waiting.all("p.board-empty").length, 0);
  } finally {
    await waiting.close();
  }
});

test("CB-D720b 空のボードで、まだ確認していないときだけ「MRの状況を確認で調べる」案内を出す", async () => {
  const unchecked = await openBoard(boardOf([], []));
  try {
    assert.equal(unchecked.all('[data-merge="hint"]').length, 1);
  } finally {
    await unchecked.close();
  }
  const checked = await openBoard(boardOf([], []), { merge: view([]) });
  try {
    assert.equal(checked.all('[data-merge="hint"]').length, 0);
  } finally {
    await checked.close();
  }
});

test("CB-D722 マージ待ち（Draftなら付記）・マージ済み・閉じた・確認できない・記録が不完全・確認中の行の言葉", async () => {
  const merge = view([
    row("a-open", { state: "open", draft: false }),
    row("b-draft", { state: "open", draft: true }),
    row("c-merged", { state: "merged" }),
    row("d-closed", { state: "closed", abandon: "sh /ws/.ccnavi/scripts/ccnavi-launcher.sh worktree retire --abandon d-closed" }),
    row("e-unknown", { state: "unknown", url: "", reason: "auth", detail: "認証に失敗した（トークンかghの認証を確かめる）" }),
    row("f-timeout", { state: "unknown", url: "", reason: "timeout", detail: "時間内に終わりませんでした" }),
    row("g-format", { state: "unknown", url: "", reason: "format", detail: "出力の形が想定外です（要求したMR（#5）と番号が違います（6））" }),
    row("h-failed", { state: "unknown", url: "", reason: "failed", detail: "shが終了コード127で終わりました" }),
    row("i-incomplete", { state: "incomplete", url: "", mr: null, detail: "記録を読めない（壊れている）" }),
    row("j-wait", { state: "checking", url: "" }),
  ]);
  const page = await openBoard(json(), { merge });
  try {
    const state = (parent: string): string => text(page, `.merge-row[data-parent="${parent}"] .merge-state`);
    assert.equal(state("a-open"), "マージ待ち MRを開く");
    assert.equal(state("b-draft"), "マージ待ち（Draft） MRを開く");
    assert.equal(state("c-merged"), "マージ済み 片付ける");
    assert.match(state("d-closed"), /^マージせず閉じました。自動では片付けません。破棄した親の記録は、ターミナルでsh \/ws\/\.ccnavi\/scripts\/ccnavi-launcher\.sh worktree retire --abandon d-closedを実行すると消えます/);
    assert.equal(state("e-unknown"), "確認できません（認証: 認証に失敗した（トークンかghの認証を確かめる））");
    assert.equal(state("f-timeout"), "確認できません（タイムアウト: 時間内に終わりませんでした）");
    assert.match(state("g-format"), /^確認できません（出力の形が想定外: 出力の形が想定外です/);
    assert.equal(state("h-failed"), "確認できません（実行できません: shが終了コード127で終わりました）");
    assert.equal(state("i-incomplete"), "確認できません（記録が不完全: 記録を読めない（壊れている））");
    assert.equal(state("j-wait"), "確認中…");
    // 確認した時刻
    assert.equal(text(page, '[data-merge="checked-at"]'), "確認した時刻: 2026-10-01 10:00:00");
    // マージ済み以外に［片付ける］は出ない
    assert.deepEqual(page.all('button[data-action="merge-retire"]').map((b) => b.getAttribute("data-parent")), ["c-merged"]);
  } finally {
    await page.close();
  }
});

test("CB-D723 帯のボタンとリンクはcard-button（［片付ける］だけが主）。URLがhttp(s)のときだけ［MRを開く］をリンクにし、状態は出す", async () => {
  const merge = view([
    row("a", { state: "open", url: "https://example.com/pull/5" }),
    row("b", { state: "open", url: "javascript:alert(1)" }),
    row("c", { state: "merged", url: "data:text/html,x" }),
    row("d", { state: "closed", url: "ftp://x" }),
    row("e", { state: "merged" }),
  ]);
  const page = await openBoard(json(), { merge });
  try {
    const links = page.all(".merge-band a");
    assert.deepEqual(links.map((a) => a.getAttribute("href")), ["https://example.com/pull/5"], "javascript: などをhrefにしない");
    for (const a of links) {
      assert.ok(a.classList.contains("card-button"));
    }
    // 状態は出す（リンクにしないだけ）
    assert.match(text(page, '.merge-row[data-parent="b"] .merge-state'), /^マージ待ち$/);
    assert.match(text(page, '.merge-row[data-parent="c"] .merge-state'), /^マージ済み 片付ける$/);
    for (const b of page.all(".merge-band button")) {
      assert.ok(b.classList.contains("card-button"), b.getAttribute("data-action") ?? "");
      assert.ok(!b.classList.contains("action"));
    }
    assert.deepEqual(page.all(".merge-band button.primary").map((b) => b.getAttribute("data-action")), ["merge-retire", "merge-retire"]);
    assert.equal(page.all('.merge-band button[data-action="merge-copy-abandon"]').every((b) => !b.classList.contains("primary")), true);
  } finally {
    await page.close();
  }
});

test("CB-D724 ［片付ける］はmergeRetireを送る。ダイアログ中（busy）は押せず、送ったら「送りました」にして押せなくする。送れなかったら理由を出して押し直せる", async () => {
  const page = await openBoard(json(), { merge: view([row("c-merged")]) });
  try {
    page.click(page.one('button[data-action="merge-retire"]'));
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "mergeRetire", parent: "c-merged" });
  } finally {
    await page.close();
  }
  // ダイアログを開いている間は押せない
  const busy = await openBoard(json(), { merge: view([row("c-merged", { busy: true })]) });
  try {
    assert.equal(busy.one<HTMLButtonElement>('button[data-action="merge-retire"]').disabled, true);
  } finally {
    await busy.close();
  }
  // 送ったあと: 「片付けのコマンドをターミナルに送りました。結果はターミナルで確認してください」。ボタンは出さない
  const sent = await openBoard(json(), { merge: view([row("c-merged", { sent: true })]) });
  try {
    assert.equal(text(sent, ".merge-sent"), "片付けのコマンドをターミナルに送りました。結果はターミナルで確認してください");
    assert.equal(sent.all('button[data-action="merge-retire"]').length, 0);
  } finally {
    await sent.close();
  }
  // 送れなかった: 「送りました」とは言わず、理由を出し、押し直せる
  const failed = await openBoard(json(), { merge: view([row("c-merged", { sendError: "ターミナルに送れませんでした" })]) });
  try {
    assert.equal(failed.all(".merge-sent").length, 0);
    assert.equal(text(failed, '[data-merge="send-error"]'), "送れませんでした: ターミナルに送れませんでした");
    assert.equal(failed.one<HTMLButtonElement>('button[data-action="merge-retire"]').disabled, false);
    assert.ok(!failed.document.body.textContent.includes("送りました"));
  } finally {
    await failed.close();
  }
});

test("CB-D725 マージせずクローズした親は、記録を消すコマンドのコピーのボタンだけ（片付けるボタンも、ボードから送る操作も出さない）", async () => {
  const page = await openBoard(json(), { merge: view([row("d", { state: "closed", abandon: "sh /ws/x.sh worktree retire --abandon d" })]) });
  try {
    assert.equal(page.all('button[data-action="merge-retire"]').length, 0);
    const copy = page.one('button[data-action="merge-copy-abandon"]');
    assert.equal(copy.textContent, "コマンドをコピー");
    page.click(copy);
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "mergeCopyAbandon", parent: "d" });
    // 画面はコマンドの文字列を送らない（拡張ホストが組み直したものをコピーする）
    assert.equal(Object.keys(page.posted.at(-1) ?? {}).sort().join(","), "parent,type");
  } finally {
    await page.close();
  }
});

test("CB-D726 一覧を取れなかったときは「一覧を取得できません」をエラーに出し、「ありません」とは言わない。正しく取れて0件のときだけ「マージ待ちの親はありません」", async () => {
  const error = await openBoard(json(), { merge: view([], { error: "一覧を取得できません（形式の異常: 先頭の行がretired 1ではありません）" }) });
  try {
    const alert = error.one('[data-merge="list-error"]');
    assert.equal(alert.textContent, "一覧を取得できません（形式の異常: 先頭の行がretired 1ではありません）");
    assert.equal(alert.getAttribute("role"), "alert");
    assert.equal(error.all('[data-merge="empty"]').length, 0);
    assert.ok(!error.document.body.textContent.includes("マージ待ちの親はありません"));
  } finally {
    await error.close();
  }
  const none = await openBoard(json(), { merge: view([]) });
  try {
    assert.equal(text(none, '[data-merge="empty"]'), "マージ待ちの親はありません");
    assert.equal(none.all('[data-merge="list-error"]').length, 0);
  } finally {
    await none.close();
  }
  // incompleteだけ: 「ありません」ではなく確認できない行
  const only = await openBoard(json(), { merge: view([row("x", { state: "incomplete", mr: null, url: "", detail: "mrが無い" })]) });
  try {
    assert.equal(only.all('[data-merge="empty"]').length, 0);
    assert.match(text(only, '.merge-row[data-parent="x"] .merge-state'), /確認できません（記録が不完全: mrが無い）/);
  } finally {
    await only.close();
  }
});

test("CB-D727 結果は拡張ホストが持つので、画面が作り直されても（新しい中身が届いても）残る。閉じたボードの結果は届かない", async () => {
  const merge = view([row("c-merged")]);
  const page = await openBoard(json(), { merge });
  try {
    assert.equal(page.all('.merge-row[data-parent="c-merged"]').length, 1);
    // 画面を作り直す（readyを受けた拡張ホストが同じmergeを渡し直す）
    await page.send({ type: "data", data: { kind: "board", board: buildBoard(json()), merge } });
    assert.equal(page.all('.merge-row[data-parent="c-merged"]').length, 1);
    // 結果が無いデータ（ボードを開き直したあと）では、帯は出ない
    await page.send({ type: "data", data: { kind: "board", board: buildBoard(json()) } });
    assert.equal(page.all(".merge-band").length, 0);
  } finally {
    await page.close();
  }
  // 1枚目のHTMLに埋まった中身からも描く
  const html = boardPage({ kind: "board", board: buildBoard(json()), merge });
  assert.ok(html.includes("c-merged"));
});

test("CB-D728 帯の見た目の規則: 狭い画面で折り返し、ボタンはcard-buttonの寸法", () => {
  const css = flatStyle(boardPage({ kind: "board", board: buildBoard(json()), merge: view([row("a")]) }));
  assert.match(css, /\.merge-row \{[^}]*flex-wrap: wrap;/);
  assert.match(css, /\.merge-state \{[^}]*overflow-wrap: anywhere;/);
});
