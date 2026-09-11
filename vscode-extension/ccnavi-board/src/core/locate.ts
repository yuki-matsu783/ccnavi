/**
 * ccnavi の実行ファイルの探し方。.claude/scripts/*.sh と同じ順で探す。
 *
 *   1. 拡張の設定 `ccnaviBoard.binPath`
 *   2. `.claude/settings.json` の env `CCNAVI_BIN_PATH`
 *   3. `dist/ccnavi/ccnavi`
 *   4. ソースがあれば `uv run python -m ccnavi`
 *
 * どれも相対ならワークスペースルートからの相対。Windows の `.exe` は綴りに無くても試す。
 * ファイルの有無は呼び手が渡す（テストで実際のファイルシステムを要らなくするため）。
 */
import type { Launcher } from "./commands.js";

export interface LocateInput {
  readonly root: string;
  /** 拡張の設定。空なら見ない */
  readonly setting: string;
  /** .claude/settings.json の env.CCNAVI_BIN_PATH。無ければ undefined */
  readonly settingsEnvBin: string | undefined;
  readonly exists: (filePath: string) => boolean;
  /** パスの結合。テストでは "/" 結合を渡し、拡張では path.join を渡す */
  readonly join: (...parts: string[]) => string;
  readonly isAbsolute: (filePath: string) => boolean;
}

export const DEFAULT_BIN = "dist/ccnavi/ccnavi";
export const SOURCE_MARKER = "ccnavi/__main__.py";
const SUFFIXES = ["", ".exe"] as const;

export function locate(input: LocateInput): Launcher | undefined {
  const candidates = [input.setting, input.settingsEnvBin ?? "", DEFAULT_BIN].filter(
    (c) => c !== "",
  );
  for (const candidate of candidates) {
    const base = input.isAbsolute(candidate) ? candidate : input.join(input.root, candidate);
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
