/**
 * 実行ファイルの JSON を、列とカードを持つボードに組み立てる。VS Code の API には依存しない。
 *
 * 列は 未着手 / 作業中 / 完了 / 取り消し。置き場（ADR-0055）との対応は、未着手 = `wip/proposals/todo/`、
 * 作業中 = `.ccnavi/approved/doing/` と `wip/proposals/review/`（レビュー待ちも作業中の列。待ちはカードの属性で言う）、
 * 完了 = `.ccnavi/approved/done/`、取り消し = 同じ `done/` で `cancelled_at` を持つもの。承認済みチケット・マーカー・
 * レビュー待ち・ワークツリーはカードのバッジで出す。止まっているかや承認待ちの判断はここでやり直さない。JSON が
 * 言ったことを並べるだけで、判定と同じ答えを 2 か所で出さない。
 */
import type {
  BoardJson,
  CopyStatus,
  ParentJson,
  PhaseJson,
  ProposalState,
  ProposalJson,
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

/**
 * 人が押せる操作。承認と受け入れは実行ファイルか端末へ、レビュー済みの連絡は Claude Code に渡す文を組む
 * （判定は動かさない。`check` を打つのはその文を受けたエージェント）。
 */
export type Action =
  | { readonly kind: "approve" }
  | { readonly kind: "accept"; readonly parent: string; readonly phase: number }
  | { readonly kind: "reviewed"; readonly parent: string; readonly phase: number };

export interface PhaseChip {
  readonly parent: string;
  readonly number: number;
  readonly label: string;
  readonly state: PhaseJson["state"];
  readonly marks: readonly string[];
  readonly gateClosed: boolean;
  /** 依頼を出したのに止まったまま（人のレビュー待ち）。JSON の `review_waiting` の写し */
  readonly reviewWaiting: boolean;
  readonly reviewRequired: boolean;
  /** 実績のリスクの水準（LOW / MEDIUM / HIGH / CRITICAL）。測っていなければ空 */
  readonly riskLevel: string;
  readonly riskLine: string;
  readonly tickets: readonly string[];
  /** 依頼のマーカーが持つマージリクエストの URL（依頼の投稿を指す）。無ければ空 */
  readonly mrUrl: string;
  /** 依頼のマーカーが持つマージリクエストの番号。無ければ null */
  readonly mrNumber: number | null;
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
  /** 提案の置き場（todo / review）。承認済みチケットの側にあれば null */
  readonly proposalState: ProposalJson["state"] | null;
  readonly proposalTree: string;
  /** 絞り込みの単位。親なら自分、子なら親の識別子 */
  readonly family: string;
  /** カードを選んだときに開くファイル。提案があれば提案、無ければ承認済みチケット */
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
  /** 取り消した時刻。空でなければ取り消しの列 */
  readonly cancelledAt: string;
  readonly cancelReason: string;
  readonly riskLevel: string;
  readonly riskPoints: number | null;
  readonly seenIn: readonly SeenInJson[];
  /** どれが本物か決まらない写りの全部。決まっていれば空。判定と同じ答えを実行ファイルが出す */
  readonly scattered: readonly SeenInJson[];
  /** 子なら自分のフェーズのマーカー、親なら空 */
  readonly marks: readonly string[];
  readonly gateClosed: boolean;
  /** 子なら自分のフェーズが人のレビュー待ちか、親なら false */
  readonly reviewWaiting: boolean;
  readonly pendingApproval: boolean;
  /** 親だけ */
  readonly stage: string;
  readonly wrapped: boolean;
  readonly ready: boolean;
  readonly phases: readonly PhaseChip[];
  readonly actions: readonly Action[];
  /** 読み手が気づくべき食い違い */
  readonly issues: readonly string[];
  /**
   * 空でなければ、そのワークツリーへの書き込みが全部止まっている理由（ADR-0058）。
   * `copyStatus` は `open` のままなので、列や承認済みの札からは分からない。
   */
  readonly blocked: string;
  /** 親だけ。フェーズの依頼のマーカーから引いたマージリクエストの URL（依頼の投稿ではなくマージリクエスト自体）。無ければ空 */
  readonly mrUrl: string;
  readonly mrNumber: number | null;
  /**
   * 人が動く必要があるか。「要対応だけ」の絞り込みが見る。条件は、承認待ち（`pending_approval`。新規の未承認と
   * 親の改版。札の「未承認」は承認済みチケットの有無なので、改版を落とし取り消しを拾う。ここは承認待ちで見る）、
   * レビュー準備中／レビュー待ち、未着手・作業中なのにワークツリーが無い、HIGH 以上、本物が決まらない写り、不備、
   * 親ならフェーズ行の要約に出るもの（レビュー準備中／レビュー待ち・HIGH 以上）
   */
  readonly attention: boolean;
}

export interface BoardColumn extends ColumnDef {
  readonly cards: readonly Card[];
  readonly count: number;
}

/** 親の絞り込みの候補。識別子と題名 */
export interface ParentOption {
  readonly id: string;
  readonly title: string;
}

export interface Board {
  readonly columns: readonly BoardColumn[];
  readonly projects: readonly string[];
  /** 親の絞り込みの候補。識別子順 */
  readonly parents: readonly ParentOption[];
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
    parents: cards.filter((card) => card.isParent).map((card) => ({ id: card.id, title: card.title })),
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
  // 止まっていることは不備として挙げる。札は一目で分かる短い言葉しか出せないので、
  // 理由の全文はここに置く（`attention` もこれで立つ）。
  if (t.blocked !== "") {
    issues.push(`書き込みが止まっている: ${t.blocked}`);
  }
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
  const mr = isParent ? mrOf(phases) : { url: "", number: null };

  const actions: Action[] = [];
  if (pending.has(t.ticket)) {
    actions.push({ kind: "approve" });
  }
  const wrapped = ownParent?.wrapup !== null && ownParent?.wrapup !== undefined;
  const riskLevel = typeof t.risk?.level === "string" ? t.risk.level : "";
  const gateClosed = !isParent && (ownPhase?.gate_closed ?? false);
  const reviewWaiting = !isParent && (ownPhase?.review_waiting ?? false);
  const attention =
    pending.has(t.ticket) ||
    gateClosed ||
    (!t.worktree.exists && (column === "todo" || column === "doing")) ||
    reviewWaiting ||
    isHighRisk(riskLevel) ||
    t.scattered.length > 0 ||
    issues.length > 0 ||
    phases.some((p) => p.gateClosed || p.reviewWaiting || isHighRisk(p.riskLevel));

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
    family: isParent ? t.ticket : t.parent,
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
    cancelledAt: t.cancelled_at,
    cancelReason: t.cancel_reason,
    riskLevel,
    riskPoints: typeof t.risk?.points === "number" ? t.risk.points : null,
    seenIn: t.seen_in,
    scattered: t.scattered,
    marks: isParent ? [] : marks,
    gateClosed,
    reviewWaiting,
    pendingApproval: pending.has(t.ticket),
    stage: ownParent && isParent ? ownParent.stage : "",
    wrapped: isParent && wrapped,
    ready: isParent && ownParent?.ready !== null && ownParent?.ready !== undefined,
    phases,
    actions,
    issues,
    blocked: t.blocked,
    mrUrl: mr.url,
    mrNumber: mr.number,
    attention,
  };
}

function isHighRisk(level: string): boolean {
  return level === "HIGH" || level === "CRITICAL";
}

/**
 * 親カードに出すマージリクエスト。依頼のマーカーの URL は依頼の投稿（`#issuecomment-…`）を指すので、
 * 断片を落としてマージリクエスト自体にする。マージリクエストは親ブランチに 1 本なので、
 * 番号の大きいフェーズの依頼を採る（同じ番号のはず。違えば新しいほうが本物）。
 */
function mrOf(phases: readonly PhaseChip[]): { url: string; number: number | null } {
  for (let i = phases.length - 1; i >= 0; i -= 1) {
    const p = phases[i];
    if (p.mrUrl !== "") {
      return { url: p.mrUrl.replace(/#.*$/, ""), number: p.mrNumber };
    }
  }
  return { url: "", number: null };
}

/**
 * 列は置き場から引く。提案の側にあれば `todo/` は未着手、`review/` は作業中（レビュー待ちは属性で言う）。
 * 無ければ承認済みチケットから。閉じた承認済みチケット（`done/`）は取り消しの時刻があれば cancelled、無ければ done。
 * 開いている承認済みチケット（`doing/`）は doing。どちらにも無いのは食い違いなので、todo に置いたうえで不備として言う。
 */
function columnOf(t: TicketJson, issues: string[]): ProposalState {
  if (t.proposal !== null) {
    return t.proposal.state === "review" ? "doing" : "todo";
  }
  if (t.copy.status === "closed") {
    return t.cancelled_at !== "" ? "cancelled" : "done";
  }
  if (t.copy.status === "open") {
    return "doing";
  }
  issues.push("提案が見つからない（承認済みチケットだけがある）");
  return "todo";
}

function toChip(parent: ParentJson, p: PhaseJson): PhaseChip {
  const marks = Object.keys(p.marks).sort();
  const actions: Action[] = [];
  // 受け入れて進めるのは、人のレビュー待ち（依頼を出したのに止まったまま）のとき。待ちかどうかは
  // 判定が `review_waiting` で言う。子カードの札・フェーズ行の「レビュー依頼済」・受け入れの操作はみな
  // それを読み、止まっているかとマーカーからここで組み直さない。
  if (p.review_waiting) {
    actions.push({ kind: "accept", parent: parent.ticket, phase: p.number });
    actions.push({ kind: "reviewed", parent: parent.ticket, phase: p.number });
  }
  // 依頼のマーカー `{head, mr, url, host, since}`（設計 §9.10）。URL は依頼の投稿を指す。中身を解釈せず写すだけ。
  // 依頼のマーカーは mr と url を必ず一緒に持ち、リンクは url があるときだけ出すので、他のマーカーの mr は読まない
  const requested = p.marks.requested ?? {};
  return {
    parent: parent.ticket,
    number: p.number,
    label: p.label,
    state: p.state,
    marks,
    gateClosed: p.gate_closed,
    reviewWaiting: p.review_waiting,
    reviewRequired: p.review_required,
    riskLevel: typeof p.risk?.level === "string" ? p.risk.level : "",
    riskLine: p.risk_line,
    tickets: p.tickets,
    mrUrl: typeof requested.url === "string" ? requested.url : "",
    mrNumber: typeof requested.mr === "number" ? requested.mr : null,
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

/** 親の識別子から、その親のワークツリーのパス。承認の sh はそこで打つ */
export function parentTreeOf(board: Board, parent: string): string | undefined {
  const card = parentCardOf(board, parent);
  return card !== undefined && card.worktreeExists ? card.worktreePath : undefined;
}

/** 親の識別子から、その親のカード。無ければ undefined */
export function parentCardOf(board: Board, parent: string): Card | undefined {
  for (const column of board.columns) {
    for (const card of column.cards) {
      if (card.id === parent && card.isParent) {
        return card;
      }
    }
  }
  return undefined;
}

/** 親の識別子とフェーズの番号から、そのフェーズ行。無ければ undefined */
export function phaseChipOf(board: Board, parent: string, phase: number): PhaseChip | undefined {
  return parentCardOf(board, parent)?.phases.find((p) => p.number === phase);
}
