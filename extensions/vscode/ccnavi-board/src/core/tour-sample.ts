/**
 * 吹き出しの案内（`webview/Tour.tsx`）の間だけ画面に出す見本。実際のチケットやプロジェクトではない。
 *
 * 入れたばかりのワークスペースにはチケットもプロジェクトも無く、案内が指す先（カード・行）が無い。
 * そこで案内の間だけ、中身が空の画面にこの見本を出す。閉じたら消え、拡張ホストへは何も送らない
 * （見本を出している間は全面の覆いがクリックを受けるので、見本のボタンは押せない）。
 *
 * ボードの見本は実行ファイルの出力と同じ形（`BoardJson`）で書き、`buildBoard` を通す。カードの形を
 * ここで組むと、カードの欄が増えたときに見本だけが古くなる。ワークフロー編集タブの見本も同じで、
 * `--plan-order <親> --json` の答えの実例（拡張のテストのフィクスチャ `plan-order.json` の `open`）を写して
 * `parsePlanOrder` を通す（実例とずれないことはテストが見る）。DOM に触れないので単体で試せる。
 */
import { buildBoard, type Board } from "./board.js";
import type { BoardJson, ParentJson, PhaseJson, TicketJson } from "./model.js";
import { parsePlanOrder, type PlanOrderAnswer } from "./planordermodel.js";

/** 見本の目印。画面は見本を出している間、この文を帯で出す */
export const SAMPLE_NOTE = "案内のための見本を表示しています。実際のチケット・プロジェクトではなく、案内を閉じると消えます。";

const AT = "2026-01-01T09:00:00+0900";

function ticket(fields: Partial<TicketJson> & Pick<TicketJson, "ticket" | "title">): TicketJson {
  return {
    parent: "",
    phase: null,
    project: "",
    issue: null,
    predecessors: [],
    predecessors_unmet: [],
    human_review: { required: false, reason: "" },
    proposal: null,
    blocked: "",
    copy: { status: "open", approved_at: AT },
    worktree: { exists: true, path: "" },
    started_at: AT,
    completed_at: "",
    base_sha: "",
    cancelled_at: "",
    cancel_reason: "",
    seen_in: [],
    scattered: [],
    risk: null,
    judge: null,
    flow: null,
    history: [],
    issues: [],
    attention: false,
    ...fields,
  };
}

function phase(fields: Partial<PhaseJson> & Pick<PhaseJson, "number" | "type" | "title" | "state">): PhaseJson {
  return {
    label: `${fields.number}（${fields.title}）`,
    tickets: [],
    states: {},
    marks: {},
    review_required: false,
    gate_closed: false,
    review_waiting: false,
    deferred: false,
    review_at: null,
    covers: [],
    risk: null,
    risk_escalates: false,
    risk_line: "",
    ...fields,
  };
}

const SAMPLE_PARENT: ParentJson = {
  ticket: "sample-b",
  closed: false,
  stage: "作業中（2（設計））",
  // 計画の項は `after` で先行を書く（実行ファイルの `--explain --json` と同じ形）。最後の項がほかの全部を待つ
  plan: ["research", { type: "design", after: [1] }, { type: "implement", after: [2] }],
  feedback: null,
  close_early: null,
  ready: null,
  accepted_threads: [],
  phases: [
    phase({ number: 1, type: "research", title: "調査", state: "ended", tickets: ["sample-b-01-01"], states: { "sample-b-01-01": "done" }, risk_line: "リスク: 0 (LOW)" }),
    phase({ number: 2, type: "design", title: "設計", state: "active", tickets: ["sample-b-02-01"], states: { "sample-b-02-01": "doing" }, review_required: true }),
    phase({ number: 3, type: "implement", title: "実装とテスト", state: "planned", review_required: true }),
  ],
};

/**
 * ボードの見本。承認待ちの親 1 枚、作業中の親と子、閉じた子。`root` と `generatedAt` は今のボードのものを
 * そのまま使う（脚注が見本のために変わらないように）。
 */
export function sampleBoard(root: string, generatedAt: string): Board {
  const json: BoardJson = {
    version: 1,
    root,
    generated_at: generatedAt,
    settings: { ticket_control: "enable", tickets: "", approved: "", projects: "" },
    trees: [],
    layers: [],
    sums: [],
    projects: [],
    problems: [],
    pending_approval: ["sample-a"],
    tickets: [
      ticket({
        ticket: "sample-a",
        title: "（見本）ログイン画面にパスワードの再設定を足す",
        proposal: { state: "todo", tree: "", tree_root: "", path: "", has_plan: true },
        copy: { status: "none" },
        worktree: { exists: false, path: "" },
        started_at: "",
        human_review: { required: true, reason: "見本" },
        // 実行ファイルなら承認待ちを要対応と言う
        attention: true,
      }),
      ticket({
        ticket: "sample-b",
        title: "（見本）検索の応答を速くする",
        human_review: { required: true, reason: "見本" },
      }),
      ticket({ ticket: "sample-b-01-01", parent: "sample-b", phase: 1, title: "（見本）遅いクエリを調べる", copy: { status: "closed", approved_at: AT }, completed_at: AT, risk: { points: 0, level: "LOW" } }),
      ticket({ ticket: "sample-b-02-01", parent: "sample-b", phase: 2, title: "（見本）索引の張り方を決める" }),
    ],
    parents: [SAMPLE_PARENT],
  };
  return buildBoard(json);
}

/** 見本の計画の項（実行ファイルの答えと同じ形） */
function planItem(number: number, type: string, title: string, review: string): Record<string, unknown> {
  return { number, from: number, type, title, review, deferred: false, review_at: null, locked: false };
}

/**
 * ワークフロー編集タブの見本の計画。全体計画 1〜4（2 と 3 が 1 を待ち、4 が両方を待つ）と、フィードバック計画 5〜6。
 * 図を出せない答え（計画が空）のタブで、案内の間だけ読むだけで出す。保存にも線の頼みにも使わない
 */
export function sampleWorkflow(): PlanOrderAnswer {
  const sha = "sample";
  const json = {
    version: 1,
    ticket: "sample-a",
    source_sha: sha,
    plans: [
      {
        ticket: "sample-a",
        part: "plan",
        source_sha: sha,
        items: [planItem(1, "research", "調査", "none"), planItem(2, "design", "設計", "mr"), planItem(3, "implement", "実装とテスト", "mr"), planItem(4, "docs", "docs", "")],
        after: { "2": [1], "3": [1], "4": [2, 3] },
        proposed: { "2": [1], "3": [1], "4": [2, 3] },
        current: null,
        loose: [],
        ready: [1],
        problems: [],
      },
      {
        ticket: "sample-a",
        part: "feedback",
        source_sha: sha,
        items: [planItem(5, "design-feedback", "design-feedback", ""), planItem(6, "implement-feedback", "実装フィードバック対応", "mr")],
        after: { "6": [5] },
        proposed: { "6": [5] },
        current: null,
        loose: [],
        ready: [5],
        problems: [],
      },
    ],
    problems: [],
    loose: [],
    refused: [],
    revision_problems: [],
    children: [],
    child_problems: [],
    mismatched: [],
    writable: true,
  };
  const parsed = parsePlanOrder(JSON.stringify(json));
  if (!parsed.ok) {
    // 見本が実行ファイルの形から外れた（テストが先に落ちる）
    throw new Error(parsed.error);
  }
  return parsed.value;
}
