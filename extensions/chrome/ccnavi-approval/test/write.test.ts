/**
 * Chrome からの承認と取り下げ。模擬の GitHub と Node の上の Pyodide（拡張と同じ zip）で回す。
 *
 * - 1 コミットの組み立て（`createCommitOnBranch`、`expectedHeadOid` = 読んだ P の先頭）と、書いた後の確かめ
 * - 先頭が動いたら新しい Snapshot で判定と plan をやり直す。その内容が有効なら書き、通らなければ書かない
 * - 指した識別子だけを、承認する時点の Snapshot で判定して書く。決まらない親子のチケット・版ずれでは書かない
 * - 取り下げ: 承認コミット（merge を飛ばす）の親の提案をそのまま戻す
 */
import { before, test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import type { PyCall } from "../src/core/py.js";
import { renderBoard } from "./helpers/board-dom.js";
import { createRenderer } from "../src/core/sanitize.js";
import type { RepoConfig } from "../src/core/settings.js";
import { collectRepo, type RepoBoard } from "../src/core/snapshot.js";
import { approveFamily, fileChanges, localStamp, MAX_ROUNDS, withdrawTicket, type WriteDeps } from "../src/core/write.js";
import { fixture, NOW, type FixtureBranch } from "./fixtures/repo.js";
import { deps, hostCall, memoryCache, newStats } from "./helpers/host.js";
import { COMPAT } from "./helpers/compat.js";
import { blobSha, LOGIN, MockGitHub, TOKEN } from "./helpers/mock-github.js";
import { pyodidePy } from "./helpers/python.js";

let py: PyCall;
before(async () => {
  py = (await pyodidePy()).call;
});

const REPO: RepoConfig = { host: "github.com", owner: "acme", repo: "widgets", integration: "", recentDays: 3, extraBranches: [], project: "", workspace: "" };
const VERSION = "9.9.9";

function depsFor(mock: MockGitHub): WriteDeps {
  const stats = newStats();
  return {
    call: hostCall(deps(mock, new Map([["github.com", TOKEN]])), stats),
    py,
    cache: memoryCache(),
    now: () => new Date(NOW),
    stats,
    version: VERSION,
    sleep: async () => undefined,
  };
}

function world(branches: Record<string, FixtureBranch> = fixture()) {
  const mock = new MockGitHub(branches);
  return { mock, d: depsFor(mock) };
}

/** 子の提案の無い見本（親を取り下げられる形。子が無い） */
function parentOnly(): Record<string, FixtureBranch> {
  const f = fixture();
  delete f.i0001.files["wip/proposals/todo/i0001-01-01.md"];
  return f;
}

async function board(d: WriteDeps, repo: Partial<RepoConfig> = {}): Promise<RepoBoard> {
  return collectRepo({ ...REPO, ...repo }, d);
}

function shownOf(b: RepoBoard, family: string) {
  const r = b.families.find((f) => f.family.name === family)?.result;
  assert.ok(r?.batch, JSON.stringify(r));
  return (r.batch ?? []).map((e) => e.ticket);
}

const TODO = "wip/proposals/todo/i0001.md";
const DOING = ".ccnavi/approved/doing/i0001.md";
const EVENTS = ".ccnavi/approved/events/i0001.ndjson";
const WORKFLOW = ".ccnavi/approved/phases/i0001/workflow.yml";

test("CX-T100 承認は P への 1 コミット。条件は読んだ先頭、足す・消すは Python の Changes のとおり。書いた後の中身も確かめる", async () => {
  const { mock, d } = world();
  const before = mock.head("i0001");
  const proposal = mock.files("i0001")[TODO];
  const out = await approveFamily(REPO, "i0001", shownOf(await board(d), "i0001"), d);
  assert.equal(out.kind, "written", JSON.stringify(out));
  assert.equal(mock.commitCalls.length, 1);
  const call = mock.commitCalls[0];
  assert.equal(call.branch, "i0001");
  assert.equal(call.expected, before);
  assert.equal(call.headline, `ccnavi: i0001 を承認（Chrome 拡張 ${VERSION}）`);
  assert.deepEqual(call.deletions, [{ path: TODO }]);
  // 待ち方のファイルは書かない（承認済みチケットの計画の項の after から都度計算する）
  assert.deepEqual(call.additions.map((a) => a.path).sort(), [DOING, EVENTS].sort());
  // 書いた中身: 提案のままの承認済みチケット（欄を書き足さない）、状態の履歴（経路・アカウント・拡張の版）
  const files = mock.files("i0001");
  assert.ok(!(TODO in files));
  assert.equal(files[DOING], proposal);
  assert.doesNotMatch(files[DOING], /^ccnavi_approved:/m);
  assert.ok(!(WORKFLOW in files));
  const event = JSON.parse(files[EVENTS].trim().split("\n").pop() as string);
  assert.deepEqual({ kind: event.kind, via: event.via, actor: event.actor, version: event.version }, { kind: "approved", via: "chrome", actor: LOGIN, version: VERSION });
  // 統合先・ほかのブランチには書かない
  assert.ok(mock.commitCalls.every((c) => c.branch === "i0001"));
  // 承認した後のボード: 承認待ちから消え、作業中の承認済みチケットとして出る。子の提案があるので
  // 取り下げは出さない
  const after = (await board(d)).families.find((f) => f.family.name === "i0001")?.result;
  assert.deepEqual(after?.batch?.map((e) => e.ticket), []);
  // 理由には押した後の答えと同じ表記でコードが載る
  assert.deepEqual(after?.withdrawable?.map((w) => [w.ticket, w.problems]), [["i0001", ["[CORE_WITHDRAW_HAS_CHILDREN] todo/ に子の提案がある"]]]);
});

test("CX-T101 先頭が動いたら（expectedHeadOid の競合）新しい Snapshot で判定し直し、有効なら書く", async () => {
  const { mock, d } = world();
  const shown = shownOf(await board(d), "i0001");
  mock.beforeCommit = (b) => mock.push(b, { "src/app.py": "print('moved')\n" }, "コードだけの push");
  const out = await approveFamily(REPO, "i0001", shown, d);
  assert.equal(out.kind, "written", JSON.stringify(out));
  assert.equal(out.kind === "written" && out.rounds, 2);
  assert.deepEqual(mock.commitCalls.map((c) => c.result), ["stale", "written"]);
  assert.notEqual(mock.commitCalls[0].expected, mock.commitCalls[1].expected);
  // 相手の push を消していない
  assert.equal(mock.files("i0001")["src/app.py"], "print('moved')\n");
});

test("CX-T102 先頭が動いて承認待ちの中身も変わっても、現在の内容が有効なら新しい先頭で判定し直して書く", async () => {
  const { mock, d } = world();
  const shown = shownOf(await board(d), "i0001");
  mock.beforeCommit = (b) => mock.push(b, { [TODO]: mock.files(b)[TODO].replace("- **調べる**", "- **全部消す**") });
  const out = await approveFamily(REPO, "i0001", shown, d);
  assert.equal(out.kind, "written", JSON.stringify(out));
  assert.deepEqual(mock.commitCalls.map((c) => c.result), ["stale", "written"]);
  // 書いたのは現在の内容（表示後に変わった内容）
  assert.match(mock.files("i0001")[DOING], /全部消す/);
});

test("CX-T103 表示後に提案が変わっても有効なら承認する。承認待ちから消えた識別子・別の識別子の代わりは承認しない", async () => {
  // ① 表示後に提案が変わっても、有効なら承認される
  const a = world();
  const shownA = shownOf(await board(a.d), "i0001");
  a.mock.push("i0001", { [TODO]: a.mock.files("i0001")[TODO] + "\n追記\n" });
  const ok = await approveFamily(REPO, "i0001", shownA, a.d);
  assert.equal(ok.kind, "written", JSON.stringify(ok));
  assert.equal(a.mock.commitCalls.length, 1);

  // ② 承認待ちに無い識別子を指せば拒否し、何も書かない（別のものを代わりに承認しない）
  const b = world();
  const shownB = shownOf(await board(b.d), "i0001");
  const gone = await approveFamily(REPO, "i0001", [...shownB, "i0009"], b.d);
  assert.equal(gone.kind, "refused", JSON.stringify(gone));
  assert.equal(gone.kind === "refused" && gone.code, "CORE_APPROVE_NOTHING");
  assert.match(gone.message, /^\[CORE_APPROVE_NOTHING\] /);
  assert.equal(b.mock.commitCalls.length, 0);
  const none = await approveFamily(REPO, "i0001", [], b.d);
  assert.equal(none.kind, "refused");
  assert.equal(b.mock.commitCalls.length, 0);
});

test("CX-T103b 別の提案が増えても、指定した識別子だけを承認する", async () => {
  const { mock, d } = world();
  const shown = shownOf(await board(d), "i0001");
  const before = Object.keys(mock.files("i0001")).filter((p) => p.startsWith("wip/proposals/todo/"));
  assert.ok(before.length >= 1);
  // 指定を親だけにして、子の提案は残す
  const parentOnly = shown.filter((id) => id === "i0001");
  const out = await approveFamily(REPO, "i0001", parentOnly, d);
  if (out.kind === "written") {
    const files = mock.files("i0001");
    assert.ok(!(TODO in files));
    assert.ok(Object.keys(files).some((p) => p.startsWith("wip/proposals/todo/")), "指定していない提案は残る");
  } else {
    // 親だけでは検査に通らない構成なら、全体を拒否して何も書かない
    assert.equal(out.kind, "refused", JSON.stringify(out));
    assert.equal(mock.commitCalls.length, 0);
  }
});

test("CX-T104 決まらない親子のチケットでは書かない（読めない入力・ホストに無い P_X の古いチケット）", async () => {
  // 統合先の done/ に読めない（バイナリの）ファイル
  const bin = world();
  const shown = shownOf(await board(bin.d), "i0001");
  bin.mock.binaryBlobs.add(blobSha(bin.mock.files("main")[".ccnavi/approved/done/i0005.md"]));
  const fresh = depsFor(bin.mock);
  const b = await board(fresh);
  assert.match(b.families.find((f) => f.family.name === "i0001")?.result?.undecided ?? "", /判定できません/);
  const out = await approveFamily(REPO, "i0001", shown, fresh);
  assert.equal(out.kind, "refused");
  assert.match(out.kind === "refused" ? out.message : "", /バイナリ/);
  assert.equal(bin.mock.commitCalls.length, 0);

  // ホストに無い親子のチケット i0009 の古いチケットを P の上に持っていても、先行を満たしたとは数えない
  const f = fixture();
  const done = f.main.files[".ccnavi/approved/done/i0005-01-01.md"].replace(/i0005/g, "i0009");
  f.i0001.files[".ccnavi/approved/done/i0009-01-01.md"] = done;
  f.i0001.files["wip/proposals/todo/i0001-01-01.md"] = f.i0001.files["wip/proposals/todo/i0001-01-01.md"].replace("i0003-01-01", "i0009-01-01");
  const gone = world(f);
  const r = (await board(gone.d)).families.find((x) => x.family.name === "i0001")?.result;
  assert.deepEqual(r?.closure.absent, ["i0009"]);
  const rejected = r?.rejected?.find((x) => x.ticket === "i0001-01-01");
  assert.ok(rejected?.problems.some((p) => /i0009/.test(p) && /gone/.test(p)), JSON.stringify(r?.rejected));
});

test("CX-T105 互換の版が違えば承認も取り下げも書かない（7.3）。ボタンも出さない", async () => {
  const { mock, d } = world(fixture(COMPAT + 1));
  const b = await board(d);
  const r = b.families.find((f) => f.family.name === "i0001")?.result;
  assert.equal(r?.write?.allowed, false);
  assert.match(r?.write?.reason ?? "", /拡張を更新してください/);
  const out = await approveFamily(REPO, "i0001", shownOf(b, "i0001"), d);
  assert.equal(out.kind, "refused");
  assert.match(out.kind === "refused" ? out.message : "", /拡張を更新してください/);
  const w = await withdrawTicket(REPO, "i0001", "i0001", "", d);
  assert.equal(w.kind, "refused");
  assert.equal(mock.commitCalls.length, 0);
  const dom = new JSDOM("<!doctype html><body></body>");
  const md = createRenderer(dom.window as unknown as Parameters<typeof createRenderer>[0]);
  renderBoard(dom.window.document, md, b, { approve: () => undefined, withdraw: () => undefined });
  assert.equal(dom.window.document.querySelectorAll("button").length, 0);
});

test("CX-T106 取り下げ: 承認コミットの親の提案をバイト列のまま todo/ に戻し、doing/ を消す 1 コミット（待ち方のファイルは無い）", async () => {
  const { mock, d } = world(parentOnly());
  const original = mock.files("i0001")[TODO];
  assert.equal((await approveFamily(REPO, "i0001", shownOf(await board(d), "i0001"), d)).kind, "written");
  // 承認の後に関係の無い merge が積まれても、承認コミットを引ける
  mock.merge("i0001", "feature-x");
  const out = await withdrawTicket(REPO, "i0001", "i0001", "押し間違い", d);
  assert.equal(out.kind, "written", JSON.stringify(out));
  const call = mock.commitCalls[mock.commitCalls.length - 1];
  assert.equal(call.headline, `ccnavi: i0001 の承認を取り下げ（Chrome 拡張 ${VERSION}）`);
  assert.deepEqual(call.deletions, [{ path: DOING }]);
  const files = mock.files("i0001");
  assert.equal(files[TODO], original);
  assert.ok(!(DOING in files));
  assert.ok(!(WORKFLOW in files));
  const event = JSON.parse(files[EVENTS].trim().split("\n").pop() as string);
  assert.deepEqual(
    { kind: event.kind, via: event.via, actor: event.actor, version: event.version, reason: event.reason },
    { kind: "withdrawn", via: "chrome", actor: LOGIN, version: VERSION, reason: "押し間違い" },
  );
});

test("CX-T107 取り下げ: 承認が merge で入った（親が 2 つのコミットでしか足していない）なら取り下げない", async () => {
  const f = parentOnly();
  const side = { ...f.i0001.files };
  delete side[TODO];
  side[DOING] = f.i0001.files[TODO];
  f.side = { committedDate: "2026-09-01T00:00:00Z", files: side };
  const { mock, d } = world(f);
  mock.merge("i0001", "side");
  mock.push("i0001", { [TODO]: null });
  const out = await withdrawTicket(REPO, "i0001", "i0001", "", d);
  assert.equal(out.kind, "refused", JSON.stringify(out));
  assert.match(out.kind === "refused" ? out.message : "", /承認コミット/);
  assert.equal(mock.commitCalls.length, 0);
});

test("CX-T108 書けたのに応答だけが返らなかったら、先頭が書いたとおりか確かめて書けたとする（2 度書かない）", async () => {
  const { mock, d } = world();
  mock.loseCommitResponse = true;
  const out = await approveFamily(REPO, "i0001", shownOf(await board(d), "i0001"), d);
  assert.equal(out.kind, "written", JSON.stringify(out));
  assert.deepEqual(mock.commitCalls.map((c) => c.result), ["written"]);
});

test(`CX-T109 ${MAX_ROUNDS} 周しても先頭が動き続けたら、ユーザの対応に切り替える`, async () => {
  const { mock, d } = world();
  const shown = shownOf(await board(d), "i0001");
  let n = 0;
  const again = (b: string) => {
    mock.push(b, { "src/app.py": `print(${n++})\n` });
    mock.beforeCommit = again;
  };
  mock.beforeCommit = again;
  const out = await approveFamily(REPO, "i0001", shown, d);
  assert.equal(out.kind, "conflict");
  assert.equal(mock.commitCalls.length, MAX_ROUNDS);
  assert.ok(mock.commitCalls.every((c) => c.result === "stale"));
});

test("CX-T110 Changes を 1 コミットの足す・消すに分ける（本文は UTF-8 の base64、base64 の中身はそのまま。GitLab のために作るか書き換えるかも持つ）", () => {
  const { additions, deletions } = fileChanges([
    { op: "create", path: "a.md", content: "日本語\n" },
    { op: "update", path: "b.bin", base64: "AAEC" },
    { op: "delete", path: "c.md" },
  ]);
  assert.deepEqual(deletions, ["c.md"]);
  assert.deepEqual(additions, [
    { op: "create", path: "a.md", contents: Buffer.from("日本語\n").toString("base64") },
    { op: "update", path: "b.bin", contents: "AAEC" },
  ]);
  assert.match(localStamp(new Date(NOW)), /^2026-09-\d\dT\d\d:\d\d:\d\d[+-]\d{4}$/);
});

test("CX-T111 承認の画面（ボタンのある形）でも、悪意のある Markdown は消毒して描き、押しても何も動かない", async () => {
  const { d } = world();
  const b = await board(d);
  const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only" });
  const doc = dom.window.document;
  const md = createRenderer(dom.window as unknown as Parameters<typeof createRenderer>[0]);
  const pressed: string[] = [];
  renderBoard(doc, md, b, { approve: (_, f) => pressed.push(`approve ${f.family.name}`), withdraw: () => undefined });
  const box = doc.querySelector('[data-family="i0002"]');
  assert.ok(box);
  assert.equal(box.querySelectorAll("script, img, svg, iframe, form, style, input").length, 0);
  const buttons = [...doc.querySelectorAll<HTMLButtonElement>("button[data-action=approve]")];
  assert.deepEqual(buttons.map((x) => x.closest<HTMLElement>("[data-family]")?.dataset.family), ["i0001", "i0002"]);
  for (const x of buttons) x.click();
  for (const a of box.querySelectorAll("a")) a.dispatchEvent(new dom.window.MouseEvent("click"));
  assert.deepEqual(pressed, ["approve i0001", "approve i0002"]);
  assert.equal((dom.window as unknown as { __pwned?: string }).__pwned, undefined);
  const attrs = [...box.querySelectorAll("*")].flatMap((e) => [...e.attributes].map((a) => a.name));
  assert.ok(!attrs.some((n) => /^on/i.test(n) || n === "style"), attrs.join(","));
});
