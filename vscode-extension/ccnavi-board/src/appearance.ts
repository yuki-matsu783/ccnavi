/**
 * 見た目の設定（`ccnaviBoard.appearance`）の読み書きと、変わったときの通知。
 * 切り替えはサイドパネルのタイトルバーの歯車とコマンドパレットから。値は利用者の設定（グローバル）に書く。
 * VS Code の API に触れるので単体テストの対象外。
 */
import * as vscode from "vscode";

import { APPEARANCE_LABELS, APPEARANCES, type Appearance, type AppearanceSink, parseAppearance, sendAppearance } from "./core/appearance.js";

const KEY = "appearance";

export function readAppearance(): Appearance {
  return parseAppearance(vscode.workspace.getConfiguration("ccnaviBoard").get<string>(KEY));
}

/** 設定が変わったら呼ぶ。開いている画面はこれで body のクラスを付け替える */
export function onDidChangeAppearance(listener: (appearance: Appearance) => void): vscode.Disposable {
  return vscode.workspace.onDidChangeConfiguration((event) => {
    if (event.affectsConfiguration(`ccnaviBoard.${KEY}`)) {
      listener(readAppearance());
    }
  });
}

/**
 * いまの見た目を画面へ送る。**画面に中身を渡す段取り（`ScreenHost`）を通す。**
 * 届いたら真、組み上がっていない画面と捨てられた画面には送らないので偽。
 *
 * 落ちたぶんは持ち越さない。入れ物ごと入れ直す道では組む側が HTML に埋め（`bodyTag`）、
 * 画面が組み上がったところで呼ぶ側が送り直すので、どちらの道でもいまの値が後から渡る。
 */
export function postAppearance(host: AppearanceSink): boolean {
  return sendAppearance(host, readAppearance());
}

/**
 * 設定が変わったら、開いている画面に送る。パネルを作ったときに購読し、閉じたら外す。
 *
 * **送り先は段取り（`ScreenHost`）で、`panel.webview.postMessage` は叩かない**（issue #87）。
 * 表に戻ったときの送り直しもここでは持たない。保持しない画面（ボード・プロジェクト管理）は
 * 表に戻ると入れ物から作り直され、`ready` で呼ぶ側が送り直す。保持する画面（ルール設定・
 * リスク管理・フェーズ管理）は、裏にいる間の `lock` と `changed` を送り直すのと同じところで
 * 一緒に送り直す（ADR-0062）。**送り直す場所は画面の種類ごとに 1 か所**で、ここが別に持つと
 * 同じことを 2 か所でやることになる。
 */
export function followAppearance(panel: vscode.WebviewPanel, host: AppearanceSink): void {
  const sub = onDidChangeAppearance((appearance) => {
    sendAppearance(host, appearance);
  });
  panel.onDidDispose(() => {
    sub.dispose();
  });
}

/**
 * サイドパネルのタイトルバーの歯車とコマンドパレットから。今の値に印を付けた 3 択を出し、選んだ値を設定に書く。
 * 書く先は、いま値が定義されている置き場（フォルダ → ワークスペース → 利用者）。利用者の設定に書いても
 * ワークスペースの設定が勝って何も変わらない、ということが起きないように。
 */
export async function pickAppearance(): Promise<void> {
  const now = readAppearance();
  const picked = await vscode.window.showQuickPick(
    APPEARANCES.map((value) => ({ label: APPEARANCE_LABELS[value], picked: value === now, value })),
    { placeHolder: "ccnavi の画面の見た目", title: "ccnavi ボード: 見た目" },
  );
  if (picked === undefined || picked.value === now) {
    return;
  }
  const config = vscode.workspace.getConfiguration("ccnaviBoard");
  await config.update(KEY, picked.value, targetOf(config.inspect<string>(KEY)));
}

/** 値が定義されている置き場。無ければ利用者の設定 */
function targetOf(found: { workspaceFolderValue?: unknown; workspaceValue?: unknown } | undefined): vscode.ConfigurationTarget {
  if (found?.workspaceFolderValue !== undefined) {
    return vscode.ConfigurationTarget.WorkspaceFolder;
  }
  if (found?.workspaceValue !== undefined) {
    return vscode.ConfigurationTarget.Workspace;
  }
  return vscode.ConfigurationTarget.Global;
}
