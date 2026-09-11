/**
 * 実行ファイルの JSON を、列とカードを持つボードに組み立てる。VS Code の API には依存しない。
 *
 * 列は提案の置き場（todo / doing / done / cancelled）。写し・印・ゲート・作業ツリーは
 * カードのバッジで出す。ゲートの開閉や承認待ちの判断はここでやり直さない。JSON が
 * 言ったことを並べるだけで、判定と同じ答えを 2 か所で出さない。
 */
import type {
  BoardJson,
  CopyStatus,
  ParentJson,
  PhaseJson,
  ProposalState,
  SeenInJson,
  TicketJson,
} from "./model.js";

export interface ColumnDef {
  readonly state: ProposalState;
  readonly label: string;
}

/** 列の並び。該当が 0 件でも落とさない */
export const COLUMNS: readonly ColumnDef[] = [
  { state: "todo", label: "未着手" },
  { state: "doing", label: "作業中" },
  { state: "done", label: "完了" },
  { state: "cancelled", label: "取り消し" },
];

/** 人が押せる操作。ターミナルへ送るコマンドの種類 */
export type Action =
  | { readonly kind: "approve" }
  | { readonly kind: "accept"; readonly parent: string; readonly phase: number }
  | { readonly kind: "wrapup"; readonly parent: string };

export interface PhaseChip {
  readonly parent: string;
  readonly number: number;
  readonly label: string;
  readonly state: PhaseJson["state"];
  readonly marks: readonly string[];
  readonly gateClosed: boolean;
  readonly reviewRequired: boolean;
  readonly riskLine: string;
  readonly tickets: readonly string[];
  readonly actions: readonly Action[];
}

export interface Card {
  readonly id: string;
  readonly title: string;
  readonly parent: string;
  readonly phase: number | null;
  readonly project: string;
  readonly isParent: boolean;
  readonly column: ProposalState;
  readonly proposalState: ProposalState | null;
  readonly proposalTree: string;
  /** カードを選んだときに開くファイル。提案があれば提案、無ければ写し */
  readonly openPath: string;
  readonly copyStatus: CopyStatus;
  readonly approvedAt: string;
  readonly reviewRequired: boolean;
  readonly reviewReason: string;
  readonly worktreeExists: boolean;
  readonly worktreePath: string;
  readonly baseSha: string;
  readonly startedAt: string;
  readonly completedAt: string;
  readonly cancelReason: string;
  readonly riskLevel: string;
  readonly riskPoints: number | null;
  readonly seenIn: readonly SeenInJson[];
  /** 子なら自分のフェーズの印、親なら空 */
  readonly marks: readonly string[];
  readonly gateClosed: boolean;
  readonly pendingApproval: boolean;
  /** 親だけ */
  readonly stage: string;
  readonly wrapped: boolean;
  readonly ready: boolean;
  readonly phases: readonly PhaseChip[];
  readonly actions: readonly Action[];
  /** 読み手が気づくべき食い違い */
  readonly issues: readonly string[];
}

export interface BoardColumn extends ColumnDef {
  readonly cards: readonly Card[];
  readonly count: number;
}

export interface Board {
  readonly columns: readonly BoardColumn[];
  readonly projects: readonly string[];
  readonly problems: readonly string[];
  readonly pendingApproval: readonly string[];
  readonly totalCount: number;
  readonly remainingCount: number;
  readonly issueCount: number;
  readonly generatedAt: string;
  readonly root: string;
}

const REMAINING: readonly ProposalState[] = ["todo", "doing"];

export function buildBoard(json: BoardJson): Board {
  const parents = new Map<string, ParentJson>(json.parents.map((p) => [p.ticket, p]));
  const ids = new Set(json.tickets.map((t) => t.ticket));
  const pending = new Set(json.pending_approval);
  const cards = json.tickets.map((t) => toCard(t, parents, ids, pending));
  cards.sort(compareCards);

  const columns: BoardColumn[] = COLUMNS.map((column) => {
    const own = cards.filter((card) => card.column === column.state);
    return { ...column, cards: own, count: own.length };
  });
  return {
    columns,
    projects: json.projects,
    problems: json.problems,
    pendingApproval: json.pending_approval,
    totalCount: cards.length,
    remainingCount: cards.filter((card) => REMAINING.includes(card.column)).length,
    issueCount: cards.filter((card) => card.issues.length > 0).length,
    generatedAt: json.generated_at,
    root: json.root,
  };
}

/** 親のあとにその子が並ぶ。親の識別子、次に自分の識別子で引く */
function compareCards(a: Card, b: Card): number {
  const familyA = a.parent || a.id;
  const familyB = b.parent || b.id;
  if (familyA !== familyB) {
    return familyA < familyB ? -1 : 1;
  }
  if (a.isParent !== b.isParent) {
    return a.isParent ? -1 : 1;
  }
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

function toCard(
  t: TicketJson,
  parents: ReadonlyMap<string, ParentJson>,
  ids: ReadonlySet<string>,
  pending: ReadonlySet<string>,
): Card {
  const issues: string[] = [];
  const isParent = t.parent === "";
  const column = columnOf(t, issues);
  if (!isParent && !ids.has(t.parent)) {
    issues.push(`親 ${t.parent} が見つからない`);
  }

  const ownParent = parents.get(isParent ? t.ticket : t.parent);
  const ownPhase =
    ownParent && t.phase !== null
      ? ownParent.phases.find((p) => p.number === t.phase)
      : undefined;
  const marks = ownPhase ? Object.keys(ownPhase.marks).sort() : [];
  const phases = isParent && ownParent ? ownParent.phases.map((p) => toChip(ownParent, p)) : [];

  const actions: Action[] = [];
  if (pending.has(t.ticket)) {
    actions.push({ kind: "approve" });
  }
  const wrapped = ownParent?.wrapup !== null && ownParent?.wrapup !== undefined;
  if (isParent && ownParent && !ownParent.closed && !wrapped && t.copy.status === "open") {
    actions.push({ kind: "wrapup", parent: t.ticket });
  }

  return {
    id: t.ticket,
    title: t.title,
    parent: t.parent,
    phase: t.phase,
    project: t.project,
    isParent,
    column,
    proposalState: t.proposal?.state ?? null,
    proposalTree: t.proposal?.tree ?? "",
    openPath: t.proposal?.path || t.copy.path || "",
    copyStatus: t.copy.status,
    approvedAt: t.copy.approved_at ?? "",
    reviewRequired: t.human_review.required,
    reviewReason: t.human_review.reason,
    worktreeExists: t.worktree.exists,
    worktreePath: t.worktree.path,
    baseSha: t.base_sha,
    startedAt: t.started_at,
    completedAt: t.completed_at,
    cancelReason: t.cancel_reason,
    riskLevel: typeof t.risk?.level === "string" ? t.risk.level : "",
    riskPoints: typeof t.risk?.points === "number" ? t.risk.points : null,
    seenIn: t.seen_in,
    marks: isParent ? [] : marks,
    gateClosed: !isParent && (ownPhase?.gate_closed ?? false),
    pendingApproval: pending.has(t.ticket),
    stage: ownParent && isParent ? ownParent.stage : "",
    wrapped: isParent && wrapped,
    ready: isParent && ownParent?.ready !== null && ownParent?.ready !== undefined,
    phases,
    actions,
    issues,
  };
}

/**
 * 列は提案の置き場。提案が無い（写しだけがある）ときは写しから推す。
 * 閉じた写しは done、取り消しの時刻があれば cancelled。開いている写しなのに提案が無いのは
 * 食い違いなので、todo に置いたうえで不備として言う。
 */
function columnOf(t: TicketJson, issues: string[]): ProposalState {
  if (t.proposal !== null) {
    return t.proposal.state;
  }
  if (t.copy.status === "closed") {
    return t.cancelled_at !== "" ? "cancelled" : "done";
  }
  issues.push("提案が見つからない（写しだけがある）");
  return "todo";
}

function toChip(parent: ParentJson, p: PhaseJson): PhaseChip {
  const marks = Object.keys(p.marks).sort();
  const actions: Action[] = [];
  // 受け入れて進めるのは、依頼を出したのにゲートが閉じたまま（未解決のスレッドが残っている）とき。
  // 依頼を出していないフェーズは、先に親が request を打つ。
  if (p.gate_closed && marks.includes("requested")) {
    actions.push({ kind: "accept", parent: parent.ticket, phase: p.number });
  }
  return {
    parent: parent.ticket,
    number: p.number,
    label: p.label,
    state: p.state,
    marks,
    gateClosed: p.gate_closed,
    reviewRequired: p.review_required,
    riskLine: p.risk_line,
    tickets: p.tickets,
    actions,
  };
}

/** Webview から届いたパスが、いま表示しているカードのどれかのものであるときだけ true */
export function isKnownPath(board: Board, filePath: string): boolean {
  if (filePath === "") {
    return false;
  }
  return board.columns.some((column) =>
    column.cards.some(
      (card) => card.openPath === filePath || card.seenIn.some((s) => s.path === filePath),
    ),
  );
}

/** 親の識別子から、その親の作業ツリーのパス。承認の sh はそこで打つ */
export function parentTreeOf(board: Board, parent: string): string | undefined {
  for (const column of board.columns) {
    for (const card of column.cards) {
      if (card.id === parent && card.isParent) {
        return card.worktreeExists ? card.worktreePath : undefined;
      }
    }
  }
  return undefined;
}
