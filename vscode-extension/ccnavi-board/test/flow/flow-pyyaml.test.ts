/**
 * 画面が書き出す本文（`serializeFlow`）を、実行ファイルの読み手（PyYAML。`ccnavi/flow.py` の `parse`）で実際に読み戻す。
 * 読み戻した中身（`flow.as_json`。`--lint --json --flow` の `flow.data` と同じ形）が画面の中身と同じかを
 * `flow-agree.ts` の見比べで確かめる。乱数の入力（固定の種）でも確かめる。
 *
 * 実行ファイルの読み手を起こすのは、リポジトリのルートで `uv run python`（無ければ `python3`）。どちらも起こせない
 * 環境では飛ばす（理由を出す）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import * as path from "node:path";

import { flowDisagreement } from "../../src/core/flow-agree.js";
import { parseFlowValue, serializeFlow, type FlowDoc } from "../../src/core/flow-doc.js";

/** リポジトリのルート（`out/test/flow/` から 5 段上） */
const REPO_ROOT = path.join(__dirname, "..", "..", "..", "..", "..");

const SCRIPT = `
import base64, json, sys
from ccnavi import flow
out = []
for item in json.load(sys.stdin):
    data, why = flow.parse(base64.b64decode(item))
    out.append({"why": why, "data": flow.as_json(data) if not why else None})
sys.stdout.write(json.dumps(out, ensure_ascii=True))
`;

interface Read {
  readonly why: string;
  readonly data: unknown;
}

type Reader = { readonly ok: true; readonly read: (bodies: readonly Uint8Array[]) => Read[] } | { readonly ok: false; readonly why: string };

function pyyaml(): Reader {
  const tries: readonly (readonly string[])[] = [
    ["uv", "run", "python", "-c", SCRIPT],
    ["python3", "-c", SCRIPT],
  ];
  const said: string[] = [];
  for (const [command, ...args] of tries) {
    const run = (input: string) =>
      spawnSync(command, args, {
        cwd: REPO_ROOT,
        input,
        encoding: "utf8",
        maxBuffer: 64 * 1024 * 1024,
        env: { ...process.env, PYTHONPATH: REPO_ROOT, PYTHONUTF8: "1" },
      });
    const probe = run("[]");
    if (probe.status === 0 && probe.stdout.trim() === "[]") {
      return {
        ok: true,
        read: (bodies) => {
          const ran = run(JSON.stringify(bodies.map((b) => Buffer.from(b).toString("base64"))));
          assert.equal(ran.status, 0, ran.stderr);
          return JSON.parse(ran.stdout) as Read[];
        },
      };
    }
    said.push(`${command}: ${probe.error?.message ?? probe.stderr.split("\n").filter(Boolean).pop() ?? `終了 ${probe.status}`}`);
  }
  return { ok: false, why: `実行ファイルの読み手（PyYAML）を起こせないので飛ばす（${said.join(" / ")}）` };
}

const reader = pyyaml();

function readBack(docs: readonly unknown[]): Read[] {
  assert.ok(reader.ok);
  return reader.read(docs.map((doc) => Buffer.from(serializeFlow(doc as unknown as FlowDoc), "utf8")));
}

function node(id: string, data: Record<string, unknown>): { readonly id: string; readonly [key: string]: unknown } {
  return { id, type: "prompt", name: id, position: { x: 80, y: 160 }, data };
}

test("CB-T248 PyYAML が裸や `|` では読めない・別の文字に読む文字列を、書き出しが読める形で書く", { skip: reader.ok ? false : reader.why }, () => {
  const values = [
    "a\tb",
    "\tlead",
    "trail\t",
    "a b",
    "a b",
    "a\u0085b",
    "\u0085",
    " \n",
    " \nx",
    "  \n\n",
    "x\n \ny",
    "x  \ny",
    "x\ny  ",
    "x\n\n\n",
    "\n",
    "\n\nx",
    "a\r\nb",
    "a\rb",
    "a\x00b\x01\x1b",
    "a\x7fb\x80\x84\x86\x9f",
    "￾￿",
    "\ud800",
    "x\udc00",
    "😀 ﻿",
    "  - 箇条\n  - 書き",
    "1 行目\n  字下げ\n戻る",
    "yes",
    "0755",
    "1:30",
    "2026-01-01",
    "<<",
    "=",
    "",
    "~",
    "'\"\\",
  ];
  const docs = values.map((v, i) => ({ id: `f${i}`, nodes: [node(`n-${i}`, { prompt: v, [v === "" ? "empty" : v]: v })], connections: [] }));
  const reads = readBack(docs);
  reads.forEach((read, i) => {
    assert.equal(read.why, "", `${JSON.stringify(values[i])}: ${read.why}\n${serializeFlow(docs[i] as unknown as FlowDoc)}`);
    assert.equal(flowDisagreement(docs[i], read.data), undefined, `${JSON.stringify(values[i])}\n${serializeFlow(docs[i] as unknown as FlowDoc)}`);
  });
  // 1 行の文字列のタブは裸で書かない（JSON のときからの退行）
  assert.match(serializeFlow({ nodes: [node("a", { prompt: "a\tb" })] }), /prompt: "a\\tb"/);
  assert.match(serializeFlow({ nodes: [node("a", { prompt: "a\u0085b" })] }), /prompt: "a\\Nb"/);
});

/** 固定の種の乱数（mulberry32） */
function random(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const PIECES = [
  "", " ", "  ", "\t", "\n", "\n\n", "\r", "\r\n", "\u0085", " ", " ", " ", "﻿", "\x00", "\x01", "\x1b", "\x7f", "\x80",
  "\x9f", "￾", "\ud800", "\udc00", "😀", "あ", "#", " #", ":", ": ", "-", "- ", "?", "? ", "'", '"', "\\", "|", ">", "&a", "*a", "!",
  "!!str", "%", "@", "`", "{", "}", "[", "]", ",", "yes", "No", "on", "OFF", "y", "n", "~", "null", "Null", "true", "0755", "0o17", "0x1F",
  "0b101", "1e3", "1_000", "1:30", "190:20:30", "2026-01-01", "2026-01-01 10:00:00", "1.", ".5", "-.5", "+1", "1.5e+3", ".inf", ".NaN",
  "<<", "=", "---", "...", "a", "b", "xyz", "プロンプト", "\\n", "\\t",
];

function pick<T>(rand: () => number, items: readonly T[]): T {
  return items[Math.floor(rand() * items.length)];
}

function text(rand: () => number): string {
  const n = Math.floor(rand() * 6);
  let out = "";
  for (let i = 0; i < n; i += 1) {
    out += pick(rand, PIECES);
  }
  return out;
}

function key(rand: () => number): string {
  const k = text(rand);
  return k === "__proto__" ? "proto" : k;
}

function scalar(rand: () => number): unknown {
  const r = rand();
  if (r < 0.55) {
    return text(rand);
  }
  if (r < 0.7) {
    return Math.floor((rand() - 0.5) * 2 ** (1 + Math.floor(rand() * 52)));
  }
  if (r < 0.8) {
    // 整数でない小数（指数の綴りにならない範囲）
    return Math.floor((rand() - 0.5) * 1e6) + 0.25 * (1 + Math.floor(rand() * 3));
  }
  if (r < 0.9) {
    return rand() < 0.5;
  }
  return null;
}

function value(rand: () => number, depth: number): unknown {
  const r = rand();
  if (depth >= 3 || r < 0.6) {
    return scalar(rand);
  }
  if (r < 0.8) {
    return Array.from({ length: Math.floor(rand() * 4) }, () => value(rand, depth + 1));
  }
  return record(rand, depth + 1);
}

function record(rand: () => number, depth: number): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const n = Math.floor(rand() * 4);
  for (let i = 0; i < n; i += 1) {
    Object.defineProperty(out, key(rand), { value: value(rand, depth), enumerable: true, writable: true, configurable: true });
  }
  return out;
}

test("CB-T249 乱数の中身（固定の種、400 本）を書き出して PyYAML で読み戻すと、どれも同じ中身になる", { skip: reader.ok ? false : reader.why }, () => {
  const rand = random(20260926);
  const docs: Record<string, unknown>[] = [];
  for (let i = 0; i < 400; i += 1) {
    const nodes = Array.from({ length: 1 + Math.floor(rand() * 3) }, (_, j) => ({
      ...record(rand, 0),
      id: `${text(rand)}#${j}`,
      type: pick(rand, ["start", "end", "prompt", "subAgent", "ifElse", "mystery"]),
      name: text(rand),
      position: { x: Math.floor(rand() * 2000) - 1000, y: Math.floor(rand() * 2000) },
      data: record(rand, 1),
    }));
    const connections = Array.from({ length: Math.floor(rand() * 3) }, () => ({ ...record(rand, 1), from: text(rand), to: text(rand) }));
    docs.push({ ...record(rand, 1), id: text(rand), name: text(rand), nodes, connections });
  }
  const reads = readBack(docs);
  reads.forEach((read, i) => {
    const shown = serializeFlow(docs[i] as unknown as FlowDoc);
    assert.equal(read.why, "", `${i}: ${read.why}\n${shown}`);
    const found = flowDisagreement(docs[i], read.data);
    assert.equal(found, undefined, `${i}: ${JSON.stringify(found)}\n${shown}`);
    // 画面の読み手でも同じ中身に読める（書いたものを画面が開き直せる）
    const again = parseFlowValue(shown);
    assert.ok(again.ok, `${i}`);
    assert.equal(flowDisagreement(again.value, read.data), undefined, `${i} を画面で読み直すと食い違う\n${shown}`);
  });
});

test("CB-T250 画面の読み（YAML 1.2）と PyYAML の読みが違う綴りは、開くときの見比べで食い違いになる。引用符で囲めば揃う", { skip: reader.ok ? false : reader.why }, () => {
  assert.ok(reader.ok);
  const spelled = ["0755", "yes", "on", "1:30", "0o17", "1e3", "1_000", "1.", "2026-01-01", "!!float 1", "!!binary aGk=", ".inf", "123456789012345678901", "{1: a}"];
  const texts = spelled.map((v) => `nodes:\n  - id: a\n    data:\n      v: ${v}\n`);
  // 別名を使わないマージキー。PyYAML は畳み、画面の読み手は畳まない
  texts.push("nodes: [{<<: {id: a}}]\n");
  const quoted = spelled.map((v) => `nodes:\n  - id: a\n    data:\n      v: "${v}"\n`);
  const reads = reader.read([...texts, ...quoted].map((t) => Buffer.from(t, "utf8")));
  texts.forEach((t, i) => {
    assert.equal(reads[i].why, "", t);
    const screen = parseFlowValue(t);
    assert.ok(screen.ok, t);
    const found = flowDisagreement(screen.value, reads[i].data);
    assert.notEqual(found, undefined, t);
  });
  // 場所はノードの id と欄のパスで言う
  const first = parseFlowValue(texts[0]);
  assert.ok(first.ok);
  assert.deepEqual(flowDisagreement(first.value, reads[0].data), { where: 'ノード "a" の data.v', screen: "整数 755", executable: "整数 493" });
  quoted.forEach((t, i) => {
    const screen = parseFlowValue(t);
    assert.ok(screen.ok, t);
    assert.equal(flowDisagreement(screen.value, reads[texts.length + i].data), undefined, t);
  });
});
