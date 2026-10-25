/**
 * チケット制御の値を実行ファイルに聞き、VS Code の context key に反映する。
 * サイドパネルの「チケット管理」「リスク管理」「フェーズ管理」「テンプレート管理」の有無と、コマンドパレットの
 * `when` がこのキーを見る。画面を開く側は `requireTickets` でもう一度見る。
 * VS Code の API と子プロセスに触れるので単体テストの対象外。読み方は core/ticket-control.ts。
 */
import * as vscode from "vscode";

import { loadBoard } from "./ccnavi.js";
import { TICKET_CONTROL_ENV, ticketControlOf, type TicketControl } from "./core/ticket-control.js";
import { watchGlobs } from "./panel-common.js";

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
 * チケット制御が有効なら真。disable なら理由を出して偽を返す。
 *
 * 一覧と `when` は disable の入口を隠すが、キーバインド・他の拡張・前に開いた画面の中の
 * ボタンはそこを通らない。開く側でもう一度見て、隠れている画面が横から開かないようにする。
 * `what` は開こうとしたものの名前（「チケット管理画面」「リスク管理画面」など）。
 */
export function requireTickets(what: string): boolean {
  if (current === "enable") {
    return true;
  }
  vscode.window.showInformationMessage(
    `このワークスペースはチケット制御を使っていない（${TICKET_CONTROL_ENV}=disable）ので、${what}は開けません。ルール管理画面とプロジェクト管理画面は利用できます`,
  );
  return false;
}

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

/**
 * 実行ファイルの答え（ボードの JSON）を受け取って反映する。ボードを読み直した画面もここへ渡す。
 * 値が変わったときだけ context key と聞き手に知らせる
 */
export function noteTicketControl(value: TicketControl): void {
  if (value === current) {
    return;
  }
  current = value;
  void vscode.commands.executeCommand("setContext", CONTEXT_KEY, value);
  for (const listener of listeners) {
    listener(value);
  }
}

/** 実行ファイルに聞き直す。読めなければ enable（入口を消さない） */
async function refresh(root: string): Promise<void> {
  const result = await loadBoard(root, binSetting());
  noteTicketControl(ticketControlOf(result.ok ? result.board : undefined));
}

/** 起動時に 1 度聞き、設定ファイルの変化で聞き直す */
export function watchTicketControl(context: vscode.ExtensionContext): void {
  const folder = vscode.workspace.workspaceFolders?.[0];
  // 最初は既定の enable を VS Code 側に知らせておき、答えが来たら差し替える。起動を待たせない
  void vscode.commands.executeCommand("setContext", CONTEXT_KEY, current);
  if (folder === undefined) {
    return;
  }
  const again = () => void refresh(folder.uri.fsPath);
  again();
  context.subscriptions.push(...watchGlobs(folder, SETTINGS_FILES, again));
}
