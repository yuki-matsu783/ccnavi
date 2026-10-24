/**
 * 画面ごとの初回の案内（吹き出しのツアー）を見たかどうか。拡張の `globalState` に画面の名前ごとに持つ。
 *
 * `globalState` はマシンごとに VS Code が持つ拡張の置き場で、ワークスペースを跨いで残る。画面の
 * `setState` はタブを閉じると消えるので、「初回だけ」を決めるのには使えない。
 * 見直したいときは、画面のヘッダ右上の ?（`webview/Tour.tsx` の `TourButton`）から出せる。
 *
 * 左のパネル（サイドパネル）は VS Code 標準のツリー表示で、吹き出しを出せない。左パネルの案内は
 * VS Code のウォークスルー（`package.json` の `contributes.walkthroughs`）に置き、拡張の版が変わった
 * 最初の起動で開く（`openSidebarTourOnNewVersion`）。そこから「プロジェクト管理 → チケット管理」の
 * 基本の案内を続けて動かせる（`runBasicTours`）。
 * VS Code の API に触れるので単体テストの対象外。
 */
import * as vscode from "vscode";

import { screens } from "./core/screens.js";
import { basicTourScreens, SIDEBAR_WALKTHROUGH } from "./core/tour-sidebar.js";
import { ticketControl } from "./ticket-control.js";

let memento: vscode.Memento | undefined;
/** 見たかどうかに関わらず、次に開いたとき案内を出す画面 */
const forced = new Set<string>();
/** 続けて動かしている基本の案内の残り。先頭の画面の案内を閉じたら、次を開く */
let chain: string[] = [];

/** 拡張を起こしたときに 1 度だけ呼ぶ */
export function initTours(context: vscode.ExtensionContext): void {
  memento = context.globalState;
}

function key(screen: string): string {
  return `tour.${screen}`;
}

/** その画面の案内を見たか。置き場がまだ無い（起こす前）なら見たことにして、出さない */
export function tourSeen(screen: string): boolean {
  return memento === undefined || memento.get<boolean>(key(screen)) === true;
}

/**
 * 画面が組み上がったときに、基本の案内を頼むか。まだ見ていない画面と、`forceTour` で頼まれた画面で真。
 * 画面は開いていても、タブを閉じて開き直しても、頼まれたものは閉じるまで残る
 */
export function tourDue(screen: string): boolean {
  return memento !== undefined && (forced.has(screen) || !tourSeen(screen));
}

export function markTourSeen(screen: string): void {
  forced.delete(screen);
  void memento?.update(key(screen), true);
  if (chain[0] === screen) {
    chain.shift();
    openNext();
  }
}

/** 見たことがあっても、次にその画面が組み上がったとき基本の案内を出す */
export function forceTour(screen: string): void {
  forced.add(screen);
}

/** 基本の案内を動かせる画面を開く。開けない画面（案内の入口が無い）は false */
async function openScreen(screen: string): Promise<boolean> {
  const open = screens();
  if (screen === "projects") {
    await open.projects();
    return true;
  }
  if (screen === "board") {
    await open.board();
    return true;
  }
  return false;
}

function openNext(): void {
  const next = chain[0];
  if (next === undefined) {
    return;
  }
  forceTour(next);
  void openScreen(next).then((opened) => {
    // 開けない画面で止まらず、次へ進む
    if (!opened && chain[0] === next) {
      forced.delete(next);
      chain.shift();
      openNext();
    }
  });
}

/**
 * プロジェクト管理 → チケット管理の順に、基本の案内を続けて動かす。前の案内を閉じる（最後まで見るか、やめる）と次を開く。
 * チケット制御が無効のワークスペースではチケット管理を開けないので、プロジェクト管理だけにする
 */
export function runBasicTours(): void {
  chain = basicTourScreens(ticketControl() === "enable").slice();
  openNext();
}

/** 1 つの画面の基本の案内だけを動かす（ウォークスルーの各段のボタン） */
export function runScreenTour(screen: string): void {
  chain = [screen];
  openNext();
}

const VERSION_KEY = "tour.sidebarVersion";

/**
 * 拡張の版が、左パネルの案内を最後に出したときと違えば、左パネルの案内（ウォークスルー）を開く。
 * 新しく入れたときと、入れ直して版が上がったときに 1 度ずつ出る。開く前に版を覚えるので、開けなくても繰り返さない。
 * ワークスペースが開かれていない間は出さない（画面の案内が動かせない）
 */
export function openSidebarTourOnNewVersion(context: vscode.ExtensionContext): void {
  const version = String(context.extension.packageJSON.version ?? "");
  if (version === "" || context.globalState.get<string>(VERSION_KEY) === version) {
    return;
  }
  if (vscode.workspace.workspaceFolders?.[0] === undefined) {
    return;
  }
  void context.globalState.update(VERSION_KEY, version);
  openSidebarTour(context);
}

/** 左パネルの案内（ウォークスルー）を開く。タイトルバーの ? からも開く */
export function openSidebarTour(context: vscode.ExtensionContext): void {
  void vscode.commands.executeCommand("workbench.action.openWalkthrough", `${context.extension.id}#${SIDEBAR_WALKTHROUGH}`, false);
}
