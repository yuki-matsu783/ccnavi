/**
 * 見た目の設定（`ccnaviBoard.appearance`）の読み書きと、変わったときの通知。
 * 切り替えはサイドパネルの「見た目」とコマンドパレットから。値は利用者の設定（グローバル）に書く。
 * VS Code の API に触れるので単体テストの対象外。
 */
import * as vscode from "vscode";

import { APPEARANCE_LABELS, APPEARANCES, type Appearance, type AppearanceMessage, parseAppearance } from "./core/appearance.js";

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
 * 開いている Webview に今の見た目を送る。パネルを作ったときに購読し、閉じたら外す。
 * 裏に回っている間は postMessage が届かない（retainContextWhenHidden が偽の画面は HTML ごと捨てられ、
 * 再表示で作り直される）ので、見えるようになったときにも今の値を送り直す。
 */
export function followAppearance(panel: vscode.WebviewPanel): void {
  const send = (appearance: Appearance): void => {
    const message: AppearanceMessage = { type: "appearance", value: appearance };
    void panel.webview.postMessage(message);
  };
  const subs = [
    onDidChangeAppearance(send),
    panel.onDidChangeViewState((event) => {
      if (event.webviewPanel.visible) {
        send(readAppearance());
      }
    }),
  ];
  panel.onDidDispose(() => {
    for (const sub of subs) {
      sub.dispose();
    }
  });
}

/**
 * サイドパネルとコマンドパレットから。今の値に印を付けた 3 択を出し、選んだ値を設定に書く。
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
