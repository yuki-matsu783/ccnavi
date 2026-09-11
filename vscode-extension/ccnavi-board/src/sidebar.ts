/**
 * アクティビティバーの ccnavi から開くサイドパネル。入口は 2 つで、どちらも Webview パネルを開く。
 * VS Code の API に触れるので単体テストの対象外。
 */
import * as vscode from "vscode";

interface Entry {
  readonly label: string;
  readonly description: string;
  readonly command: string;
  readonly icon: string;
}

const ENTRIES: readonly Entry[] = [
  {
    label: "ルール管理",
    description: "ルールを直し、判定を試し、hook を眺める",
    command: "ccnaviBoard.openRules",
    icon: "shield",
  },
  {
    label: "チケット管理",
    description: "提案・写し・印・作業ツリーをカンバンで",
    command: "ccnaviBoard.open",
    icon: "checklist",
  },
];

class EntryProvider implements vscode.TreeDataProvider<Entry> {
  getTreeItem(entry: Entry): vscode.TreeItem {
    const item = new vscode.TreeItem(entry.label, vscode.TreeItemCollapsibleState.None);
    item.description = entry.description;
    item.tooltip = entry.description;
    item.iconPath = new vscode.ThemeIcon(entry.icon);
    item.command = { command: entry.command, title: entry.label };
    return item;
  }

  getChildren(): Entry[] {
    return [...ENTRIES];
  }
}

export function registerSidebar(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.window.registerTreeDataProvider("ccnaviBoard.entries", new EntryProvider()),
  );
}
