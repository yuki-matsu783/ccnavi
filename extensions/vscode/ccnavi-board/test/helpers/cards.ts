/**
 * カードの組み立てを、見本（`test/fixtures/board.json`）に頼らず、欄を一つずつ指定して試すための部品。
 * 実行ファイルの JSON と同じ形の 1 件（`TicketJson` / `PhaseJson` / `ParentJson`）を、既定の欄で埋めて作る。
 * 見本の殻（設定・レイヤー・統合結果）だけは見本を借りる。
 */
import type { BoardJson, ParentJson, PhaseJson, TicketJson } from "../../src/core/model.js";
import { fixture } from "./fixture.js";

/** 親でも子でもない状態の、順調な作業中のチケット。必要な欄だけ上書きする */
export function ticketJson(ticket: string, over: Partial<TicketJson> = {}): TicketJson {
  return {
    ticket,
    parent: "",
    phase: null,
    title: `タイトル ${ticket}`,
    project: "",
    issue: null,
    mr: null,
    branch: "",
    remote: null,
    predecessors: [],
    predecessors_unmet: [],
    human_review: { required: false, reason: "" },
    proposal: null,
    template_draft: false,
    blocked: "",
    copy: { status: "open", approved_at: "2026-10-01T00:00:00+0900", approved_from: "commit", path: `/ws/.ccnavi/approved/doing/${ticket}.md` },
    worktree: { exists: true, path: `/ws/.claude/worktrees/${ticket}`, project: "" },
    started_at: "2026-10-01T01:00:00+0900",
    completed_at: "",
    base_sha: "",
    merged_sha: "",
    cancelled_at: "",
    cancel_reason: "",
    in_progress: true,
    seen_in: [],
    scattered: [],
    risk: null,
    judge: null,
    flow: null,
    issues: [],
    attention: false,
    ...over,
  };
}

/** 閉じた（完了）チケット */
export function closedJson(ticket: string, over: Partial<TicketJson> = {}): TicketJson {
  return ticketJson(ticket, {
    copy: { status: "closed", approved_at: "2026-10-01T00:00:00+0900", approved_from: "commit", path: `/ws/.ccnavi/approved/done/${ticket}.md` },
    completed_at: "2026-10-02T00:00:00+0900",
    in_progress: false,
    ...over,
  });
}

/** 承認待ち（提案だけ）のチケット */
export function proposalJson(ticket: string, over: Partial<TicketJson> = {}): TicketJson {
  return ticketJson(ticket, {
    proposal: { state: "todo", tree: ticket, tree_root: `/ws/.claude/worktrees/${ticket}`, path: `/ws/wip/proposals/todo/${ticket}.md`, has_plan: false },
    copy: { status: "none" },
    started_at: "",
    in_progress: false,
    ...over,
  });
}

/** レビュー待ちの置き場（`wip/proposals/review/`）にある子 */
export function reviewJson(ticket: string, over: Partial<TicketJson> = {}): TicketJson {
  return ticketJson(ticket, {
    proposal: { state: "review", tree: ticket, tree_root: `/ws/.claude/worktrees/${ticket}`, path: `/ws/wip/proposals/review/${ticket}.md`, has_plan: false },
    copy: { status: "review", approved_at: "2026-10-01T00:00:00+0900", approved_from: "commit", path: `/ws/.ccnavi/approved/doing/${ticket}.md` },
    completed_at: "2026-10-02T00:00:00+0900",
    in_progress: false,
    ...over,
  });
}

export function phaseJson(number: number, over: Partial<PhaseJson> = {}): PhaseJson {
  return {
    number,
    type: "implement",
    title: `フェーズ${number}`,
    label: `${number}（フェーズ${number}）`,
    state: "active",
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
    manual: "",
    ...over,
  };
}

/** 依頼を出したのに止まったまま（ユーザのレビュー待ち）のフェーズ */
export function waitingPhase(number: number, mr = 12, url = `https://example.com/o/r/pull/${mr}#note-1`, over: Partial<PhaseJson> = {}): PhaseJson {
  return phaseJson(number, {
    state: "ended",
    review_required: true,
    gate_closed: true,
    review_waiting: true,
    marks: { pending: {}, requested: { head: "abc", mr, url, host: "example", since: "t" } },
    ...over,
  });
}

export function parentJson(ticket: string, phases: readonly PhaseJson[], over: Partial<ParentJson> = {}): ParentJson {
  return { ticket, closed: false, stage: "作業中", plan: [], feedback: null, close_early: null, ready: null, phases, ...over };
}

/** 見本の殻（設定・レイヤー・統合結果）に、指定したチケットと親だけを入れたボード */
export function boardOf(tickets: readonly TicketJson[], parents: readonly ParentJson[] = [], extra: Partial<BoardJson> = {}): BoardJson {
  const base = fixture();
  return { ...base, tickets, parents, pending_approval: [], archived: [], problems: [], projects: [], ...extra };
}
