/**
 * アクティビティバーの ccnavi から開くサイドパネル。並ぶのは画面の入口 5 つで、どれも Webview パネルを開く。
 * 「チケット管理」「リスク管理」「フェーズ管理」は、チケット制御（CCNAVI_TICKET_CONTROL）が
 * disable のワークスペースでは出さない。配点は子チケットを閉じるときに、フェーズの種類は親の
 * 計画と子の範囲にしか読まれないので、disable の間はどちらも何も動かさない。効かない設定の
 * 入口を残すと、直したのに効いていない、という読み違いの元になる。
 *
 * 見た目の切り替えは並びに混ぜず、パネルのタイトルバーの歯車に置く（`package.json` の `view/title`）。
 * 押すと画面が開く行と、押すと設定が変わる行が 1 列に並ぶと、押す前に何が起きるか読めない。
 * 今どれを選んでいるかは、行の代わりにビューの見出しの横（`view.description`）に出す。
 * VS Code の API に触れるので単体テストの対象外。
 */
import * as vscode from "vscode";

import { onDidChangeAppearance, readAppearance } from "./appearance.js";
import { APPEARANCE_LABELS } from "./core/appearance.js";
import { onDidChangeTicketControl, ticketControl } from "./ticket-control.js";

interface Entry {
  readonly label: string;
  readonly description: string;
  readonly command: string;
  readonly icon: string;
  /** チケット制御が効いているときだけ出す */
  readonly needsTickets: boolean;
}

const ENTRIES: readonly Entry[] = [
  {
    label: "プロジェクト管理",
    description: "projects/ の一覧、clone・fetch・pull の実行",
    command: "ccnaviBoard.openProjects",
    icon: "repo",
    needsTickets: false,
  },
  {
    label: "ルール設定",
    description: "ルールの編集と保存、判定の試行、hook の確認",
    command: "ccnaviBoard.openRules",
    icon: "shield",
    needsTickets: false,
  },
  {
    label: "リスク管理",
    description: "実績で測るリスクの配点（境目の点と項目）の編集と保存",
    command: "ccnaviBoard.openRisk",
    icon: "pulse",
    needsTickets: true,
  },
  {
    label: "フェーズ管理",
    description: "フェーズの種類（計画に並べる型、範囲の上限、レビューの既定）の編集と保存",
    command: "ccnaviBoard.openPhases",
    icon: "milestone",
    needsTickets: true,
  },
  {
    label: "チケット管理",
    description: "チケットをカンバンで見て、承認とレビューを進めます",
    command: "ccnaviBoard.open",
    icon: "checklist",
    needsTickets: true,
  },
];

class EntryProvider implements vscode.TreeDataProvider<Entry> {
  private readonly changed = new vscode.EventEmitter<Entry | undefined>();
  readonly onDidChangeTreeData = this.changed.event;

  refresh(): void {
    this.changed.fire(undefined);
  }

  getTreeItem(entry: Entry): vscode.TreeItem {
    const item = new vscode.TreeItem(entry.label, vscode.TreeItemCollapsibleState.None);
    // 並びは名前だけにして、説明はマウスを重ねたときに出す（横に並べると狭いパネルで切れて読めない）
    item.tooltip = entry.description;
    item.iconPath = new vscode.ThemeIcon(entry.icon);
    item.command = { command: entry.command, title: entry.label };
    return item;
  }

  getChildren(): Entry[] {
    const enabled = ticketControl() === "enable";
    return ENTRIES.filter((entry) => enabled || !entry.needsTickets);
  }
}

export function registerSidebar(context: vscode.ExtensionContext): void {
  const provider = new EntryProvider();
  // 見出しの横に今の見た目を出すので、provider を登録するだけでなくビューを持つ
  const view = vscode.window.createTreeView("ccnaviBoard.entries", { treeDataProvider: provider });
  view.description = APPEARANCE_LABELS[readAppearance()];
  context.subscriptions.push(view);
  onDidChangeTicketControl(() => provider.refresh());
  context.subscriptions.push(
    onDidChangeAppearance((appearance) => {
      view.description = APPEARANCE_LABELS[appearance];
    }),
  );
}
