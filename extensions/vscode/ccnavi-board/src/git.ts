/**
 * ローカルのgitを読み取り専用で起こす。プロジェクト管理画面がoriginを読むためだけに使う。
 * `remote get-url`はローカルの設定を読むだけ。
 * Nodeの子プロセスを使うがVS Codeには依存しない。
 */
import { execFile } from "node:child_process";

const TIMEOUT_MS = 5000;

/** `git remote get-url origin`。gitが無い、リポジトリでない、originが無いときは空 */
export function readOrigin(dir: string): Promise<string> {
  return new Promise((resolve) => {
    execFile(
      "git",
      ["remote", "get-url", "origin"],
      { cwd: dir, windowsHide: true, timeout: TIMEOUT_MS },
      (error, stdout) => resolve(error ? "" : stdout.trim()),
    );
  });
}
