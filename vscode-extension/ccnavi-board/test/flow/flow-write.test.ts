/**
 * フローのファイルの読み書き（`src/core/flow-write.ts`）。リンクを辿らず、入れ替えで書き、外の変更を上書きしない。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { connectionLabel, connectionsOf, templateFlow, type FlowDoc } from "../../src/core/flow-doc.js";
import { spawnSync } from "node:child_process";
import { decodeFlowBytes, FLOW_FILE_LIMIT, linkedSegment, readFlowFile, removeDraftFile, writeFlowFile } from "../../src/core/flow-write.js";
import * as crypto from "node:crypto";

function scratch(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-flow-write-"));
  return fs.realpathSync(dir);
}

function place(tree: string, name = "i0001-01"): string {
  return path.join(tree, ".ccnavi", "approved", "flows", `${name}.yml`);
}

const NEW = { exists: false, mtimeMs: 0 } as const;

test("CB-T231 無い置き場は 1 段ずつ作って入れ替えで書く。一時ファイルは残らない。読むとその中身", () => {
  const tree = scratch();
  const file = place(tree);
  const written = writeFlowFile(tree, file, '{"nodes":[]}\n', NEW);
  assert.deepEqual(written, { ok: true });
  assert.equal(fs.readFileSync(file, "utf8"), '{"nodes":[]}\n');
  assert.deepEqual(fs.readdirSync(path.dirname(file)), ["i0001-01.yml"]);
  const read = readFlowFile(tree, file);
  assert.ok(read !== undefined);
  assert.equal(Buffer.from(read.bytes).toString("utf8"), '{"nodes":[]}\n');
  // 読んだときのままなら上書きする
  const again = writeFlowFile(tree, file, '{"nodes":[1]}\n', { exists: true, mtimeMs: read.mtimeMs });
  assert.deepEqual(again, { ok: true });
  assert.equal(fs.readFileSync(file, "utf8"), '{"nodes":[1]}\n');
  // 無いファイルは undefined（フローは任意）
  assert.equal(readFlowFile(tree, place(tree, "i0001-09")), undefined);
});

test("CB-T232 ファイルか途中のディレクトリがリンクなら、読まないし書かない。リンクの先も変わらない", () => {
  const tree = scratch();
  const outside = scratch();
  const target = path.join(outside, "real.yml");
  fs.writeFileSync(target, "keep");
  // ファイルがリンク
  const file = place(tree);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.symlinkSync(target, file);
  assert.equal(linkedSegment(tree, file), file);
  const first = writeFlowFile(tree, file, "x", { exists: true, mtimeMs: fs.statSync(target).mtimeMs });
  assert.equal(first.ok, false);
  assert.match(first.ok ? "" : first.error, /シンボリックリンク/);
  assert.equal(fs.readFileSync(target, "utf8"), "keep");
  assert.throws(() => readFlowFile(tree, file), /シンボリックリンク/);
  // 途中のディレクトリ（flows/）がリンク
  const other = scratch();
  const flows = path.join(other, ".ccnavi", "approved", "flows");
  fs.mkdirSync(path.dirname(flows), { recursive: true });
  fs.symlinkSync(outside, flows);
  const second = writeFlowFile(other, place(other), "x", NEW);
  assert.equal(second.ok, false);
  assert.equal(linkedSegment(other, place(other)), flows);
  assert.deepEqual(fs.readdirSync(outside), ["real.yml"]);
  // ツリーの外は書かない
  const third = writeFlowFile(tree, path.join(outside, "x.yml"), "x", NEW);
  assert.equal(third.ok, false);
  assert.match(third.ok ? "" : third.error, /ツリーの外/);
});

test("CB-T233 読み込んでから外で作られた・変わったファイルは上書きしない", () => {
  const tree = scratch();
  const file = place(tree);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, "theirs");
  const created = writeFlowFile(tree, file, "mine", NEW);
  assert.equal(created.ok, false);
  assert.match(created.ok ? "" : created.error, /外で作られています/);
  const changed = writeFlowFile(tree, file, "mine", { exists: true, mtimeMs: 1 });
  assert.equal(changed.ok, false);
  assert.match(changed.ok ? "" : changed.error, /外で変更されています/);
  assert.equal(fs.readFileSync(file, "utf8"), "theirs");
});

test("CB-T234 線の言葉は、出口が項目の id とちょうど同じか branch-<番号> のときだけ（実行ファイルと同じ読み方）", () => {
  const base = templateFlow("i0001-01", "調査");
  const doc: FlowDoc = {
    ...base,
    nodes: [
      ...base.nodes,
      {
        id: "q",
        type: "askUserQuestion",
        name: "q",
        position: { x: 0, y: 0 },
        data: { options: [{ id: "a", label: "YES" }, { id: "b", label: "NO" }] },
      },
    ],
    connections: [
      { id: "c1", from: "q", to: "end", fromPort: "branch-0", toPort: "input" },
      { id: "c2", from: "q", to: "end", fromPort: "b", toPort: "input" },
      { id: "c3", from: "q", to: "end", fromPort: "xbranch-1", toPort: "input" },
      { id: "c4", from: "q", to: "end", fromPort: "port1", toPort: "input" },
    ],
  };
  const labels = connectionsOf(doc).map((c) => connectionLabel(doc, c));
  assert.deepEqual(labels, ["YES", "NO", "", ""]);
});

test("CB-T235 ハードリンクのフローは読まないし書かない。別名の中身も変わらない", () => {
  const tree = scratch();
  const file = place(tree);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, '{"nodes":[]}');
  const alias = path.join(tree, "alias.yml");
  fs.linkSync(file, alias);
  assert.throws(() => readFlowFile(tree, file), /ハードリンク/);
  const written = writeFlowFile(tree, file, '{"nodes":[1]}', { exists: true, mtimeMs: fs.lstatSync(file).mtimeMs });
  assert.equal(written.ok, false);
  assert.match(written.ok ? "" : written.error, /ハードリンク/);
  assert.equal(fs.readFileSync(alias, "utf8"), '{"nodes":[]}');
  assert.deepEqual(fs.readdirSync(path.dirname(file)), ["i0001-01.yml"]);
});

test("CB-T236 名前付きパイプは読まずに戻る（開いて待たない）", { skip: process.platform === "win32" }, () => {
  const tree = scratch();
  const file = place(tree);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const made = spawnSync("mkfifo", [file]);
  if (made.status !== 0) {
    return; // mkfifo が無い機械
  }
  assert.throws(() => readFlowFile(tree, file), /ふつうのファイルではない/);
  const written = writeFlowFile(tree, file, "{}", { exists: true, mtimeMs: fs.lstatSync(file).mtimeMs });
  assert.equal(written.ok, false);
});

test("CB-T237 256KB を超えるフローは読まないし書かない。書けなかったとき一時ファイルは残らない", () => {
  const tree = scratch();
  const file = place(tree);
  const big = `{"nodes":[],"pad":"${"x".repeat(FLOW_FILE_LIMIT)}"}`;
  const refused = writeFlowFile(tree, file, big, NEW);
  assert.equal(refused.ok, false);
  assert.match(refused.ok ? "" : refused.error, /大きすぎる/);
  assert.deepEqual(fs.readdirSync(path.dirname(file)), []);
  fs.writeFileSync(file, big);
  assert.throws(() => readFlowFile(tree, file), /大きすぎる/);
  // 入れ替えの直前に外で作られた（2 度目の確かめで止まる）ときも、一時ファイルは消す
  fs.rmSync(file);
  const raced = writeFlowFile(tree, file, "{}", { exists: true, mtimeMs: 1 });
  assert.equal(raced.ok, false);
  assert.deepEqual(fs.readdirSync(path.dirname(file)), []);
});

test("CB-T238 線の言葉は真偽値を空として読み、整数の値は表記で比べる（実行ファイルの flow._text と同じ）", () => {
  const base = templateFlow("i0001-01", "調査");
  const doc: FlowDoc = {
    ...base,
    nodes: [
      ...base.nodes,
      {
        id: "q",
        type: "ifElse",
        name: "q",
        position: { x: 0, y: 0 },
        data: { branches: [{ id: true, label: "T" }, { id: 1, label: "ONE" }, { id: "x", label: false }] },
      },
    ],
    connections: [
      { id: "c1", from: "q", to: "end", fromPort: true, toPort: "input" },
      { id: "c2", from: "q", to: "end", fromPort: "true", toPort: "input" },
      { id: "c3", from: "q", to: "end", fromPort: "1", toPort: "input" },
      { id: "c4", from: "q", to: "end", fromPort: "x", toPort: "input" },
      { id: "c5", from: "q", to: "end", fromPort: "branch-0", toPort: "input", condition: true },
      { id: "c6", from: "q", to: "end", fromPort: "branch-0", toPort: "input", condition: 2 },
    ],
  };
  const labels = connectionsOf(doc).map((c) => connectionLabel(doc, c));
  assert.deepEqual(labels, ["", "", "ONE", "", "T", "2"]);
});

test("CB-T245 読んだバイトは UTF-8 として壊れていれば文字にしない（置き換え文字で埋めない）。BOM は 1 つ外す", () => {
  const tree = scratch();
  const file = place(tree);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const broken = Buffer.concat([Buffer.from("nodes:\n  - id: a\n    name: "), Buffer.from([0xff, 0xfe]), Buffer.from("\n")]);
  fs.writeFileSync(file, broken);
  const read = readFlowFile(tree, file);
  assert.ok(read !== undefined);
  // 読むのはバイトのまま（実行ファイルにこのバイトを確かめさせる）
  assert.deepEqual(Buffer.from(read.bytes), broken);
  assert.deepEqual(decodeFlowBytes(read.bytes), { ok: false, error: "UTF-8 として読めません" });
  // 実行ファイル（utf-8-sig）と同じく、先頭の BOM は 1 つだけ外す
  assert.deepEqual(decodeFlowBytes(Buffer.from("\uFEFFnodes: []\n", "utf8")), { ok: true, text: "nodes: []\n" });
  assert.deepEqual(decodeFlowBytes(Buffer.from("\uFEFF\uFEFFx", "utf8")), { ok: true, text: "\uFEFFx" });
  // 途中で切れた多バイト文字も UTF-8 として読めない
  assert.equal(decodeFlowBytes(Buffer.from("あ", "utf8").subarray(0, 2)).ok, false);
});

function draftPlace(tree: string, name = "i0001-01"): string {
  return path.join(tree, "wip", "proposals", "flows", `${name}.yml`);
}

function sha(text: string): string {
  return crypto.createHash("sha256").update(text).digest("hex");
}

test("CB-T297 取り込んだ下書きは、中身が取り込んだときと同じときだけ消す。書き直されていれば消さない。無ければ何もしない", () => {
  const tree = scratch();
  const file = draftPlace(tree);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, "nodes: []\n");
  // 取り込んだあとにエージェントが書き直した
  fs.writeFileSync(file, "nodes: [x]\n");
  const rewritten = removeDraftFile(tree, file, sha("nodes: []\n"));
  assert.equal(rewritten.ok, false);
  assert.match(rewritten.ok ? "" : rewritten.error, /書き直されている/);
  assert.equal(fs.readFileSync(file, "utf8"), "nodes: [x]\n");
  // 同じ中身なら消す
  assert.deepEqual(removeDraftFile(tree, file, sha("nodes: [x]\n")), { ok: true, removed: true });
  assert.equal(fs.existsSync(file), false);
  // もう無ければ何もしない
  assert.deepEqual(removeDraftFile(tree, file, sha("nodes: [x]\n")), { ok: true, removed: false });
});

test("CB-T298 リンク・ハードリンク・ふつうのファイルでないもの・ツリーの外の下書きは消さない", () => {
  const tree = scratch();
  const outside = scratch();
  const target = path.join(outside, "real.yml");
  fs.writeFileSync(target, "keep");
  // ファイルがリンク
  const file = draftPlace(tree);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.symlinkSync(target, file);
  const linked = removeDraftFile(tree, file, sha("keep"));
  assert.equal(linked.ok, false);
  assert.match(linked.ok ? "" : linked.error, /シンボリックリンク/);
  assert.ok(fs.lstatSync(file).isSymbolicLink());
  assert.equal(fs.readFileSync(target, "utf8"), "keep");
  // 途中のディレクトリがリンク
  const other = scratch();
  const flows = path.join(other, "wip", "proposals", "flows");
  fs.mkdirSync(path.dirname(flows), { recursive: true });
  fs.symlinkSync(outside, flows);
  const through = removeDraftFile(other, path.join(flows, "real.yml"), sha("keep"));
  assert.equal(through.ok, false);
  assert.equal(fs.readFileSync(target, "utf8"), "keep");
  // ハードリンク
  const hard = draftPlace(tree, "i0001-02");
  fs.writeFileSync(hard, "nodes: []\n");
  fs.linkSync(hard, path.join(outside, "alias.yml"));
  const hardlinked = removeDraftFile(tree, hard, sha("nodes: []\n"));
  assert.equal(hardlinked.ok, false);
  assert.match(hardlinked.ok ? "" : hardlinked.error, /ハードリンク/);
  assert.ok(fs.existsSync(hard));
  // ツリーの外
  const away = removeDraftFile(tree, target, sha("keep"));
  assert.equal(away.ok, false);
  assert.ok(fs.existsSync(target));
  // ふつうのファイルでない（名前付きパイプ）
  const fifo = draftPlace(tree, "i0001-03");
  const made = spawnSync("mkfifo", [fifo]);
  if (made.status === 0) {
    const piped = removeDraftFile(tree, fifo, sha(""));
    assert.equal(piped.ok, false);
    assert.ok(fs.existsSync(fifo));
  }
});

test("CB-T300 確かめてから消すまでの間に書き直された下書きは、移したものを確かめ直して元へ戻し、消さない", () => {
  const tree = scratch();
  const file = draftPlace(tree);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, "nodes: []\n");
  // import の名前空間は読むだけなので、本体の node:fs を差し替える（ソースの fs は呼ぶたびに本体を引く）
  const real = require("node:fs") as { renameSync: typeof fs.renameSync };
  const original = real.renameSync;
  // 照合のあと、移す直前にエージェントが書き直した、を真似る
  real.renameSync = (from, to) => {
    if (String(from) === file) {
      fs.writeFileSync(file, "nodes: [new]\n");
    }
    original(from, to);
  };
  try {
    const raced = removeDraftFile(tree, file, sha("nodes: []\n"));
    assert.equal(raced.ok, false);
    assert.match(raced.ok ? "" : raced.error, /書き直されている/);
  } finally {
    real.renameSync = original;
  }
  assert.equal(fs.readFileSync(file, "utf8"), "nodes: [new]\n");
  // 移した名前（.*.removing）は残らない
  assert.deepEqual(fs.readdirSync(path.dirname(file)), ["i0001-01.yml"]);
  // 消せたときも残らない
  assert.deepEqual(removeDraftFile(tree, file, sha("nodes: [new]\n")), { ok: true, removed: true });
  assert.deepEqual(fs.readdirSync(path.dirname(file)), []);
});
