/**
 * フロー編集画面の案内の見本（`webview/tours/flow.yml` の `sample:`）を、画面が描く形にする。
 *
 * 見本のフローは、フローのファイルと同じ書き方で YAML に書く。案内の YAML は実行時に解析しない（ビルドのときに JSON になる）ので、
 * ここでは `flow-doc.ts` の `asFlowDoc` を通して `FlowDoc` にする（実際に開くときと同じ型）。`yaml` を要る読み取り（`parseFlow`）は
 * 通らないが、同じ中身になることはテストが見る（YAML の本文に戻して `parseFlow` で読む。実行ファイルの読みとも見比べる）。
 * 実行ファイル（`--lint --json --flow`）が言うこと（`checks`）も、同じ形（`FlowChecks`）で YAML に書く。画面は判定し直さず、そのまま描く。
 *
 * 案内の間は、この見本だけを描く。実際の編集内容・履歴・選択・保存・エージェントの下書きには触れない。
 * 手順ごとの様子（`scene`）の読み方もここに置く（`sceneOf`）。キーの一覧は `flow.yml` の頭に書いてある。
 */
import { asFlowDoc, connectionId, connectionsOf, isGroup, type FlowDoc } from "./flow-doc.js";
import { flowRequestPrompt, type FlowChecks, type FlowOffer, type FlowProposal } from "./flow-view.js";
import type { LintFlowCandidate } from "./lintmodel.js";
import type { Scene } from "./tour-def.js";

export interface SamplePage {
  readonly ticket: string;
  readonly title: string;
  readonly parent: string;
  readonly flowPath: string;
  readonly flowRel: string;
}

/** 見本のフロー 1 本。実行ファイルがそのフローについて言ったこと（`checks`）と一緒に持つ */
export interface SampleFlow {
  readonly doc: FlowDoc;
  readonly checks: FlowChecks;
}

export interface FlowSample {
  readonly page: SamplePage;
  /** 依頼のボタンの言葉 */
  readonly request: string;
  readonly flows: Readonly<Record<string, SampleFlow>>;
  /** 既定の見本の名前（`scene.doc` を書かない手順） */
  readonly main: string;
  readonly offer: FlowOffer;
  /** エージェントの下書き。中身は `flows` の 1 本 */
  readonly proposal: FlowProposal;
  /** 「エージェントに頼む文」。実際の文（`flowRequestPrompt`）を見本の値で組む */
  readonly requestPrompt: string;
  /** 下書きの確かめの命令（`requestPrompt` に入る） */
  readonly lintCommand: string;
}

/** 既定の見本の名前 */
export const MAIN_FLOW = "main";

/** 下書きのハッシュの見本（見せない。取り込みを送らないので使われない） */
const SAMPLE_HASH = "0".repeat(64);

function object(value: unknown, at: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${at} がオブジェクトではない`);
  }
  return value as Record<string, unknown>;
}

function text(value: unknown, at: string): string {
  if (typeof value !== "string" || value === "") {
    throw new Error(`${at} が空か文字列ではない`);
  }
  return value;
}

function candidates(value: unknown, at: string): readonly LintFlowCandidate[] {
  if (!Array.isArray(value)) {
    throw new Error(`${at} がリストではない`);
  }
  return value.map((item, index) => {
    const entry = object(item, `${at}[${index}]`);
    return { name: text(entry.name, `${at}[${index}].name`), source: text(entry.source, `${at}[${index}].source`) };
  });
}

/** 実行ファイルの答え（`FlowChecks`）。形が違えば、どこが違うかを言って落とす */
export function parseFlowChecks(raw: unknown, at = "checks"): FlowChecks {
  const map = object(raw, at);
  if (!Array.isArray(map.warns) || !map.warns.every((warn) => typeof warn === "string")) {
    throw new Error(`${at}.warns は文字列のリスト`);
  }
  if (!Array.isArray(map.rendered) || !map.rendered.every((line) => typeof line === "string")) {
    throw new Error(`${at}.rendered は文字列のリスト`);
  }
  const cands = object(map.candidates, `${at}.candidates`);
  return {
    warns: map.warns as string[],
    rendered: map.rendered as string[],
    candidates: { agents: candidates(cands.agents, `${at}.candidates.agents`), skills: candidates(cands.skills, `${at}.candidates.skills`) },
  };
}

export function parseFlowSample(raw: unknown): FlowSample {
  const root = object(raw, "sample");
  const page = object(root.page, "sample.page");
  const draft = object(root.draft, "sample.draft");
  const flowsRaw = object(root.flows, "sample.flows");
  const flows: Record<string, SampleFlow> = {};
  for (const [name, value] of Object.entries(flowsRaw)) {
    const entry = object(value, `sample.flows.${name}`);
    const doc = asFlowDoc(entry.doc);
    if (doc === undefined) {
      throw new Error(`sample.flows.${name}.doc を FlowDoc として読み込めない（ノードのリストと id が必要です）`);
    }
    flows[name] = { doc, checks: parseFlowChecks(entry.checks, `sample.flows.${name}.checks`) };
  }
  if (flows[MAIN_FLOW] === undefined) {
    throw new Error(`sample.flows に ${MAIN_FLOW} がありません`);
  }
  const proposalFlow = text(object(root.proposal, "sample.proposal").flow, "sample.proposal.flow");
  if (flows[proposalFlow] === undefined) {
    throw new Error(`sample.proposal.flow「${proposalFlow}」が sample.flows にありません`);
  }
  const samplePage: SamplePage = {
    ticket: text(page.ticket, "sample.page.ticket"),
    title: text(page.title, "sample.page.title"),
    parent: text(page.parent, "sample.page.parent"),
    flowPath: text(page.flowPath, "sample.page.flowPath"),
    flowRel: text(page.flowRel, "sample.page.flowRel"),
  };
  const draftPath = text(draft.path, "sample.draft.path");
  const lintCommand = text(draft.lintCommand, "sample.draft.lintCommand");
  return {
    page: samplePage,
    request: text(root.request, "sample.request"),
    flows,
    main: MAIN_FLOW,
    offer: { draftPath },
    proposal: { doc: flows[proposalFlow].doc, hash: SAMPLE_HASH, draftPath },
    requestPrompt: flowRequestPrompt({
      ticket: samplePage.ticket,
      title: samplePage.title,
      parent: samplePage.parent,
      draftPath,
      flowPath: samplePage.flowPath,
      redo: false,
      lintCommand,
    }),
    lintCommand,
  };
}

// ---- 手順ごとの様子（scene）

/** 図の外から見た「いま選んでいるもの」。`webview/flow/Canvas.tsx` の `Selection` と同じ形 */
export type SceneSelect = { readonly kind: "node"; readonly id: string } | { readonly kind: "edge"; readonly index: number };

/** `scene` のキー。画面はこれだけを読む（`flow.yml` の頭に意味を書いてある） */
export const SCENE_KEYS = ["doc", "select", "pick", "base", "dirty", "history", "clip", "minimap", "lock", "changed", "offer", "review", "proposal", "requestText", "checking"] as const;
const FLAG_KEYS = ["dirty", "history", "clip", "minimap", "lock", "changed", "offer", "review", "proposal", "requestText", "checking"] as const;

/** 手順の `scene` を、画面が描く値にしたもの */
export interface FlowScene {
  /** 描く見本の名前 */
  readonly name: string;
  readonly flow: SampleFlow;
  /** 差分の「いまのフロー」にする見本（保存前の差分・下書きの差分） */
  readonly base: SampleFlow;
  readonly selected: SceneSelect | undefined;
  /** 図で選んでいるノードの id */
  readonly picked: readonly string[];
  readonly dirty: boolean;
  readonly history: boolean;
  readonly clip: boolean;
  readonly minimap: boolean;
  readonly lock: boolean;
  readonly changed: boolean;
  readonly offer: boolean;
  readonly review: boolean;
  readonly proposal: boolean;
  readonly requestText: boolean;
  readonly checking: boolean;
}

function ids(value: Scene[string] | undefined): readonly string[] {
  return typeof value === "string" ? value.split(",").map((id) => id.trim()).filter((id) => id !== "") : [];
}

function selectOf(doc: FlowDoc, value: Scene[string] | undefined): SceneSelect | undefined {
  if (typeof value !== "string" || value === "") {
    return undefined;
  }
  if (value.startsWith("edge:")) {
    const index = connectionsOf(doc).findIndex((c) => connectionId(c) === value.slice("edge:".length));
    return index < 0 ? undefined : { kind: "edge", index };
  }
  return doc.nodes.some((node) => node.id === value) ? { kind: "node", id: value } : undefined;
}

/** 読めなかった値は既定に戻す（案内を落とさない）。綴り違いは `sceneProblem` が名指しする */
export function sceneOf(sample: FlowSample, scene: Scene): FlowScene {
  const name = typeof scene.doc === "string" && scene.doc in sample.flows ? scene.doc : sample.main;
  const flow = sample.flows[name];
  const base = sample.flows[typeof scene.base === "string" && scene.base in sample.flows ? scene.base : sample.main];
  const selected = selectOf(flow.doc, scene.select);
  const pick = ids(scene.pick).filter((id) => flow.doc.nodes.some((node) => node.id === id));
  const picked = pick.length > 0 ? pick : selected?.kind === "node" ? [selected.id] : [];
  const flag = (key: (typeof FLAG_KEYS)[number]): boolean => scene[key] === true;
  return {
    name,
    flow,
    base,
    selected,
    picked,
    dirty: flag("dirty"),
    history: flag("history"),
    clip: flag("clip"),
    minimap: flag("minimap"),
    lock: flag("lock"),
    changed: flag("changed"),
    offer: flag("offer"),
    review: flag("review"),
    proposal: flag("proposal"),
    requestText: flag("requestText"),
    checking: flag("checking"),
  };
}

/** 画面が読めない `scene`（知らないキー・見本に無い名前や id・型違い）の理由。読めれば undefined。テストが全手順で確かめる */
export function sceneProblem(sample: FlowSample, scene: Scene): string | undefined {
  for (const key of Object.keys(scene)) {
    if (!(SCENE_KEYS as readonly string[]).includes(key)) {
      return `scene のキー ${key} を画面は読まない`;
    }
  }
  for (const key of ["doc", "base"] as const) {
    const value = scene[key];
    if (value !== undefined && (typeof value !== "string" || !(value in sample.flows))) {
      return `${key} の ${String(value)} が見本のフローにありません`;
    }
  }
  const flow = sample.flows[typeof scene.doc === "string" ? scene.doc : sample.main];
  if (scene.select !== undefined && selectOf(flow.doc, scene.select) === undefined) {
    return `select の ${String(scene.select)} が見本のフロー（${typeof scene.doc === "string" ? scene.doc : sample.main}）のノード・線にありません`;
  }
  for (const id of ids(scene.pick)) {
    const node = flow.doc.nodes.find((n) => n.id === id);
    if (node === undefined || isGroup(node)) {
      return `pick の ${id} が見本のフローの（グループでない）ノードにありません`;
    }
  }
  for (const key of FLAG_KEYS) {
    if (scene[key] !== undefined && typeof scene[key] !== "boolean") {
      return `${key} は真偽にする`;
    }
  }
  return undefined;
}
