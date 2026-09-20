/**
 * 承認のオーバーレイの遷移（`src/core/approval-machine.ts`）。どの状態で何を受け、何を返すか。
 *
 * 後半は**変異テスト**。見張りを 1 つずつ消したソースをその場で組み立てて、
 * 「見張りが効いていること」を確かめる関数が落ちることまで見る。見張りを足したら `GUARDS` にも足す。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import * as vm from "node:vm";
import * as ts from "typescript";

import {
  approvalStep,
  CLOSED,
  type ApprovalEffect,
  type ApprovalState,
  type ApprovalStep,
  type ApprovalInput,
} from "../../src/core/approval-machine.js";
import type { ApproveMismatch, ApprovePreview, ApproveResult } from "../../src/core/approvemodel.js";
import type { PhaseChip } from "../../src/core/board.js";

type Step = (state: ApprovalState, input: ApprovalInput) => ApprovalStep;

/** 効いたことの並び。中身まで見ない確かめは、この形で比べる */
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
  };
}

function resultOf(approved: readonly string[], prompt = "承認の文"): ApproveResult {
  return { version: 1, approved, copies: [], lines: [], prompt };
}

function mismatchOf(expected: readonly string[], current: readonly string[]): ApproveMismatch {
  return { expected, current, digest: { expected: "d1", current: "d2" } };
}

function chipOf(overrides: Partial<PhaseChip> = {}): PhaseChip {
  return {
    parent: "i0001",
    number: 1,
    label: "1 設計",
    state: "active",
    marks: [],
    gateClosed: false,
    reviewWaiting: true,
    reviewRequired: true,
    riskLevel: "",
    riskLine: "",
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
  return step(toLoading(step, only), { kind: "previewed", result: { ok: true, value: previewOf(tickets) } }).state;
}

function toApproving(step: Step, tickets: readonly string[] = ["i0001"], only: readonly string[] = []): ApprovalState {
  return step(toPreview(step, tickets, only), { kind: "confirm", tickets }).state;
}

function toDone(step: Step): ApprovalState {
  return step(toApproving(step), {
    kind: "approved",
    outcome: { ok: true, value: resultOf(["i0001"]) },
    carrier: true,
  }).state;
}

// --- 見張り。表の 1 行が 1 つの `check`。変異テストが同じ関数を使う ---

interface Guard {
  /** 何を守っているか */
  readonly what: string;
  /** 消すために置き換えるソースの 1 行（ちょうど 1 か所に出ること） */
  readonly find: string;
  /** 置き換えた後（見張りが効かなくなる形） */
  readonly into: string;
  /** 効いていることの確かめ。**見張りを消したらここが落ちる**（または、そこで投げる） */
  readonly check: (step: Step) => void;
}

const GUARDS: readonly Guard[] = [
  {
    what: "承認を打っている最中は閉じない",
    find: 'return state.overlay?.kind === "approving" ? stay(state) : move(state, CLOSED);',
    into: "return move(state, CLOSED);",
    check(step) {
      const approving = toApproving(step);
      const after = step(approving, { kind: "cancel" });
      assert.equal(after.state.overlay?.kind, "approving");
      assert.equal(after.redraw, false);
      assert.deepEqual(kinds(after.effects), []);
    },
  },
  {
    what: "承認を打てるのは、一覧を見せているとき（preview）だけ",
    find: 'if (state.overlay?.kind !== "preview" || tickets.length === 0) {',
    into: "if (tickets.length === 0) {",
    check(step) {
      const done = toDone(step);
      const after = step(done, { kind: "confirm", tickets: ["i0001"] });
      assert.equal(after.state.overlay?.kind, "done");
      assert.deepEqual(kinds(after.effects), []);
    },
  },
  {
    what: "承認の途中（loading / preview / approving）は二重に開かない",
    find: 'if (kind === "loading" || kind === "preview" || kind === "approving") {',
    into: "if (false) {",
    check(step) {
      const preview = toPreview(step);
      const after = step(preview, { kind: "approve", tickets: [], filtered: false, pending: [] });
      assert.equal(after.state, preview, "状態ごとそのまま（作り直さない）");
      assert.deepEqual(kinds(after.effects), []);
    },
  },
  {
    what: "一覧を受けるのは、それを頼んだ状態（loading）のときだけ",
    find: 'if (state.overlay?.kind !== "loading") {',
    into: "if (false) {",
    check(step) {
      const after = step(CLOSED, { kind: "previewed", result: { ok: true, value: previewOf(["i0001"]) } });
      assert.equal(after.state.overlay, undefined, "閉じたあとに返ってきた一覧で開き直さない");
      assert.deepEqual(kinds(after.effects), []);
    },
  },
  {
    what: "文を渡せるのは、渡す文があるとき（done / prompt）だけ",
    find: 'if (overlay?.kind !== "done" && overlay?.kind !== "prompt") {',
    into: "if (false) {",
    check(step) {
      const loading = toLoading(step);
      const after = step(loading, { kind: "handOver", how: "promptCopy" });
      assert.equal(after.state.overlay?.kind, "loading");
      assert.deepEqual(kinds(after.effects), []);
    },
  },
  {
    what: "承認の文（done）の上に、レビュー済みの連絡を被せない",
    find: 'if (kind !== undefined && kind !== "error" && kind !== "prompt") {',
    into: "if (false) {",
    check(step) {
      const done = toDone(step);
      const after = step(done, {
        kind: "reviewed",
        parent: "i0001",
        phase: 1,
        tree: "/w/.claude/worktrees/i0001",
        chip: chipOf(),
        root: "/w",
      });
      assert.equal(after.state.overlay?.kind, "done", "承認の文は、渡し終えるか閉じるまで消さない");
      assert.deepEqual(kinds(after.effects), []);
    },
  },
  {
    what: "絞り込みで送られた識別子が、いまのボードでも承認待ちか",
    find: "if (!input.tickets.every((id) => pending.has(id))) {",
    into: "if (false) {",
    check(step) {
      const after = step(CLOSED, {
        kind: "approve",
        tickets: ["i0001"],
        filtered: true,
        pending: ["i0002"],
      });
      assert.equal(after.state.overlay, undefined, "古いボードの識別子では開かない");
      assert.deepEqual(kinds(after.effects), ["warn", "refresh"]);
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

test("CB-T170 絞り込みは見えている識別子だけを絞りに通す。1 件も無ければ言うだけ", () => {
  const ok = approvalStep(CLOSED, {
    kind: "approve",
    tickets: ["i0001", "i0002"],
    filtered: true,
    pending: ["i0001", "i0002", "i0003"],
  });
  assert.deepEqual(ok.effects, [{ kind: "loadPreview", only: ["i0001", "i0002"] }]);
  assert.deepEqual(ok.state.only, ["i0001", "i0002"], "読み直しにも承認にも同じ絞りを通す");

  const none = approvalStep(CLOSED, { kind: "approve", tickets: [], filtered: true, pending: ["i0001"] });
  assert.equal(none.state.overlay, undefined);
  assert.deepEqual(none.effects, [{ kind: "warn", text: "絞り込みで見えている承認待ちが無い" }]);
});

test("CB-T171 一覧が返ったら見せる。読めなければ、そう見せる", () => {
  const loading = toLoading(approvalStep);
  const shown = approvalStep(loading, { kind: "previewed", result: { ok: true, value: previewOf(["i0001"]) } });
  assert.equal(shown.state.overlay?.kind, "preview");
  assert.equal(shown.redraw, true);
  assert.deepEqual(shown.effects, []);

  const failed = approvalStep(loading, { kind: "previewed", result: { ok: false, error: "読めない" } });
  assert.deepEqual(failed.state.overlay, { kind: "error", error: "読めない" });
});

test("CB-T172 「この N 件を承認する」は、見せた指紋と絞りをそのまま渡す", () => {
  const preview = toPreview(approvalStep, ["i0001", "i0002"], ["i0001", "i0002"]);
  const after = approvalStep(preview, { kind: "confirm", tickets: ["i0001", "i0002"] });
  assert.equal(after.state.overlay?.kind, "approving");
  assert.deepEqual(after.effects, [
    { kind: "approve", tickets: ["i0001", "i0002"], digest: "d1", only: ["i0001", "i0002"] },
  ]);
  // 0 件は打たない（画面はボタンを出さないが、受け側でも持つ）
  assert.deepEqual(approvalStep(preview, { kind: "confirm", tickets: [] }).effects, []);
});

test("CB-T173 承認できたら渡す文を見せ、運ぶ sh を端末に送る。無ければ言う。0 件なら送らない", () => {
  const approving = toApproving(approvalStep);
  const done = approvalStep(approving, {
    kind: "approved",
    outcome: { ok: true, value: resultOf(["i0001"], "文") },
    carrier: true,
  });
  assert.deepEqual(done.state.overlay, { kind: "done", count: 1, prompt: "文", carried: true });
  assert.deepEqual(kinds(done.effects), ["carry"]);
  assert.deepEqual(done.state.only, [], "承認できたら絞りは畳む");

  const noScript = approvalStep(approving, {
    kind: "approved",
    outcome: { ok: true, value: resultOf(["i0001"], "文") },
    carrier: false,
  });
  assert.deepEqual(noScript.state.overlay, { kind: "done", count: 1, prompt: "文", carried: false });
  assert.deepEqual(kinds(noScript.effects), ["carryMissing"]);

  const zero = approvalStep(approving, {
    kind: "approved",
    outcome: { ok: true, value: resultOf([], "文") },
    carrier: true,
  });
  assert.deepEqual(zero.state.overlay, { kind: "done", count: 0, prompt: "文", carried: false });
  assert.deepEqual(kinds(zero.effects), [], "1 件も置かれていないなら運ばない");
});

test("CB-T174 食い違いは、見せたまま同じ絞りで読み直し、返った一覧に理由を添える", () => {
  const approving = toApproving(approvalStep, ["i0001"], ["i0001"]);
  const again = approvalStep(approving, {
    kind: "approved",
    outcome: { ok: false, mismatch: mismatchOf(["i0001"], ["i0001"]) },
    carrier: true,
  });
  assert.equal(again.state.overlay?.kind, "approving", "読み直している間も、見せているものは変えない");
  assert.equal(again.redraw, false);
  assert.deepEqual(again.effects, [{ kind: "loadPreview", only: ["i0001"] }]);

  const shown = approvalStep(again.state, { kind: "previewed", result: { ok: true, value: previewOf(["i0001"]) } });
  assert.equal(shown.state.overlay?.kind, "preview");
  assert.equal(
    shown.state.overlay?.kind === "preview" ? shown.state.overlay.notice : "",
    "見せた承認画面と今の本文が違った（提案の中身が変わった）。見直してから承認する",
    "識別子が同じなら、変わったのは本文",
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
    result: { ok: true, value: previewOf(["i0001", "i0002"]) },
  });
  assert.equal(
    otherShown.state.overlay?.kind === "preview" ? otherShown.state.overlay.notice : "",
    "見せた一覧と今の一覧が違った（提案が増えたか減った）。見直してから承認する",
  );
});

test("CB-T175 絞りが通らなくなったときだけ、1 度だけ絞りを外して読み直す", () => {
  const approving = toApproving(approvalStep, ["i0001"], ["i0001"]);
  const again = approvalStep(approving, {
    kind: "approved",
    outcome: { ok: false, mismatch: mismatchOf(["i0001"], []) },
    carrier: true,
  });
  const dropped = approvalStep(again.state, { kind: "previewed", result: { ok: false, error: "絞りが通らない" } });
  assert.deepEqual(dropped.effects, [{ kind: "loadPreview", only: [] }]);
  assert.deepEqual(dropped.state.only, [], "絞りを外して、いま何が承認待ちかを全部見せにいく");
  assert.equal(dropped.state.overlay?.kind, "approving");

  // 外したあとも読めなければ、そこで諦めて文面を見せる（二度は外さない）
  const failed = approvalStep(dropped.state, { kind: "previewed", result: { ok: false, error: "読めない" } });
  assert.deepEqual(failed.state.overlay, { kind: "error", error: "読めない" });
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

test("CB-T177 レビュー済みの連絡は、閉じているときと、error / prompt の上にだけ出す", () => {
  const input: ApprovalInput = {
    kind: "reviewed",
    parent: "i0001",
    phase: 1,
    tree: "/w/.claude/worktrees/i0001",
    chip: chipOf(),
    root: "/w",
  };
  const after = approvalStep(CLOSED, input);
  assert.equal(after.state.overlay?.kind, "prompt");
  assert.equal(after.redraw, true);
  assert.deepEqual(after.effects, [], "マーカーは置かない。文を組むだけ");
  const overlay = after.state.overlay;
  assert.equal(overlay?.kind === "prompt" ? overlay.title : "", "フェーズ 1 設計 のレビュー済みを連絡");
  assert.match(
    overlay?.kind === "prompt" ? overlay.prompt : "",
    /ccnavi-review\.sh check --phase 1'/,
    "打つのは親のワークツリーでの check 1 本",
  );

  // 読めなかった（error）の上には被せてよい
  const onError = approvalStep({ overlay: { kind: "error", error: "x" }, only: [] }, input);
  assert.equal(onError.state.overlay?.kind, "prompt");

  // ボードから引けなければ言うだけ
  const noTree = approvalStep(CLOSED, { ...input, tree: undefined });
  assert.equal(noTree.state.overlay, undefined);
  assert.deepEqual(noTree.effects, [
    { kind: "warn", text: "親 i0001 のワークツリーかフェーズ 1 が無いので、レビュー済みの連絡を組めない" },
  ]);

  // 人のレビュー待ちでなければ、ボタンの前提が無い。言って読み直す
  const notWaiting = approvalStep(CLOSED, { ...input, chip: chipOf({ reviewWaiting: false }) });
  assert.equal(notWaiting.state.overlay, undefined);
  assert.deepEqual(notWaiting.effects, [
    { kind: "warn", text: "親 i0001 のフェーズ 1 設計 は人のレビュー待ちではない。ボードを更新する" },
    { kind: "refresh" },
  ]);
});

test("CB-T178 文を渡したら閉じる。コピーと新しいセッションで、呼び名は同じ文に付く", () => {
  const done = toDone(approvalStep);
  const copied = approvalStep(done, { kind: "handOver", how: "promptCopy" });
  assert.equal(copied.state.overlay, undefined);
  assert.equal(copied.redraw, true);
  assert.deepEqual(copied.effects, [{ kind: "copy", prompt: "承認の文", what: "承認の文" }]);

  const prompt = approvalStep(CLOSED, {
    kind: "reviewed",
    parent: "i0001",
    phase: 1,
    tree: "/w/.claude/worktrees/i0001",
    chip: chipOf(),
    root: "/w",
  }).state;
  const opened = approvalStep(prompt, { kind: "handOver", how: "promptOpen" });
  assert.equal(opened.state.overlay, undefined);
  assert.deepEqual(kinds(opened.effects), ["openSession"]);
  const copiedPrompt = approvalStep(prompt, { kind: "handOver", how: "promptCopy" });
  assert.equal(
    copiedPrompt.effects[0]?.kind === "copy" ? copiedPrompt.effects[0].what : "",
    "レビュー済みの連絡の文",
  );
});

test("CB-T179 「やめる」は閉じる。承認の結果は、どの状態でも受ける（置かれたものを捨てない）", () => {
  const preview = toPreview(approvalStep);
  const closed = approvalStep(preview, { kind: "cancel" });
  assert.deepEqual(closed.state, CLOSED);
  assert.equal(closed.redraw, true);

  // 承認の結果だけは見張らない。承認済みチケットは既に置かれていることがあり、捨てると人に届かない
  const late = approvalStep(CLOSED, {
    kind: "approved",
    outcome: { ok: true, value: resultOf(["i0001"], "文") },
    carrier: true,
  });
  assert.equal(late.state.overlay?.kind, "done");
});

test("CB-T180 見張りは全部効いている（表の 1 行ずつ）", () => {
  for (const guard of GUARDS) {
    guard.check(approvalStep);
  }
  assert.equal(GUARDS.length, 7, "見張りを足したら GUARDS にも足す");
});

// --- 変異テスト ---

/** 遷移のソース（TypeScript のまま）。変異させてから、その場で組み立てる */
const SOURCE = path.join(__dirname, "..", "..", "..", "src", "core", "approval-machine.ts");
/** 組み上がった側の置き場。遷移が読む `./commands.js` はここから引く */
const CORE_OUT = path.join(__dirname, "..", "..", "src", "core");

/**
 * TypeScript の文字列を、その場で組み立てて読み込む。型は見ない（`transpileModule`）ので、
 * 見張りを消して届かなくなった行が残っていても通る。
 */
function load(source: string): { readonly approvalStep: Step } {
  const js = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: SOURCE,
  }).outputText;
  const box: { exports: Record<string, unknown> } = { exports: {} };
  const wrapper = vm.runInThisContext(`(function (exports, require, module) {${js}\n})`, { filename: SOURCE });
  wrapper(box.exports, (spec: string) => require(spec.startsWith(".") ? path.join(CORE_OUT, spec) : spec), box);
  return box.exports as unknown as { readonly approvalStep: Step };
}

test("CB-T181 見張りを 1 つ消すと、それを確かめるテストが落ちる（変異テスト）", () => {
  const source = fs.readFileSync(SOURCE, "utf8");

  // 組み立て直したものが、読み込んだものと同じに動くこと。ここが崩れていると、以下は何も見ていない
  const same = load(source);
  for (const guard of GUARDS) {
    guard.check(same.approvalStep);
  }

  for (const guard of GUARDS) {
    const at = source.split(guard.find).length - 1;
    assert.equal(at, 1, `変異させる 1 行が見つからない（${guard.what}）。ソースを直したら find も直す`);
    const mutated = load(source.replace(guard.find, guard.into));
    assert.throws(
      () => guard.check(mutated.approvalStep),
      `見張りを消しても確かめが通った: ${guard.what}。確かめが緩い`,
    );
  }
});
