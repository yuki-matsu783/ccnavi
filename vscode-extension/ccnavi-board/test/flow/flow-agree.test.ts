/**
 * 画面の中身と実行ファイルが読んだ中身の見比べ（`core/flow-agree.ts`）。実行ファイルの答えは `flow.as_json` の形で組む。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { flowDisagreement, JSON_MARK, openDisagreementText, saveDisagreementText } from "../../src/core/flow-agree.js";

const float = (value: number) => ({ [JSON_MARK]: "float", value });

test("CB-T251 整数と浮動小数は決め打ちで揃える。整数の値を持つ浮動小数・有限でない数・印の値は食い違い", () => {
  assert.equal(flowDisagreement({ a: 1, b: 1.5, c: "x", d: true, e: null, f: [1] }, { a: 1, b: float(1.5), c: "x", d: true, e: null, f: [1] }), undefined);
  // 実行ファイルが浮動小数で読んだ 1.0 を、画面は整数 1 で持つ（書けば 1 になる）
  assert.deepEqual(flowDisagreement({ a: 1 }, { a: float(1) }), { where: "a", screen: "整数 1", executable: "浮動小数 1.0" });
  // 実行ファイルの整数を画面が小数で持つ
  assert.notEqual(flowDisagreement({ a: 1.5 }, { a: 1 }), undefined);
  for (const mark of [
    { [JSON_MARK]: "float", text: "inf" },
    { [JSON_MARK]: "int", text: "123456789012345678901" },
    { [JSON_MARK]: "date", text: "2026-01-01" },
    { [JSON_MARK]: "bytes", text: "aGk=" },
    { [JSON_MARK]: "map", items: [[1, "a"]] },
  ]) {
    for (const screen of [Infinity, 123456789012345678901, "2026-01-01", "aGk=", { 1: "a" }]) {
      assert.notEqual(flowDisagreement({ v: screen }, { v: mark }), undefined, `${JSON.stringify(mark)} ${String(screen)}`);
    }
  }
  // 型の違い・数の違い・並びの長さ・キーの有無
  assert.notEqual(flowDisagreement({ v: "yes" }, { v: true }), undefined);
  assert.notEqual(flowDisagreement({ v: 755 }, { v: 493 }), undefined);
  assert.notEqual(flowDisagreement({ v: [1, 2] }, { v: [1] }), undefined);
  assert.deepEqual(flowDisagreement({ v: 1, w: 2 }, { v: 1 }), { where: "w", screen: "整数 2", executable: "無い" });
  assert.deepEqual(flowDisagreement({ v: 1 }, { v: 1, w: 2 }), { where: "w", screen: "無い", executable: "整数 2" });
  // 画面の側で値が undefined のキーは無いものとして読む
  assert.equal(flowDisagreement({ v: 1, w: undefined }, { v: 1 }), undefined);
  // 画面の側の作りの違う値（Buffer など）は食い違い
  assert.notEqual(flowDisagreement({ v: Buffer.from("hi") }, { v: { 0: 104, 1: 105 } }), undefined);
});

test("CB-T252 場所は nodes の中ならノードの id と欄のパスで言い、文面に両者の値を載せる", () => {
  const exec = { nodes: [{ id: "s", data: {} }, { id: "p-1", data: { branches: [{ label: true }] } }], connections: [{ condition: "x" }] };
  const screen = { nodes: [{ id: "s", data: {} }, { id: "p-1", data: { branches: [{ label: "yes" }] } }], connections: [{ condition: "x" }] };
  const found = flowDisagreement(screen, exec);
  assert.deepEqual(found, { where: 'ノード "p-1" の data.branches[0].label', screen: '文字列 "yes"', executable: "真偽値 true" });
  assert.ok(found !== undefined);
  assert.match(openDisagreementText(found), /ノード "p-1" の data\.branches\[0\]\.label。画面: 文字列 "yes"、実行ファイル: 真偽値 true/);
  assert.match(openDisagreementText(found), /エディタで引用符を付ける/);
  assert.match(saveDisagreementText(found), /書かない/);
  assert.equal(flowDisagreement({ connections: [{ condition: "y" }] }, { connections: [{ condition: "x" }] })?.where, "connections[0].condition");
  assert.equal(flowDisagreement({ "a b": 1 }, { "a b": 2 })?.where, '["a b"]');
  assert.equal(flowDisagreement("x", { nodes: [] })?.where, "最上位");
});
