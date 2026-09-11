import * as vscode from "vscode";

import { approveFromPalette, openBoard, refreshBoard } from "./board-panel.js";

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("ccnaviBoard.open", () => void openBoard()),
    vscode.commands.registerCommand("ccnaviBoard.refresh", refreshBoard),
    vscode.commands.registerCommand("ccnaviBoard.approve", approveFromPalette),
  );
}

export function deactivate(): void {
  // 後始末は context.subscriptions とパネルの onDidDispose が担う
}
