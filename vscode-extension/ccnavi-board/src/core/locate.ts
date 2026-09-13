/**
 * ccnavi の実行ファイルの探し方。.ccnavi/scripts/*.sh と同じ順で探す。
 *
 *   1. 拡張の設定 `ccnaviBoard.binPath`
 *   2. `.claude/settings.json` の env `CCNAVI_BIN_PATH`
 *   3. `dist/ccnavi/ccnavi`
 *   4. `.ccnavi/bin/ccnavi`（scripts/ccnavi-setup.sh の既定の配布先）
 *   5. ソースがあれば `uv run python -m ccnavi`
 *
 * どれも相対ならワークスペースルートからの相対。Windows の `.exe` は綴りに無くても試す。
 *
 * 配布先では、指す先は振り分けの sh で、実体はその隣の `<os>-<arch>/` に並ぶ。
 * 拡張は sh を通さず、隣の実体を先に探す。Windows では sh を直接起動できないので、
 * sh を返すと起動に失敗する。語は ccnavi/platformtag.py と揃える。
 *
 * ファイルの有無は呼び手が渡す（テストで実際のファイルシステムを要らなくするため）。
 */
import type { Launcher } from "./commands.js";

export interface LocateInput {
  readonly root: string;
  /** 拡張の設定。空なら見ない */
  readonly setting: string;
  /** .claude/settings.json の env.CCNAVI_BIN_PATH。無ければ undefined */
  readonly settingsEnvBin: string | undefined;
  /** この機械の `<os>-<arch>`。hostTarget で作る */
  readonly hostTarget: string;
  readonly exists: (filePath: string) => boolean;
  /** パスの結合。テストでは "/" 結合を渡し、拡張では path.join を渡す */
  readonly join: (...parts: string[]) => string;
  readonly isAbsolute: (filePath: string) => boolean;
}

export const DEFAULT_BINS = ["dist/ccnavi/ccnavi", ".ccnavi/bin/ccnavi"] as const;
export const SOURCE_MARKER = "ccnavi/__main__.py";
const SUFFIXES = ["", ".exe"] as const;
const EXECUTABLE_NAMES = ["ccnavi", "ccnavi.exe"] as const;

/** Node の process.platform と process.arch から `<os>-<arch>` を作る */
export function hostTarget(platform: string, arch: string): string {
  const os = platform === "win32" ? "windows" : platform;
  const cpu = arch === "x64" ? "x86_64" : arch;
  return `${os}-${cpu}`;
}

/** この機械で動く組み立ての語を、先に選ぶ順に。arm64 の macOS と Windows は x86_64 を変換して動かす */
export function runnableTargets(host: string): string[] {
  switch (host) {
    case "darwin-arm64":
      return [host, "darwin-x86_64"];
    case "windows-arm64":
      return [host, "windows-x86_64"];
    default:
      return [host];
  }
}

function dirOf(filePath: string): string {
  const cut = Math.max(filePath.lastIndexOf("/"), filePath.lastIndexOf("\\"));
  return cut < 0 ? "" : filePath.slice(0, cut);
}

export function locate(input: LocateInput): Launcher | undefined {
  const candidates = [input.setting, input.settingsEnvBin ?? "", ...DEFAULT_BINS].filter(
    (c) => c !== "",
  );
  for (const candidate of candidates) {
    const base = input.isAbsolute(candidate) ? candidate : input.join(input.root, candidate);
    const dir = dirOf(base);
    if (dir !== "") {
      for (const target of runnableTargets(input.hostTarget)) {
        for (const name of EXECUTABLE_NAMES) {
          const filePath = input.join(dir, target, name);
          if (input.exists(filePath)) {
            return { kind: "exe", path: filePath };
          }
        }
      }
    }
    for (const suffix of SUFFIXES) {
      const filePath = base + suffix;
      if (input.exists(filePath)) {
        return { kind: "exe", path: filePath };
      }
    }
  }
  if (input.exists(input.join(input.root, SOURCE_MARKER))) {
    return { kind: "uv", root: input.root };
  }
  return undefined;
}

/** `.claude/settings.json` の本文から env.CCNAVI_BIN_PATH を取り出す。読めなければ undefined */
export function binFromSettingsJson(text: string): string | undefined {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return undefined;
  }
  if (typeof raw !== "object" || raw === null) {
    return undefined;
  }
  const env = (raw as { env?: unknown }).env;
  if (typeof env !== "object" || env === null) {
    return undefined;
  }
  const value = (env as { CCNAVI_BIN_PATH?: unknown }).CCNAVI_BIN_PATH;
  return typeof value === "string" && value !== "" ? value : undefined;
}
