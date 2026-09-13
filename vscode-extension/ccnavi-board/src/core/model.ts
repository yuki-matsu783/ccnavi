/**
 * `ccnavi --explain --json` が出す形。実行ファイルと拡張の契約で、拡張はこれ以外を読まない。
 * 形の定義は ccnavi の README「ボードの JSON」。同じ例が test/fixtures/board.json にあり、
 * Python 側のテスト（tests/test_board.py）が同じ例で形を確かめる。
 */

/** 拡張が読める版。実行ファイルが違う版を出したら、解釈せずに版の違いを伝える */
export const BOARD_VERSION = 1;

export type ProposalState = "todo" | "doing" | "done" | "cancelled";
export type CopyStatus = "open" | "closed" | "none";
export type PhaseState = "planned" | "active" | "ended";
export type TreeKind = "main" | "project" | "worktree";

export interface TreeJson {
  readonly name: string;
  readonly root: string;
  readonly project: string;
  readonly kind: TreeKind;
}

export interface ProposalJson {
  readonly state: ProposalState;
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
  readonly copy: CopyJson;
  readonly worktree: WorktreeJson;
  readonly started_at: string;
  readonly completed_at: string;
  readonly base_sha: string;
  readonly cancelled_at: string;
  readonly cancel_reason: string;
  readonly seen_in: readonly SeenInJson[];
  readonly risk: Record<string, unknown> | null;
  readonly judge: Record<string, unknown> | null;
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
  readonly wrapup: Record<string, unknown> | null;
  readonly ready: Record<string, unknown> | null;
  readonly accepted_threads: readonly string[];
  readonly phases: readonly PhaseJson[];
}

/** 層の設定ファイル 1 本の置き場 */
export interface LayerFileJson {
  /** 実行ファイルが解いたパス。ファイルが無くても本来の置き場を指す */
  readonly path: string;
  /** 読めなかった理由。空なら読めた（無いファイルも空として読めた扱い） */
  readonly unreadable: string;
}

/**
 * 層 1 つ（設計 §11.2）。拡張が使うのはルールとフェーズの種類のファイルの置き場だけなので、それだけを読む。
 * 宣言の中身と risk は読まない（リスク管理画面は層に追従していない、設計 §11.11）。
 */
export interface LayerJson {
  /** `common` / `self` / プロジェクトの名前 */
  readonly name: string;
  readonly rules: LayerFileJson;
  /** フェーズの種類のファイル（`phases_file`）。共通層は `CCNAVI_PHASES` の綴り */
  readonly phasesFile: LayerFileJson;
}

export interface BoardJson {
  readonly version: number;
  readonly root: string;
  readonly generated_at: string;
  readonly settings: {
    /** チケット制御を使うか。実行ファイルが解決した値（enable / disable）。古い実行ファイルは空 */
    readonly ticket_control: string;
    readonly tickets: string;
    readonly approved: string;
    readonly projects: string;
  };
  readonly trees: readonly TreeJson[];
  /** 並びは 共通層 → 自身の層 → プロジェクト（名前順）。古い実行ファイルは空 */
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
    return { ok: false, error: `JSON として読めない: ${(error as Error).message}` };
  }
  if (!isRecord(raw)) {
    return { ok: false, error: "JSON の最上位がオブジェクトではない" };
  }
  const version = typeof raw.version === "number" ? raw.version : NaN;
  if (version !== BOARD_VERSION) {
    return {
      ok: false,
      error: `ボードの版が違う（拡張は ${BOARD_VERSION}、実行ファイルは ${String(raw.version)}）`,
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
    copy: {
      status: status === "open" || status === "closed" ? status : "none",
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
    seen_in: list(raw.seen_in)
      .filter(isRecord)
      .map((s) => ({ tree: str(s.tree), state: str(s.state), path: str(s.path) })),
    risk: isRecord(raw.risk) ? raw.risk : null,
    judge: isRecord(raw.judge) ? raw.judge : null,
  };
}

function proposal(raw: Record<string, unknown>): ProposalJson | null {
  const state = str(raw.state);
  if (state !== "todo" && state !== "doing" && state !== "done" && state !== "cancelled") {
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
    wrapup: isRecord(raw.wrapup) ? raw.wrapup : null,
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
