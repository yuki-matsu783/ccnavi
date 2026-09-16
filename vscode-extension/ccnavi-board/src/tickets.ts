/**
 * チケットの置き場の監視と、実行ファイルの読みを画面どうしで分け合う。
 *
 * 5 つの画面はどれも同じ場所（提案・承認済みチケット・マーカー・作業ツリーの登録）を見ていて、
 * 同じ 1 つの変化で同じ答えを取りに行く。画面ごとに監視と子プロセスを持つと、ルール設定と
 * フェーズ管理が対象ごとに開ける分だけ増える。ワークスペースに 1 組だけ持ち、結果を配る。
 *
 * VS Code の API に触れるので単体テストの対象外。配る仕組みは core/fanout.ts、
 * 答えを分け合う仕組みは core/share.ts にあり、そちらはテストがある。
 */
import * as vscode from "vscode";

import { loadBoard, type LoadResult } from "./ccnavi.js";
import { Fanout } from "./core/fanout.js";
import { shareInFlight } from "./core/share.js";

/**
 * ファイルの変化を束ねる待ち時間（ミリ秒）。**画面ごとに同じ値を使う。**
 * 同じ変化で起きた読みが同じ拍に揃うから、画面どうしで 1 つの子プロセスを分け合える。
 * 画面ごとに別の値にすると、共有は黙って効かなくなる（子が画面の数だけ立つ）。
 */
export const DEBOUNCE_MS = 120;

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
  readonly fanout: Fanout;
}

/** ワークスペースフォルダごとに 1 組。聞く画面が 1 つも無くなったら畳む */
const watches = new Map<string, Watch>();

/**
 * 変化の世代。変化が来るたびに 1 つ進む。**読みを分け合う相手は同じ世代に限る。**
 * 変化の前に始まった読みは、その変化を見ていない答えを返すため。
 */
let epoch = 0;

/**
 * チケットが動いたら呼ぶ。返す取っ手を捨てると監視が畳まれないので、パネルの寿命の箱に入れる。
 * 束ねる待ち時間は画面ごとに持つ（それぞれ別の仕事をする）。ここは来たまま配る。
 */
export function onTicketsChanged(folder: vscode.WorkspaceFolder, listener: () => void): vscode.Disposable {
  const key = folder.uri.fsPath;
  let watch = watches.get(key);
  if (watch === undefined) {
    const watchers = WATCH_PATTERNS.map((pattern) =>
      vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(folder, pattern)),
    );
    watch = { watchers, fanout: new Fanout() };
    watches.set(key, watch);
    const fire = () => {
      epoch += 1;
      watches.get(key)?.fanout.fire((error) => {
        console.error("ccnavi: チケットの変化を配れなかった", error);
      });
    };
    for (const watcher of watchers) {
      watcher.onDidCreate(fire);
      watcher.onDidChange(fire);
      watcher.onDidDelete(fire);
    }
  }
  const here = watch;
  const remove = here.fanout.add(listener);
  return new vscode.Disposable(() => {
    remove();
    if (here.fanout.size > 0 || watches.get(key) !== here) {
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

/** 鍵に世代を混ぜる。走っていても、前の世代の読みには合流しない */
const shared = shareInFlight<[string, string, number], LoadResult>(
  (root, setting) => loadBoard(root, setting),
  (root, setting, at) => JSON.stringify([root, setting, at]),
);

/**
 * 監視の変化で走る読みはこれを使う。同じ変化で 4 画面が同時に取りに行っても、走るのは 1 つ。
 *
 * 分け合うのは**同じ世代の、走っている最中の読み**だけ。次の変化が来れば新しく走らせるし、
 * 答えが返った後も分け合わない。保存の直後など新しい答えが要る場面は `loadBoard` を直に呼ぶ
 * （書く前に始まった読みの答えを掴まないため）。
 */
export const loadBoardShared: Loader = (root, setting) => shared(root, setting, epoch);
