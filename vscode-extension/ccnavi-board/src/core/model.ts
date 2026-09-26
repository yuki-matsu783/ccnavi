/**
 * `ccnavi --explain --json` が出す形。実行ファイルと拡張の契約で、拡張はこれ以外を読まない。
 * 形の定義は ccnavi の README「ボードの JSON」。同じ例が test/fixtures/board.json にあり、
 * Python 側のテスト（tests/ticket/test_board.py）が同じ例で形を確かめる。
 */

/** 拡張が読める版。実行ファイルが違う版を出したら、解釈せずに版の違いを伝える */
export const BOARD_VERSION = 1;

/**
 * ボードの列。置き場は 4 つ（`wip/proposals/{todo,review}/`、`.ccnavi/approved/{doing,done}/`、ADR-0055）だが、
 * 列は 未着手（`todo/`）/ 作業中（`approved/doing/` と `review/`）/ 完了（`approved/done/`）/ 取り消し
 * （`approved/done/` で `cancelled_at` を持つ）の 4 つ。レビュー待ちは列ではなくカードの属性で分かる
 */
export type ProposalState = "todo" | "doing" | "done" | "cancelled";
/**
 * 承認済みチケットの今。`open` は `.ccnavi/approved/doing/`、`review` は `wip/proposals/review/`（承認済みのまま
 * 人のレビューを待つ）、`closed` は `.ccnavi/approved/done/`（取り消しも `cancelled_at` を持ってここ）、`none` は
 * 承認待ちの提案だけ
 */
export type CopyStatus = "open" | "review" | "closed" | "none";
export type PhaseState = "planned" | "active" | "ended";
export type TreeKind = "main" | "project" | "worktree";

export interface TreeJson {
  readonly name: string;
  readonly root: string;
  readonly project: string;
  readonly kind: TreeKind;
}

export interface ProposalJson {
  /** 提案の置き場。`todo`（承認待ち）か `review`（レビュー待ち）だけ */
  readonly state: "todo" | "review";
  readonly tree: string;
  readonly tree_root: string;
  readonly path: string;
}

export interface CopyJson {
  readonly status: CopyStatus;
  readonly approved_at?: string;
  readonly source_tree?: string;
  readonly path?: string;
}

export interface WorktreeJson {
  readonly exists: boolean;
  readonly path: string;
  readonly project?: string;
}

export interface SeenInJson {
  readonly tree: string;
  readonly state: string;
  readonly path: string;
}

/**
 * 子チケットのフロー（設計 9.3.1、ADR-0085）。親は null。
 * `locked` は判定がいまそのファイルへの書き込みを `DENY_TICKET_FLOW_LOCKED` で止めているか（着手中）。
 * 拡張は写すだけで、`started_at` などから組み直さない（ADR-0035）。
 */
export interface FlowJson {
  /** 読む先の絶対パス（権威のツリーの版、無ければ子のワークツリーの版。どちらにも無ければ権威のツリーの側の綴り） */
  readonly path: string;
  /** ツリーのルートからの相対。承認済みの領域の固定の置き場（既定 `.ccnavi/approved/flows/<子>.yml`） */
  readonly rel: string;
  /** ファイルを持つツリーのルート。保存はここからファイルまでの途中にリンクがあれば書かない */
  readonly tree: string;
  readonly exists: boolean;
  /** ファイルか、ツリーのルートからそこまでの途中がシンボリックリンク（実行ファイルの答え） */
  readonly linked: boolean;
  readonly locked: boolean;
}

export interface TicketJson {
  readonly ticket: string;
  readonly parent: string;
  readonly phase: number | null;
  readonly title: string;
  readonly project: string;
  readonly issue: number | null;
  readonly predecessors: readonly string[];
  readonly human_review: { readonly required: boolean; readonly reason: string };
  readonly proposal: ProposalJson | null;
  /**
   * 空でなければ「読めるが信じられない」理由（ADR-0058）。判定はこのチケットの
   * ワークツリーへの書き込みを `DENY_TICKET_BLOCKED` で全部止める。`copy.status` は
   * `open` のままなので、止まっていることはこの欄でしか分からない。
   */
  readonly blocked: string;
  readonly copy: CopyJson;
  readonly worktree: WorktreeJson;
  readonly started_at: string;
  readonly completed_at: string;
  readonly base_sha: string;
  readonly cancelled_at: string;
  readonly cancel_reason: string;
  readonly seen_in: readonly SeenInJson[];
  /** どれが本物か決まらない写りの全部。決まっていれば空 */
  readonly scattered: readonly SeenInJson[];
  readonly risk: Record<string, unknown> | null;
  readonly judge: Record<string, unknown> | null;
  /** 子のフロー。親と、この欄を出さない古い実行ファイルでは null */
  readonly flow: FlowJson | null;
}

export interface PhaseJson {
  readonly number: number;
  readonly type: string;
  readonly title: string;
  readonly label: string;
  readonly state: PhaseState;
  readonly tickets: readonly string[];
  readonly states: Readonly<Record<string, string>>;
  readonly marks: Readonly<Record<string, Record<string, unknown>>>;
  readonly review_required: boolean;
  readonly gate_closed: boolean;
  /** 依頼を出したのに止まったまま（人のレビュー待ち）。判定が出した値で、拡張は組み直さない */
  readonly review_waiting: boolean;
  readonly deferred: boolean;
  readonly review_at: number | null;
  readonly covers: readonly number[];
  readonly risk: Record<string, unknown> | null;
  readonly risk_escalates: boolean;
  readonly risk_line: string;
}

export interface ParentJson {
  readonly ticket: string;
  readonly closed: boolean;
  readonly stage: string;
  readonly plan: readonly unknown[];
  readonly feedback: readonly unknown[] | null;
  readonly close_early: Record<string, unknown> | null;
  readonly ready: Record<string, unknown> | null;
  readonly accepted_threads: readonly string[];
  readonly phases: readonly PhaseJson[];
}

/** 設定ファイル 1 本の場所 */
export interface LayerFileJson {
  /** 実行ファイルが解いたパス。ファイルが無くても本来の場所を指す */
  readonly path: string;
  /** 読めなかった理由。空なら読めた（無いファイルも空として読めた扱い） */
  readonly unreadable: string;
}

/**
 * 層（layer）1 つ（設計 11.2。共通・ワークスペース・プロジェクトの設定のどれか）。拡張が使うのはルールとフェーズの種類のファイルの場所だけなので、それだけを読む。
 * 宣言の中身と risk は読まない（リスク管理画面はワークスペースとプロジェクトの設定に追従していない、設計 11.11）。
 */
export interface LayerJson {
  /** `common` / `self` / プロジェクトの名前 */
  readonly name: string;
  readonly rules: LayerFileJson;
  /** フェーズの種類のファイル（`phases_file`）。共通の設定は `.ccnavi/common/phases.yml` 固定 */
  readonly phasesFile: LayerFileJson;
}

export interface BoardJson {
  readonly version: number;
  readonly root: string;
  readonly generated_at: string;
  readonly settings: {
    /** チケット制御を使うか。実行ファイルが解決した値（enable / disable） */
    readonly ticket_control: string;
    readonly tickets: string;
    readonly approved: string;
    readonly projects: string;
  };
  readonly trees: readonly TreeJson[];
  /** 並びは 共通の設定 → ワークスペースの設定 → プロジェクトの設定（名前順） */
  readonly layers: readonly LayerJson[];
  readonly projects: readonly string[];
  readonly problems: readonly string[];
  readonly pending_approval: readonly string[];
  readonly tickets: readonly TicketJson[];
  readonly parents: readonly ParentJson[];
}

export type ParseResult =
  | { readonly ok: true; readonly board: BoardJson }
  | { readonly ok: false; readonly error: string };

/**
 * 実行ファイルの標準出力を読む。欠けている項目は既定値で埋め、版が違えば読まない。
 * 1 件の欠けで全体を捨てないのは、参考にした拡張の「1 枚の不備で他を止めない」に倣う。
 */
export function parseBoardJson(text: string): ParseResult {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    return { ok: false, error: `JSON として読めません: ${(error as Error).message}` };
  }
  if (!isRecord(raw)) {
    return { ok: false, error: "JSON の最上位がオブジェクトではありません" };
  }
  const version = typeof raw.version === "number" ? raw.version : NaN;
  if (version !== BOARD_VERSION) {
    return {
      ok: false,
      error: `ccnavi --explain --json の版が違います（拡張は ${BOARD_VERSION}、実行ファイルは ${String(raw.version)}）`,
    };
  }
  const settings = isRecord(raw.settings) ? raw.settings : {};
  return {
    ok: true,
    board: {
      version,
      root: str(raw.root),
      generated_at: str(raw.generated_at),
      settings: {
        ticket_control: str(settings.ticket_control),
        tickets: str(settings.tickets),
        approved: str(settings.approved),
        projects: str(settings.projects),
      },
      trees: list(raw.trees).filter(isRecord).map(tree),
      layers: list(raw.layers).filter(isRecord).map(layer),
      projects: list(raw.projects).map(str),
      problems: list(raw.problems).map(str),
      pending_approval: list(raw.pending_approval).map(str),
      tickets: list(raw.tickets).filter(isRecord).map(ticket),
      parents: list(raw.parents).filter(isRecord).map(parent),
    },
  };
}

function tree(raw: Record<string, unknown>): TreeJson {
  const kind = str(raw.kind);
  return {
    name: str(raw.name),
    root: str(raw.root),
    project: str(raw.project),
    kind: kind === "project" || kind === "worktree" ? kind : "main",
  };
}

function layer(raw: Record<string, unknown>): LayerJson {
  const file = (value: unknown): LayerFileJson => {
    const record = isRecord(value) ? value : {};
    return { path: str(record.path), unreadable: str(record.unreadable) };
  };
  return { name: str(raw.name), rules: file(raw.rules), phasesFile: file(raw.phases_file) };
}

function ticket(raw: Record<string, unknown>): TicketJson {
  const review = isRecord(raw.human_review) ? raw.human_review : {};
  const copy = isRecord(raw.copy) ? raw.copy : {};
  const worktree = isRecord(raw.worktree) ? raw.worktree : {};
  const status = str(copy.status);
  return {
    ticket: str(raw.ticket),
    parent: str(raw.parent),
    phase: num(raw.phase),
    title: str(raw.title),
    project: str(raw.project),
    issue: num(raw.issue),
    predecessors: list(raw.predecessors).map(str),
    human_review: { required: raw !== undefined && review.required === true, reason: str(review.reason) },
    proposal: isRecord(raw.proposal) ? proposal(raw.proposal) : null,
    blocked: str(raw.blocked),
    copy: {
      status: status === "open" || status === "review" || status === "closed" ? status : "none",
      approved_at: str(copy.approved_at),
      source_tree: str(copy.source_tree),
      path: str(copy.path),
    },
    worktree: {
      exists: worktree.exists === true,
      path: str(worktree.path),
      project: str(worktree.project),
    },
    started_at: str(raw.started_at),
    completed_at: str(raw.completed_at),
    base_sha: str(raw.base_sha),
    cancelled_at: str(raw.cancelled_at),
    cancel_reason: str(raw.cancel_reason),
    seen_in: seenIn(raw.seen_in),
    scattered: seenIn(raw.scattered),
    risk: isRecord(raw.risk) ? raw.risk : null,
    judge: isRecord(raw.judge) ? raw.judge : null,
    flow: isRecord(raw.flow) ? flow(raw.flow) : null,
  };
}

function flow(raw: Record<string, unknown>): FlowJson | null {
  const path = str(raw.path);
  if (path === "") {
    return null;
  }
  return {
    path,
    rel: str(raw.rel),
    tree: str(raw.tree),
    exists: raw.exists === true,
    // 欄が欠けていたら書かない側にする（リンクかを確かめられない）
    linked: raw.linked !== false,
    // 欄が欠けていたら閉じる側にする（止まっているかを確かめられないので、書かせない）
    locked: raw.locked !== false,
  };
}

function seenIn(value: unknown): readonly SeenInJson[] {
  return list(value)
    .filter(isRecord)
    .map((s) => ({ tree: str(s.tree), state: str(s.state), path: str(s.path) }));
}

function proposal(raw: Record<string, unknown>): ProposalJson | null {
  const state = str(raw.state);
  if (state !== "todo" && state !== "review") {
    return null;
  }
  return { state, tree: str(raw.tree), tree_root: str(raw.tree_root), path: str(raw.path) };
}

function parent(raw: Record<string, unknown>): ParentJson {
  return {
    ticket: str(raw.ticket),
    closed: raw.closed === true,
    stage: str(raw.stage),
    plan: list(raw.plan),
    feedback: Array.isArray(raw.feedback) ? raw.feedback : null,
    close_early: isRecord(raw.close_early) ? raw.close_early : null,
    ready: isRecord(raw.ready) ? raw.ready : null,
    accepted_threads: list(raw.accepted_threads).map(str),
    phases: list(raw.phases).filter(isRecord).map(phase),
  };
}

function phase(raw: Record<string, unknown>): PhaseJson {
  const state = str(raw.state);
  const marks: Record<string, Record<string, unknown>> = {};
  if (isRecord(raw.marks)) {
    for (const [kind, value] of Object.entries(raw.marks)) {
      marks[kind] = isRecord(value) ? value : {};
    }
  }
  const states: Record<string, string> = {};
  if (isRecord(raw.states)) {
    for (const [id, value] of Object.entries(raw.states)) {
      states[id] = str(value);
    }
  }
  return {
    number: num(raw.number) ?? 0,
    type: str(raw.type),
    title: str(raw.title),
    label: str(raw.label) || String(num(raw.number) ?? ""),
    state: state === "active" || state === "ended" ? state : "planned",
    tickets: list(raw.tickets).map(str),
    states,
    marks,
    review_required: raw.review_required === true,
    gate_closed: raw.gate_closed === true,
    review_waiting: raw.review_waiting === true,
    deferred: raw.deferred === true,
    review_at: num(raw.review_at),
    covers: list(raw.covers).map((c) => num(c) ?? 0),
    risk: isRecord(raw.risk) ? raw.risk : null,
    risk_escalates: raw.risk_escalates === true,
    risk_line: str(raw.risk_line),
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function list(value: unknown): readonly unknown[] {
  return Array.isArray(value) ? value : [];
}

function str(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return "";
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
