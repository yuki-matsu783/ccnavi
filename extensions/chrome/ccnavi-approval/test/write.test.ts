/**
 * Chrome からの承認。模擬の GitHub と Node の上の Pyodide（拡張と同じ zip）で回す。
 *
 * - 1 コミットの組み立て（`createCommitOnBranch`、`expectedHeadOid` = 読んだ P の先頭）と、書いた後の確かめ
 * - 先頭が動いたら新しい Snapshot で判定と plan をやり直す。ダイジェストが同じなら見せ直さずに書き、違えば書かない
 * - 見せたダイジェストが違えば書かない。決まらない親子のチケット・版ずれでは書かない
 */
import { before, test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import type { PyCall } from "../src/core/py.js";
import { renderBoard } from "./helpers/board-dom.js";
import { createRenderer } from "../src/core/sanitize.js";
import type { RepoConfig } from "../src/core/settings.js";
import { collectRepo, type RepoBoard } from "../src/core/snapshot.js";
import { approveFamily, fileChanges, localStamp, MAX_ROUNDS, type WriteDeps } from "../src/core/write.js";
import { fixture, NOW, type FixtureBranch } from "./fixtures/repo.js";
import { deps, hostCall, memoryCache, newStats } from "./helpers/host.js";
import { COMPAT } from "./helpers/compat.js";
import { blobSha, MockGitHub, TOKEN } from "./helpers/mock-github.js";
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

async function board(d: WriteDeps, repo: Partial<RepoConfig> = {}): Promise<RepoBoard> {
  return collectRepo({ ...REPO, ...repo }, d);
}

function shownOf(b: RepoBoard, family: string) {
  const r = b.families.find((f) => f.family.name === family)?.result;
  assert.ok(r?.digest, JSON.stringify(r));
  return { ids: (r.batch ?? []).map((e) => e.ticket), digest: r.digest, only: r.only ?? null };
}

const TODO = "wip/proposals/todo/i0001.md";
const DOING = ".ccnavi/approved/doing/i0001.md";
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
  assert.deepEqual(call.additions.map((a) => a.path).sort(), [DOING].sort());
  // 書いた中身: 提案のままの承認済みチケット（欄を書き足さない）
  const files = mock.files("i0001");
  assert.ok(!(TODO in files));
  assert.equal(files[DOING], proposal);
  assert.doesNotMatch(files[DOING], /^ccnavi_approved:/m);
  assert.ok(!(WORKFLOW in files));
  // 統合先・ほかのブランチには書かない
  assert.ok(mock.commitCalls.every((c) => c.branch === "i0001"));
  // 承認した後のボード: 承認待ちから消える
  const after = (await board(d)).families.find((f) => f.family.name === "i0001")?.result;
  assert.deepEqual(after?.batch?.map((e) => e.ticket), []);
});

test("CX-T101 先頭が動いたら（expectedHeadOid の競合）新しい Snapshot で判定し直し、ダイジェストが同じなら見せ直さずに書く", async () => {
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

test("CX-T102 先頭が動いて承認待ちの中身も変わったら、書かずに preview からやり直させる", async () => {
  const { mock, d } = world();
  const shown = shownOf(await board(d), "i0001");
  mock.beforeCommit = (b) => mock.push(b, { [TODO]: mock.files(b)[TODO].replace("- **調べる**", "- **全部消す**") });
  const out = await approveFamily(REPO, "i0001", shown, d);
  assert.equal(out.kind, "changed", JSON.stringify(out));
  assert.equal(out.kind === "changed" && out.code, "CORE_APPROVE_DIGEST_CHANGED");
  assert.deepEqual(mock.commitCalls.map((c) => c.result), ["stale"]);
  assert.ok(!(DOING in mock.files("i0001")));
});

test("CX-T103 見せたダイジェストが今の中身と違えば、1 つも書かない", async () => {
  const { mock, d } = world();
  const shown = shownOf(await board(d), "i0001");
  const out = await approveFamily(REPO, "i0001", { ...shown, digest: "0".repeat(64) }, d);
  assert.equal(out.kind, "changed");
  // 分岐の材料は鍵の code。文面の頭にも同じコードが載る
  assert.equal(out.kind === "changed" && out.code, "CORE_APPROVE_DIGEST_CHANGED");
  assert.match(out.message, /^\[CORE_APPROVE_DIGEST_CHANGED\] /);
  // 見せた識別子が今の一覧と違えば、中身より先にそちらの種別
  const listed = await approveFamily(REPO, "i0001", { ...shown, ids: [...shown.ids, "i0009"] }, d);
  assert.equal(listed.kind === "changed" && listed.code, "CORE_APPROVE_LIST_CHANGED");
  mock.push("i0001", { [TODO]: mock.files("i0001")[TODO] + "\n追記\n" });
  const again = await approveFamily(REPO, "i0001", shown, d);
  assert.equal(again.kind, "changed");
  assert.equal(mock.commitCalls.length, 0);
});

test("CX-T103b 表示した後で提案のバイト列が変わったら（改行・BOM・本文）、書かずに表示し直させる。承認済みチケットだけの変化は照合しない", async () => {
  for (const [label, edit] of [
    ["CRLF", (t: string) => t.replace(/\n/g, "\r\n")],
    ["本文", (t: string) => t.replace("- **調べる**", "- **全部消す**")],
  ] as const) {
    const { mock, d } = world();
    const shown = shownOf(await board(d), "i0001");
    mock.push("i0001", { [TODO]: edit(mock.files("i0001")[TODO]) });
    const out = await approveFamily(REPO, "i0001", shown, d);
    assert.equal(out.kind, "changed", `${label}: ${JSON.stringify(out)}`);
    assert.match(out.kind === "changed" ? out.message : "", /^\[CORE_APPROVE_DIGEST_CHANGED\] 内容が更新されました/, label);
    assert.equal(mock.commitCalls.length, 0, label);
    assert.ok(!(DOING in mock.files("i0001")), label);
  }
  // BOM を足すと親の提案を frontmatter として読めなくなる。表示し直す前に、書かずに止まる
  {
    const { mock, d } = world();
    const shown = shownOf(await board(d), "i0001");
    mock.push("i0001", { [TODO]: "\uFEFF" + mock.files("i0001")[TODO] });
    const out = await approveFamily(REPO, "i0001", shown, d);
    assert.notEqual(out.kind, "written", JSON.stringify(out));
    assert.equal(mock.commitCalls.length, 0);
    assert.ok(!(DOING in mock.files("i0001")));
  }
  // 提案の外（コード）が動いただけなら、見せ直さずに書く（CX-T101）
  const { mock, d } = world();
  const shown = shownOf(await board(d), "i0001");
  mock.push("i0001", { "docs/note.md": "x\n" });
  const out = await approveFamily(REPO, "i0001", shown, d);
  assert.equal(out.kind, "written", JSON.stringify(out));
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
  assert.ok(rejected?.problems.some((p) => /i0009/.test(p) && /親のワークツリー（.claude\/worktrees\/i0009）がありません/.test(p)), JSON.stringify(r?.rejected));
});

test("CX-T105 互換の版が違えば承認を書かない（7.3）。ボタンも出さない", async () => {
  const { mock, d } = world(fixture(COMPAT + 1));
  const b = await board(d);
  const r = b.families.find((f) => f.family.name === "i0001")?.result;
  assert.equal(r?.write?.allowed, false);
  assert.match(r?.write?.reason ?? "", /拡張を更新してください/);
  const out = await approveFamily(REPO, "i0001", shownOf(b, "i0001"), d);
  assert.equal(out.kind, "refused");
  assert.match(out.kind === "refused" ? out.message : "", /拡張を更新してください/);
  assert.equal(mock.commitCalls.length, 0);
  const dom = new JSDOM("<!doctype html><body></body>");
  const md = createRenderer(dom.window as unknown as Parameters<typeof createRenderer>[0]);
  renderBoard(dom.window.document, md, b, { approve: () => undefined });
  assert.equal(dom.window.document.querySelectorAll("button").length, 0);
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
  renderBoard(doc, md, b, { approve: (_, f) => pressed.push(`approve ${f.family.name}`) });
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
