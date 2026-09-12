import * as vscode from "vscode";

import { openBoard, refreshBoard } from "./board-panel.js";
import { openProjects } from "./projects-panel.js";
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
    // 承認はボードのボタンだけ。パレットからは打てない（承認内容を見ずに押せる入口を作らない）。
    vscode.commands.registerCommand("ccnaviBoard.openRules", (project?: unknown) =>
      void openRules(typeof project === "string" && project !== "" ? { kind: "project", name: project } : { kind: "workspace" }),
    ),
    vscode.commands.registerCommand("ccnaviBoard.openProjects", () => void openProjects()),
  );
}

export function deactivate(): void {
  // 後始末は context.subscriptions とパネルの onDidDispose が担う
}
