/**
 * サイドパネルのツリーの中身。見出しは「ユーザ待ち」「進行中の親」「画面」の3つ。VS CodeのAPIに触れないので単体で試せる。
 *
 * 件数と分類は実行ファイルの答え（`--explain --json`の`inbox`）をそのまま並べ、ここでは分け直さない。
 * 0件の種類は行を出さない。バッジの数は、出したユーザ待ちの件数の合計（取り込み待ちはツリーごとの数の和）。
 * 片付けはリモートのMRを見ないと分からないので数えず、押すとチケット管理画面の「MRの状況を確認」を始める行だけを出す。
 * レビュー待ちの行も、押すまではリモートを見ない（押すとチケット管理画面の「レビュー完了」と同じく指摘を取りに行く）。
 *
 * チケット制御が無効なら、チケットに関わる行（承認待ち・レビュー待ち・注意・フローの下書き・片付け・進行中の親、
 * 画面のうちチケット管理・リスク管理・フェーズ管理・テンプレート管理）を出さない（REQ-VSC-04）。取り込み待ちは残す。
 */
import { buildBoard } from "./board.js";
import { foldPath, keyLabel, treeKey } from "./handoffmodel.js";
import type { BoardJson } from "./model.js";
import type { BoardTarget } from "./screens.js";

/** 行を押したときに開くもの */
export type SidebarAction =
  | { readonly kind: "board"; readonly target?: BoardTarget }
  | { readonly kind: "handoff"; readonly tree: string }
  | { readonly kind: "flow"; readonly ticket: string }
  /** 画面の入口。コマンドを呼ぶ */
  | { readonly kind: "command"; readonly command: string };

export interface SidebarNode {
  /** ツリーの中で一意。VS Codeが開閉を覚えるキーになるので、同じものには同じ値を付ける */
  readonly id: string;
  readonly label: string;
  /** 名前の横に薄く出す（件数・状態） */
  readonly description: string;
  /** マウスを重ねたときに出す説明。無ければ空 */
  readonly tooltip: string;
  /** ThemeIconの名前。無ければ空 */
  readonly icon: string;
  readonly action?: SidebarAction;
  readonly children: readonly SidebarNode[];
  /** 子を持つ行を、最初に開いて見せるか */
  readonly expanded: boolean;
}

export interface SidebarTree {
  readonly nodes: readonly SidebarNode[];
  /** ユーザ待ちの合計。0ならバッジを出さない */
  readonly badge: number;
}

/** いまの答え。`board`が無ければ、まだ読んでいない（`error`も無い）か、読めなかった */
export interface SidebarInput {
  readonly board?: BoardJson;
  readonly error?: string;
  readonly tickets: boolean;
}

interface Entry {
  readonly label: string;
  readonly description: string;
  readonly command: string;
  readonly icon: string;
  /** チケット制御が有効なときだけ出す */
  readonly needsTickets: boolean;
}

/** 画面の入口。並びは使う頻度の順。毎日開くチケット管理を先頭に、設定の画面をその後に、取り込み待ちを最後に置く */
export const ENTRIES: readonly Entry[] = [
  {
    label: "チケット管理",
    description: "チケットのカンバン表示、承認とレビューの進行",
    command: "ccnaviBoard.open",
    icon: "checklist",
    needsTickets: true,
  },
  {
    label: "プロジェクト管理",
    description: "projects/の一覧、clone・fetch・pullの実行",
    command: "ccnaviBoard.openProjects",
    icon: "repo",
    needsTickets: false,
  },
  {
    label: "ルール管理",
    description: "ルールの編集と保存、判定の試行、hookの確認",
    command: "ccnaviBoard.openRules",
    icon: "shield",
    needsTickets: false,
  },
  {
    label: "リスク管理",
    description: "実際の変更内容で評価するリスクの配点（閾値と項目）の編集と保存",
    command: "ccnaviBoard.openRisk",
    icon: "pulse",
    needsTickets: true,
  },
  {
    label: "フェーズ管理",
    description: "フェーズ定義（計画で使うフェーズの種類、編集できる範囲の上限、レビューの既定）の編集と保存",
    command: "ccnaviBoard.openPhases",
    icon: "milestone",
    needsTickets: true,
  },
  {
    label: "テンプレート管理",
    description: "作業・レビュー計画のテンプレートと下書きの一覧と、テンプレートの編集",
    command: "ccnaviBoard.openTemplates",
    icon: "files",
    needsTickets: true,
  },
  {
    label: "取り込み待ち",
    description: "wip/handoff/に置いた下書き（テンプレート・設定・docs）の差分を読んで取り込む（ワークスペースのツリーを開く）",
    command: "ccnaviBoard.openHandoff",
    icon: "inbox",
    needsTickets: false,
  },
];

function node(id: string, label: string, rest: Partial<Omit<SidebarNode, "id" | "label">> = {}): SidebarNode {
  return { id, label, description: "", tooltip: "", icon: "", children: [], expanded: true, ...rest };
}

/** 見出しの下の種類1つ。子が無ければ出さない */
function kind(id: string, label: string, icon: string, count: number, children: readonly SidebarNode[], tooltip: string): SidebarNode[] {
  return children.length === 0 ? [] : [node(id, label, { description: String(count), icon, children, tooltip })];
}

function screensNode(tickets: boolean): SidebarNode {
  const entries = ENTRIES.filter((entry) => tickets || !entry.needsTickets);
  return node("screens", "画面", {
    expanded: false,
    children: entries.map((entry) =>
      node(`screen:${entry.command}`, entry.label, { tooltip: entry.description, icon: entry.icon, action: { kind: "command", command: entry.command } }),
    ),
  });
}

function waitingNode(board: BoardJson, tickets: boolean): { readonly node: SidebarNode; readonly count: number } {
  const titles = new Map<string, string>();
  for (const t of board.tickets) {
    if (!titles.has(t.ticket)) {
      titles.set(t.ticket, t.title);
    }
  }
  const named = (id: string): string => (titles.get(id) ? `${id} ${titles.get(id)}` : id);
  const isParent = new Set(board.tickets.filter((t) => t.parent === "").map((t) => t.ticket));
  const issuesOf = new Map(board.tickets.map((t) => [t.ticket, t.issues]));
  const inbox = board.inbox;

  const approval = tickets ? inbox.approval : [];
  const review = tickets ? inbox.review : [];
  const notice = tickets ? inbox.notice : [];
  const flowDraft = tickets ? inbox.flow_draft : [];
  const handoffCount = inbox.handoff.reduce((sum, h) => sum + h.count, 0);

  const phaseName = (parent: string, phase: number): string => {
    const label = board.parents.find((p) => p.ticket === parent)?.phases.find((p) => p.number === phase)?.label ?? "";
    return label === "" ? `フェーズ${phase}` : `フェーズ${label}`;
  };
  const treeOf = (root: string): string | undefined => {
    const found = board.trees.find((t) => foldPath(t.root) === foldPath(root));
    return found === undefined ? undefined : treeKey(found);
  };

  const children: SidebarNode[] = [
    ...kind(
      "approval",
      "承認待ち",
      "verified",
      approval.length,
      approval.map((id) =>
        node(`approval:${id}`, named(id), {
          description: isParent.has(id) ? "親" : "子",
          tooltip: "押すと、このチケットの承認の画面を開きます",
          action: { kind: "board", target: { kind: "approve", ticket: id } },
        }),
      ),
      "承認するチケット（計画の提案を含む）",
    ),
    ...kind(
      "review",
      "レビュー待ち",
      "comment-discussion",
      review.length,
      review.map((r) =>
        node(`review:${r.parent}:${r.phase}`, `${r.parent} ${phaseName(r.parent, r.phase)}`, {
          description: titles.get(r.parent) ?? "",
          tooltip: "押すと、このフェーズのレビュー完了の画面を開きます（MRの残った指摘を取りに行きます）",
          action: { kind: "board", target: { kind: "decide", parent: r.parent, phase: r.phase } },
        }),
      ),
      "MRでのレビューを待っているフェーズ",
    ),
    ...kind(
      "notice",
      "注意",
      "warning",
      notice.length,
      notice.map((id) =>
        node(`notice:${id}`, named(id), {
          description: issuesOf.get(id)?.[0] ?? "",
          tooltip: [...(issuesOf.get(id) ?? []), "押すと、チケット管理でこのカードを選びます"].join("\n"),
          action: { kind: "board", target: { kind: "select", ticket: id } },
        }),
      ),
      "承認待ち・レビュー待ちのほかに、ユーザの確認が要るチケット",
    ),
    ...kind(
      "handoff",
      "取り込み待ち",
      "inbox",
      handoffCount,
      inbox.handoff.map((h) => {
        const key = treeOf(h.tree);
        return node(`handoff:${h.tree}`, key === undefined ? h.tree : keyLabel(key), {
          description: `${h.count}件`,
          tooltip: key === undefined ? "" : "押すと、このツリーの取り込み待ち画面を開きます",
          ...(key === undefined ? {} : { action: { kind: "handoff", tree: key } as const }),
        });
      }),
      "wip/handoff/に置かれた、取り込める下書き",
    ),
    ...kind(
      "flow-draft",
      "フローの下書き",
      "edit",
      flowDraft.length,
      flowDraft.map((id) =>
        node(`flow-draft:${id}`, named(id), {
          tooltip: "押すと、この子のフロー編集画面を開きます（下書きの差分を読んで取り込めます）",
          action: { kind: "flow", ticket: id },
        }),
      ),
      "エージェントが書いた、いまのフローと違う下書き",
    ),
    ...(tickets
      ? [
          node("retire", "片付け", {
            description: "MRの状況を確認",
            icon: "git-merge",
            tooltip: "押すと、チケット管理で「MRの状況を確認」を始めます（リモートのMRを見に行きます）。結果はチケット管理に出ます",
            action: { kind: "board", target: { kind: "mergeCheck" } },
          }),
        ]
      : []),
  ];
  const count = approval.length + review.length + notice.length + handoffCount + flowDraft.length;
  return {
    count,
    node: node("waiting", "ユーザ待ち", {
      description: String(count),
      children: children.length === 0 ? [node("waiting:none", "ありません")] : children,
    }),
  };
}

function progressNode(board: BoardJson): SidebarNode {
  const parents = buildBoard(board)
    .columns.filter((column) => column.state === "doing")
    .flatMap((column) => column.cards.filter((card) => card.isParent));
  return node("progress", "進行中の親", {
    description: String(parents.length),
    children:
      parents.length === 0
        ? [node("progress:none", "ありません")]
        : parents.map((card) =>
            node(`progress:${card.project}:${card.id}`, card.title === "" ? card.id : `${card.id} ${card.title}`, {
              description: card.stage === "" ? card.status.label : card.stage,
              tooltip: "押すと、チケット管理でこのカードを選びます",
              action: { kind: "board", target: { kind: "select", ticket: card.id } },
            }),
          ),
  });
}

/** ツリー全体。答えがまだ無い・読めないときは、ユーザ待ちの見出しの下にそう書く */
export function sidebarTree(input: SidebarInput): SidebarTree {
  const screens = screensNode(input.tickets);
  if (input.board === undefined) {
    const label = input.error === undefined ? "読み込み中…" : "読み込めません";
    return {
      badge: 0,
      nodes: [node("waiting", "ユーザ待ち", { children: [node("waiting:state", label, { tooltip: input.error ?? "" })] }), screens],
    };
  }
  const waiting = waitingNode(input.board, input.tickets);
  return {
    badge: waiting.count,
    nodes: input.tickets ? [waiting.node, progressNode(input.board), screens] : [waiting.node, screens],
  };
}
