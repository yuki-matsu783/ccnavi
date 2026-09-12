/**
 * アクティビティバーの ccnavi から開くサイドパネル。入口は 3 つで、どれも Webview パネルを開く。
 * 「チケット管理」はチケット制御（CCNAVI_TICKET_CONTROL）が disable のプロジェクトでは出さない。
 * 全体ルールだけを使うプロジェクトに、開いても空のボードしか出ない入口を見せないため。
 * VS Code の API に触れるので単体テストの対象外。
 */
import * as vscode from "vscode";

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
    label: "ルール管理",
    description: "ルールの編集と保存、判定の試行、hook の確認",
    command: "ccnaviBoard.openRules",
    icon: "shield",
    needsTickets: false,
  },
  {
    label: "チケット管理",
    description: "チケットをカンバンで見て、承認とレビューを進める",
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
    item.description = entry.description;
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
  context.subscriptions.push(
    vscode.window.registerTreeDataProvider("ccnaviBoard.entries", provider),
  );
  onDidChangeTicketControl(() => provider.refresh());
}
