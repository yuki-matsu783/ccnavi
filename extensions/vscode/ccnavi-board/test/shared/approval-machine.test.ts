/**
 * 承認のオーバーレイの遷移（`src/core/approval-machine.ts`）。どの状態で何を受け、何を返すか。
 *
 * 後半は変異テスト。ガードを 1 つずつ消したソースをその場で組み立てて、
 * 「ガードが有効であること」を確かめる関数が失敗することまで見る。ガードを足したら `GUARDS` にも足す。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import * as vm from "node:vm";
import * as ts from "typescript";

import {
  approvalStep,
  blockedBeforeApprove,
  CLOSED,
  SHOWN_STALE,
  type ApprovalEffect,
  type ApprovalState,
  type ApprovalStep,
  type ApprovalInput,
} from "../../src/core/approval-machine.js";
import type { ApproveMismatch, ApprovePlan, ApprovePreview, ApproveResult } from "../../src/core/approvemodel.js";
import type { PhaseChip } from "../../src/core/board.js";
import type { DecidePreview, DecideResult } from "../../src/core/decidemodel.js";

type Step = (state: ApprovalState, input: ApprovalInput) => ApprovalStep;
/** 遷移のファイルが出すもののうち、ガードの確かめが使うもの（変異させたものも同じ形） */
interface Machine {
  readonly approvalStep: Step;
  readonly blockedBeforeApprove: (tickets: readonly string[], unsaved: readonly string[]) => string[];
}
const MACHINE: Machine = { approvalStep, blockedBeforeApprove };

/** 出た効果の配列。中身まで見ない確かめは、この形で比べる */
function kinds(effects: readonly ApprovalEffect[]): string[] {
  return effects.map((e) => e.kind);
}

function previewOf(tickets: readonly string[], digest = "d1"): ApprovePreview {
  return {
    version: 1,
    root: "/w",
    generated_at: "2026-09-20T00:00:00Z",
    batch: tickets.map((ticket) => ({
      ticket,
      title: `題 ${ticket}`,
      parent: null,
      phase: null,
      revision: false,
      tree: "",
      path: `wip/proposals/todo/${ticket}.md`,
      overflow: [],
    })),
    text: `承認画面の本文（${tickets.join(",")}）`,
    digest,
    rejected: [],
    problems: [],
    plans: [],
  };
}

/** 計画 1 つぶんの図の中身。中身は見ないので、識別子と部分とハッシュだけ意味がある */
function planOf(ticket: string, sha: string, part = "plan"): ApprovePlan {
  return {
    ticket,
    part,
    source_sha: sha,
    items: [{ number: 1, from: 1, type: "research", title: "調査", review: "none", deferred: false, review_at: null, locked: false }],
    after: {},
    proposed: {},
    current: null,
    loose: [],
    ready: [1],
    problems: [],
  };
}

/** 計画を持つ親の一覧。`plans` は親ごとに全体計画とフィードバック計画（同じハッシュ）。子も一覧に入れられる */
function planPreviewOf(parents: Readonly<Record<string, string>>, children: readonly string[] = []): ApprovePreview {
  const base = previewOf(Object.keys(parents));
  return {
    ...base,
    batch: [
      ...base.batch,
      ...children.map((ticket) => ({ ...previewOf([ticket]).batch[0], parent: ticket.split("-")[0], phase: 1 })),
    ],
    plans: Object.entries(parents).flatMap(([ticket, sha]) => [planOf(ticket, sha), planOf(ticket, sha, "feedback")]),
  };
}

/** 計画を持つ親の一覧を見せた状態 */
function toPlanPreview(step: Step, parents: Readonly<Record<string, string>> = { i0001: "s1" }, saved: Readonly<Record<string, string>> = {}): ApprovalState {
  return step(toLoading(step), { kind: "previewed", saved, result: { ok: true, value: planPreviewOf(parents) } }).state;
}

function resultOf(approved: readonly string[], prompt = "承認の文", written: readonly string[] = []): ApproveResult {
  return { version: 1, approved, copies: [], written, lines: [], worktrees: [], prompt };
}

function mismatchOf(expected: readonly string[], current: readonly string[]): ApproveMismatch {
  return { expected, current, digest: { expected: "d1", current: "d2" } };
}

const TREE = "/w/.claude/worktrees/i0001";

function decidePreviewOf(keys: readonly string[] = ["u1", "u2"], digest = "v2.eyJiIjoiYSIsInQiOnt9fQ", canIssue = false): DecidePreview {
  return {
    version: 2,
    parent: "i0001",
    phase: 1,
    mr: { number: 7, url: "https://example.com/mr/7" },
    can_issue: canIssue,
    threads: keys.map((key) => ({
      key,
      url: key,
      path: "src/a.py",
      line: 1,
      body: `指摘 ${key}`,
      version: "v".repeat(32), last_comment: { id: "", at: "", author: "" },
    })),
    digest,
  };
}

function chipOf(overrides: Partial<PhaseChip> = {}): PhaseChip {
  return {
    parent: "i0001",
    number: 1,
    label: "1 設計",
    state: "active",
    status: "レビュー待ち",
    gateClosed: false,
    reviewWaiting: true,
    reviewRequired: true,
    riskLevel: "",
    riskLine: "",
    manual: "",
    tickets: [],
    mrUrl: "",
    mrNumber: null,
    actions: [],
    ...overrides,
  };
}

// --- 状態の組み立て。遷移そのものを通して作る（変異させた遷移でも同じ手で作れるように） ---

function toLoading(step: Step, only: readonly string[] = []): ApprovalState {
  return step(CLOSED, {
    kind: "approve",
    tickets: only,
    filtered: only.length > 0,
    pending: only,
  }).state;
}

function toPreview(step: Step, tickets: readonly string[] = ["i0001"], only: readonly string[] = []): ApprovalState {
  return step(toLoading(step, only), { kind: "previewed", saved: {}, result: { ok: true, value: previewOf(tickets) } }).state;
}

function toApproving(step: Step, tickets: readonly string[] = ["i0001"], only: readonly string[] = []): ApprovalState {
  return step(toPreview(step, tickets, only), { kind: "confirm", digest: "d1", unsaved: [], tickets }).state;
}

/**
 * 名前で状態を作る。ガードの確かめは「守る状態を全部」回す。
 * 1 つの状態でしか押さないと、ガードから状態を 1 つ抜いた（消すのではなく弱めた）ときに失敗せずに通る
 */
function named(step: Step, kind: string): ApprovalState {
  switch (kind) {
    case "closed":
      return CLOSED;
    case "loading":
      return toLoading(step);
    case "preview":
      return toPreview(step);
    case "approving":
      return toApproving(step);
    case "done":
      return toDone(step);
    case "error":
      return { overlay: { kind: "error", error: "読み込めない" }, only: [] };
    case "prompt":
      return toPrompt(step);
    case "decideLoading":
      return toDecideLoading(step);
    case "decidePreview":
      return toDecidePreview(step);
    case "deciding":
      return toDeciding(step);
    case "decided":
      return toDecided(step);
    default:
      throw new Error(`知らない状態: ${kind}`);
  }
}

/** 名前で作った状態が、狙ったものになっているか（作り方が誤っていたら、確かめは何も見ていない） */
function assertNamed(step: Step, kinds_: readonly string[]): void {
  for (const kind of kinds_) {
    const state = named(step, kind);
    assert.equal(state.overlay?.kind ?? "closed", kind, `${kind} の状態を作れていない`);
  }
}

/**
 * 取り返せないものではないプロンプト（keep でない prompt）を見せている状態。表示した後に指摘が追加されて、
 * レビュー済みにしていない結果（決め直しへ戻れる）
 */
function toPrompt(step: Step): ApprovalState {
  return step(toDeciding(step), {
    kind: "decided",
    outcome: { ok: true, value: { ...resultValue(), reviewed: false, pending_extra: ["u3"], followup: "" } },
  }).state;
}

function resultValue(): DecideResult {
  return { parent: "i0001", phase: 1, reviewed: true, pending_extra: [], followup: "", prompt: "決めたプロンプト", issue_url: "", warning: "" };
}

function toDecideLoading(step: Step): ApprovalState {
  return step(CLOSED, { kind: "decide", parent: "i0001", phase: 1, tree: TREE, chip: chipOf() }).state;
}

function toDecidePreview(step: Step): ApprovalState {
  return step(toDecideLoading(step), {
    kind: "decidePreviewed",
    result: { ok: true, value: decidePreviewOf() },
  }).state;
}

function toDeciding(step: Step): ApprovalState {
  return step(toDecidePreview(step), { kind: "decideConfirm", choices: { u1: "keep", u2: "fix" } }).state;
}

function toDecided(step: Step): ApprovalState {
  const state = step(toDeciding(step), {
    kind: "decided",
    outcome: {
      ok: true,
      value: {
        parent: "i0001",
        phase: 1,
        reviewed: false,
        pending_extra: [],
        followup: "i0001-02-03",
        prompt: "決めた文",
        issue_url: "",
        warning: "",
      },
    },
  }).state;
  // 名前で引くときは overlay.kind で確かめるので、ここでは prompt になっていることだけを見る
  return state;
}

function toDone(step: Step): ApprovalState {
  return step(toApproving(step), {
    kind: "approved",
    outcome: { ok: true, value: resultOf(["i0001"]) },
    carrier: true,
  }).state;
}

// --- ガード。表の 1 行が 1 つの `confirm`。変異テストが同じ関数を使う ---

interface Guard {
  /** 何を守っているか */
  readonly what: string;
  /** 消すために置き換えるソースの 1 行（ちょうど 1 か所に出ること） */
  readonly find: string;
  /** 置き換えた後（ガードが有効でなくなる形） */
  readonly into: string;
  /** 有効であることの確かめ。ガードを消したらここが失敗する（または、そこで投げる） */
  readonly check: (step: Step, machine: Machine) => void;
}

const GUARDS: readonly Guard[] = [
  {
    what: "承認を打っている最中は閉じない",
    find: 'return kind !== "approving" && kind !== "deciding";',
    into: 'return kind !== "deciding";',
    check(step) {
      const approving = toApproving(step);
      const after = step(approving, { kind: "cancel" });
      assert.equal(after.state.overlay?.kind, "approving");
      assert.equal(after.redraw, false);
      assert.deepEqual(kinds(after.effects), []);
      // 逆向きも見る。ここを「どの状態でも閉じない」に広げられたら、閉じる方法が消える
      const closable = ["loading", "preview", "done", "error", "prompt", "decideLoading", "decidePreview"];
      assertNamed(step, closable);
      for (const kind of closable) {
        assert.deepEqual(step(named(step, kind), { kind: "cancel" }).state, CLOSED, kind);
      }
    },
  },
  {
    what: "対応方針を置いている最中は閉じない",
    find: 'return kind !== "approving" && kind !== "deciding";',
    into: 'return kind !== "approving";',
    check(step) {
      const deciding = toDeciding(step);
      const after = step(deciding, { kind: "cancel" });
      assert.equal(after.state.overlay?.kind, "deciding");
      assert.deepEqual(kinds(after.effects), []);
    },
  },
  {
    what: "対応方針を置くのは、残った指摘を見せているとき（decidePreview）だけ",
    find: 'if (overlay?.kind !== "decidePreview") {',
    into: 'if (overlay === undefined || !("preview" in overlay) || !("tree" in overlay)) {',
    check(step) {
      // 置いている最中にもう一度押しても 2 本目は出ない（続きの子が 2 本起きる）
      const deciding = toDeciding(step);
      const after = step(deciding, { kind: "decideConfirm", choices: { u1: "keep", u2: "keep" } });
      assert.equal(after.state, deciding);
      assert.deepEqual(kinds(after.effects), []);
      assertNamed(step, ["closed", "decideLoading", "preview", "prompt", "error"]);
      for (const kind of ["closed", "decideLoading", "preview", "prompt", "error"]) {
        const state = named(step, kind);
        assert.deepEqual(
          kinds(step(state, { kind: "decideConfirm", choices: { u1: "keep", u2: "keep" } }).effects),
          [],
          kind,
        );
      }
    },
  },
  {
    what: "対応方針が見せた指摘の全部に 1 つずつ付いているときだけ送る",
    find: "if (problem !== undefined) {",
    into: "if (false) {",
    check(step) {
      const shown = toDecidePreview(step);
      const cases: readonly Record<string, string>[] = [
        { u1: "keep" },
        { u1: "keep", u2: "keep", u3: "keep" },
        { u1: "keep", u2: "later" },
      ];
      for (const choices of cases) {
        const after = step(shown, { kind: "decideConfirm", choices });
        assert.equal(after.state, shown, JSON.stringify(choices));
        assert.deepEqual(kinds(after.effects), ["warn"], JSON.stringify(choices));
      }
    },
  },
  {
    what: "残った指摘を読むのは、ユーザのレビュー待ちのフェーズだけ",
    find: "if (chip.reviewWaiting !== true) {",
    into: "if (false) {",
    check(step) {
      const after = step(CLOSED, {
        kind: "decide",
        parent: "i0001",
        phase: 1,
        tree: TREE,
        chip: chipOf({ reviewWaiting: false }),
      });
      assert.equal(after.state.overlay, undefined);
      assert.deepEqual(kinds(after.effects), ["warn", "refresh"]);
    },
  },
  {
    what: "残った指摘の一覧を受けるのは、それを頼んだ状態（decideLoading）のときだけ",
    find: 'if (overlay?.kind !== "decideLoading") {',
    into: "if (false) {",
    check(step) {
      // 閉じている状態は最後（ガードを外すと、無いプロパティを読んで投げる）
      const kinds_ = ["decidePreview", "deciding", "preview", "prompt", "error", "closed"];
      assertNamed(step, kinds_);
      for (const kind of kinds_) {
        const state = named(step, kind);
        const after = step(state, { kind: "decidePreviewed", result: { ok: true, value: decidePreviewOf(["u9"]) } });
        assert.equal(after.state, state, `${kind}: 頼んでいない一覧で開き直さない`);
      }
    },
  },
  {
    what: "残った指摘を決める途中には、承認のオーバーレイを開かない",
    find: "  if (busyDeciding(state)) {",
    into: "  if (false) {",
    check(step) {
      assertNamed(step, ["decideLoading", "decidePreview", "deciding"]);
      for (const kind of ["decideLoading", "decidePreview", "deciding"]) {
        const state = named(step, kind);
        const after = step(state, { kind: "approve", tickets: [], filtered: false, pending: [] });
        assert.equal(after.state, state, kind);
        assert.deepEqual(kinds(after.effects), [], kind);
      }
    },
  },
  {
    what: "承認の途中・承認した文・決める途中には、残った指摘のオーバーレイを被せない",
    find: 'const coverable = kind === undefined || kind === "error" || (kind === "prompt" && !keptPrompt(state));',
    into: "const coverable = true;",
    check(step) {
      const kinds_ = ["loading", "preview", "approving", "done", "decideLoading", "decidePreview", "deciding"];
      assertNamed(step, kinds_);
      for (const kind of kinds_) {
        const state = named(step, kind);
        const after = step(state, { kind: "decide", parent: "i0001", phase: 1, tree: TREE, chip: chipOf() });
        assert.equal(after.state, state, kind);
        assert.deepEqual(kinds(after.effects), [], kind);
      }
    },
  },
  {
    what: "承認を打てるのは、一覧を見せているとき（preview）だけ",
    find: 'if (state.overlay?.kind !== "preview" || tickets.length === 0) {',
    into: "if (tickets.length === 0) {",
    check(step) {
      // `approving` は `preview` を持っているので、ガードを弱めても投げずに 2 本目が出る。
      // そのぶん、失敗するのが確かめのほうになる
      const approving = toApproving(step);
      const after = step(approving, { kind: "confirm", digest: "d1", unsaved: [], tickets: ["i0001"] });
      assert.equal(after.state, approving, "承認中に押し直しても、打つのは 1 本きり");
      assert.deepEqual(kinds(after.effects), []);
      // 残りの状態も全部。1 つだけ見ると、そこ以外をガードから抜かれたときに失敗せずに通る
      assertNamed(step, ["closed", "loading", "done", "error", "prompt"]);
      for (const kind of ["closed", "loading", "done", "error", "prompt"]) {
        const state = named(step, kind);
        assert.deepEqual(kinds(step(state, { kind: "confirm", digest: "d1", unsaved: [], tickets: ["i0001"] }).effects), [], kind);
      }
    },
  },
  {
    what: "承認の途中（loading / preview / approving）は二重に開かない",
    find: 'if (kind === "loading" || kind === "preview" || kind === "approving") {',
    into: "if (false) {",
    check(step) {
      // 3 つとも回す。 1 つだけ見ると、ガードからその 1 つ以外を抜かれたときに失敗せずに通る
      assertNamed(step, ["loading", "preview", "approving"]);
      for (const kind of ["loading", "preview", "approving"]) {
        const state = named(step, kind);
        const after = step(state, { kind: "approve", tickets: [], filtered: false, pending: [] });
        assert.equal(after.state, state, `${kind}: 状態ごとそのまま（作り直さない）`);
        assert.deepEqual(kinds(after.effects), [], kind);
      }
    },
  },
  {
    what: "一覧を受けるのは、それを頼んだ状態（loading）のときだけ",
    find: 'if (state.overlay?.kind !== "loading") {',
    into: "if (false) {",
    check(step) {
      // `approving` は `recheck` を持たない形で回す（食い違いの読み直し中ではない）
      assertNamed(step, ["closed", "preview", "approving", "done", "error", "prompt"]);
      for (const kind of ["closed", "preview", "approving", "done", "error", "prompt"]) {
        const state = named(step, kind);
        const after = step(state, { kind: "previewed", saved: {}, result: { ok: true, value: previewOf(["i0001"]) } });
        assert.equal(after.state, state, `${kind}: 頼んでいない一覧で開き直さない`);
        assert.deepEqual(kinds(after.effects), [], kind);
      }
    },
  },
  {
    what: "文を渡せるのは、渡す文があるとき（done / prompt）だけ",
    find: 'if (overlay?.kind !== "done" && overlay?.kind !== "prompt") {',
    into: "if (false) {",
    check(step) {
      // 閉じている状態は最後。 ガードを外すと、そこは無いプロパティを読んで投げるので、
      // 先に置くと変異テストが「確かめが失敗した」ではなく「投げた」を見ることになる
      assertNamed(step, ["loading", "preview", "approving", "error", "closed"]);
      for (const kind of ["loading", "preview", "approving", "error", "closed"]) {
        const state = named(step, kind);
        const after = step(state, { kind: "handOver", how: "promptCopy" });
        assert.equal(after.state, state, kind);
        assert.deepEqual(kinds(after.effects), [], kind);
      }
    },
  },
  {
    what: "絞り込みで送られた識別子が、いまのボードでも承認待ちか",
    find: "if (!input.tickets.every((id) => pending.has(id))) {",
    into: "if (false) {",
    check(step) {
      // 1 件でも欠けたら止める（削って進まない）
      for (const [tickets, pending] of [
        [["i0001"], ["i0002"]],
        [["i0001", "i0002"], ["i0001"]],
        [["i0001"], []],
      ] as const) {
        const after = step(CLOSED, { kind: "approve", tickets, filtered: true, pending });
        assert.equal(after.state.overlay, undefined, `${tickets.join(",")}: 古いボードの識別子では開かない`);
        assert.deepEqual(kinds(after.effects), ["warn", "refresh"], tickets.join(","));
      }
    },
  },
  {
    what: "決めた結果の文の上に、次の「決める」を被せない",
    find: 'const coverable = kind === undefined || kind === "error" || (kind === "prompt" && !keptPrompt(state));',
    into: 'const coverable = kind === undefined || kind === "error" || kind === "prompt";',
    check(step) {
      const decided = toDecided(step);
      const after = step(decided, { kind: "decide", parent: "i0001", phase: 1, tree: TREE, chip: chipOf() });
      assert.equal(after.state, decided);
      assert.deepEqual(kinds(after.effects), []);
      // 連絡の文（keep でない prompt）の上には開ける。ここまで制限すると、決める方法が消える
      const prompt = toPrompt(step);
      assert.equal(step(prompt, { kind: "decide", parent: "i0001", phase: 1, tree: TREE, chip: chipOf() }).state.overlay?.kind, "decideLoading");
    },
  },
  {
    what: "未保存の計画編集タブがある親を含む一覧は、承認を打たない",
    find: "if (blocked.length > 0) {",
    into: "if (false) {",
    check(step) {
      const shown = toPlanPreview(step, { i0001: "s1", i0002: "s2" });
      for (const unsaved of [["i0001"], ["i0002"], ["i0002", "i0009"]]) {
        const after = step(shown, { kind: "confirm", digest: "d1", unsaved, tickets: ["i0001", "i0002"] });
        assert.equal(after.state, shown, unsaved.join(","));
        assert.deepEqual(kinds(after.effects), ["warn"], unsaved.join(","));
      }
    },
  },
  {
    what: "承認を打つ直前に、未保存の計画編集タブがある親が打つ識別子に入っていないかを見る",
    find: "return tickets.filter((ticket) => held.has(ticket)).sort();",
    into: "return [];",
    check(_step, machine) {
      assert.deepEqual(machine.blockedBeforeApprove(["i0002", "i0001", "i0001-01-01"], ["i0001", "i0002", "i0009"]), ["i0001", "i0002"]);
      assert.deepEqual(machine.blockedBeforeApprove(["i0001-01-01"], ["i0001"]), [], "子の識別子は親のタブと取り違えない");
    },
  },
  {
    what: "「計画を編集」でタブを開くのは、一覧を見せているとき（preview）だけ",
    find: 'if (overlay?.kind !== "preview") {',
    into: 'if (overlay === undefined || !("preview" in overlay)) {',
    check(step) {
      // 承認中にタブを開くと、承認の結果を受ける場所が消える（閉じてから開くので）
      const approving = step(toPlanPreview(step), { kind: "confirm", digest: "d1", unsaved: [], tickets: ["i0001"] }).state;
      assert.equal(approving.overlay?.kind, "approving");
      const after = step(approving, { kind: "editWorkflow", ticket: "i0001" });
      assert.equal(after.state, approving);
      assert.deepEqual(kinds(after.effects), []);
      assertNamed(step, ["closed", "loading", "done", "error", "prompt", "decidePreview"]);
      for (const kind of ["closed", "loading", "done", "error", "prompt", "decidePreview"]) {
        const state = named(step, kind);
        assert.deepEqual(kinds(step(state, { kind: "editWorkflow", ticket: "i0001" }).effects), [], kind);
      }
    },
  },
  {
    what: "「計画を編集」で開くのは、一覧の図にある親だけ",
    find: "if (!overlay.preview.plans.some((plan) => plan.ticket === ticket)) {",
    into: "if (false) {",
    check(step) {
      const shown = toPlanPreview(step);
      for (const ticket of ["i0002", "i0001-01-01", ""]) {
        const after = step(shown, { kind: "editWorkflow", ticket });
        assert.equal(after.state, shown, ticket);
        assert.deepEqual(kinds(after.effects), [], ticket);
      }
    },
  },
  {
    what: "読み取れなかった指摘は、件数を出さずにエラーにする（0 件と読み間違えて「レビュー済みにする」を見せない）",
    find: '    return move(state, { overlay: { kind: "error", error: unreadable(result.error) }, only: state.only });',
    into: '    return move(state, { overlay: { kind: "decidePreview", preview: { version: 2, parent: overlay.parent, phase: overlay.phase, mr: { number: 0, url: "" }, can_issue: true, threads: [], digest: "v2.x" }, tree: overlay.tree }, only: state.only });',
    check(step) {
      const loading = toDecideLoading(step);
      const failed = step(loading, { kind: "decidePreviewed", result: { ok: false, error: "threads が配列ではありません" } });
      assert.equal(failed.state.overlay?.kind, "error");
      assert.equal(failed.state.overlay?.kind === "error" ? failed.state.overlay.error : "", "指摘を読み取れませんでした（threads が配列ではありません）");
      assert.deepEqual(kinds(failed.effects), []);
    },
  },
  {
    what: "頼んだフェーズと違う一覧では開かない",
    find: "if (result.value.parent !== overlay.parent || result.value.phase !== overlay.phase) {",
    into: "if (false) {",
    check(step) {
      const loading = toDecideLoading(step);
      for (const other of [{ phase: 2 }, { parent: "i0002" }]) {
        const after = step(loading, { kind: "decidePreviewed", result: { ok: true, value: { ...decidePreviewOf(), ...other } } });
        assert.equal(after.state.overlay?.kind, "error", JSON.stringify(other));
      }
    },
  },
  {
    what: "相談のプロンプトを組むのは、1 件以上の指摘を見せているとき（decidePreview）だけ",
    find: 'if (overlay?.kind !== "decidePreview" || overlay.preview.threads.length === 0) {',
    into: 'if (overlay === undefined || !("preview" in overlay) || !("tree" in overlay)) {',
    check(step) {
      // 置いている最中は組まない（確定中は押せない）。先に回す: ガードを外すと、ここで効果が出て確かめが失敗する
      const deciding = toDeciding(step);
      assert.deepEqual(kinds(step(deciding, { kind: "consult", root: "/w" }).effects), []);
      // 0 件の画面でも組まない（相談する指摘が無い）
      const empty = step(toDecideLoading(step), { kind: "decidePreviewed", result: { ok: true, value: { ...decidePreviewOf(), threads: [] } } }).state;
      assert.equal(empty.overlay?.kind, "decidePreview");
      assert.deepEqual(kinds(step(empty, { kind: "consult", root: "/w" }).effects), []);
      assertNamed(step, ["closed", "decideLoading", "prompt", "error"]);
      for (const kind of ["closed", "decideLoading", "prompt", "error"]) {
        assert.deepEqual(kinds(step(named(step, kind), { kind: "consult", root: "/w" }).effects), [], kind);
      }
    },
  },
  {
    what: "相談のコピーの結果を受けるのは、見せている画面（decidePreview）のときだけ",
    find: 'if (state.overlay?.kind !== "decidePreview") {',
    into: "if (false) {",
    check(step) {
      assertNamed(step, ["closed", "decideLoading", "deciding", "prompt", "error"]);
      for (const kind of ["closed", "decideLoading", "deciding", "prompt", "error"]) {
        const state = named(step, kind);
        const after = step(state, { kind: "consultCopied", error: undefined });
        assert.equal(after.state, state, `${kind}: 閉じたあとに返った結果で、別の画面を変えない`);
      }
    },
  },
  {
    what: "決め直しへ戻れる結果（続きの子が起きていない）は、取り返せないものとして扱わない",
    find: "keep: !retry,",
    into: "keep: true,",
    check(step) {
      const prompt = toPrompt(step);
      assert.equal(prompt.overlay?.kind === "prompt" ? prompt.overlay.keep : undefined, false);
      assert.deepEqual(prompt.overlay?.kind === "prompt" ? prompt.overlay.retry : undefined, { parent: "i0001", phase: 1 });
      const again = step(prompt, { kind: "decide", parent: "i0001", phase: 1, tree: TREE, chip: chipOf() });
      assert.equal(again.state.overlay?.kind, "decideLoading", "決め直しへ戻れる");
    },
  },
];

// --- 遷移そのもの ---

test("CB-T169 「承認」で一覧を読みにいく。絞っていなければ承認待ち全部", () => {
  const after = approvalStep(CLOSED, { kind: "approve", tickets: [], filtered: false, pending: ["i0001"] });
  assert.equal(after.state.overlay?.kind, "loading");
  assert.equal(after.redraw, true, "読んでいる間もオーバーレイを出す");
  assert.deepEqual(after.effects, [{ kind: "loadPreview", only: [] }]);
});

test("CB-T170 絞り込みは見えている識別子だけを絞りに通す。1 件もなければ言うだけ", () => {
  const ok = approvalStep(CLOSED, {
    kind: "approve",
    tickets: ["i0001", "i0002"],
    filtered: true,
    pending: ["i0001", "i0002", "i0003"],
  });
  assert.deepEqual(ok.effects, [{ kind: "loadPreview", only: ["i0001", "i0002"] }]);
  assert.deepEqual(ok.state.only, ["i0001", "i0002"], "再読み込みにも承認にも同じ絞りを通す");

  const none = approvalStep(CLOSED, { kind: "approve", tickets: [], filtered: true, pending: ["i0001"] });
  assert.equal(none.state.overlay, undefined);
  assert.deepEqual(none.effects, [{ kind: "warn", text: "絞り込みで見えている承認待ちがありません" }]);
});

test("CB-T171 一覧が返ったら見せる。読めなければ、そう見せる", () => {
  const loading = toLoading(approvalStep);
  const shown = approvalStep(loading, { kind: "previewed", saved: {}, result: { ok: true, value: previewOf(["i0001"]) } });
  assert.equal(shown.state.overlay?.kind, "preview");
  assert.equal(shown.redraw, true);
  assert.deepEqual(shown.effects, []);

  const failed = approvalStep(loading, { kind: "previewed", saved: {}, result: { ok: false, error: "読み込めない" } });
  assert.deepEqual(failed.state.overlay, { kind: "error", error: "読み込めない" });
});

test("CB-T172 「この N 件を承認する」は、見せたダイジェストと絞りをそのまま渡す", () => {
  const preview = toPreview(approvalStep, ["i0001", "i0002"], ["i0001", "i0002"]);
  const after = approvalStep(preview, { kind: "confirm", digest: "d1", unsaved: [], tickets: ["i0001", "i0002"] });
  assert.equal(after.state.overlay?.kind, "approving");
  assert.deepEqual(after.effects, [
    { kind: "approve", tickets: ["i0001", "i0002"], digest: "d1", only: ["i0001", "i0002"] },
  ]);
  // 0 件は打たない（画面はボタンを出さないが、受け側でも持つ）
  assert.deepEqual(approvalStep(preview, { kind: "confirm", digest: "d1", unsaved: [], tickets: [] }).effects, []);
});

test("CB-T172b 押した画面のダイジェストが手元の最新と違えば打たず、今の一覧を表示し直す", () => {
  const preview = toPreview(approvalStep, ["i0001"], ["i0001"]);
  // 読み直しで一覧が d2 に差し替わったが、画面はまだ d1 を出していた（描き直す前に押した）
  const replaced = approvalStep(preview, { kind: "confirm", digest: "d0", unsaved: [], tickets: ["i0001"] });
  assert.deepEqual(replaced.effects, [], "見ていない中身を承認しない");
  assert.equal(replaced.state.overlay?.kind, "preview", "今の一覧を出し直す");
  assert.equal(replaced.state.overlay?.kind === "preview" ? replaced.state.overlay.notice : "", SHOWN_STALE);
  // 表示し直した一覧で押し直せば打つ
  const again = approvalStep(replaced.state, { kind: "confirm", digest: "d1", unsaved: [], tickets: ["i0001"] });
  assert.deepEqual(kinds(again.effects), ["approve"]);
});

test("CB-T173 承認できたら渡す文を見せ、承認の push の sh を端末に送り、ボードを再読み込みする。sh がなければ言う。0 件なら何もしない", () => {
  const approving = toApproving(approvalStep);
  const done = approvalStep(approving, {
    kind: "approved",
    outcome: { ok: true, value: resultOf(["i0001"], "文") },
    carrier: true,
  });
  assert.deepEqual(done.state.overlay, { kind: "done", count: 1, prompt: "文", carried: true });
  assert.deepEqual(kinds(done.effects), ["carry", "refresh"], "監視だけに頼らず、承認の側からも再読み込みする");
  const wrote = approvalStep(approving, {
    kind: "approved",
    outcome: { ok: true, value: resultOf(["i0001"], "文", ["a/.ccnavi/approved/doing/i0001.md"]) },
    carrier: true,
  });
  assert.deepEqual(
    wrote.effects[0],
    { kind: "carry", paths: ["a/.ccnavi/approved/doing/i0001.md"] },
    "送るのはこの承認で書いた・消したパスだけ",
  );
  assert.deepEqual(done.state.only, [], "承認できたら絞りは畳む");

  const noScript = approvalStep(approving, {
    kind: "approved",
    outcome: { ok: true, value: resultOf(["i0001"], "文") },
    carrier: false,
  });
  assert.deepEqual(noScript.state.overlay, { kind: "done", count: 1, prompt: "文", carried: false });
  assert.deepEqual(kinds(noScript.effects), ["warn", "refresh"], "端末には送らず、何をすればよいかを言う");
  assert.match(
    noScript.effects[0]?.kind === "warn" ? noScript.effects[0].text : "",
    /ccnavi-user-push-approved\.sh がないので/,
  );

  const zero = approvalStep(approving, {
    kind: "approved",
    outcome: { ok: true, value: resultOf([], "文") },
    carrier: true,
  });
  assert.deepEqual(zero.state.overlay, { kind: "done", count: 0, prompt: "文", carried: false });
  assert.deepEqual(kinds(zero.effects), [], "1 件も置かれていないなら、承認の push も再読み込みもしない");
});

test("CB-T174 食い違いは、見せたまま同じ絞りで再読み込みし、返った一覧に理由を添える", () => {
  const approving = toApproving(approvalStep, ["i0001"], ["i0001"]);
  const again = approvalStep(approving, {
    kind: "approved",
    outcome: { ok: false, mismatch: mismatchOf(["i0001"], ["i0001"]) },
    carrier: true,
  });
  assert.equal(again.state.overlay?.kind, "approving", "再読み込みしている間も、見せているものは変えない");
  assert.equal(again.redraw, false);
  assert.deepEqual(again.effects, [{ kind: "loadPreview", only: ["i0001"] }]);

  const shown = approvalStep(again.state, { kind: "previewed", saved: {}, result: { ok: true, value: previewOf(["i0001"]) } });
  assert.equal(shown.state.overlay?.kind, "preview");
  assert.equal(
    shown.state.overlay?.kind === "preview" ? shown.state.overlay.notice : "",
    "内容が更新されました（表示したあとに提案ファイルが書き換えられました）。何も承認していません。最新の内容を表示し直したので、見直してからもう一度承認してください",
    "識別子が同じなら、変わったのは提案ファイルの中身",
  );
  assert.equal(shown.state.recheck, undefined);

  // 識別子が増減していれば、言い方が変わる
  const other = approvalStep(approving, {
    kind: "approved",
    outcome: { ok: false, mismatch: mismatchOf(["i0001"], ["i0001", "i0002"]) },
    carrier: true,
  });
  const otherShown = approvalStep(other.state, {
    kind: "previewed",
    saved: {},
    result: { ok: true, value: previewOf(["i0001", "i0002"]) },
  });
  assert.equal(
    otherShown.state.overlay?.kind === "preview" ? otherShown.state.overlay.notice : "",
    "内容が更新されました（表示したあとに承認待ちの提案が増えたか減りました）。何も承認していません。最新の一覧を表示し直したので、見直してからもう一度承認してください",
  );
});

test("CB-T175 絞りが通らなくなったときだけ、1 度だけ絞りを外して再読み込みする", () => {
  const approving = toApproving(approvalStep, ["i0001"], ["i0001"]);
  const again = approvalStep(approving, {
    kind: "approved",
    outcome: { ok: false, mismatch: mismatchOf(["i0001"], []) },
    carrier: true,
  });
  const dropped = approvalStep(again.state, { kind: "previewed", saved: {}, result: { ok: false, error: "絞りが通らない" } });
  assert.deepEqual(dropped.effects, [{ kind: "loadPreview", only: [] }]);
  assert.deepEqual(dropped.state.only, [], "絞りを外して、いま何が承認待ちかを全部見せにいく");
  assert.equal(dropped.state.overlay?.kind, "approving");

  // 外したあとも読めなければ、そこで諦めて文面を見せる（二度は外さない）
  const failed = approvalStep(dropped.state, { kind: "previewed", saved: {}, result: { ok: false, error: "読み込めない" } });
  assert.deepEqual(failed.state.overlay, { kind: "error", error: "読み込めない" });
  assert.deepEqual(failed.effects, []);
});

test("CB-T176 承認が失敗したら、そのまま文面を見せる（絞りは畳まない）", () => {
  const approving = toApproving(approvalStep, ["i0001"], ["i0001"]);
  const after = approvalStep(approving, {
    kind: "approved",
    outcome: { ok: false, error: "途中で止まった" },
    carrier: true,
  });
  assert.deepEqual(after.state.overlay, { kind: "error", error: "途中で止まった" });
  assert.deepEqual(after.state.only, ["i0001"]);
  assert.deepEqual(after.effects, []);
});

test("CB-T176b 親だけ書けて子が止まった承認は、書けた分のコミット・pushを送り、書けなかった子を名指しする", () => {
  const approving = toApproving(approvalStep, ["i0001", "i0001-01-01"], ["i0001", "i0001-01-01"]);
  const partial = {
    placed: ["i0001"],
    paths: [".ccnavi/approved/doing/i0001.md"],
    written: [".ccnavi/approved/doing/i0001.md", "wip/proposals/todo/i0001.md"],
    ticket: "i0001-01-01",
    reason: "保存できませんでした",
    lines: [],
  };
  const carried = approvalStep(approving, { kind: "approved", outcome: { ok: false, partial }, carrier: true });
  assert.deepEqual(
    carried.effects[0],
    { kind: "carry", paths: [".ccnavi/approved/doing/i0001.md", "wip/proposals/todo/i0001.md"] },
    "送るのはこの承認で書いた・消したパスだけ",
  );
  assert.equal(carried.state.overlay?.kind, "error");
  const text = carried.state.overlay?.kind === "error" ? carried.state.overlay.error : "";
  assert.ok(text.includes(".ccnavi/approved/doing/i0001.md"), "書けたパスを言う");
  assert.ok(text.includes("書き込めませんでした: i0001-01-01"), "書けなかった子を言う");
  assert.deepEqual(kinds(carried.effects), ["carry", "refresh"], "書けた分は送る");

  // push の sh が無ければ送れない。未送信と言う
  const missing = approvalStep(approving, { kind: "approved", outcome: { ok: false, partial }, carrier: false });
  assert.deepEqual(kinds(missing.effects), ["refresh"]);
  assert.ok((missing.state.overlay?.kind === "error" ? missing.state.overlay.error : "").includes("未送信"));

  // 1 件も書けていなければ送らない
  const none = approvalStep(approving, {
    kind: "approved",
    outcome: { ok: false, partial: { ...partial, placed: [], paths: [], ticket: "i0001" } },
    carrier: true,
  });
  assert.deepEqual(kinds(none.effects), []);
});

test("CB-T177 ［レビュー完了］は残った指摘を読む。待ちでなければ言って読み直す。ボードから引けなければ言うだけ", () => {
  const input: ApprovalInput = { kind: "decide", parent: "i0001", phase: 1, tree: TREE, chip: chipOf({ mrNumber: 7 }) };
  // 押すと、頼んだ親・フェーズ・MR の番号を添えて指摘を読む（読み取りの検証が照らす）。何も置かない
  const after = approvalStep(CLOSED, input);
  assert.equal(after.state.overlay?.kind, "decideLoading");
  assert.equal(after.redraw, true);
  assert.deepEqual(after.effects, [{ kind: "loadDecide", tree: TREE, parent: "i0001", phase: 1, mr: 7 }]);
  // 読み込み中に、承認・もう一度のレビュー完了を押しても被せない。やめれば閉じ、遅れた答えは受けない
  assert.equal(approvalStep(after.state, { kind: "approve", tickets: [], filtered: false, pending: [] }).state, after.state);
  assert.equal(approvalStep(after.state, input).state, after.state);
  const cancelled = approvalStep(after.state, { kind: "cancel" });
  assert.equal(cancelled.state.overlay, undefined);
  assert.equal(approvalStep(cancelled.state, { kind: "decidePreviewed", result: { ok: true, value: decidePreviewOf() } }).state.overlay, undefined);
  // 読めなかった（error）の上には被せてよい
  assert.equal(approvalStep({ overlay: { kind: "error", error: "x" }, only: [] }, input).state.overlay?.kind, "decideLoading");
  // ボードから引けなければ言うだけ
  const noTree = approvalStep(CLOSED, { ...input, tree: undefined } as ApprovalInput);
  assert.equal(noTree.state.overlay, undefined);
  assert.deepEqual(noTree.effects, [
    { kind: "warn", text: "親 i0001 のワークツリーかフェーズ 1 がないので、未解決（Unresolved）の指摘を読み込めません" },
  ]);
  // ユーザのレビュー待ちでなければ、ボタンの前提が無い。言って読み直す
  const notWaiting = approvalStep(CLOSED, { ...input, chip: chipOf({ reviewWaiting: false }) } as ApprovalInput);
  assert.equal(notWaiting.state.overlay, undefined);
  assert.deepEqual(notWaiting.effects, [
    { kind: "warn", text: "親 i0001 のフェーズ 1 設計 はユーザのレビュー待ちではありません。チケット管理画面を更新しました" },
    { kind: "refresh" },
  ]);
});

test("CB-T177b 未解決が 0 件と正しく読めたときだけ「レビュー済みにする」の画面。読み取れなかったときは件数を出さず、確定の画面にならない", () => {
  const loading = toDecideLoading(approvalStep);
  const zero = approvalStep(loading, { kind: "decidePreviewed", result: { ok: true, value: { ...decidePreviewOf(), threads: [] } } });
  assert.equal(zero.state.overlay?.kind, "decidePreview");
  assert.deepEqual(zero.state.overlay?.kind === "decidePreview" ? zero.state.overlay.preview.threads : undefined, []);
  // 0 件でも確定は選ばれた対応方針なしで送れる（実行ファイルがレビュー済みにする）
  const confirm = approvalStep(zero.state, { kind: "decideConfirm", choices: {} });
  assert.equal(confirm.state.overlay?.kind, "deciding");
  assert.deepEqual(confirm.effects, [{ kind: "decide", tree: TREE, phase: 1, choices: {}, digest: "v2.eyJiIjoiYSIsInQiOnt9fQ" }]);
  // 読み取れなかった: 理由を言い、エラーのオーバーレイ（確定の画面ではない）
  for (const reason of ["JSON として解釈できません", "mr.number が整数ではありません", "ccnavi-review.sh decide --preview が失敗しました: 認証"]) {
    const failed = approvalStep(loading, { kind: "decidePreviewed", result: { ok: false, error: reason } });
    assert.deepEqual(failed.state.overlay, { kind: "error", error: `指摘を読み取れませんでした（${reason}）` });
    assert.equal(approvalStep(failed.state, { kind: "decideConfirm", choices: {} }).state, failed.state, "確定は受けない");
  }
});

test("CB-T177c 相談のプロンプトのコピー: オーバーレイを閉じず、選びかけを変えず、できた・できないを画面の中で言う。確定中と 0 件では組まない", () => {
  const shown = toDecidePreview(approvalStep);
  const asked = approvalStep(shown, { kind: "consult", root: "/w" });
  assert.equal(asked.state, shown, "状態も画面もそのまま（選びかけの対応方針は画面の中にあり、描き直さない）");
  assert.equal(asked.redraw, false);
  assert.equal(asked.effects.length, 1);
  const effect = asked.effects[0];
  assert.equal(effect.kind, "copyConsult");
  const prompt = effect.kind === "copyConsult" ? effect.prompt : "";
  assert.ok(prompt.includes("親 i0001 のフェーズ 1"));
  assert.ok(prompt.includes("- マージリクエスト: https://example.com/mr/7"));
  assert.ok(prompt.includes(`- 親のワークツリー: ${TREE}`));
  assert.ok(prompt.includes("'sh /w/.ccnavi/scripts/ccnavi-review.sh decide 1 --preview'"));
  // できた: 短く言う。選びかけの指摘（overlay.preview）はそのまま
  const ok = approvalStep(shown, { kind: "consultCopied", error: undefined });
  assert.equal(ok.state.overlay?.kind, "decidePreview");
  assert.equal(ok.redraw, true);
  assert.deepEqual(ok.state.overlay?.kind === "decidePreview" ? ok.state.overlay.consult?.ok : undefined, true);
  assert.equal(ok.state.overlay?.kind === "decidePreview" ? ok.state.overlay.preview : undefined, shown.overlay?.kind === "decidePreview" ? shown.overlay.preview : null);
  // できなかった: 失敗と言う（できたとは言わない）
  const ng = approvalStep(shown, { kind: "consultCopied", error: "クリップボードが使えません" });
  const consult = ng.state.overlay?.kind === "decidePreview" ? ng.state.overlay.consult : undefined;
  assert.equal(consult?.ok, false);
  assert.match(consult?.text ?? "", /コピーできませんでした（クリップボードが使えません）/);
  assert.doesNotMatch(consult?.text ?? "", /コピーしました/);
  // 確定中は組まない・結果も受けない
  const deciding = toDeciding(approvalStep);
  assert.deepEqual(kinds(approvalStep(deciding, { kind: "consult", root: "/w" }).effects), []);
  assert.equal(approvalStep(deciding, { kind: "consultCopied", error: undefined }).state, deciding);
});

test("CB-T177d 確定した結果は実行ファイルの答えに従う。reviewed が真ならレビュー済み、偽で pending_extra があれば追加された指摘を言って決め直しへ戻れる", () => {
  const deciding = toDeciding(approvalStep);
  const decide = (value: Partial<DecideResult>) => approvalStep(deciding, { kind: "decided", outcome: { ok: true, value: { ...resultValue(), ...value } } });
  const overlayOf = (value: Partial<DecideResult>) => {
    const o = decide(value).state.overlay;
    assert.equal(o?.kind, "prompt");
    return o?.kind === "prompt" ? o : undefined;
  };
  // レビュー済み
  const reviewed = overlayOf({});
  assert.equal(reviewed?.title, "フェーズ 1 をレビュー済みにしました");
  assert.equal(reviewed?.retry, undefined);
  assert.equal(reviewed?.keep, true);
  // 見せたあとに指摘が追加された: レビュー済みにしていないと言い、決め直しへ戻れる。取り返せるのでプロンプトを kept にしない
  const extra = overlayOf({ reviewed: false, pending_extra: ["u3", "u4"] });
  assert.equal(extra?.title, "表示した後に指摘が追加されたため、レビュー済みにしていません");
  assert.match(extra?.note ?? "", /追加された指摘 2 件は判断待ちです/);
  assert.deepEqual(extra?.retry, { parent: "i0001", phase: 1 });
  assert.equal(extra?.keep, false);
  // 続きの子が起きたときは、追加分があっても取り返せない（プロンプトを kept にして、決め直しは出さない）
  const fixed = overlayOf({ reviewed: false, pending_extra: ["u3"], followup: "i0001-02-03" });
  assert.equal(fixed?.retry, undefined);
  assert.equal(fixed?.keep, true);
  assert.match(fixed?.note ?? "", /i0001-02-03/);
  assert.match(fixed?.note ?? "", /追加された指摘 1 件は判断待ち/);
  // 答えが「レビュー済みでない」のに理由が見当たらないときは、レビュー済みになったとは言わない
  const odd = overlayOf({ reviewed: false });
  assert.doesNotMatch(odd?.title ?? "", /をレビュー済みにしました/);
  assert.match(odd?.note ?? "", /レビュー済みになっていません/);
});

test("CB-T178 文を渡したら閉じる。コピーと新しいセッションで、呼び名は同じ文に付く", () => {
  const done = toDone(approvalStep);
  const copied = approvalStep(done, { kind: "handOver", how: "promptCopy" });
  assert.equal(copied.state.overlay, undefined);
  assert.equal(copied.redraw, true);
  assert.deepEqual(copied.effects, [{ kind: "copy", prompt: "承認の文", what: "承認のプロンプト" }]);

  const prompt = toPrompt(approvalStep);
  const opened = approvalStep(prompt, { kind: "handOver", how: "promptOpen" });
  assert.equal(opened.state.overlay, undefined);
  assert.deepEqual(kinds(opened.effects), ["openSession"]);
  const copiedPrompt = approvalStep(prompt, { kind: "handOver", how: "promptCopy" });
  assert.equal(
    copiedPrompt.effects[0]?.kind === "copy" ? copiedPrompt.effects[0].what : "",
    "対応方針のプロンプト",
  );
});

test("CB-T179 「キャンセル」は閉じる。承認の結果は、どの状態でも受ける（置かれたものを捨てない）", () => {
  const preview = toPreview(approvalStep);
  const closed = approvalStep(preview, { kind: "cancel" });
  assert.deepEqual(closed.state, CLOSED);
  assert.equal(closed.redraw, true);
  // 閉じているところへ届いても、変わっていないので描き直さない
  assert.equal(approvalStep(CLOSED, { kind: "cancel" }).redraw, false);

  // 承認の結果だけは見張らない。承認済みチケットは既に置かれていることがあり、捨てるとユーザに届かない
  const late = approvalStep(CLOSED, {
    kind: "approved",
    outcome: { ok: true, value: resultOf(["i0001"], "文") },
    carrier: true,
  });
  assert.equal(late.state.overlay?.kind, "done");
});

test("CB-T202 ［レビュー完了］で残った指摘を読み、返ったら見せる。頼んだフェーズと違う答えでは開かない", () => {
  const opened = approvalStep(CLOSED, { kind: "decide", parent: "i0001", phase: 1, tree: TREE, chip: chipOf() });
  assert.equal(opened.state.overlay?.kind, "decideLoading");
  assert.deepEqual(opened.effects, [{ kind: "loadDecide", tree: TREE, parent: "i0001", phase: 1, mr: null }]);
  const shown = approvalStep(opened.state, { kind: "decidePreviewed", result: { ok: true, value: decidePreviewOf() } });
  assert.equal(shown.state.overlay?.kind, "decidePreview");
  const failed = approvalStep(opened.state, { kind: "decidePreviewed", result: { ok: false, error: "依頼の記録がありません" } });
  assert.deepEqual(failed.state.overlay, { kind: "error", error: "指摘を読み取れませんでした（依頼の記録がありません）" });
  const other = approvalStep(opened.state, {
    kind: "decidePreviewed",
    result: { ok: true, value: { ...decidePreviewOf(), phase: 2 } },
  });
  assert.equal(other.state.overlay?.kind, "error");
  // ワークツリーかフェーズが引けない（古いボード）なら言うだけ
  const stale = approvalStep(CLOSED, { kind: "decide", parent: "i0001", phase: 1, tree: undefined, chip: chipOf() });
  assert.equal(stale.state.overlay, undefined);
  assert.deepEqual(kinds(stale.effects), ["warn"]);
});

test("CB-T203 「この対応方針で決める」は、選んだ対応方針と見せたダイジェストをそのまま渡す", () => {
  const shown = toDecidePreview(approvalStep);
  const after = approvalStep(shown, { kind: "decideConfirm", choices: { u1: "keep", u2: "fix" } });
  assert.equal(after.state.overlay?.kind, "deciding");
  assert.deepEqual(after.effects, [
    { kind: "decide", tree: TREE, phase: 1, choices: { u1: "keep", u2: "fix" }, digest: "v2.eyJiIjoiYSIsInQiOnt9fQ" },
  ]);
});

test("CB-T204 置けたら渡す文を見せて再読み込みする。投稿の警告は言う。食い違いは再読み込みして見せ直す。失敗は見せる", () => {
  const deciding = toDeciding(approvalStep);
  const done = approvalStep(deciding, {
    kind: "decided",
    outcome: {
      ok: true,
      value: {
        parent: "i0001",
        phase: 1,
        reviewed: false,
        pending_extra: [],
        followup: "i0001-02-03",
        prompt: "決めた文",
        issue_url: "",
        warning: "コメントを残せなかった",
      },
    },
  });
  assert.equal(done.state.overlay?.kind, "prompt");
  assert.equal(done.state.overlay?.kind === "prompt" ? done.state.overlay.prompt : "", "決めた文");
  // fix のスレッドはホスト上で未解決のまま残る。resolve するのはユーザ（keep・issue の返信と解決は対象外）
  const note = done.state.overlay?.kind === "prompt" ? done.state.overlay.note : "";
  assert.match(note, /修正を確認したら、ユーザがホスト上で該当スレッドを resolve する。エージェントは fix を理由に resolve しない。/);
  assert.deepEqual(kinds(done.effects), ["warn", "refresh"]);
  const copied = approvalStep(done.state, { kind: "handOver", how: "promptCopy" });
  assert.deepEqual(copied.effects, [{ kind: "copy", prompt: "決めた文", what: "対応方針のプロンプト" }]);
  const again = approvalStep(deciding, { kind: "decided", outcome: { ok: false, mismatch: true } });
  assert.equal(again.state.overlay?.kind, "decideLoading");
  assert.ok(again.state.overlay?.kind === "decideLoading" && again.state.overlay.notice);
  assert.deepEqual(again.effects, [{ kind: "loadDecide", tree: TREE, parent: "i0001", phase: 1, mr: 7 }]);
  const failed = approvalStep(deciding, { kind: "decided", outcome: { ok: false, error: "投げた" } });
  assert.deepEqual(failed.state.overlay, { kind: "error", error: "投げた" });
  assert.deepEqual(kinds(failed.effects), ["refresh"]);
});

test("CB-T378 未保存の計画編集タブがある親を含む一覧は、承認を打たずに理由を言う。タブのない親と子だけなら打てる", () => {
  const shown = toPlanPreview(approvalStep, { i0001: "s1" });
  const blocked = approvalStep(shown, { kind: "confirm", digest: "d1", unsaved: ["i0001"], tickets: ["i0001"] });
  assert.equal(blocked.state, shown, "一覧を見せたまま");
  assert.equal(blocked.redraw, false);
  assert.deepEqual(blocked.effects, [
    { kind: "warn", text: "親 i0001 の計画編集タブに未保存の変更があります。保存するか破棄してから承認してください" },
  ]);
  // 別の親のタブだけが未保存なら、この一覧は打てる
  const other = approvalStep(shown, { kind: "confirm", digest: "d1", unsaved: ["i0002"], tickets: ["i0001"] });
  assert.equal(other.state.overlay?.kind, "approving");
  assert.deepEqual(kinds(other.effects), ["approve"]);
  // 子の識別子は親のタブと取り違えない（子だけの一覧は親を含まない）
  const children = approvalStep(toLoading(approvalStep), {
    kind: "previewed",
    saved: {},
    result: { ok: true, value: planPreviewOf({}, ["i0001-01-01"]) },
  }).state;
  assert.deepEqual(kinds(approvalStep(children, { kind: "confirm", digest: "d1", unsaved: ["i0001"], tickets: ["i0001-01-01"] }).effects), ["approve"]);
  // 2 つの親が未保存なら、両方を名指しする
  const two = approvalStep(toPlanPreview(approvalStep, { i0001: "s1", i0002: "s2" }), { kind: "confirm", digest: "d1", unsaved: ["i0002", "i0001"], tickets: ["i0001", "i0002"] });
  assert.deepEqual(two.effects, [
    { kind: "warn", text: "親 i0001, i0002 の計画編集タブに未保存の変更があります。保存するか破棄してから承認してください" },
  ]);
});

test("CB-T379 「計画を編集」は、オーバーレイを閉じてから親のタブを開く（開いたままにしない）", () => {
  const shown = toPlanPreview(approvalStep);
  const after = approvalStep(shown, { kind: "editWorkflow", ticket: "i0001" });
  assert.deepEqual(after.state, CLOSED);
  assert.equal(after.redraw, true, "閉じたことを先に描く");
  assert.deepEqual(after.effects, [{ kind: "openWorkflow", ticket: "i0001" }]);
});

test("CB-T380 一覧を開くとき、タブで保存したときのハッシュと今の source_sha が違う親を覚える（保存したあとに提案が変わった）。承認は止めない", () => {
  // 保存したまま変わっていない親・保存していない親は言わない。変わった親だけ
  const shown = toPlanPreview(approvalStep, { i0001: "s1-after-agent", i0002: "s2", i0003: "s3" }, { i0001: "s1-saved", i0002: "s2" });
  assert.equal(shown.overlay?.kind, "preview");
  assert.deepEqual(shown.overlay?.kind === "preview" ? shown.overlay.changedSinceSave : undefined, ["i0001"]);
  assert.deepEqual(kinds(approvalStep(shown, { kind: "confirm", digest: "d1", unsaved: [], tickets: ["i0001", "i0002", "i0003"] }).effects), ["approve"]);
  // 変わった親が無ければ持たない
  const same = toPlanPreview(approvalStep, { i0001: "s1" }, { i0001: "s1" });
  assert.deepEqual(same.overlay?.kind === "preview" ? same.overlay.changedSinceSave ?? [] : undefined, []);
  // 食い違いで読み直したときも比べる
  const approving = approvalStep(toPlanPreview(approvalStep), { kind: "confirm", digest: "d1", unsaved: [], tickets: ["i0001"] }).state;
  const again = approvalStep(approving, { kind: "approved", outcome: { ok: false, mismatch: mismatchOf(["i0001"], ["i0001"]) }, carrier: true });
  const reshown = approvalStep(again.state, { kind: "previewed", saved: { i0001: "s0" }, result: { ok: true, value: planPreviewOf({ i0001: "s1" }) } });
  assert.deepEqual(reshown.state.overlay?.kind === "preview" ? reshown.state.overlay.changedSinceSave : undefined, ["i0001"]);
});

test("CB-T180 ガードは全部効いている（表の 1 行ずつ）", () => {
  for (const guard of GUARDS) {
    guard.check(approvalStep, MACHINE);
  }
  assert.equal(GUARDS.length, 23, "ガードを追加したら GUARDS にも追加する");
});

// --- 変異テスト ---

/** 遷移のソース（TypeScript のまま）。変異させてから、その場で組み立てる */
const SOURCE = path.join(__dirname, "..", "..", "..", "src", "core", "approval-machine.ts");
/** 組み上がった側の置き場。遷移が読む `./commands.js` はここから引く */
const CORE_OUT = path.join(__dirname, "..", "..", "src", "core");

/**
 * TypeScript の文字列を、その場で組み立てて読み込む。型は見ない（`transpileModule`）ので、
 * ガードを消して届かなくなった行が残っていても通る。
 */
function load(source: string): Machine {
  const js = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: SOURCE,
  }).outputText;
  const box: { exports: Record<string, unknown> } = { exports: {} };
  const wrapper = vm.runInThisContext(`(function (exports, require, module) {${js}\n})`, { filename: SOURCE });
  wrapper(box.exports, (spec: string) => require(spec.startsWith(".") ? path.join(CORE_OUT, spec) : spec), box);
  return box.exports as unknown as Machine;
}

test("CB-T181 ガードを 1 つ削除すると、それを確認するテストが失敗する（変異テスト）", () => {
  const source = fs.readFileSync(SOURCE, "utf8");

  // 組み立て直したものが、読み込んだものと同じに動くこと。ここが成り立たないと、以下は何も見ていない
  const same = load(source);
  for (const guard of GUARDS) {
    guard.check(same.approvalStep, same);
  }

  for (const guard of GUARDS) {
    const at = source.split(guard.find).length - 1;
    assert.equal(at, 1, `変異させる 1 行が見つからない（${guard.what}）。ソースを直したら find も直す`);
    const mutated = load(source.replace(guard.find, guard.into));
    // `assert.AssertionError` に限る。 無いプロパティを読んだ `TypeError` で偶然「失敗した」ことに
    // しない（確かめる状態は、ガードを外しても投げない側を選んである）
    assert.throws(
      () => guard.check(mutated.approvalStep, mutated),
      assert.AssertionError,
      `ガードを削除しても確認が通った（か、確認の外で投げた）: ${guard.what}`,
    );
  }
});
