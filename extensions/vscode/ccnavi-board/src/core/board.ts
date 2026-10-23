/**
 * 実行ファイルの JSON を、列とカードを持つボードに組み立てる。VS Code の API には依存しない。
 *
 * 列は 未着手 / 作業中 / 完了 / 取り消し / アーカイブ。置き場との対応は、未着手 = `wip/proposals/todo/`、
 * 作業中 = `.ccnavi/approved/doing/` と `wip/proposals/review/`（レビュー待ちも作業中の列。待ちはカードの札で言う）、
 * 完了 = `.ccnavi/approved/done/`、取り消し = 同じ `done/` で `cancelled_at` を持つもの、アーカイブ = 手元の退避
 * （`logs/archive/`。JSON の `archived` の欄。既定では画面が隠す）。カードの状況は状態の札（`status.ts`）、
 * ユーザができること（承認・レビュー完了）は操作（`actions`）で言う。この 2 つは別の規則で決める。
 * 止まっているかや承認待ちの判断はここでやり直さない。JSON が言ったことを並べるだけで、判定と同じ答えを 2 か所で出さない。
 */
import type {
  ArchivedTicketJson,
  BoardJson,
  CopyStatus,
  FlowJson,
  ParentJson,
  PredecessorUnmetJson,
  PhaseJson,
  ProposalState,
  ProposalJson,
  RemoteJson,
  SeenInJson,
  TicketJson,
} from "./model.js";
import { phaseStatusOf, statusOf, type PhaseStatus, type Status } from "./status.js";

export interface ColumnDef {
  readonly state: ProposalState;
  readonly label: string;
}

/** 列の順序。該当が 0 件でも省かない */
export const COLUMNS: readonly ColumnDef[] = [
  { state: "todo", label: "未着手" },
  { state: "doing", label: "作業中" },
  { state: "done", label: "完了" },
  { state: "cancelled", label: "取り消し" },
  { state: "archived", label: "アーカイブ" },
];

/**
 * ユーザが押せる操作。承認は実行ファイルへ、レビュー完了は「残った指摘の読み取り」（`ccnavi-review.sh decide <N> --preview`）へ
 * 進む（判定は動かさない。置くのは実行ファイル）。画面は［承認］と、［MR を開く］［レビュー完了］の組を出す。
 */
export type Action =
  | { readonly kind: "approve" }
  /**
   * レビュー完了。札（`status`）やフェーズ行の状態の一言が「レビュー待ち」のときだけ付く。
   * 押すと残った指摘を読み、0 件なら「レビュー済みにする」、1 件以上なら対応方針の画面になる
   */
  | { readonly kind: "review"; readonly parent: string; readonly phase: number; readonly mrUrl: string };

export interface PhaseChip {
  readonly parent: string;
  readonly number: number;
  readonly label: string;
  readonly state: PhaseJson["state"];
  /** フェーズ行の状態の一言（`core/status.ts` の `phaseStatusOf`）。JSON の欄から決めた言葉で、画面はこれを出す */
  readonly status: PhaseStatus;
  readonly gateClosed: boolean;
  /** 依頼を出したのに止まったまま（ユーザのレビュー待ち）。JSON の `review_waiting` をそのまま持つ */
  readonly reviewWaiting: boolean;
  readonly reviewRequired: boolean;
  /** 実績のリスクの水準（LOW / MEDIUM / HIGH / CRITICAL）。測っていなければ空 */
  readonly riskLevel: string;
  readonly riskLine: string;
  /** 手で動かした子の確認待ち・移動が未共有など（判定の 1 行）。無ければ空 */
  readonly manual: string;
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
  readonly startedAt: string;
  readonly completedAt: string;
  /** 取り消した時刻。空でなければ取り消しの列 */
  readonly cancelledAt: string;
  readonly cancelReason: string;
  readonly riskLevel: string;
  readonly riskPoints: number | null;
  readonly seenIn: readonly SeenInJson[];
  /** どれが正か決まらないチケットの全部。決まっていれば空。判定と同じ答えを実行ファイルが出す */
  readonly scattered: readonly SeenInJson[];
  readonly gateClosed: boolean;
  /** 子なら自分のフェーズがユーザのレビュー待ちか、親なら false */
  readonly reviewWaiting: boolean;
  readonly pendingApproval: boolean;
  /** 親だけ。承認済みの親に改版の提案が出ているか（承認待ちで、承認済みチケットも在る） */
  readonly revision: boolean;
  /** 子だけ。自分のフェーズの名前（`1（調査）`）。引けなければ空 */
  readonly phaseLabel: string;
  /** 子だけ。自分のフェーズの状態の一言。フェーズが引けなければ空 */
  readonly phaseStatus: PhaseStatus | "";
  /** 状態の札（`core/status.ts` の `statusOf`）。見出しの右端に 1 つ出す */
  readonly status: Status;
  /** 親だけ */
  readonly stage: string;
  readonly wrapped: boolean;
  readonly ready: boolean;
  readonly phases: readonly PhaseChip[];
  readonly actions: readonly Action[];
  /** 読み手が気づくべき食い違い。実行ファイルの `issues` をそのまま持つ */
  readonly issues: readonly string[];
  /**
   * 空でなければ、そのワークツリーへの書き込みが全部止まっている理由（親が引けないなど、範囲をどこで切り詰めるか決まらない）。
   * `copyStatus` は `open` のままなので、列や承認済みのバッジからは分からない。
   */
  readonly blocked: string;
  /**
   * 親ならフェーズの依頼のマーカーから、子なら自分のフェーズの依頼のマーカーから引いたマージリクエストの URL
   * （依頼の投稿ではなくマージリクエスト自体）。無ければ空
   */
  readonly mrUrl: string;
  readonly mrNumber: number | null;
  /** 親だけ。元になった issue の番号（親チケットの `issue:`）。無ければ null */
  readonly issueNumber: number | null;
  /**
   * 親だけ。親を載せる MR の番号（親チケットの `mr:`）。フェーズの依頼のマーカーから引く `mrNumber` とは別の値
   */
  readonly parentMr: number | null;
  /** 親だけ。親のブランチ名（ワークツリーが今いるブランチを先に、無ければ親チケットの値）。子は空 */
  readonly branch: string;
  /** 親だけ。親のワークツリーのリモートとの差。ワークツリーが無い・子・古い実行ファイルは null */
  readonly remote: RemoteJson | null;
  /**
   * ユーザが動く必要があるか。「要対応のみ」の絞り込みが見る。実行ファイルの `attention` をそのまま持ち、
   * 条件はここで組み直さない（条件は ccnavi の docs/requirements/json-shapes.md「ボードの JSON」）
   */
  readonly attention: boolean;
  /**
   * 子のフロー。親は null。在るか・着手中で書けないかは実行ファイルの答えをそのまま持ち、
   * カードの「フロー」ボタンの言葉だけに使う。ユーザが動く必要（`attention`）には数えない
   */
  readonly flow: FlowJson | null;
  /**
   * 親の提案のカードの「計画編集」（計画の順序を直すタブの入口）を出すか。計画を持つ（実行ファイルの
   * `proposal.has_plan`）承認待ち（`todo/`）の親の提案だけ真。新規の親も改版も。承認済みだけの親と子は偽
   */
  readonly workflow: boolean;
  /** 親のカードの「下書きを作る」（完了した計画付きの親から、テンプレートの下書きを作る）を出すか。実行ファイルの `template_draft` のまま */
  readonly templateDraft: boolean;
  /**
   * 満たしていない先行。空でなければ、承認も着手も止まる。実行ファイルの答えをそのまま持ち、
   * カードの「先行待ち」のバッジに使う
   */
  readonly predecessorsUnmet: readonly PredecessorUnmetJson[];
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
  /** 退避した親（アーカイブの列の親）。アーカイブを表示しているときだけ、親の絞り込みの候補に足す */
  readonly archivedParents: readonly ParentOption[];
  readonly problems: readonly string[];
  readonly pendingApproval: readonly string[];
  /** 置き場のカードの数 */
  readonly totalCount: number;
  readonly remainingCount: number;
  readonly issueCount: number;
  /** アーカイブのカードの数 */
  readonly archivedCount: number;
  readonly generatedAt: string;
  readonly root: string;
  /** プロジェクトの置き場（設定の `projects`）。マージ状況の確認が、プロジェクトのリポジトリを cwd にするのに使う */
  readonly projectsDir: string;
}

const REMAINING: readonly ProposalState[] = ["todo", "doing"];

export function buildBoard(json: BoardJson): Board {
  const parents = new Map<string, ParentJson>(json.parents.map((p) => [p.ticket, p]));
  const pending = new Set(json.pending_approval);
  const live = json.tickets.map((t) => toCard(t, parents, pending));
  // 退避のカードは置き場のカードと識別子が重ならないものだけ（重なれば置き場の側が正）
  // 鍵はプロジェクトと識別子。ワークスペースとプロジェクトで同じ識別子を使っていても取り違えない
  const liveKeys = new Set(json.tickets.map((t) => `${t.project}\u0000${t.ticket}`));
  const archived = (json.archived ?? []).filter((a) => !liveKeys.has(`${a.project}\u0000${a.ticket}`)).map(toArchivedCard);
  const cards = [...live, ...archived];
  cards.sort(compareCards);

  const columns: BoardColumn[] = COLUMNS.map((column) => {
    const own = cards.filter((card) => card.column === column.state);
    return { ...column, cards: own, count: own.length };
  });
  return {
    columns,
    projects: json.projects,
    parents: cards.filter((card) => card.isParent && card.column !== "archived").map((card) => ({ id: card.id, title: card.title })),
    archivedParents: cards.filter((card) => card.isParent && card.column === "archived").map((card) => ({ id: card.id, title: card.title })),
    problems: json.problems,
    pendingApproval: json.pending_approval,
    // 集計は置き場のカードだけ。アーカイブは既定で隠すので、数に入れると見えないものを数えることになる
    totalCount: live.length,
    remainingCount: live.filter((card) => REMAINING.includes(card.column)).length,
    issueCount: live.filter((card) => card.issues.length > 0).length,
    archivedCount: archived.length,
    generatedAt: json.generated_at,
    root: json.root,
    projectsDir: json.settings.projects,
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
  pending: ReadonlySet<string>,
): Card {
  const isParent = t.parent === "";
  const column = columnOf(t);

  const ownParent = parents.get(isParent ? t.ticket : t.parent);
  const ownPhase =
    ownParent && t.phase !== null
      ? ownParent.phases.find((p) => p.number === t.phase)
      : undefined;
  const phases = isParent && ownParent ? ownParent.phases.map((p) => toChip(ownParent, p)) : [];
  // 親は最後に依頼を出したフェーズのマージリクエスト、子は自分のフェーズの依頼のマージリクエスト
  const own = ownPhase ? requestOf(ownPhase) : { url: "", number: null };
  const mr = isParent ? mrOf(phases) : { url: mrPage(own.url), number: own.number };

  const wrapped = ownParent?.close_early !== null && ownParent?.close_early !== undefined;
  const riskLevel = typeof t.risk?.level === "string" ? t.risk.level : "";
  const gateClosed = !isParent && (ownPhase?.gate_closed ?? false);
  const reviewWaiting = !isParent && (ownPhase?.review_waiting ?? false);
  const pendingApproval = pending.has(t.ticket);

  const base: Omit<Card, "status" | "actions"> = {
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
    startedAt: t.started_at,
    completedAt: t.completed_at,
    cancelledAt: t.cancelled_at,
    cancelReason: t.cancel_reason,
    riskLevel,
    riskPoints: typeof t.risk?.points === "number" ? t.risk.points : null,
    seenIn: t.seen_in,
    scattered: t.scattered,
    gateClosed,
    reviewWaiting,
    pendingApproval,
    revision: isParent && pendingApproval && t.copy.status !== "none",
    phaseLabel: !isParent && ownPhase ? ownPhase.label : "",
    phaseStatus: !isParent && ownPhase && ownParent ? phaseStatusOf(ownPhase, ownParent.phases) : "",
    stage: ownParent && isParent ? ownParent.stage : "",
    wrapped: isParent && wrapped,
    ready: isParent && ownParent?.ready !== null && ownParent?.ready !== undefined,
    phases,
    issues: t.issues,
    blocked: t.blocked,
    mrUrl: mr.url,
    mrNumber: mr.number,
    issueNumber: isParent ? t.issue : null,
    parentMr: isParent ? t.mr : null,
    branch: isParent ? (t.remote?.branch || t.branch) : "",
    remote: isParent ? t.remote : null,
    attention: t.attention,
    flow: isParent ? null : flowOf(t.flow, column),
    workflow: isParent && t.proposal?.state === "todo" && t.proposal.has_plan === true,
    templateDraft: isParent && t.template_draft,
    predecessorsUnmet: t.predecessors_unmet,
  };
  return withStatus(base);
}

/**
 * 状態の札と、ユーザの番の操作を付ける。操作は札と別の規則で決める。
 * 承認は `pendingApproval` だけを条件にする（札が「要確認」でも出す）。レビュー完了は札が「レビュー待ち」の子だけ
 * （承認待ちの子には出さない）。親のレビュー完了は各フェーズ行が持つ
 */
function withStatus(base: Omit<Card, "status" | "actions">): Card {
  const status = statusOf(base);
  const actions: Action[] = [];
  if (base.pendingApproval) {
    actions.push({ kind: "approve" });
  } else if (!base.isParent && status.label === "レビュー待ち" && base.phase !== null) {
    actions.push({ kind: "review", parent: base.parent, phase: base.phase, mrUrl: mrPage(base.mrUrl) });
  }
  return { ...base, status, actions };
}

/**
 * 手元の退避にある閉じたチケットのカード。表示するだけで、操作も要対応も持たない。
 * 選ぶと退避したファイルを開く（`isKnownPath` は列のカードの `openPath` を見るので、そのまま開ける）
 */
function toArchivedCard(a: ArchivedTicketJson): Card {
  const isParent = a.parent === "";
  return withStatus({
    id: a.ticket,
    title: a.title,
    parent: a.parent,
    phase: a.phase,
    project: a.project,
    isParent,
    column: "archived",
    proposalState: null,
    proposalTree: "",
    family: isParent ? a.ticket : a.parent,
    openPath: a.path,
    copyStatus: "archived",
    approvedAt: "",
    reviewRequired: false,
    reviewReason: "",
    worktreeExists: false,
    worktreePath: "",
    startedAt: a.started_at,
    completedAt: a.completed_at,
    cancelledAt: a.cancelled_at,
    cancelReason: a.cancel_reason,
    riskLevel: "",
    riskPoints: null,
    seenIn: [],
    scattered: [],
    gateClosed: false,
    reviewWaiting: false,
    pendingApproval: false,
    revision: false,
    phaseLabel: "",
    phaseStatus: "",
    stage: "",
    wrapped: false,
    ready: false,
    phases: [],
    issues: [],
    blocked: "",
    mrUrl: "",
    mrNumber: null,
    issueNumber: null,
    parentMr: null,
    branch: "",
    remote: null,
    attention: false,
    flow: null,
    workflow: false,
    templateDraft: false,
    predecessorsUnmet: [],
  });
}

/**
 * カードに載せる子のフロー。閉じた子（完了・取り消し）でファイルが無ければ null（作る先が無いので「作成」を出さない）。
 * 実行ファイルもこの子には `flow` を null で返すが、古い実行ファイルの答えでもボタンを出さないための念押し。
 * ファイルが在れば閉じた子でも残す（中身を見られる）
 */
function flowOf(flow: FlowJson | null, column: ProposalState): FlowJson | null {
  if (flow === null) {
    return null;
  }
  return (column === "done" || column === "cancelled") && !flow.exists ? null : flow;
}

/** 依頼のマーカーの URL は依頼の投稿（`#note-…`）を指すので、断片を除いてマージリクエスト自体にする */
export function mrPage(url: string): string {
  return url.replace(/#.*$/, "");
}

/**
 * フェーズの依頼のマーカー `{head, mr, url, host, since}`。URL は依頼の投稿を指す。中身を解釈せずそのまま渡すだけ。
 * 依頼のマーカーは mr と url を必ず一緒に持ち、リンクは url があるときだけ出すので、他のマーカーの mr は読まない
 */
function requestOf(p: PhaseJson): { url: string; number: number | null } {
  const requested = p.marks.requested ?? {};
  return {
    url: typeof requested.url === "string" ? requested.url : "",
    number: typeof requested.mr === "number" ? requested.mr : null,
  };
}

/**
 * 親カードに出すマージリクエスト。依頼のマーカーの URL は依頼の投稿（`#issuecomment-…`）を指すので、
 * 断片を除いてマージリクエスト自体にする。マージリクエストは親ブランチに 1 本なので、
 * 番号の大きいフェーズの依頼を採る（同じ番号のはず。違えば新しいほうを採る）。
 */
function mrOf(phases: readonly PhaseChip[]): { url: string; number: number | null } {
  for (let i = phases.length - 1; i >= 0; i -= 1) {
    const p = phases[i];
    if (p.mrUrl !== "") {
      return { url: mrPage(p.mrUrl), number: p.mrNumber };
    }
  }
  return { url: "", number: null };
}

/**
 * 列は置き場から引く。提案の側にあれば `todo/` は未着手、`review/` は作業中（レビュー待ちは札で言う）。
 * 無ければ承認済みチケットから。閉じた承認済みチケット（`done/`）は取り消しの時刻があれば cancelled、無ければ done。
 * 開いている承認済みチケット（`doing/`）は doing。どちらにも無いのは食い違いなので todo に置く（不備の文は実行ファイルの `issues`）。
 */
function columnOf(t: TicketJson): ProposalState {
  if (t.proposal !== null) {
    return t.proposal.state === "review" ? "doing" : "todo";
  }
  if (t.copy.status === "closed") {
    return t.cancelled_at !== "" ? "cancelled" : "done";
  }
  if (t.copy.status === "open") {
    return "doing";
  }
  return "todo";
}

/**
 * フェーズ行。状態の一言は JSON の欄から `phaseStatusOf` が決める。レビュー完了の操作は、一言が「レビュー待ち」の行だけに付く
 * （待ちかどうかは判定が `gate_closed` と `review_waiting` で言う。ここでマーカーから組み直さない）。
 */
function toChip(parent: ParentJson, p: PhaseJson): PhaseChip {
  const status = phaseStatusOf(p, parent.phases);
  const request = requestOf(p);
  const actions: Action[] = [];
  if (status === "レビュー待ち") {
    actions.push({ kind: "review", parent: parent.ticket, phase: p.number, mrUrl: mrPage(request.url) });
  }
  return {
    parent: parent.ticket,
    number: p.number,
    label: p.label,
    state: p.state,
    status,
    gateClosed: p.gate_closed,
    reviewWaiting: p.review_waiting,
    reviewRequired: p.review_required,
    riskLevel: typeof p.risk?.level === "string" ? p.risk.level : "",
    riskLine: p.risk_line,
    manual: p.manual,
    tickets: p.tickets,
    mrUrl: request.url,
    mrNumber: request.number,
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

/** 子の識別子から、フローを持つ子のカード。親・無い識別子・フローの欄が無い子は undefined */
export function flowCardOf(board: Board, ticket: string): Card | undefined {
  for (const column of board.columns) {
    for (const card of column.cards) {
      if (card.id === ticket && !card.isParent && card.flow !== null) {
        return card;
      }
    }
  }
  return undefined;
}

/** 承認待ち（`todo/`）の提案がある親の識別子（新規と改版） */
export function todoParentsOf(board: Board): string[] {
  return board.columns.flatMap((column) => column.cards.filter((card) => card.isParent && card.proposalState === "todo").map((card) => card.id));
}

/** 親の識別子から、「計画編集」を出しているカード（計画を持つ承認待ちの親の提案）。無ければ undefined */
export function workflowCardOf(board: Board, ticket: string): Card | undefined {
  const card = parentCardOf(board, ticket);
  return card?.workflow === true ? card : undefined;
}

/** 親の識別子から、「下書きを作る」を出しているカード（完了した計画付きの親）。無ければ undefined */
export function templateDraftCardOf(board: Board, ticket: string): Card | undefined {
  const card = parentCardOf(board, ticket);
  return card?.templateDraft === true ? card : undefined;
}

/** 親の識別子とフェーズの番号から、そのフェーズ行。無ければ undefined */
export function phaseChipOf(board: Board, parent: string, phase: number): PhaseChip | undefined {
  return parentCardOf(board, parent)?.phases.find((p) => p.number === phase);
}

/**
 * ボードのアーカイブにある親の題。プロジェクトと識別子が一致するものだけを引く（別プロジェクトの同じ識別子を取り違えない）。
 * 無ければ undefined（呼び手は識別子だけを出す）。マージ状況の帯が、片付けの記録（題を持たない）の親に題を添えるのに使う
 */
export function archivedTitleOf(board: Board, project: string, parent: string): string | undefined {
  const column = board.columns.find((c) => c.state === "archived");
  const card = column?.cards.find((c) => c.isParent && c.id === parent && c.project === project);
  return card === undefined || card.title === "" ? undefined : card.title;
}
