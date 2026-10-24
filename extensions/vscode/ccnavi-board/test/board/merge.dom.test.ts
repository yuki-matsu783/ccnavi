/**
 * マージ状況の帯と、ツールバーの「マージ状況を確認」（React の画面を happy-dom で動かす）。
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

test("CB-D720 「マージ状況を確認」はツールバーに通常の色で置く。押すと mergeCheck を送り、確認中は「確認中…」で押せず、二重に送らない", async () => {
  const page = await openBoard(json());
  try {
    const button = page.one<HTMLButtonElement>('.controls button[data-action="merge-check"]');
    assert.equal(button.textContent.trim(), "マージ状況を確認");
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
    assert.equal(idle.textContent.trim(), "マージ状況を確認");
  } finally {
    await page.close();
  }
});

test("CB-D721 帯の行: 親・題・MR !N ・状態。題はボードのアーカイブの同じ親（プロジェクトと ID が一致）から取り、無ければ ID だけ", async () => {
  const rows = [row("i1"), row("i2", { project: "web", mr: 6 }), row("i2", { project: "", mr: 7 }), row("nowhere", { mr: null })];
  // 同じ ID が 2 行あると key が重なるので、別の ID でプロジェクトの取り違えを確かめる
  const page = await openBoard(json(), { merge: view([row("i1"), row("i2", { project: "web", mr: 6 }), row("nowhere", { mr: null })]) });
  try {
    const line = (parent: string): string => text(page, `.merge-row[data-parent="${parent}"] .merge-who`);
    assert.equal(line("i1"), "i1 古い親 MR !5");
    assert.equal(line("i2"), "i2 別のプロジェクトの親 MR !6");
    assert.equal(line("nowhere"), "nowhere", "題が無ければ ID だけ（MR の番号も無ければ出さない）");
  } finally {
    await page.close();
  }
  // プロジェクトが違えば別の親: web の i2 の題を、ワークスペースの i2 に付けない
  const other = await openBoard(json(), { merge: view([row("i2", { project: "" })]) });
  try {
    assert.equal(text(other, '.merge-row[data-parent="i2"] .merge-who'), "i2 MR !5");
  } finally {
    await other.close();
  }
  void rows;
});

test("CB-D722 マージ待ち（Draft なら付記）・マージ済み・閉じた・確認できない・記録が不完全・確認中の行の言葉", async () => {
  const merge = view([
    row("a-open", { state: "open", draft: false }),
    row("b-draft", { state: "open", draft: true }),
    row("c-merged", { state: "merged" }),
    row("d-closed", { state: "closed", abandon: "sh /ws/.ccnavi/scripts/ccnavi-launcher.sh worktree retire --abandon d-closed" }),
    row("e-unknown", { state: "unknown", url: "", reason: "auth", detail: "認証に失敗した（トークンか gh の認証を確かめる）" }),
    row("f-timeout", { state: "unknown", url: "", reason: "timeout", detail: "時間内に終わりませんでした" }),
    row("g-format", { state: "unknown", url: "", reason: "format", detail: "出力の形が想定外です（番号が頼んだ MR（#5）と違います（6））" }),
    row("h-failed", { state: "unknown", url: "", reason: "failed", detail: "sh が終了コード 127 で終わりました" }),
    row("i-incomplete", { state: "incomplete", url: "", mr: null, detail: "記録を読めない（壊れている）" }),
    row("j-wait", { state: "checking", url: "" }),
  ]);
  const page = await openBoard(json(), { merge });
  try {
    const state = (parent: string): string => text(page, `.merge-row[data-parent="${parent}"] .merge-state`);
    assert.equal(state("a-open"), "- マージ待ち MR を開く");
    assert.equal(state("b-draft"), "- マージ待ち（Draft） MR を開く");
    assert.equal(state("c-merged"), "- マージ済み 片付ける");
    assert.match(state("d-closed"), /^- マージせず閉じた - 自動では片付けません。捨てた親なら、ユーザが端末で sh \/ws\/\.ccnavi\/scripts\/ccnavi-launcher\.sh worktree retire --abandon d-closed を実行して記録を消します/);
    assert.equal(state("e-unknown"), "- 確認できない（認証: 認証に失敗した（トークンか gh の認証を確かめる））");
    assert.equal(state("f-timeout"), "- 確認できない（期限切れ: 時間内に終わりませんでした）");
    assert.match(state("g-format"), /^- 確認できない（出力の形が想定外: 出力の形が想定外です/);
    assert.equal(state("h-failed"), "- 確認できない（実行できない: sh が終了コード 127 で終わりました）");
    assert.equal(state("i-incomplete"), "- 確認できない（記録が不完全: 記録を読めない（壊れている））");
    assert.equal(state("j-wait"), "確認中…");
    // 確認した時刻
    assert.equal(text(page, '[data-merge="checked-at"]'), "確認した時刻 2026-10-01 10:00:00");
    // マージ済み以外に［片付ける］は出ない
    assert.deepEqual(page.all('button[data-action="merge-retire"]').map((b) => b.getAttribute("data-parent")), ["c-merged"]);
  } finally {
    await page.close();
  }
});

test("CB-D723 帯のボタンとリンクは card-button（［片付ける］だけが主）。URL が http(s) のときだけ［MR を開く］をリンクにし、状態は出す", async () => {
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
    assert.deepEqual(links.map((a) => a.getAttribute("href")), ["https://example.com/pull/5"], "javascript: などを href にしない");
    for (const a of links) {
      assert.ok(a.classList.contains("card-button"));
    }
    // 状態は出す（リンクにしないだけ）
    assert.match(text(page, '.merge-row[data-parent="b"] .merge-state'), /^- マージ待ち$/);
    assert.match(text(page, '.merge-row[data-parent="c"] .merge-state'), /^- マージ済み 片付ける$/);
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

test("CB-D724 ［片付ける］は mergeRetire を送る。ダイアログ中（busy）は押せず、送ったら「送信しました」にして押せなくする。送れなかったら理由を出して押し直せる", async () => {
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
  // 送ったあと: 「片付けのコマンドを送信しました。結果はターミナルで確認してください」。ボタンは出さない
  const sent = await openBoard(json(), { merge: view([row("c-merged", { sent: true })]) });
  try {
    assert.equal(text(sent, ".merge-sent"), "片付けのコマンドを送信しました。結果はターミナルで確認してください");
    assert.equal(sent.all('button[data-action="merge-retire"]').length, 0);
  } finally {
    await sent.close();
  }
  // 送れなかった: 「送信しました」とは言わず、理由を出し、押し直せる
  const failed = await openBoard(json(), { merge: view([row("c-merged", { sendError: "ターミナルに送れませんでした" })]) });
  try {
    assert.equal(failed.all(".merge-sent").length, 0);
    assert.equal(text(failed, '[data-merge="send-error"]'), "送信できませんでした: ターミナルに送れませんでした");
    assert.equal(failed.one<HTMLButtonElement>('button[data-action="merge-retire"]').disabled, false);
    assert.ok(!failed.document.body.textContent.includes("送信しました"));
  } finally {
    await failed.close();
  }
});

test("CB-D725 マージせず閉じた親は、記録を消すコマンドのコピーのボタンだけ（片付けるボタンも、ボードから送る操作も出さない）", async () => {
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

test("CB-D726 一覧を取れなかったときは「一覧を取れない」をエラーに出し、「ありません」とは言わない。正しく取れて 0 件のときだけ「マージ待ちの親はありません」", async () => {
  const error = await openBoard(json(), { merge: view([], { error: "一覧を取れない（形式の異常: 頭の行が retired 1 ではありません）" }) });
  try {
    const alert = error.one('[data-merge="list-error"]');
    assert.equal(alert.textContent, "一覧を取れない（形式の異常: 頭の行が retired 1 ではありません）");
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
  // incomplete だけ: 「ありません」ではなく確認できない行
  const only = await openBoard(json(), { merge: view([row("x", { state: "incomplete", mr: null, url: "", detail: "mr が無い" })]) });
  try {
    assert.equal(only.all('[data-merge="empty"]').length, 0);
    assert.match(text(only, '.merge-row[data-parent="x"] .merge-state'), /確認できない（記録が不完全: mr が無い）/);
  } finally {
    await only.close();
  }
});

test("CB-D727 結果は拡張ホストが持つので、画面が作り直されても（新しい中身が届いても）残る。閉じたボードの結果は届かない", async () => {
  const merge = view([row("c-merged")]);
  const page = await openBoard(json(), { merge });
  try {
    assert.equal(page.all('.merge-row[data-parent="c-merged"]').length, 1);
    // 画面を作り直す（ready を受けた拡張ホストが同じ merge を渡し直す）
    await page.send({ type: "data", data: { kind: "board", board: buildBoard(json()), merge } });
    assert.equal(page.all('.merge-row[data-parent="c-merged"]').length, 1);
    // 結果が無いデータ（ボードを開き直したあと）では、帯は出ない
    await page.send({ type: "data", data: { kind: "board", board: buildBoard(json()) } });
    assert.equal(page.all(".merge-band").length, 0);
  } finally {
    await page.close();
  }
  // 1 枚目の HTML に埋まった中身からも描く
  const html = boardPage({ kind: "board", board: buildBoard(json()), merge });
  assert.ok(html.includes("c-merged"));
});

test("CB-D728 帯の見た目の規則: 狭い画面で折り返し、ボタンは card-button の寸法", () => {
  const css = flatStyle(boardPage({ kind: "board", board: buildBoard(json()), merge: view([row("a")]) }));
  assert.match(css, /\.merge-row \{[^}]*flex-wrap: wrap;/);
  assert.match(css, /\.merge-state \{[^}]*overflow-wrap: anywhere;/);
});
