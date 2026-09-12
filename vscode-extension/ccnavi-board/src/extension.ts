import * as vscode from "vscode";

import { approveFromPalette, openBoard, refreshBoard } from "./board-panel.js";
import { openProjects } from "./projects-panel.js";
import { openRisk } from "./risk-panel.js";
import { openRules } from "./rules-panel.js";
import { registerSidebar } from "./sidebar.js";
import { watchTicketControl } from "./ticket-control.js";

export function activate(context: vscode.ExtensionContext): void {
  // サイドパネルより先に読む。入口の並びがこの値で決まる。
  watchTicketControl(context);
  registerSidebar(context);
  context.subscriptions.push(
    // 引数はプロジェクト管理画面からの導線でだけ渡る。パレットとサイドパネルからは無い。
    vscode.commands.registerCommand("ccnaviBoard.open", (project?: unknown) =>
      void openBoard(typeof project === "string" ? project : undefined),
    ),
    vscode.commands.registerCommand("ccnaviBoard.refresh", refreshBoard),
    vscode.commands.registerCommand("ccnaviBoard.approve", approveFromPalette),
    vscode.commands.registerCommand("ccnaviBoard.openRules", (project?: unknown) =>
      void openRules(typeof project === "string" && project !== "" ? { kind: "project", name: project } : { kind: "workspace" }),
    ),
    vscode.commands.registerCommand("ccnaviBoard.openProjects", () => void openProjects()),
    vscode.commands.registerCommand("ccnaviBoard.openRisk", () => void openRisk()),
  );
}

export function deactivate(): void {
  // 後始末は context.subscriptions とパネルの onDidDispose が担う
}
