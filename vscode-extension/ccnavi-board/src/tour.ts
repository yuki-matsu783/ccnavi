/**
 * 画面ごとの初回の案内（吹き出しのツアー）を見たかどうか。拡張の `globalState` に画面の名前ごとに持つ。
 *
 * `globalState` はマシンごとに VS Code が持つ拡張の置き場で、ワークスペースを跨いで残る。画面の
 * `setState` はタブを閉じると消えるので、「初回だけ」を決めるのには使えない。
 * 印を消す操作は用意していない。見直したいときは、画面のツールバーの「？ 案内」（フェーズ管理画面はヘルプの「案内をもう一度見る」）から出せる。
 * VS Code の API に触れるので単体テストの対象外。
 */
import type * as vscode from "vscode";

let memento: vscode.Memento | undefined;

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

export function markTourSeen(screen: string): void {
  void memento?.update(key(screen), true);
}
