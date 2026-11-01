/**
 * 開いたばかりのタブに「読み込み中」を入れる。6画面が共通で使う（HTMLは`core/loading-render.ts`）。
 *
 * 段取り（`ScreenHost`）を通さず`webview.html`に直に入れる。段取りはまだ1枚も入れていない
 * つもりのままなので、読み終えて最初に渡す中身は入れ物ごと入り、この1枚と入れ替わる。
 * VS CodeのAPIに触れるので単体テストの対象外。
 */
import * as crypto from "node:crypto";
import type * as vscode from "vscode";

import { readAppearance } from "./appearance.js";
import { renderLoadingPage } from "./core/loading-render.js";
import { webviewStyle } from "./webview-asset.js";

/** `screen`は画面の名前（`"board"`）。CSSはその画面のバンドルを使う。`what`は読むもの（「チケット」） */
export function showLoading(panel: vscode.WebviewPanel, title: string, screen: string, what: string): void {
  panel.webview.html = renderLoadingPage(title, what, {
    nonce: crypto.randomBytes(16).toString("base64"),
    style: webviewStyle(screen),
    appearance: readAppearance(),
  });
}
