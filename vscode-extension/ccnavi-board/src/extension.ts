import * as vscode from "vscode";

import { approveFromPalette, openBoard, refreshBoard } from "./board-panel.js";
import { openRules } from "./rules-panel.js";
import { registerSidebar } from "./sidebar.js";

export function activate(context: vscode.ExtensionContext): void {
  registerSidebar(context);
  context.subscriptions.push(
    vscode.commands.registerCommand("ccnaviBoard.open", () => void openBoard()),
    vscode.commands.registerCommand("ccnaviBoard.refresh", refreshBoard),
    vscode.commands.registerCommand("ccnaviBoard.approve", approveFromPalette),
    vscode.commands.registerCommand("ccnaviBoard.openRules", () => void openRules()),
  );
}

export function deactivate(): void {
  // 後始末は context.subscriptions とパネルの onDidDispose が担う
}
