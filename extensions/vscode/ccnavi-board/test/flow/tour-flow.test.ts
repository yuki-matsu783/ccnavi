/**
 * フロー編集画面の案内の見本（`tours/flow.yml` の `sample:`）の検査。見本のフローは、フローのファイルと同じ書き方で YAML に書き、
 * 画面は `tour-flow.ts` で `FlowDoc` にする。実際の読み取り（`parseFlow`）・実行ファイルの読みと答え・実際の編集の関数と
 * 同じ結果になることを見る（見本が実際の形から外れたときに、テストで気づく）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { stringify } from "yaml";

import { diffFlows, isEmptyDiff, textDiff } from "../../src/core/flow-diff.js";
import {
  addNode,
  branchKey,
  connectionFrom,
  connectionId,
  connectionTo,
  connectionsOf,
  copyNodes,
  flowNotices,
  groupNodes,
  isGroup,
  nodeType,
  parseFlow,
  pasteNodes,
  serializeFlow,
  type FlowDoc,
} from "../../src/core/flow-doc.js";
import { flowMismatch } from "../../src/core/flow-match.js";
import { parseFlowChecks, parseFlowSample, sceneOf, sceneProblem, MAIN_FLOW } from "../../src/core/tour-flow.js";
import { INTRO_ID } from "../../src/core/tour-def.js";
import { exeRunner } from "../helpers/flow-exe.js";
import { loadTour } from "../helpers/tour.js";

const tour = loadTour("flow");
const sample = parseFlowSample(tour.sample);
const names = Object.keys(sample.flows);

test("CB-T480 フローの見本は、すべて FlowDoc として読め、線の端・グループの親が見本の中に在り、画面が欄を持つ種類を一通り含む", () => {
  assert.ok(names.length >= 10);
  const types = new Set<string>();
  for (const name of names) {
    const doc = sample.flows[name].doc;
    const ids = doc.nodes.map((node) => node.id);
    assert.equal(new Set(ids).size, ids.length, `${name}: ノードの id が重なっている`);
    const ports = new Set<string>();
    for (const c of connectionsOf(doc)) {
      assert.ok(ids.includes(connectionFrom(c)) && ids.includes(connectionTo(c)), `${name}: 線 ${connectionId(c)} の端がありません`);
      ports.add(connectionId(c));
    }
    assert.equal(ports.size, connectionsOf(doc).length, `${name}: 線の id が重なっている`);
    for (const node of doc.nodes) {
      types.add(nodeType(node));
      if (typeof node.parentId === "string") {
        assert.ok(doc.nodes.some((other) => other.id === node.parentId && isGroup(other)), `${name}: ${node.id} の親がありません`);
      }
      const key = branchKey(nodeType(node));
      if (key !== undefined && (node.data as Record<string, unknown>)[key] === undefined) {
        assert.fail(`${name}: ${node.id} に ${key} がありません`);
      }
    }
  }
  for (const type of ["start", "end", "prompt", "subAgent", "askUserQuestion", "ifElse", "switch", "loop", "group", "subAgentFlow"]) {
    assert.ok(types.has(type), `見本に ${type} がありません`);
  }
});

test("CB-T481 見本のフローは、YAML の本文に書き出して実際の読み取り（parseFlow）で読むと、同じ FlowDoc になる", () => {
  for (const name of names) {
    const doc = sample.flows[name].doc;
    const read = parseFlow(serializeFlow(doc));
    assert.ok(read.ok, `${name}: parseFlow が断った`);
    assert.deepEqual(read.doc, doc, name);
    // フローのファイルの書き方のまま（YAML.stringify の素の本文）でも同じ
    const plain = parseFlow(stringify(doc));
    assert.ok(plain.ok && flowMismatch(plain.doc, doc) === undefined, name);
  }
});

const exe = exeRunner();

test("CB-T482 見本のフローは、実行ファイル（PyYAML）が読んでも同じ中身で、error がありません", { skip: exe.ok ? false : exe.reason }, () => {
  assert.ok(exe.ok);
  for (const name of names) {
    const read = exe.read(sample.flows[name].doc, sample.page.flowPath);
    assert.equal(flowMismatch(sample.flows[name].doc, read.data), undefined, `${name}: 画面の読みと実行ファイルの読みが食い違う`);
    assert.deepEqual(read.problems.filter((problem) => problem.severity === "error"), [], name);
  }
});

test("CB-T483 見本の checks（warns・rendered）は、実行ファイルがそのフローに返す答えと同じ", { skip: exe.ok ? false : exe.reason }, () => {
  assert.ok(exe.ok);
  for (const name of names) {
    const { doc, checks } = sample.flows[name];
    const read = exe.read(doc, sample.page.flowPath);
    assert.deepEqual(checks.warns, read.problems.map((problem) => problem.detail), `${name}: warns`);
    assert.deepEqual(checks.rendered, read.rendered, `${name}: rendered`);
  }
});

test("CB-T484 見本の checks の形は、実行ファイルの答えの形（FlowChecks）で、足りないものは断る", () => {
  const ok = parseFlowChecks({ warns: [], rendered: ["1. [start] 開始"], candidates: { agents: [{ name: "Explore", source: "builtin" }], skills: [] } });
  assert.deepEqual(ok.rendered, ["1. [start] 開始"]);
  assert.throws(() => parseFlowChecks({ warns: "x", rendered: [], candidates: { agents: [], skills: [] } }), /warns/);
  assert.throws(() => parseFlowChecks({ warns: [], rendered: [1], candidates: { agents: [], skills: [] } }), /rendered/);
  assert.throws(() => parseFlowChecks({ warns: [], rendered: [], candidates: { agents: [{ name: "" }], skills: [] } }), /name/);
  assert.throws(() => parseFlowSample({ ...(tour.sample as object), flows: {} }), /main/);
  const candidates = sample.flows[MAIN_FLOW].checks.candidates;
  assert.ok(candidates !== undefined && candidates.agents.some((agent) => agent.name === "Explore"));
  // 候補の欄の見本（builtInType が候補に有る）は、warn が出ない main で確かめる
  assert.deepEqual(sample.flows[MAIN_FLOW].checks.warns, []);
});

test("CB-T485 実際の編集の関数（addNode・groupNodes・pasteNodes）の結果と、見本の「追加した直後」「グループ化したあと」「貼り付けたあと」が同じ", () => {
  const main = sample.flows.main.doc;
  const grouped = groupNodes(main, ["fix-small", "redesign"]);
  assert.ok(grouped !== undefined);
  assert.deepEqual(grouped.doc.nodes, sample.flows.grouped.doc.nodes);
  assert.equal(grouped.id, "group-1");
  const clip = copyNodes(main, ["investigate"]);
  assert.ok(clip !== undefined);
  assert.deepEqual(pasteNodes(main, clip).doc.nodes, sample.flows["main-pasted"].doc.nodes);
  const built = addNode(sample.flows["build-1"].doc, "prompt", { x: 0, y: 270 });
  assert.deepEqual(built.doc.nodes, sample.flows["build-2"].doc.nodes);
  assert.equal(built.id, "prompt-1");
});

test("CB-T486 画面の注意（flowNotices）は、入れ子の見本（deep）で上限を言い、完成した見本（main）では何も言わない", () => {
  assert.deepEqual(flowNotices(sample.flows.main.doc, { exe: true }), []);
  const deep = flowNotices(sample.flows.deep.doc, { exe: true });
  assert.ok(deep.some((notice) => notice.includes("子の下に 3 段重なっています")), deep.join("\n"));
  // 開始が無い見本（broken）の注意は、実行ファイルの warn（start が無い）に任せ、画面は言わない
  assert.deepEqual(flowNotices(sample.flows.broken.doc, { exe: true }), []);
  assert.ok(sample.flows.broken.checks.warns.some((warn) => warn.includes("start がありません")));
});

test("CB-T487 保存前の差分と下書きの差分は、見本どうしで意味のある違いになる（追加した・削除した・変えた）", () => {
  const edited = diffFlows(sample.flows.main.doc, sample.flows["main-edited"].doc);
  assert.ok(edited.meta.length >= 1 && edited.addedNodes.length >= 1 && edited.changedNodes.length >= 1);
  assert.ok(edited.addedConnections.length >= 1 && edited.removedConnections.length >= 1);
  assert.equal(isEmptyDiff(diffFlows(sample.flows.main.doc, sample.flows.main.doc)), true);
  const draft = textDiff(sample.flows.main.doc, sample.proposal.doc);
  assert.equal(draft.problem, undefined);
  assert.ok(draft.changes.some((change) => change.kind === "changed-node") && draft.changes.some((change) => change.kind === "added-connection"));
  assert.match(sample.requestPrompt, /wip\/proposals\/flows\/i0042-01-01\.yml/);
  assert.match(sample.requestPrompt, /--lint --flow/);
});

/** 画面が静的に付ける印（`data-tour`）。ノード・線・部品箱の印は見本から決まる（下の検査） */
const STATIC_TARGETS = new Set([
  "palette",
  "graph",
  "side",
  "inspector",
  "inspector-question",
  "inspector-branches",
  "inspector-multiselect",
  "inspector-target",
  "inspector-loop",
  "inspector-agent",
  "inspector-prompt",
  "inspector-condition",
  "inspector-ungroup",
  "preview",
  "notices",
  "offer",
  "lock",
  "changed",
  "dirty",
  "undo",
  "redo",
  "copy",
  "paste",
  "duplicate",
  "group",
  "minimap",
  "request",
  "save",
  "review",
  "review-skip",
  "proposal",
  "proposal-import",
  "request-text",
  "tour",
]);
const PALETTE_TARGETS = new Set(["start", "end", "prompt", "sub-agent", "ask", "if-else", "switch", "loop", "skill"]);

test("CB-T488 フローの案内は、基本の案内が先頭で、使い方ごとの案内が 5〜8 本あり、どれも 4〜10 手順で、タイトルが重ならない", () => {
  assert.equal(tour.courses[0].id, INTRO_ID);
  assert.deepEqual(
    tour.courses[0].steps.map((step) => step.title),
    ["部品箱", "図", "欄", "保存", "案内"],
  );
  const uses = tour.courses.slice(1);
  assert.ok(uses.length >= 5 && uses.length <= 8, `使い方ごとの案内が ${uses.length} 本`);
  for (const course of uses) {
    assert.ok(course.steps.length >= 4 && course.steps.length <= 10, `${course.id}: ${course.steps.length} 手順`);
    assert.equal(new Set(course.steps.map((step) => step.title)).size, course.steps.length, `${course.id}: 手順のタイトルが重なっている`);
  }
  assert.ok(tour.courses[0].steps.length <= 6, "基本の案内は今くらいの量");
});

test("CB-T489 フローの案内の scene は、見本にある名前・ノード・線だけを指し、指す先（target）はその手順の見本に在る", () => {
  for (const course of tour.courses) {
    for (const [index, step] of course.steps.entries()) {
      const at = `${course.id} の ${index + 1} 番目の手順（${step.title}）`;
      assert.equal(sceneProblem(sample, step.scene), undefined, at);
      const scene = sceneOf(sample, step.scene);
      const doc: FlowDoc = scene.flow.doc;
      const target = step.target;
      assert.notEqual(target, "", `${at}: 指す先が空（吹き出しが中ほどに出る）`);
      if (target.startsWith("node-")) {
        assert.ok(doc.nodes.some((node) => `node-${node.id}` === target), `${at}: ${target} が見本（${scene.name}）のノードにありません`);
      } else if (target.startsWith("edge-")) {
        assert.ok(connectionsOf(doc).some((c) => `edge-${connectionId(c)}` === target), `${at}: ${target} が見本（${scene.name}）の線にありません`);
      } else if (target.startsWith("palette-")) {
        assert.ok(PALETTE_TARGETS.has(target.slice("palette-".length)), `${at}: ${target}`);
      } else {
        assert.ok(STATIC_TARGETS.has(target), `${at}: ${target} は画面が付ける印にありません`);
      }
    }
  }
});

test("CB-T490 scene の綴り違い・見本にない値は、sceneProblem が名指しする（既定へ黙って戻らない）", () => {
  assert.match(sceneProblem(sample, { dok: "main" }) ?? "", /dok/);
  assert.match(sceneProblem(sample, { doc: "nothing" }) ?? "", /nothing/);
  assert.match(sceneProblem(sample, { doc: "main", select: "nobody" }) ?? "", /nobody/);
  assert.match(sceneProblem(sample, { doc: "main", select: "edge:nothing" }) ?? "", /nothing/);
  assert.match(sceneProblem(sample, { doc: "main", pick: "start,missing" }) ?? "", /missing/);
  assert.match(sceneProblem(sample, { doc: "main", dirty: "yes" }) ?? "", /dirty/);
  assert.equal(sceneProblem(sample, { doc: "main", select: "edge:c-start-investigate", pick: "start,end", dirty: true }), undefined);
  // 読めない値は既定の見本へ戻る（案内を落とさない）
  assert.equal(sceneOf(sample, { doc: "nothing" }).name, "main");
  assert.deepEqual(sceneOf(sample, { doc: "main", select: "ask-policy" }).picked, ["ask-policy"]);
  assert.deepEqual(sceneOf(sample, { doc: "main", select: "edge:c-start-investigate" }).selected, { kind: "edge", index: 0 });
});
