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

/** 開いている Webview に今の見た目を送る。パネルを作ったときに購読し、閉じたら外す */
export function followAppearance(panel: vscode.WebviewPanel): void {
  const sub = onDidChangeAppearance((appearance) => {
    const message: AppearanceMessage = { type: "appearance", value: appearance };
    void panel.webview.postMessage(message);
  });
  panel.onDidDispose(() => sub.dispose());
}

/** サイドパネルとコマンドパレットから。今の値に印を付けた 3 択を出し、選んだ値を設定に書く */
export async function pickAppearance(): Promise<void> {
  const now = readAppearance();
  const picked = await vscode.window.showQuickPick(
    APPEARANCES.map((value) => ({ label: APPEARANCE_LABELS[value], description: value === now ? "（今の設定）" : "", value })),
    { placeHolder: "ccnavi の画面の見た目", title: "ccnavi ボード: 見た目" },
  );
  if (picked === undefined || picked.value === now) {
    return;
  }
  await vscode.workspace.getConfiguration("ccnaviBoard").update(KEY, picked.value, vscode.ConfigurationTarget.Global);
}
