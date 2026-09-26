/**
 * 吹き出しの案内（`webview/Tour.tsx`）の間だけ画面に出す見本。**実際のチケットやプロジェクトではない。**
 *
 * 入れたばかりのワークスペースにはチケットもプロジェクトも無く、案内が指す先（カード・行）が無い。
 * そこで案内の間だけ、中身が空の画面にこの見本を出す。閉じたら消え、拡張ホストへは何も送らない
 * （見本を出している間は全面の覆いがクリックを受けるので、見本のボタンは押せない）。
 *
 * ボードの見本は実行ファイルの出力と同じ形（`BoardJson`）で書き、`buildBoard` を通す。カードの形を
 * ここで組むと、カードの欄が増えたときに見本だけが古くなる。DOM に触れないので単体で試せる。
 */
import { buildBoard, type Board } from "./board.js";
import type { BoardJson, ParentJson, PhaseJson, TicketJson } from "./model.js";
import type { ProjectRow } from "./projects-view.js";

/** 見本の印。画面は見本を出している間、この文を帯で出す */
export const SAMPLE_NOTE = "案内のための見本を表示しています。実際のチケット・プロジェクトではなく、案内を閉じると消えます。";

const AT = "2026-01-01T09:00:00+0900";

function ticket(fields: Partial<TicketJson> & Pick<TicketJson, "ticket" | "title">): TicketJson {
  return {
    parent: "",
    phase: null,
    project: "",
    issue: null,
    predecessors: [],
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
  ticket: "sample-2",
  closed: false,
  stage: "作業中（2（設計））",
  plan: ["research", "design", "implement"],
  feedback: null,
  close_early: null,
  ready: null,
  accepted_threads: [],
  phases: [
    phase({ number: 1, type: "research", title: "調査", state: "ended", tickets: ["sample-2-01"], states: { "sample-2-01": "done" }, risk_line: "リスク: 0 (LOW)" }),
    phase({ number: 2, type: "design", title: "設計", state: "active", tickets: ["sample-2-02"], states: { "sample-2-02": "doing" }, review_required: true }),
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
    projects: [],
    problems: [],
    pending_approval: ["sample-1"],
    tickets: [
      ticket({
        ticket: "sample-1",
        title: "（見本）ログイン画面にパスワードの再設定を足す",
        proposal: { state: "todo", tree: "", tree_root: "", path: "" },
        copy: { status: "none" },
        worktree: { exists: false, path: "" },
        started_at: "",
        human_review: { required: true, reason: "見本" },
      }),
      ticket({
        ticket: "sample-2",
        title: "（見本）検索の応答を速くする",
        human_review: { required: true, reason: "見本" },
      }),
      ticket({ ticket: "sample-2-01", parent: "sample-2", phase: 1, title: "（見本）遅いクエリを調べる", copy: { status: "closed", approved_at: AT }, completed_at: AT, risk: { points: 0, level: "LOW" } }),
      ticket({ ticket: "sample-2-02", parent: "sample-2", phase: 2, title: "（見本）索引の張り方を決める" }),
    ],
    parents: [SAMPLE_PARENT],
  };
  return buildBoard(json);
}

/** プロジェクト管理画面の見本の行。`projectsRel` は今の置き場（`projects` など） */
export function sampleProjectRow(projectsRel: string): ProjectRow {
  const rel = `${projectsRel}/sample-app`;
  return {
    name: "sample-app",
    root: "",
    rel,
    rulesRel: `${rel}/.ccnavi/config/rules.yml`,
    rulesExists: true,
    hasClaudeDir: true,
    origin: "https://gitlab.example.com/group/sample-app.git",
    originKey: "",
    worktrees: [],
    tickets: 2,
    doing: 1,
    problems: [],
  };
}
