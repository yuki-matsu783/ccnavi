/**
 * 計画編集画面の案内の見本（`webview/tours/workflow.yml` の `sample:`）を、画面が描く形にする。
 *
 * 見本は「実行ファイルの答え」（`--plan-order <親> --json` の答え。`planordermodel.ts` の `PlanOrderAnswer`）を、名前を付けて
 * 何本か持つ。手順の `scene.answer` が、その手順で画面が描く答えを名前で選ぶ。画面は答えをそのまま描くので、
 * 未保存・番号の変わる項・保存できない理由・番号の振り直しの確かめは、実際と同じ読み取り（`workflow-view.ts`）から出る。
 *
 * 答えは実行ファイルの JSON と同じ形で書き、欠けた欄は次の決まりで埋めてから `parsePlanOrder` に通す（形がずれれば落ちる）。
 *
 *   計画ごと   `ticket`・`part`・`source_sha`・項の `from`（番号）・`deferred`・`review_at`・`locked` は省ける。
 *              `proposed`（提案に書いてある先行）は、`base` があればその答えの同じ計画の値、無ければ `after`
 *   base       その答えを引く前の答え（先に定義した答えの名前）。書かなかった計画（plan / feedback）は `base` の計画をそのまま使う。
 *              「案に戻す」・外で変わった差分・点の置き場所の元になる。`base` を書かない答えは、開いたときの答え
 *   答え全体   `problems`・`loose` は計画の値を寄せ集め、`writable` は「断った線・検査の error・子の付け替えの問題・改版の錠が
 *              どれも無い」から決める（実行ファイルの答えと同じ関係。`test/fixtures/plan-order.json` の実例で確かめる）
 *
 * DOM にも VS Code の API にも触れない。
 */
import { parsePlanOrder, type PlanOrderAnswer } from "./planordermodel.js";

export interface WorkflowSampleAnswer {
  readonly answer: PlanOrderAnswer;
  /** この答えを引く前の答えの名前（自分自身なら、開いたときの答え） */
  readonly base: string;
}

export interface WorkflowSample {
  /** 親の識別子 */
  readonly ticket: string;
  readonly answers: Readonly<Record<string, WorkflowSampleAnswer>>;
}

/** 見本の計画の欄の名前。実行ファイルの `--order` が受ける 2 つ */
const PARTS = ["plan", "feedback"] as const;

const ROOT_KEYS = ["ticket", "answers"];
const ANSWER_KEYS = ["base", "plan", "feedback", "problems", "loose", "refused", "revision_problems", "children", "child_problems", "mismatched", "writable"];
const PART_KEYS = ["items", "after", "proposed", "current", "loose", "ready", "problems"];
const ITEM_KEYS = ["number", "from", "type", "title", "review", "deferred", "review_at", "locked"];

/** 見本の `source_sha`。実行ファイルのハッシュではなく、見本だと分かる印 */
export const SAMPLE_SHA = "sample";

type Json = Record<string, unknown>;

function object(value: unknown, at: string): Json {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${at} がオブジェクトではない`);
  }
  return value as Json;
}

function array(value: unknown, at: string): unknown[] {
  if (value === undefined) {
    return [];
  }
  if (!Array.isArray(value)) {
    throw new Error(`${at} がリストではない`);
  }
  return value;
}

/** 知らない鍵（綴り違い）は、黙って捨てると既定値に化けるので名指しで落とす */
function onlyKeys(map: Json, known: readonly string[], at: string): void {
  const unknown = Object.keys(map).find((key) => !known.includes(key));
  if (unknown !== undefined) {
    throw new Error(`${at} に未知の鍵 ${unknown}`);
  }
}

function itemOf(raw: unknown, at: string): Json {
  const item = object(raw, at);
  onlyKeys(item, ITEM_KEYS, at);
  // 実行ファイルの答えの読み取りは、番号の無い項を黙って落とす。見本では落とさず、書き間違いとして止める
  if (typeof item.number !== "number" || !Number.isInteger(item.number) || item.number < 1) {
    throw new Error(`${at}.number は 1 以上の整数にする`);
  }
  if (typeof item.type !== "string" || item.type === "") {
    throw new Error(`${at}.type がありません`);
  }
  return {
    number: item.number,
    from: item.from ?? item.number,
    type: item.type,
    title: item.title ?? "",
    review: item.review ?? "",
    deferred: item.deferred ?? false,
    review_at: item.review_at ?? null,
    locked: item.locked ?? false,
  };
}

function planOf(name: string, part: string, raw: unknown, ticket: string, base: Json | undefined): Json {
  const at = `sample.answers.${name}.${part}`;
  const given = object(raw, at);
  onlyKeys(given, PART_KEYS, at);
  const after = given.after ?? {};
  return {
    ticket,
    part,
    source_sha: SAMPLE_SHA,
    items: array(given.items, `${at}.items`).map((item, i) => itemOf(item, `${at}.items[${i}]`)),
    after,
    proposed: given.proposed ?? base?.proposed ?? after,
    current: given.current ?? null,
    loose: array(given.loose, `${at}.loose`),
    ready: array(given.ready, `${at}.ready`),
    problems: array(given.problems, `${at}.problems`),
  };
}

/**
 * 見本の答え 1 本を、実行ファイルの JSON と同じ形にする（`parsePlanOrder` に渡す手前）。
 * `known` は先に書いた答え（`base` を引く）。テストが実例の JSON と鍵を見比べるために出している
 */
export function completeAnswer(name: string, raw: unknown, ticket: string, known: Readonly<Record<string, Json>>): { readonly json: Json; readonly base: string } {
  const at = `sample.answers.${name}`;
  const given = object(raw, at);
  onlyKeys(given, ANSWER_KEYS, at);
  const baseName = given.base === undefined ? "" : String(given.base);
  const base = baseName === "" ? undefined : known[baseName];
  if (baseName !== "" && base === undefined) {
    throw new Error(`${at}.base「${baseName}」は先に定義した答えの名前にする`);
  }
  const basePlans = array(base?.plans, `${at}.base`) as Json[];
  const plans: Json[] = [];
  for (const part of PARTS) {
    const inherited = basePlans.find((plan) => plan.part === part);
    if (given[part] !== undefined) {
      plans.push(planOf(name, part, given[part], ticket, inherited));
    } else if (inherited !== undefined) {
      plans.push(JSON.parse(JSON.stringify(inherited)) as Json);
    }
  }
  if (plans.length === 0) {
    throw new Error(`${at} に計画（plan / feedback）がありません`);
  }
  const problems = given.problems ?? plans.flatMap((plan) => plan.problems as unknown[]);
  const revision = array(given.revision_problems, `${at}.revision_problems`);
  const children = array(given.child_problems, `${at}.child_problems`);
  const refused = array(given.refused, `${at}.refused`);
  const json: Json = {
    version: 1,
    ticket,
    source_sha: SAMPLE_SHA,
    plans,
    problems,
    loose: given.loose ?? plans.flatMap((plan) => plan.loose as unknown[]),
    refused,
    revision_problems: revision,
    children: array(given.children, `${at}.children`),
    child_problems: children,
    mismatched: array(given.mismatched, `${at}.mismatched`),
    writable: given.writable ?? (array(problems, `${at}.problems`).length === 0 && revision.length === 0 && children.length === 0 && refused.length === 0),
  };
  return { json, base: baseName === "" ? name : baseName };
}

export function parseWorkflowSample(raw: unknown): WorkflowSample {
  const root = object(raw, "sample");
  onlyKeys(root, ROOT_KEYS, "sample");
  const ticket = typeof root.ticket === "string" && root.ticket !== "" ? root.ticket : "";
  if (ticket === "") {
    throw new Error("sample.ticket がありません");
  }
  const given = object(root.answers, "sample.answers");
  const known: Record<string, Json> = {};
  const answers: Record<string, WorkflowSampleAnswer> = {};
  for (const [name, value] of Object.entries(given)) {
    const done = completeAnswer(name, value, ticket, known);
    const parsed = parsePlanOrder(JSON.stringify(done.json));
    if (!parsed.ok) {
      // 見本が実行ファイルの形から外れた（テストが先に落ちる）
      throw new Error(`sample.answers.${name}: ${parsed.error}`);
    }
    known[name] = done.json;
    answers[name] = { answer: parsed.value, base: done.base };
  }
  if (answers.open === undefined) {
    throw new Error("sample.answers.open（開いたときの答え）がありません");
  }
  return { ticket, answers };
}
