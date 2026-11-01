/**
 * ccnaviの実行ファイルの探し方。.ccnavi/scripts/*.shと同じ順で探す。
 *
 *   1. 拡張の設定`ccnaviBoard.binPath`
 *   2. `.claude/settings.json`のenv `CCNAVI_ENTRY_PATH`
 *   3. `dist/ccnavi/ccnavi`
 *   4. `.ccnavi/scripts/ccnavi-launcher.sh`（振り分けのsh。scripts/ccnavi-setup.shが配る）
 *   5. ソースがあれば`uv run python -m ccnavi`
 *   6. PATHの`ccnavi`（`ccnavi.exe`）
 *
 * 振り分けのshは、bin/ に合うものが無いとPATHのccnaviを起動する。拡張も同じ規則で、最後にPATHを見る
 * （ソースが先なのは、保護済みshのccnavi_binと同じ）。見るのは絶対パスの項目だけ。振り分けのshと
 * 同じファイル（`ccnavi`という名前で張ったリンク）は飛ばす。
 *
 * どれも相対ならワークスペースルートからの相対。Windowsの`.exe`はパスに付いていなくても試す。
 *
 * 指す先が振り分けのsh（名前が`ccnavi-launcher.sh`）なら、実体はshの置き場の親の
 * `bin/<os>-<arch>/`に並ぶ（`.ccnavi/scripts/`のshなら`.ccnavi/bin/<os>-<arch>/`）。
 * 拡張はshを通さずその実体を探し、shそのものは返さない。Windowsではshを直接起動
 * できないので、shを返すと起動に失敗する。実体が無ければ次の候補へ進む。
 * それ以外のパスは、そのパスそのものを探す。語はsrc/ccnavi/infra/platformtag.pyと揃える。
 *
 * ファイルの有無は呼び手が渡す（テストで実際のファイルシステムを要らなくするため）。
 */
import type { Launcher } from "./commands.js";

export interface LocateInput {
  readonly root: string;
  /** 拡張の設定。空なら見ない */
  readonly setting: string;
  /** .claude/settings.jsonのenv.CCNAVI_ENTRY_PATH。無ければundefined */
  readonly settingsEnvBin: string | undefined;
  /** この機械の`<os>-<arch>`。hostTargetで作る */
  readonly hostTarget: string;
  readonly exists: (filePath: string) => boolean;
  /** パスの結合。テストでは "/" 結合を渡し、拡張ではpath.joinを渡す */
  readonly join: (...parts: string[]) => string;
  readonly isAbsolute: (filePath: string) => boolean;
  /** PATHの項目（区切りで分けたもの）。省くとPATHは見ない。絶対パスでない項目は飛ばす */
  readonly pathDirs?: readonly string[];
  /** 同じファイルか（リンクを解いて比べる）。省くと比べない。PATHのリンクが振り分けのshを指すのを飛ばす */
  readonly sameFile?: (a: string, b: string) => boolean;
}

export const DEFAULT_BINS = [
  "dist/ccnavi/ccnavi",
  ".ccnavi/scripts/ccnavi-launcher.sh",
] as const;
/** 振り分けのshの名前。src/ccnavi/infra/platformtag.pyのLAUNCHER_NAMEと揃える */
export const LAUNCHER_NAME = "ccnavi-launcher.sh";
export const SOURCE_MARKER = "src/ccnavi/__main__.py";
const SUFFIXES = ["", ".exe"] as const;
const EXECUTABLE_NAMES = ["ccnavi", "ccnavi.exe"] as const;

/** Nodeのprocess.platformとprocess.archから`<os>-<arch>`を作る */
export function hostTarget(platform: string, arch: string): string {
  const os = platform === "win32" ? "windows" : platform;
  const cpu = arch === "x64" ? "x86_64" : arch;
  return `${os}-${cpu}`;
}

/** この機械で動く組み立ての語を、先に選ぶ順に。arm64のmacOSとWindowsはx86_64を変換して動かす */
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

function nameOf(filePath: string): string {
  const cut = Math.max(filePath.lastIndexOf("/"), filePath.lastIndexOf("\\"));
  return filePath.slice(cut + 1);
}

/** `dir/<target>/ccnavi[.exe]`を、この機械で動く組み立ての順に探す */
function builtIn(input: LocateInput, dir: string): Launcher | undefined {
  for (const target of runnableTargets(input.hostTarget)) {
    for (const name of EXECUTABLE_NAMES) {
      const filePath = input.join(dir, target, name);
      if (input.exists(filePath)) {
        return { kind: "exe", path: filePath };
      }
    }
  }
  return undefined;
}

export function locate(input: LocateInput): Launcher | undefined {
  const candidates = [input.setting, input.settingsEnvBin ?? "", ...DEFAULT_BINS].filter(
    (c) => c !== "",
  );
  for (const candidate of candidates) {
    const base = input.isAbsolute(candidate) ? candidate : input.join(input.root, candidate);
    if (nameOf(base) === LAUNCHER_NAME) {
      // shは`../bin/`を探す。shそのものは返さない
      const home = dirOf(dirOf(base));
      const found = home === "" ? undefined : builtIn(input, input.join(home, "bin"));
      if (found !== undefined) {
        return found;
      }
      continue;
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
  return onPath(input);
}

/** PATHのccnavi。振り分けのsh（ccnavi_launcher_path）と同じ規則 */
function onPath(input: LocateInput): Launcher | undefined {
  const launcher = input.join(input.root, DEFAULT_BINS[1]);
  for (const dir of input.pathDirs ?? []) {
    if (dir === "" || !input.isAbsolute(dir)) {
      continue;
    }
    for (const name of EXECUTABLE_NAMES) {
      const filePath = input.join(dir, name);
      if (!input.exists(filePath)) {
        continue;
      }
      if (input.sameFile?.(filePath, launcher) === true) {
        continue;
      }
      return { kind: "exe", path: filePath };
    }
  }
  return undefined;
}

/** `.claude/settings.json`の本文からenv.CCNAVI_ENTRY_PATHを取り出す。読めなければundefined */
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
  const value = (env as { CCNAVI_ENTRY_PATH?: unknown }).CCNAVI_ENTRY_PATH;
  return typeof value === "string" && value !== "" ? value : undefined;
}
