import * as vscode from "vscode";

import { pickAppearance } from "./appearance.js";
import { openBoard, refreshBoard } from "./board-panel.js";
import { registerScreens, screens } from "./core/screens.js";
import { openFlow } from "./flow-panel.js";
import { openHandoff } from "./handoff-panel.js";
import { openPhases } from "./phases-panel.js";
import { openProjects } from "./projects-panel.js";
import { openRisk } from "./risk-panel.js";
import { openRules } from "./rules-panel.js";
import { openTemplates } from "./templates-panel.js";
import { registerSidebar } from "./sidebar.js";
import { watchTicketControl } from "./ticket-control.js";
import { initTours, openSidebarTour, openSidebarTourOnNewVersion, runBasicTours, runScreenTour } from "./tour.js";
import { warnVersionSkew } from "./version-check.js";
import { openWorkflow } from "./workflow-panel.js";

export function activate(context: vscode.ExtensionContext): void {
  // 画面ごとの初回の案内を見たかどうかの置き場（`globalState`）
  initTours(context);
  // 画面の入口はここに集める。画面どうしは互いを import せず、ここで登録した一覧を通して互いを開く。
  registerScreens({
    board: openBoard,
    rules: openRules,
    risk: openRisk,
    phases: openPhases,
    templates: openTemplates,
    handoff: (tree) => openHandoff(tree),
    projects: openProjects,
    flow: openFlow,
    workflow: openWorkflow,
  });
  // サイドパネルより先に読む。入口の順序がこの値で決まる。
  watchTicketControl(context);
  registerSidebar(context);
  // 実行ファイルと拡張の互換の版が食い違っていれば知らせる。待たない（起動を遅らせない）
  void warnVersionSkew();
  context.subscriptions.push(
    vscode.commands.registerCommand("ccnaviBoard.open", () => void screens().board()),
    vscode.commands.registerCommand("ccnaviBoard.refresh", refreshBoard),
    // 承認はボードのボタンだけ。パレットからは打てない（承認内容を見ずに押せる入口を作らない）。
    vscode.commands.registerCommand("ccnaviBoard.openRules", () => void screens().rules({ kind: "workspace" })),
    vscode.commands.registerCommand("ccnaviBoard.openProjects", () => void screens().projects()),
    vscode.commands.registerCommand("ccnaviBoard.openRisk", () => void screens().risk({ kind: "workspace" })),
    vscode.commands.registerCommand("ccnaviBoard.openPhases", () => void screens().phases({ kind: "self" })),
    vscode.commands.registerCommand("ccnaviBoard.openTemplates", () => void screens().templates({ kind: "self" })),
    vscode.commands.registerCommand("ccnaviBoard.openHandoff", () => void screens().handoff()),
    vscode.commands.registerCommand("ccnaviBoard.appearance", () => void pickAppearance()),
    // 左パネルの案内（ウォークスルー）と、そこから動かす画面の基本の案内
    vscode.commands.registerCommand("ccnaviBoard.tour", () => openSidebarTour(context)),
    vscode.commands.registerCommand("ccnaviBoard.tourBasics", runBasicTours),
    vscode.commands.registerCommand("ccnaviBoard.tourProjects", () => runScreenTour("projects")),
    vscode.commands.registerCommand("ccnaviBoard.tourBoard", () => runScreenTour("board")),
    // 親の識別子を引数で受けて開く（キーバインドの args や、ほかの拡張の executeCommand から）。開く親を選ぶ段が
    // 無いので、コマンドパレットには出さない（package.json の menus.commandPalette で when: false）
    vscode.commands.registerCommand("ccnaviBoard.openWorkflow", (ticket: unknown) => void screens().workflow(String(ticket ?? ""))),
  );
  // 版が変わった最初の起動で、左パネルの案内を開く
  openSidebarTourOnNewVersion(context);
}

export function deactivate(): void {
  // 後始末は context.subscriptions とパネルの onDidDispose が担う
}
