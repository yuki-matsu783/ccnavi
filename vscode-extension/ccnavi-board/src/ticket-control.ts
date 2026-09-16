/**
 * チケット制御の値をワークスペースから読み、VS Code の context key に写す。
 * サイドパネルの「チケット管理」「リスク管理」「フェーズ管理」の有無と、コマンドパレットの
 * `when` がこの鍵を見る。画面を開く側は `requireTickets` でもう一度見る。
 * VS Code の API に触れるので単体テストの対象外。読み方は core/ticket-control.ts。
 */
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

import { TICKET_CONTROL_ENV, ticketControlFrom, type TicketControl } from "./core/ticket-control.js";

/** package.json の `when` と揃える */
export const CONTEXT_KEY = "ccnaviBoard.ticketControl";

const SETTINGS_FILES = [".claude/settings.json", ".claude/settings.local.json"] as const;

let current: TicketControl = "enable";
const listeners: Array<(value: TicketControl) => void> = [];

export function ticketControl(): TicketControl {
  return current;
}

export function onDidChangeTicketControl(listener: (value: TicketControl) => void): void {
  listeners.push(listener);
}

/**
 * チケット制御が効いていれば真。disable なら理由を出して偽を返す。
 *
 * 一覧と `when` は disable の入口を隠すが、キーバインド・他の拡張・前に開いた画面の中の
 * ボタンはそこを通らない。開く側でもう一度見て、隠れている画面が横から開かないようにする。
 * `what` は開こうとしたものの名前（「ボード」「リスク管理画面」など）。
 */
export function requireTickets(what: string): boolean {
  if (current === "enable") {
    return true;
  }
  vscode.window.showInformationMessage(
    `このワークスペースはチケット制御を使っていない（${TICKET_CONTROL_ENV}=disable）ので、${what}は開けない`,
  );
  return false;
}

/** ワークスペースの設定ファイルから読み直す。ファイルが無ければ enable */
export function readTicketControl(root: string): TicketControl {
  return ticketControlFrom({
    settings: readText(path.join(root, ".claude", "settings.json")),
    local: readText(path.join(root, ".claude", "settings.local.json")),
  });
}

/** 起動時に 1 度読み、設定ファイルの変化で読み直す */
export function watchTicketControl(context: vscode.ExtensionContext): void {
  const folder = vscode.workspace.workspaceFolders?.[0];
  const refresh = () => {
    const value = folder === undefined ? "enable" : readTicketControl(folder.uri.fsPath);
    if (value === current) {
      return;
    }
    current = value;
    void vscode.commands.executeCommand("setContext", CONTEXT_KEY, value);
    for (const listener of listeners) {
      listener(value);
    }
  };
  // 最初の 1 回は値が同じでも context key を立てる。既定の enable を VS Code 側は知らない。
  current = folder === undefined ? "enable" : readTicketControl(folder.uri.fsPath);
  void vscode.commands.executeCommand("setContext", CONTEXT_KEY, current);
  if (folder === undefined) {
    return;
  }
  for (const pattern of SETTINGS_FILES) {
    const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(folder, pattern));
    watcher.onDidCreate(refresh);
    watcher.onDidChange(refresh);
    watcher.onDidDelete(refresh);
    context.subscriptions.push(watcher);
  }
}

function readText(filePath: string): string | undefined {
  try {
    return fs.readFileSync(filePath, "utf8");
  } catch {
    return undefined;
  }
}
