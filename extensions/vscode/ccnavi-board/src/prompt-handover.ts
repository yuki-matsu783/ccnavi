/**
 * Claude Code にプロンプトを渡す 2 つの経路。承認のプロンプト・決めた結果のプロンプト（ボード）と、
 * フローの作成の依頼（フロー編集画面）が同じものを使う。
 * VS Code の API に触れるので単体テストの対象外。
 */
import * as vscode from "vscode";

/**
 * プロンプトをクリップボードに入れる。`what` は知らせに出す呼び名（「承認のプロンプト」など）。
 * 入れられたら空、入れられなかったら理由を返す（呼び手が失敗と言う。投げない）
 */
export async function copyPrompt(prompt: string, what: string): Promise<string | undefined> {
  try {
    await vscode.env.clipboard.writeText(prompt);
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    void vscode.window.showWarningMessage(`${what}をコピーできませんでした: ${reason}`);
    return reason === "" ? "理由は不明です" : reason;
  }
  vscode.window.setStatusBarMessage(`${what}をコピーしました。Claude Code に貼って送ってください`, 5000);
  return undefined;
}

/** 走っているセッションに送る公開の API は無いので、プロンプトを埋めて新しいセッションを開く（送信はユーザが Enter） */
export async function openPromptInSession(prompt: string): Promise<void> {
  await vscode.env.openExternal(vscode.Uri.parse(`vscode://anthropic.claude-code/open?prompt=${encodeURIComponent(prompt)}`));
}

/**
 * コマンド行をクリップボードに入れる（ボードからは送らない。ユーザが端末に貼って実行する）。
 * 入れられたら空、入れられなかったら理由を返す。投げない
 */
export async function copyCommand(command: string, what: string): Promise<string | undefined> {
  try {
    await vscode.env.clipboard.writeText(command);
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    void vscode.window.showWarningMessage(`${what}をコピーできませんでした: ${reason}`);
    return reason === "" ? "理由は不明です" : reason;
  }
  vscode.window.setStatusBarMessage(`${what}をコピーしました。端末に貼って実行してください`, 5000);
  return undefined;
}
