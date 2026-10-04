/**
 * Claude Code に文を渡す 2 つの経路。承認の文・レビュー済みの連絡（ボード）と、
 * フローの作成の依頼（フロー編集画面）が同じものを使う。
 * VS Code の API に触れるので単体テストの対象外。
 */
import * as vscode from "vscode";

/** 文をクリップボードに入れる。`what` は知らせに出す呼び名（「承認の文」など） */
export async function copyPrompt(prompt: string, what: string): Promise<void> {
  await vscode.env.clipboard.writeText(prompt);
  vscode.window.setStatusBarMessage(`${what}をコピーしました。Claude Code に貼って送ってください`, 5000);
}

/** 走っているセッションに送る公開の API は無いので、文を埋めて新しいセッションを開く（送信はユーザが Enter） */
export async function openPromptInSession(prompt: string): Promise<void> {
  await vscode.env.openExternal(vscode.Uri.parse(`vscode://anthropic.claude-code/open?prompt=${encodeURIComponent(prompt)}`));
}
