/**
 * 実行ファイルを探して `--explain --json` を走らせる。Node の子プロセスを使うが VS Code には依存しない。
 * 実行ファイルはネットワークに出ないので、ここで待つのはワークスペースの走査だけ。
 */
import { execFile } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";

import type { Launcher } from "./core/commands.js";
import { binFromSettingsJson, locate } from "./core/locate.js";
import { parseBoardJson, type BoardJson } from "./core/model.js";

export type LoadResult =
  | { readonly ok: true; readonly board: BoardJson; readonly launcher: Launcher }
  | { readonly ok: false; readonly error: string; readonly launcher: Launcher | undefined };

/** ボードの JSON はチケットが増えても数百 KB。余裕を持って 32 MB まで受ける */
const MAX_OUTPUT = 32 * 1024 * 1024;

export function findLauncher(root: string, setting: string): Launcher | undefined {
  return locate({
    root,
    setting,
    settingsEnvBin: readSettingsEnvBin(root),
    exists: (p) => {
      try {
        return fs.statSync(p).isFile();
      } catch {
        return false;
      }
    },
    join: path.join,
    isAbsolute: path.isAbsolute,
  });
}

function readSettingsEnvBin(root: string): string | undefined {
  try {
    return binFromSettingsJson(
      fs.readFileSync(path.join(root, ".claude", "settings.json"), "utf8"),
    );
  } catch {
    return undefined;
  }
}

export function loadBoard(root: string, setting: string): Promise<LoadResult> {
  const launcher = findLauncher(root, setting);
  if (launcher === undefined) {
    return Promise.resolve({
      ok: false,
      launcher,
      error:
        "ccnavi の実行ファイルが見つからない（dist/ccnavi/ccnavi、.claude/settings.json の CCNAVI_BIN_PATH、ccnavi/__main__.py のどれも無い）。設定 ccnaviBoard.binPath で指せる",
    });
  }
  const [file, args] =
    launcher.kind === "exe"
      ? [launcher.path, ["--root", root, "--explain", "--json"]]
      : ["uv", ["run", "python", "-m", "ccnavi", "--root", root, "--explain", "--json"]];
  return new Promise((resolve) => {
    execFile(
      file,
      args,
      {
        cwd: root,
        maxBuffer: MAX_OUTPUT,
        windowsHide: true,
        // 標準出力は ASCII に落としてあるが、標準エラーの日本語が化けないように。
        env: { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" },
      },
      (error, stdout, stderr) => {
        if (error) {
          const detail = stderr.trim() || error.message;
          resolve({ ok: false, launcher, error: `ccnavi --explain --json が失敗した: ${detail}` });
          return;
        }
        const parsed = parseBoardJson(stdout);
        if (!parsed.ok) {
          resolve({ ok: false, launcher, error: parsed.error });
          return;
        }
        resolve({ ok: true, launcher, board: parsed.board });
      },
    );
  });
}
