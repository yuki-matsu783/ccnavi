/**
 * ローカルの git を読み取り専用で起こす。プロジェクト管理画面が origin を読むためだけに使う。
 * ネットワークには出ない（`remote get-url` はローカルの設定を読むだけ）。
 * Node の子プロセスを使うが VS Code には依存しない。
 */
import { execFile } from "node:child_process";

const TIMEOUT_MS = 5000;

/** `git remote get-url origin`。git が無い、リポジトリでない、origin が無いときは空 */
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
