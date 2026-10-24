/**
 * Claude Code にプロンプトを渡す経路（コピー・新しいセッションで開く。ボードはさらにターミナルで実行）。
 * 承認のプロンプト・決めた結果のプロンプト（ボード）と、フローの作成の依頼（フロー編集画面）が同じものを使う。
 * VS Code の API に触れるので単体テストの対象外。
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import { claudeRunCommand } from "./core/commands.js";
import { runInTerminal } from "./terminal.js";

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
 * プロンプトをファイルに書き、ボードのターミナル（`terminal.ts`）で `claude -p` に渡して実行する。
 * 結果は取り込まず、ターミナルに流れるだけ（止めるのは Ctrl+C）。claude が PATH に無いときはターミナルがその旨を出す。
 * 書けなかったときは警告を出して送らない
 */
export async function runPromptInTerminal(root: string, prompt: string): Promise<void> {
  try {
    const dir = await fs.promises.mkdtemp(path.join(os.tmpdir(), "ccnavi-prompt-"));
    const file = path.join(dir, "prompt.txt");
    await fs.promises.writeFile(file, prompt, { encoding: "utf8", mode: 0o600 });
    runInTerminal(root, claudeRunCommand(file));
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    void vscode.window.showWarningMessage(`プロンプトのファイルを書けなかったため、実行できませんでした: ${reason}`);
  }
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
