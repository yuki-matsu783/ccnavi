/**
 * アクティビティバーのccnaviから開くサイドパネル。見出しは「ユーザ待ち」「進行中の親」「画面」の3つ（中身は`core/sidebar-tree.ts`）。
 *
 * - ユーザ待ちは、実行ファイルの答えの`inbox`を種類ごとの件数と識別子の行で並べ、合計をビューのバッジ（`TreeView.badge`）に出す。
 *   行を押すと該当の画面と場所を開く（承認待ち → 承認のオーバーレイ、レビュー待ち → そのフェーズのレビュー完了、
 *   注意 → チケット管理画面でそのカード、取り込み待ち → そのツリーの取り込み待ち画面、フローの下書き → その子のフロー編集、
 *   片付け → 「MRの状況を確認」）。片付けとレビュー待ちは、押すまでリモートを見ない
 * - 進行中の親は、押すとチケット管理画面でそのカードを選ぶ
 * - 画面は、画面の入口7つ（取り込み待ちを含む）。どれもWebviewパネルを開く
 *
 * 答えはチケット管理画面と同じ1回の`--explain --json`（`board-feed.ts`）で、チケット管理画面を閉じていても監視が変化を見つけたら読み直す。
 * チケット制御（CCNAVI_TICKET_CONTROL）がdisableのワークスペースでは、チケットに関わる行を出さない。配点は子チケットを
 * クローズするときに、フェーズ定義は親の計画と子の範囲にしか読まれないので、disableの間はどちらも何も動かさない。
 * 使われない設定の入口を残すと、直したのに反映されていない、という読み違いの元になる。
 *
 * 見た目の切り替えとバグ報告は一覧に混ぜず、パネルのタイトルバーのアイコン（配色・虫）に置く（`package.json`の`view/title`）。
 * 押すと画面が開く行と、押すと設定が変わる行が1列に並ぶと、押す前に何が起きるか読めない。
 * 今どれを選んでいるかは、行の代わりにビューの見出しの横（`view.description`）に出す。
 * VS CodeのAPIに触れるので単体テストの対象外（行の中身は`core/sidebar-tree.ts`で試す）。
 */
import * as vscode from "vscode";

import { onDidChangeAppearance, readAppearance } from "./appearance.js";
import { boardFeed } from "./board-feed.js";
import { APPEARANCE_LABELS } from "./core/appearance.js";
import { screens } from "./core/screens.js";
import { sidebarTree, type SidebarAction, type SidebarNode } from "./core/sidebar-tree.js";
import { assertNever } from "./panel-common.js";
import { onDidChangeTicketControl, ticketControl } from "./ticket-control.js";

/** 行を押したときのコマンド。行から呼ぶだけなので、コマンドパレットには出さない（package.jsonに載せない） */
const OPEN_COMMAND = "ccnaviBoard.sidebarOpen";

class TreeProvider implements vscode.TreeDataProvider<SidebarNode> {
  private readonly changed = new vscode.EventEmitter<SidebarNode | undefined>();
  readonly onDidChangeTreeData = this.changed.event;
  private roots: readonly SidebarNode[] = [];

  set(roots: readonly SidebarNode[]): void {
    this.roots = roots;
    this.changed.fire(undefined);
  }

  getTreeItem(entry: SidebarNode): vscode.TreeItem {
    const state =
      entry.children.length === 0
        ? vscode.TreeItemCollapsibleState.None
        : entry.expanded
          ? vscode.TreeItemCollapsibleState.Expanded
          : vscode.TreeItemCollapsibleState.Collapsed;
    const item = new vscode.TreeItem(entry.label, state);
    item.id = entry.id;
    item.description = entry.description;
    // 説明はマウスを重ねたときに出す（横に並べると狭いパネルで切れて読めない）
    if (entry.tooltip !== "") {
      item.tooltip = entry.tooltip;
    }
    if (entry.icon !== "") {
      item.iconPath = new vscode.ThemeIcon(entry.icon);
    }
    if (entry.action !== undefined) {
      item.command = { command: OPEN_COMMAND, title: entry.label, arguments: [entry.action] };
    }
    return item;
  }

  getChildren(entry?: SidebarNode): SidebarNode[] {
    return [...(entry === undefined ? this.roots : entry.children)];
  }
}

function open(action: SidebarAction): void {
  switch (action.kind) {
    case "board":
      void screens().board(action.target);
      return;
    case "handoff":
      void screens().handoff(action.tree);
      return;
    case "flow":
      void screens().flow(action.ticket);
      return;
    case "command":
      void vscode.commands.executeCommand(action.command);
      return;
    default:
      assertNever(action);
      return;
  }
}

export function registerSidebar(context: vscode.ExtensionContext): void {
  const provider = new TreeProvider();
  // バッジと見出しの横の見た目を出すので、providerを登録するだけでなくビューを持つ
  const view = vscode.window.createTreeView("ccnaviBoard.entries", { treeDataProvider: provider });
  view.description = APPEARANCE_LABELS[readAppearance()];
  const draw = (): void => {
    const latest = boardFeed.latest();
    const tree = sidebarTree({
      ...(latest?.ok === true ? { board: latest.board } : {}),
      ...(latest?.ok === false ? { error: latest.error } : {}),
      tickets: ticketControl() === "enable",
    });
    provider.set(tree.nodes);
    view.badge = tree.badge > 0 ? { value: tree.badge, tooltip: `ユーザ待ち${tree.badge}件` } : undefined;
  };
  draw();
  context.subscriptions.push(
    view,
    vscode.commands.registerCommand(OPEN_COMMAND, (action: SidebarAction) => open(action)),
    { dispose: boardFeed.subscribe(draw) },
    onDidChangeAppearance((appearance) => {
      view.description = APPEARANCE_LABELS[appearance];
    }),
  );
  onDidChangeTicketControl(draw);
}
