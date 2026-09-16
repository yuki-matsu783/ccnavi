/**
 * チケットの置き場の監視と、実行ファイルの読みを画面どうしで分け合う。
 *
 * 5 つの画面はどれも同じ場所（提案・承認済みチケット・マーカー・作業ツリーの登録）を見ていて、
 * 同じ 1 つの変化で同じ答えを取りに行く。画面ごとに監視と子プロセスを持つと、ルール設定と
 * フェーズ管理が対象ごとに開ける分だけ増える。ワークスペースに 1 組だけ持ち、結果を配る。
 *
 * VS Code の API に触れるので単体テストの対象外。
 */
import * as vscode from "vscode";

import { loadBoard, type LoadResult } from "./ccnavi.js";
import { shareInFlight } from "./core/share.js";

/**
 * 監視する場所。提案（ワークスペース、プロジェクト、全作業ツリーの `wip/tickets/`）、
 * 承認済みチケットとマーカー（同じツリーの `.ccnavi/tickets/`）、作業ツリーの登録。
 * glob は OS によらず "/" 区切り。
 */
const WATCH_PATTERNS = [
  "wip/**/tickets/**",
  "projects/*/wip/**/tickets/**",
  ".claude/worktrees/*/wip/**/tickets/**",
  ".ccnavi/tickets/**",
  "projects/*/.ccnavi/tickets/**",
  ".claude/worktrees/*/.ccnavi/tickets/**",
  ".git/worktrees/*",
  "projects/*/.git/worktrees/*",
] as const;

interface Watch {
  readonly watchers: vscode.FileSystemWatcher[];
  readonly listeners: Set<() => void>;
}

/** ワークスペースフォルダごとに 1 組。聞く画面が 1 つも無くなったら畳む */
const watches = new Map<string, Watch>();

/**
 * チケットが動いたら呼ぶ。返す取っ手を捨てると監視が畳まれないので、パネルの寿命の箱に入れる。
 * 呼び出しを束ねる待ち時間は画面ごとに持つ（それぞれ別の仕事をする）。ここは来たまま配る。
 */
export function onTicketsChanged(folder: vscode.WorkspaceFolder, listener: () => void): vscode.Disposable {
  const key = folder.uri.fsPath;
  let watch = watches.get(key);
  if (watch === undefined) {
    const watchers = WATCH_PATTERNS.map((pattern) =>
      vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(folder, pattern)),
    );
    watch = { watchers, listeners: new Set() };
    watches.set(key, watch);
    const fire = () => {
      // 聞いている途中で外れても走査が飛ばないよう、写しを回す
      for (const one of [...(watches.get(key)?.listeners ?? [])]) {
        one();
      }
    };
    for (const watcher of watchers) {
      watcher.onDidCreate(fire);
      watcher.onDidChange(fire);
      watcher.onDidDelete(fire);
    }
  }
  const here = watch;
  here.listeners.add(listener);
  return new vscode.Disposable(() => {
    here.listeners.delete(listener);
    if (here.listeners.size > 0 || watches.get(key) !== here) {
      return;
    }
    for (const watcher of here.watchers) {
      watcher.dispose();
    }
    watches.delete(key);
  });
}

/** ボードを読む手。監視で走るときだけ `loadBoardShared` に差し替える */
export type Loader = (root: string, setting: string) => Promise<LoadResult>;

/**
 * 監視の変化で走る読みはこれを使う。1 つの変化で 4 画面が同時に取りに行っても、走るのは 1 つ。
 * **答えが返った後は分け合わない。** 保存の直後など新しい答えが要る場面は `loadBoard` を直に
 * 呼ぶ（書く前に始まった読みの答えを掴まないため）。
 */
export const loadBoardShared: Loader = shareInFlight(loadBoard, (root, setting) => JSON.stringify([root, setting]));
