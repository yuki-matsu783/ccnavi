/**
 * 安全性: 読み取りの操作から書き込みのコマンドを呼ばない。
 *
 * マージ状況の確認（`mr-state` と `sync retired`）、残った指摘の読み取り（`decide <N> --preview`）、相談のプロンプトは読むだけ。
 * 書き込むコマンド（`ccnavi-sync.sh` の片付け、`ccnavi-user-decide.sh` の確定）は、ユーザが確認のダイアログや確定のボタンで
 * 答えたあとにだけ呼ぶ。ここはソースの形で、その線を保つ（VS Code の API に触れる部分は単体では動かせないため）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

import { decidePreviewArgs, mrStateArgs, syncRetiredArgs } from "../../src/core/commands.js";

const SRC = path.join(__dirname, "..", "..", "..", "src");
const read = (name: string): string => fs.readFileSync(path.join(SRC, name), "utf8");

/** 関数 1 つの本体（宣言から、行頭の `}` まで） */
function bodyOf(source: string, header: RegExp): string {
  const start = source.search(header);
  assert.ok(start >= 0, `見つからない: ${header}`);
  const end = source.indexOf("\n}\n", start);
  assert.ok(end > start);
  return source.slice(start, end);
}

/** `case "<名前>": { … }` の塊（次の case か default まで） */
function caseOf(source: string, name: string): string {
  const start = source.indexOf(`case "${name}":`);
  assert.ok(start >= 0, `見つからない: case "${name}"`);
  const rest = source.slice(start + 1);
  const next = rest.search(/\n {4}case "|\n {4}default:/);
  return source.slice(start, start + 1 + (next < 0 ? rest.length : next));
}

const WRITES = /ccnavi-sync\.sh|USER_DECIDE_SCRIPT|ccnavi-user-decide|runInTerminal|retire|--abandon|confirm|--choices|--digest|user agree|--yes/;

test("CB-T640 読み取りの引数に書き込みの語は無い（mr-state・sync retired・decide --preview）", () => {
  for (const args of [mrStateArgs(1), syncRetiredArgs(), decidePreviewArgs(2)]) {
    assert.ok(!args.some((a) => WRITES.test(a) && a !== "retired"), args.join(" "));
  }
  assert.deepEqual(mrStateArgs(1), ["mr-state", "1"]);
  assert.deepEqual(syncRetiredArgs(), ["sync", "retired"], "読むだけの問い合わせ。--abandon や片付けの語を渡さない");
  assert.deepEqual(decidePreviewArgs(2), ["decide", "2", "--preview"]);
});

test("CB-T641 実行ファイルを呼ぶ読み取り（runMrState・runSyncRetired・runDecidePreview）は、書き込みのスクリプトを呼ばない", () => {
  const source = read("ccnavi.ts");
  const mr = bodyOf(source, /export async function runMrState\(/);
  const retired = bodyOf(source, /export async function runSyncRetired\(/);
  const preview = bodyOf(source, /export async function runDecidePreview\(/);
  assert.ok(mr.includes("REVIEW_SCRIPT") && mr.includes("mrStateArgs"));
  assert.ok(retired.includes("syncRetiredArgs"));
  assert.ok(preview.includes("REVIEW_SCRIPT") && preview.includes("decidePreviewArgs"));
  for (const [name, body] of [["runMrState", mr], ["runSyncRetired", retired], ["runDecidePreview", preview]] as const) {
    assert.doesNotMatch(body.replace(/syncRetiredArgs|\/\*[\s\S]*?\*\//g, ""), WRITES, `${name} が書き込みを呼んでいる`);
  }
});

test("CB-T642 ボード側: 読み取りの効果（loadList・loadState・loadDecide・copyConsult）はターミナルに送らない。runInTerminal は承認の push と、片付けの送信（ダイアログで答えたあと）だけ", () => {
  const panel = read("board-panel.ts");
  for (const name of ["loadList", "loadState", "confirmRetire", "loadDecide", "copyConsult"]) {
    assert.doesNotMatch(caseOf(panel, name), /runInTerminal|retireCommand|ccnavi-sync/, `case ${name}`);
  }
  const callers = [...panel.matchAll(/runInTerminal\(/g)].length;
  assert.equal(callers, 2, "ターミナルへ送るのは承認の push（carry）と片付け（sendRetire）の 2 か所だけ");
  assert.match(caseOf(panel, "carry"), /runInTerminal\(root, pushApprovedCommand/);
  assert.match(caseOf(panel, "sendRetire"), /runInTerminal\(root, retireCommand\(root, effect\.parent\)\)/);
  // 破棄した親の記録を消すコマンドは、コピーするだけ（送らない）
  const abandon = bodyOf(panel, /async function copyAbandon\(/);
  assert.match(abandon, /copyCommand\(abandonCommand/);
  assert.doesNotMatch(abandon, /runInTerminal/);
  // 画面から届く片付けの操作は、遷移（merge.ts）を通る。遷移が「実行する」の答えの後にだけ送る効果を返す
  assert.match(panel, /case "mergeRetire":[\s\S]*?dispatchMerge\(current, \{ kind: "retire"/);
});

test("CB-T643 画面から届いたメッセージは形を確かめてから使う（親の識別子の形でないものはコマンド行に入れない）", () => {
  const panel = read("board-panel.ts");
  const known = bodyOf(panel, /function asMessage\(/);
  assert.match(known, /case "mergeRetire":\s*case "mergeCopyAbandon":[\s\S]*?isTicketId\(m\.parent\)/);
  assert.match(panel, /mergeCheck: true,\s*mergeRetire: true,\s*mergeCopyAbandon: true/);
});
