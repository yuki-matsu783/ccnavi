/**
 * 画面（`*-panel.ts`）が共通で使う小さな部品。VS CodeのAPIに触れるので単体テストの対象外。
 *
 * 画面ごとに違う仕事（何を監視するか、何を渡すか）は呼ぶ側が渡す。ここにあるのは、どの画面でも
 * 同じ形になる書き方だけ。
 */
import * as vscode from "vscode";

import { multiFolderText, noFolderText } from "./core/workspace-folder.js";
import { WATCH_DEBOUNCE_MS, WATCH_PATTERNS } from "./core/watch.js";
import * as diaglog from "./log.js";

/**
 * パネルの`retainContextWhenHidden`が、選んだ段取りの前提と合っているかを確かめ、合っていなければ記録に出す
 * （取り違えても型では止まらないので、窓口を作るところで見る）。
 *
 * 保持する画面（`retainedHost`）は`retained`が真。偽のままだと、非表示になった画面へ送り続けて中身が古いまま止まる。
 * 保持しない画面（`screenHost`）は偽。真だと、非表示になっても画面が破棄されず、表示の有無の読み直しが働かない。
 * 診断ログにだけ残す（consoleには出さない。.agents/docs/logging.md）。
 */
export function assertRetained(panel: vscode.WebviewPanel, root: string, screen: string, retained = true): void {
  if (panel.options.retainContextWhenHidden !== retained) {
    diaglog.get("ccnavi-board", root).error("画面の前提が崩れている", { screen, retainContextWhenHidden: !retained });
  }
}

/** `pattern`に合うファイルの作成・変更・削除で`onChange`を呼ぶ監視を張る。閉じるときに`dispose`するのは呼んだ側 */
export function watchPattern(pattern: vscode.RelativePattern, onChange: () => void): vscode.FileSystemWatcher {
  const watcher = vscode.workspace.createFileSystemWatcher(pattern);
  watcher.onDidCreate(onChange);
  watcher.onDidChange(onChange);
  watcher.onDidDelete(onChange);
  return watcher;
}

/** `folder`からの相対のglob（`patterns`）それぞれに`watchPattern`を張る */
export function watchGlobs(folder: vscode.WorkspaceFolder, patterns: readonly string[], onChange: () => void): vscode.FileSystemWatcher[] {
  return patterns.map((glob) => watchPattern(new vscode.RelativePattern(folder, glob), onChange));
}

/** チケットの置き場（`core/watch.ts`の`WATCH_PATTERNS`）に`watchPattern`を張る */
export function watchTickets(folder: vscode.WorkspaceFolder, onChange: () => void): vscode.FileSystemWatcher[] {
  return watchGlobs(folder, WATCH_PATTERNS, onChange);
}

/**
 * 監視の反応を束ねる。`owner[key]`に待ちのタイマーを持ち、前の待ちがあれば取り消して、`delayMs`後に`run`を1度だけ呼ぶ。
 * `run`の中で、画面が閉じていないかは呼ぶ側が確かめる（閉じたあとの反応を捨てるかどうかは画面ごとに違う）。
 */
export function debounce<K extends string>(owner: { [P in K]?: NodeJS.Timeout }, key: K, run: () => void, delayMs = WATCH_DEBOUNCE_MS): void {
  const pending = owner[key];
  if (pending !== undefined) {
    clearTimeout(pending);
  }
  owner[key] = setTimeout(() => {
    owner[key] = undefined;
    run();
  }, delayMs);
}

/**
 * `switch`の`default`に置く網羅の検査。種類を足したのに`case`を書き忘れると、`value`が`never`でなくなって
 * 型が合わなくなる。実行時は何もしない（届いた値を黙って捨てる）。
 */
export function assertNever(value: never): void {
  void value;
}

/** ファイルをエディタで開く。開けなければ知らせる（rules・risk・phases・flowの［エディタで開く］が共通で使う） */
export function openInEditor(target: string): void {
  void vscode.workspace.openTextDocument(target).then(
    (document) => vscode.window.showTextDocument(document),
    () => vscode.window.showInformationMessage(`ファイルを開けませんでした: ${target}`),
  );
}

/**
 * 画面の対象にするフォルダ（最初の1つ）。1つも無ければ案内して`undefined`を返す。複数あれば、最初のフォルダだけが
 * 対象になることを知らせる。複数のフォルダへの対応はしない。
 * 画面を開く入口が使う。開く前の判定だけに使うときは、案内を出さない`firstWorkspaceFolder`を使う。
 */
export function workspaceFolder(what: string): vscode.WorkspaceFolder | undefined {
  const folders = vscode.workspace.workspaceFolders;
  const first = folders?.[0];
  if (first === undefined) {
    void vscode.window.showInformationMessage(noFolderText(what));
    return undefined;
  }
  if (folders !== undefined && folders.length > 1) {
    void vscode.window.showInformationMessage(multiFolderText(first.name));
  }
  return first;
}

/** 最初のフォルダ。案内は出さない（起動時の監視や、開けるかどうかの判定に使う） */
export function firstWorkspaceFolder(): vscode.WorkspaceFolder | undefined {
  return vscode.workspace.workspaceFolders?.[0];
}
