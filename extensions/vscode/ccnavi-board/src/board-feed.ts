/**
 * 拡張全体で1つだけ持つ`ccnavi --explain --json`の答えと、その読み直しの監視。
 * サイドパネル（ユーザ待ち・進行中の親とバッジ）、チケット管理画面、フロー編集の「提案あり」、チケット制御の入口が、
 * 同じ1回の答えを読む。答えを画面ごとに読むと、サイドパネルの件数とチケット管理画面のカードが食い違いうるため。
 *
 * 監視はチケット管理画面を開いているかどうかに関わらず、拡張が動いている間ずっと続ける（`core/watch.ts`の`FEED_PATTERNS`）。
 * 読み直しのまとめ方は`core/feed.ts`。VS CodeのAPIに触れるので単体テストの対象外（docs/design/manual-checks.md）。
 */
import * as vscode from "vscode";

import { loadBoard, type LoadResult } from "./ccnavi.js";
import { makeFeed, type Feed } from "./core/feed.js";
import { FEED_PATTERNS } from "./core/watch.js";
import { noFolderText } from "./core/workspace-folder.js";
import { debounce, firstWorkspaceFolder, watchGlobs } from "./panel-common.js";

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

/** 読むのはワークスペースの最初のフォルダ。無ければ読めなかった答えを返す（画面はそれを出す） */
async function load(): Promise<LoadResult> {
  const folder = firstWorkspaceFolder();
  if (folder === undefined) {
    return { ok: false, launcher: undefined, error: noFolderText("ユーザ待ち") };
  }
  return loadBoard(folder.uri.fsPath, binSetting());
}

/** 拡張全体の答え。読み直しは`refresh`、届いた答えは`subscribe`で受ける */
export const boardFeed: Feed<LoadResult> = makeFeed(load);

const timers: { timer?: NodeJS.Timeout } = {};

/** 監視を張り、最初の読み込みを始める。`extension.ts`が、答えを聞く側（入口・サイドパネル）を登録したあとに1度呼ぶ */
export function watchBoardFeed(context: vscode.ExtensionContext): void {
  const again = (): void => {
    debounce(timers, "timer", () => void boardFeed.refresh());
  };
  const folder = firstWorkspaceFolder();
  if (folder !== undefined) {
    context.subscriptions.push(...watchGlobs(folder, FEED_PATTERNS, again));
  }
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("ccnaviBoard.binPath")) {
        again();
      }
    }),
    {
      dispose: () => {
        if (timers.timer !== undefined) {
          clearTimeout(timers.timer);
        }
      },
    },
  );
  void boardFeed.refresh();
}
