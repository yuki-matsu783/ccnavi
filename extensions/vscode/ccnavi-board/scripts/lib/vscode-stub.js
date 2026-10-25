// VS Code の窓口（`acquireVsCodeApi`）のスタブ。開発用の画面確認ツール（scripts/render-board.js）が、
// Chromium で画面を描くときに、ページへ差し込む。
//
// `page.addInitScript` は関数を文字列にしてページへ送るので、`installVsCodeStub` は外の変数を
// 読まない 1 つの関数に閉じる。単体テストは、同じ関数に偽の window を渡して振る舞いを確かめる。
//
// 画面が呼ぶのは `postMessage` / `getState` / `setState` の 3 つだけ（`src/webview/vscode.ts`）。
//   - postMessage: `window.__posted` に積む。ツールが board.log に出す（画面が拡張ホストへ送ったもの）
//   - getState / setState: 覚えておくだけ
// `ready` への応答は要らない。最初の中身は HTML に埋め込んである。
//
// 本物と同じく、`acquireVsCodeApi` は 1 度しか呼べない（2 度目は投げる）。画面が 2 度呼ぶ作りになったときに
// 気づけるようにする。
// CSP 違反（`securitypolicyviolation`）は `window.__csp` に積む。
"use strict";

/**
 * `win` に窓口のスタブを差し込む。
 * @param {object} win ページの window（偽物でもよい）
 */
function installVsCodeStub(win) {
  win.__posted = [];
  win.__csp = [];
  let acquired = false;
  let state;
  win.acquireVsCodeApi = function acquireVsCodeApi() {
    if (acquired) {
      throw new Error("An instance of the VS Code API has already been acquired");
    }
    acquired = true;
    return {
      postMessage(message) {
        win.__posted.push(message);
      },
      getState() {
        return state;
      },
      setState(next) {
        state = next;
      },
    };
  };
  if (typeof win.document !== "undefined" && win.document !== null) {
    win.document.addEventListener("securitypolicyviolation", (event) => {
      win.__csp.push(`${event.violatedDirective}: ${event.blockedURI || "inline"}`);
    });
  }
}

module.exports = { installVsCodeStub };
