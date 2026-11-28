/**
 * チケット制御の値を実行ファイルの答え（拡張全体で1つの`board-feed.ts`）から読み、VS Codeのcontext keyに反映する。
 * サイドパネルのチケットに関わる行（ユーザ待ちのチケット・進行中の親・「チケット管理」「リスク管理」「フェーズ管理」
 * 「テンプレート管理」）の有無と、コマンドパレットの`when`がこのキーを見る。画面を開く側は`requireTickets`でもう一度見る。
 * VS CodeのAPIに触れるので単体テストの対象外。読み方はcore/ticket-control.ts。
 */
import * as vscode from "vscode";

import { boardFeed } from "./board-feed.js";
import { TICKET_CONTROL_ENV, ticketControlOf, type TicketControl } from "./core/ticket-control.js";

/** package.jsonの`when`と揃える */
export const CONTEXT_KEY = "ccnaviBoard.ticketControl";

let current: TicketControl = "enable";
const listeners: Array<(value: TicketControl) => void> = [];

export function ticketControl(): TicketControl {
  return current;
}

export function onDidChangeTicketControl(listener: (value: TicketControl) => void): void {
  listeners.push(listener);
}

/**
 * チケット制御が有効なら真。disableなら理由を出して偽を返す。
 *
 * 一覧と`when`はdisableの入口を隠すが、キーバインド・他の拡張・前に開いた画面の中の
 * ボタンはそこを通らない。開く側でもう一度見て、隠れている画面が横から開かないようにする。
 * `what`は開こうとしたものの名前（「チケット管理画面」「リスク管理画面」など）。
 */
export function requireTickets(what: string): boolean {
  if (current === "enable") {
    return true;
  }
  vscode.window.showInformationMessage(
    `このワークスペースはチケット制御を使っていないため（${TICKET_CONTROL_ENV}=disable）、${what}は開けません。ルール管理画面とプロジェクト管理画面は利用できます。`,
  );
  return false;
}

/** 値が変わったときだけcontext keyと聞き手に知らせる */
function noteTicketControl(value: TicketControl): void {
  if (value === current) {
    return;
  }
  current = value;
  void vscode.commands.executeCommand("setContext", CONTEXT_KEY, value);
  for (const listener of listeners) {
    listener(value);
  }
}

/**
 * 拡張全体の答えが届くたびに反映する。読めなければenable（入口を消さない）。設定ファイルの変化は`board-feed.ts`の監視が拾う。
 * 答えを聞く側より先に呼ぶ（サイドパネルの行がこの値で決まる）
 */
export function watchTicketControl(context: vscode.ExtensionContext): void {
  // 最初は既定のenableをVS Code側に知らせておき、答えが来たら差し替える。起動を待たせない
  void vscode.commands.executeCommand("setContext", CONTEXT_KEY, current);
  const stop = boardFeed.subscribe((result) => noteTicketControl(ticketControlOf(result.ok ? result.board : undefined)));
  context.subscriptions.push({ dispose: stop });
}
