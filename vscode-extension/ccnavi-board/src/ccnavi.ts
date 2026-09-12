/**
 * 実行ファイルを探して走らせる。Node の子プロセスを使うが VS Code には依存しない。
 * 実行ファイルはネットワークに出ないので、ここで待つのはワークスペースの走査だけ。
 *
 * 走らせるのは 5 つ。`--explain --json`（ボード）、`--test --json`（1 件の判定）、
 * `--test-samples --json`（見本の一括）、`--lint`（設定の検証）、`--lint --json`（同じ苦情を
 * 機械可読で。プロジェクト管理画面が読む）。判定と検証はルールファイルを差し替えられる。
 * ワークスペースのルールは `--rules`、プロジェクトのルールは `--project-rules-file <名前>=<パス>`。
 * 検証はリスクの配点も `--risk` で、フェーズの種類も `--phases` で差し替えられる
 * （リスク管理画面・フェーズ管理画面）。
 * 編集中の内容を一時ファイルに置いて試すため。写しと控えは外し、記録も残さない
 * （試し打ちで記録を汚さない）。
 */
import { execFile } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";

import type { Launcher } from "./core/commands.js";
import { parseLintJson, type LintJson } from "./core/lintmodel.js";
import { binFromSettingsJson, locate } from "./core/locate.js";
import { parseBoardJson, type BoardJson } from "./core/model.js";
import {
  parseSamplesJson,
  parseTestJson,
  type SamplesJson,
  type TestJson,
} from "./core/testmodel.js";

export type LoadResult =
  | { readonly ok: true; readonly board: BoardJson; readonly launcher: Launcher }
  | { readonly ok: false; readonly error: string; readonly launcher: Launcher | undefined };

export type RunResult<T> =
  | { readonly ok: true; readonly value: T }
  | { readonly ok: false; readonly error: string };

export interface LintResult {
  readonly ok: boolean;
  /** `--lint` の報告そのもの。error があれば終了コードが非ゼロで ok が偽 */
  readonly report: string;
}

/** ボードの JSON はチケットが増えても数百 KB。余裕を持って 32 MB まで受ける */
const MAX_OUTPUT = 32 * 1024 * 1024;

const NOT_FOUND =
  "ccnavi の実行ファイルが見つからない（dist/ccnavi/ccnavi、.claude/settings.json の CCNAVI_BIN_PATH、ccnavi/__main__.py のどれも無い）。設定 ccnaviBoard.binPath で指せる";

/** 見るのはルールだけ。チケット制御と控えは外し、記録も残さない */
const RULES_ONLY = ["--ticket-control", "disable", "--state", "", "--log", ""] as const;

/**
 * 判定と検証に掛けるルールファイルの差し替え。ワークスペースのルールは `--rules` で、
 * プロジェクト 1 つのルールは `--project-rules-file <名前>=<パス>` で（README「lint の JSON」）。
 * どちらも診断でだけ効き、hook からの判定には届かない。
 */
export type RulesOverride =
  | { readonly kind: "workspace"; readonly path: string }
  | { readonly kind: "project"; readonly name: string; readonly path: string };

/**
 * 検証（`--lint`）に掛ける設定の差し替え。ルールに加えて、リスクの配点を `--risk` で、
 * フェーズの種類を `--phases` で差し替えられる。判定（`--test`）には配点も種類も関係ないので、
 * そちらは RulesOverride だけを受ける。
 */
export type LintOverride =
  | RulesOverride
  | { readonly kind: "risk"; readonly path: string }
  | { readonly kind: "phases"; readonly path: string };

function overrideArgs(override: LintOverride): string[] {
  switch (override.kind) {
    case "workspace":
      return ["--rules", override.path];
    case "project":
      return ["--project-rules-file", `${override.name}=${override.path}`];
    case "risk":
      return ["--risk", override.path];
    case "phases":
      return ["--phases", override.path];
  }
}

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

/** `.claude/settings.local.json` が先、無ければ `.claude/settings.json`。Claude Code の env の重なりと同じ */
function readSettingsEnvBin(root: string): string | undefined {
  for (const name of ["settings.local.json", "settings.json"]) {
    let text: string;
    try {
      text = fs.readFileSync(path.join(root, ".claude", name), "utf8");
    } catch {
      continue;
    }
    const found = binFromSettingsJson(text);
    if (found !== undefined) {
      return found;
    }
  }
  return undefined;
}

interface Ran {
  readonly code: number;
  readonly stdout: string;
  readonly stderr: string;
}

function run(launcher: Launcher, root: string, args: readonly string[]): Promise<Ran> {
  const common = ["--root", root, ...args];
  const [file, argv] =
    launcher.kind === "exe"
      ? [launcher.path, common]
      : ["uv", ["run", "python", "-m", "ccnavi", ...common]];
  return new Promise((resolve) => {
    execFile(
      file,
      argv,
      {
        cwd: root,
        maxBuffer: MAX_OUTPUT,
        windowsHide: true,
        // 標準出力は ASCII に落としてあるが、標準エラーの日本語が化けないように。
        env: { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" },
      },
      (error, stdout, stderr) => {
        const code =
          error === null
            ? 0
            : typeof (error as { code?: unknown }).code === "number"
              ? ((error as { code: number }).code as number)
              : -1;
        resolve({
          code,
          stdout,
          stderr: stderr.trim() || (error ? error.message : ""),
        });
      },
    );
  });
}

export async function loadBoard(root: string, setting: string): Promise<LoadResult> {
  const launcher = findLauncher(root, setting);
  if (launcher === undefined) {
    return { ok: false, launcher, error: NOT_FOUND };
  }
  const ran = await run(launcher, root, ["--explain", "--json"]);
  if (ran.code !== 0) {
    return { ok: false, launcher, error: `ccnavi --explain --json が失敗した: ${ran.stderr}` };
  }
  const parsed = parseBoardJson(ran.stdout);
  if (!parsed.ok) {
    return { ok: false, launcher, error: parsed.error };
  }
  return { ok: true, launcher, board: parsed.board };
}

/** 1 件を判定する。`rules` は当てるルールファイルの差し替え（編集中の内容を置いた一時ファイルでもよい） */
export async function runTest(
  root: string,
  setting: string,
  rules: RulesOverride,
  tool: string,
  subject: string,
): Promise<RunResult<TestJson>> {
  const launcher = findLauncher(root, setting);
  if (launcher === undefined) {
    return { ok: false, error: NOT_FOUND };
  }
  const ran = await run(launcher, root, [
    ...overrideArgs(rules),
    ...RULES_ONLY,
    "--test",
    tool,
    subject,
    "--json",
  ]);
  if (ran.code !== 0) {
    return { ok: false, error: `ccnavi --test --json が失敗した: ${ran.stderr}` };
  }
  const parsed = parseTestJson(ran.stdout);
  return parsed.ok ? { ok: true, value: parsed.value } : { ok: false, error: parsed.error };
}

/** 見本をぜんぶ判定に掛ける */
export async function runSamples(
  root: string,
  setting: string,
  rules: RulesOverride,
  samplesPath: string,
): Promise<RunResult<SamplesJson>> {
  const launcher = findLauncher(root, setting);
  if (launcher === undefined) {
    return { ok: false, error: NOT_FOUND };
  }
  const ran = await run(launcher, root, [
    ...overrideArgs(rules),
    ...RULES_ONLY,
    "--test-samples",
    samplesPath,
    "--json",
  ]);
  if (ran.code !== 0) {
    return { ok: false, error: `ccnavi --test-samples --json が失敗した: ${ran.stderr}` };
  }
  const parsed = parseSamplesJson(ran.stdout);
  return parsed.ok ? { ok: true, value: parsed.value } : { ok: false, error: parsed.error };
}

/** 設定を検証する。error が 1 件でもあれば ok が偽 */
export async function runLint(
  root: string,
  setting: string,
  override: LintOverride,
): Promise<RunResult<LintResult>> {
  const launcher = findLauncher(root, setting);
  if (launcher === undefined) {
    return { ok: false, error: NOT_FOUND };
  }
  const ran = await run(launcher, root, [...overrideArgs(override), "--lint"]);
  if (ran.code < 0 || ran.code > 1) {
    return { ok: false, error: `ccnavi --lint が失敗した: ${ran.stderr}` };
  }
  return { ok: true, value: { ok: ran.code === 0, report: ran.stdout.trim() } };
}

/**
 * 実運用の設定をそのまま検証し、苦情を JSON で受ける（README「lint の JSON」）。
 * 差し替えは無し。プロジェクト管理画面が、置き場とプロジェクトごとの warn を拾うために呼ぶ。
 * error があると終了コードが 1 になるが、それは JSON の中身で分かるので失敗にしない。
 */
/** 出力の最初の空でない行。画面の 1 行に収める用途で、全量は要らない */
function firstLine(text: string): string {
  return text.split(/\r?\n/).map((l) => l.trim()).find((l) => l !== "") ?? "";
}

export async function runLintJson(root: string, setting: string): Promise<RunResult<LintJson>> {
  const launcher = findLauncher(root, setting);
  if (launcher === undefined) {
    return { ok: false, error: NOT_FOUND };
  }
  const ran = await run(launcher, root, ["--lint", "--json"]);
  if (ran.code < 0 || ran.code > 1) {
    return { ok: false, error: `ccnavi --lint --json が失敗しました: ${firstLine(ran.stderr)}` };
  }
  const parsed = parseLintJson(ran.stdout);
  if (parsed.ok) {
    return { ok: true, value: parsed.value };
  }
  // 設定の不備などで実行ファイルが JSON ではなく人向けの文面を出したときは、JSON.parse の苦情より
  // その文面（先頭行）のほうが原因を指しているので、そちらを見せる
  const said = firstLine(ran.stdout) || firstLine(ran.stderr);
  return { ok: false, error: said === "" ? `ccnavi --lint --json の出力を読み取れません（${parsed.error}）` : `ccnavi --lint --json の出力: ${said}` };
}
