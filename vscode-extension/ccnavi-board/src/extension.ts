import * as vscode from "vscode";

import { pickAppearance } from "./appearance.js";
import { openBoard, refreshBoard } from "./board-panel.js";
import { registerScreens, screens } from "./core/screens.js";
import { openFlow } from "./flow-panel.js";
import { openPhases } from "./phases-panel.js";
import { openProjects } from "./projects-panel.js";
import { openRisk } from "./risk-panel.js";
import { openRules } from "./rules-panel.js";
import { registerSidebar } from "./sidebar.js";
import { watchTicketControl } from "./ticket-control.js";
import { initTours } from "./tour.js";

export function activate(context: vscode.ExtensionContext): void {
  // 画面ごとの初回の案内を見たかどうかの置き場（`globalState`）
  initTours(context);
  // 画面の入口はここに集める。画面どうしは互いを import せず、この帳面を通して開き合う。
  registerScreens({
    board: openBoard,
    rules: openRules,
    risk: openRisk,
    phases: openPhases,
    projects: openProjects,
    flow: openFlow,
  });
  // サイドパネルより先に読む。入口の並びがこの値で決まる。
  watchTicketControl(context);
  registerSidebar(context);
  context.subscriptions.push(
    // 引数はプロジェクト管理画面からの導線でだけ渡る。パレットとサイドパネルからは無い。
    vscode.commands.registerCommand("ccnaviBoard.open", (project?: unknown) =>
      void screens().board(typeof project === "string" ? project : undefined),
    ),
    vscode.commands.registerCommand("ccnaviBoard.refresh", refreshBoard),
    // 承認はボードのボタンだけ。パレットからは打てない（承認内容を見ずに押せる入口を作らない）。
    vscode.commands.registerCommand("ccnaviBoard.openRules", (project?: unknown) =>
      void screens().rules(typeof project === "string" && project !== "" ? { kind: "project", name: project } : { kind: "workspace" }),
    ),
    vscode.commands.registerCommand("ccnaviBoard.openProjects", () => void screens().projects()),
    vscode.commands.registerCommand("ccnaviBoard.openRisk", () => void screens().risk()),
    vscode.commands.registerCommand("ccnaviBoard.openPhases", () => void screens().phases({ kind: "common" })),
    vscode.commands.registerCommand("ccnaviBoard.appearance", () => void pickAppearance()),
  );
}

export function deactivate(): void {
  // 後始末は context.subscriptions とパネルの onDidDispose が担う
}
