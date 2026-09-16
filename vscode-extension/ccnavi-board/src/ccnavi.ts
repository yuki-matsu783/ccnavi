/**
 * 実行ファイルを探して走らせる。Node の子プロセスを使うが VS Code には依存しない。
 * 実行ファイルはネットワークに出ないので、ここで待つのはワークスペースの走査だけ。
 *
 * 走らせるのは 7 つ。`--explain --json`（ボード）、`--test --json`（1 件の判定）、
 * `--test-samples --json`（見本の一括）、`--lint`（設定の検証）、`--lint --json`（同じ苦情を
 * 機械可読で。プロジェクト管理画面が読む）、`--approve --preview --json`（承認待ちの一覧を見る）、
 * `--approve --yes … --json`（見せた一覧を承認する。人がオーバーレイで押したときだけ）。
 * 判定と検証はルールファイルを差し替えられる。
 * ワークスペースのルールは `--rules`、プロジェクトのルールは `--project-rules-file <名前>=<パス>`。
 * 検証はリスクの配点も `--risk` で、フェーズの種類も `--phases`（層の種類なら `--project-phases-file <名前>=<パス>`）で
 * 差し替えられる（リスク管理画面・フェーズ管理画面）。
 * 編集中の内容を一時ファイルに置いて試すため。承認済みチケットと控えは外し、記録も残さない
 * （試し打ちで記録を汚さない）。
 */
import { execFile } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";

import {
  parseApprovePreview,
  parseApproveResult,
  partialMessage,
  type ApproveMismatch,
  type ApprovePreview,
  type ApproveResult,
} from "./core/approvemodel.js";
import { approveArgs, previewArgs, type Launcher } from "./core/commands.js";
import { parseLintJson, type LintJson } from "./core/lintmodel.js";
import { binFromSettingsJson, hostTarget, locate } from "./core/locate.js";
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

/**
 * 承認の 2 本に付ける期限（ミリ秒）。承認している間オーバーレイは閉じられないので、
 * 実行ファイルが返らないとパネルが「承認している…」から戻らなくなる。
 * 走査は数百ミリ秒で終わるが、遅い機械と大きなリポジトリを見て 60 秒。
 */
const APPROVE_TIMEOUT_MS = 60_000;

/**
 * ボードの読み直し（`--explain --json`）に付ける期限（ミリ秒）。返らないと画面の「更新」が
 * 押されたまま戻らず、以後の読み直しも黙って捨てられる（board-panel の loading が立ちっぱなしになる）。
 * 走査は数百ミリ秒で終わるが、遅い機械と大きなリポジトリを見て承認と同じ 60 秒。
 */
const EXPLAIN_TIMEOUT_MS = 60_000;

/**
 * 診断の 4 本（`--test` / `--test-samples` / `--lint` / `--lint --json`）に付ける期限（ミリ秒）。
 * この 4 本を待つ間、ルール設定・リスク管理・フェーズ管理の画面はボタンを非活性にし、返事が
 * 届いたときにしか活性へ戻さない。ボードと違って HTML の総取り替えも監視の読み直しも無いので、
 * 返らないと画面を閉じるまで戻れない（編集中の内容は消える）。値はボードと承認に揃えて 60 秒。
 */
const DIAGNOSE_TIMEOUT_MS = 60_000;

/** 期限で打ち切ったときの文面。標準エラーには何も残らないので、呼び手の代わりにここで組む */
function cutOff(what: string, ms: number): string {
  return `${what} を ${ms / 1000} 秒で打ち切った`;
}

const NOT_FOUND =
  "ccnavi の実行ファイルが見つからない（設定 ccnaviBoard.binPath、.claude/settings.json の CCNAVI_BIN_PATH、dist/ccnavi/ccnavi、.ccnavi/scripts/ccnavi-launcher.sh が起動する .ccnavi/bin/<os>-<arch>/ccnavi、ccnavi/__main__.py のどれも無い）。設定 ccnaviBoard.binPath で指せる";

/** 見るのはルールだけ。チケット制御と控えは外し、記録も残さない */
const RULES_ONLY = ["--ticket-control", "disable", "--state", "", "--log", ""] as const;

/**
 * 判定と検証に掛けるルールファイルの差し替え。ワークスペースのルール（共通層）は `--rules` で、
 * プロジェクト 1 つのルールは `--project-rules-file <名前>=<パス>` で（README「lint の JSON」）。
 * 自身の層は同じオプションに名札 `self` で渡す。実行ファイルは層の名前で差し替えを引き、
 * `self` を名乗るプロジェクトは層として数えないので取り違えない。
 * どれも診断でだけ効き、hook からの判定には届かない。
 */
export type RulesOverride =
  | { readonly kind: "workspace"; readonly path: string }
  | { readonly kind: "project"; readonly name: string; readonly path: string }
  | { readonly kind: "self"; readonly path: string };

/**
 * 検証（`--lint`）に掛ける設定の差し替え。ルールに加えて、リスクの配点を `--risk` で、
 * 共通層のフェーズの種類を `--phases` で、層（`self` かプロジェクト）の種類を
 * `--project-phases-file <名前>=<パス>` で差し替えられる。層の種類は共通層と合成して確かめられる。
 * 判定（`--test`）には配点も種類も関係ないので、そちらは RulesOverride だけを受ける。
 */
export type LintOverride =
  | RulesOverride
  | { readonly kind: "risk"; readonly path: string }
  | { readonly kind: "phases"; readonly path: string }
  | { readonly kind: "layerPhases"; readonly name: string; readonly path: string };

function overrideArgs(override: LintOverride): string[] {
  switch (override.kind) {
    case "workspace":
      return ["--rules", override.path];
    case "project":
      return ["--project-rules-file", `${override.name}=${override.path}`];
    case "self":
      return ["--project-rules-file", `self=${override.path}`];
    case "risk":
      return ["--risk", override.path];
    case "phases":
      return ["--phases", override.path];
    case "layerPhases":
      return ["--project-phases-file", `${override.name}=${override.path}`];
  }
}

export function findLauncher(root: string, setting: string): Launcher | undefined {
  return locate({
    root,
    setting,
    settingsEnvBin: readSettingsEnvBin(root),
    hostTarget: hostTarget(process.platform, process.arch),
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
  /** 期限で打ち切った（execFile が殺した）。標準エラーには何も残らないので、呼び手が文面を作る */
  readonly killed: boolean;
}

function run(
  launcher: Launcher,
  root: string,
  args: readonly string[],
  timeout?: number,
): Promise<Ran> {
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
        ...(timeout === undefined ? {} : { timeout }),
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
          killed: (error as { killed?: unknown } | null)?.killed === true,
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
  const ran = await run(launcher, root, ["--explain", "--json"], EXPLAIN_TIMEOUT_MS);
  if (ran.code !== 0) {
    const why = ran.killed
      ? `${EXPLAIN_TIMEOUT_MS / 1000} 秒で返らないので打ち切った`
      : ran.stderr;
    return { ok: false, launcher, error: `ccnavi --explain --json が失敗した: ${why}` };
  }
  const parsed = parseBoardJson(ran.stdout);
  if (!parsed.ok) {
    return { ok: false, launcher, error: parsed.error };
  }
  return { ok: true, launcher, board: parsed.board };
}

export type ApproveOutcome =
  | { readonly ok: true; readonly value: ApproveResult }
  | { readonly ok: false; readonly mismatch: ApproveMismatch }
  | { readonly ok: false; readonly error: string };

/**
 * 承認待ちの一覧を見る（`--approve --preview --json`）。承認済みチケットは置かれない。
 * 記録と控えは外さない。承認の経路は試し打ちではないので、実運用の設定のまま走らせる。
 */
export async function runApprovePreview(
  root: string,
  setting: string,
  only: readonly string[] = [],
): Promise<RunResult<ApprovePreview>> {
  const launcher = findLauncher(root, setting);
  if (launcher === undefined) {
    return { ok: false, error: NOT_FOUND };
  }
  const ran = await run(launcher, root, previewArgs(only), APPROVE_TIMEOUT_MS);
  if (ran.killed) {
    return {
      ok: false,
      error: `${cutOff("ccnavi --approve --preview --json", APPROVE_TIMEOUT_MS)}。承認済みチケットは置かれていない`,
    };
  }
  if (ran.code !== 0) {
    // 標準エラーは全部見せる。絞りが通らなかった理由（「親の改版が承認待ちなのに承認の対象に無い」など）は
    // 読めない提案の行より後ろに出るので、1 行目だけでは届かない。
    return { ok: false, error: `ccnavi --approve --preview --json が失敗した:\n${ran.stderr.trim()}` };
  }
  const parsed = parseApprovePreview(ran.stdout);
  return parsed.ok ? { ok: true, value: parsed.value } : { ok: false, error: parsed.error };
}

/**
 * 見せた一覧をそのまま承認する（`--approve --yes <識別子,…> --digest <指紋> --json`）。
 * 実行ファイルは見せた一覧と本文が今と同じことを求め、違えば `mismatch` を返して何も置かない。
 */
export async function runApproveYes(
  root: string,
  setting: string,
  tickets: readonly string[],
  digest: string,
  only: readonly string[] = [],
): Promise<ApproveOutcome> {
  const launcher = findLauncher(root, setting);
  if (launcher === undefined) {
    return { ok: false, error: NOT_FOUND };
  }
  const ran = await run(launcher, root, approveArgs(tickets, digest, only), APPROVE_TIMEOUT_MS);
  // 打ち切りは読む前に見る。承認済みチケットは 1 件ずつ置かれる（approval.py の for cand in batch）ので、
  // 途中で殺されると一部だけ置かれた状態が残る。stdout も途中で切れていて「読み取れない」に落ちるため、
  // ここで拾わないと何が起きたのか伝わらない。
  if (ran.killed) {
    return {
      ok: false,
      error:
        `${cutOff("ccnavi --approve --yes", APPROVE_TIMEOUT_MS)}。` +
        "一部だけ承認済みになっている可能性がある。承認済みチケットのコミットと push は送っていない" +
        "（送るのは承認できたときだけ）。ボードを更新して、何が承認されたかを確かめる",
    };
  }
  const parsed = parseApproveResult(ran.stdout);
  if (parsed.ok) {
    return ran.code === 0
      ? parsed
      : { ok: false, error: `ccnavi --approve --yes が失敗した: ${firstLine(ran.stderr)}` };
  }
  if ("mismatch" in parsed) {
    return parsed;
  }
  // 途中で止まった。置かれたぶんは残っているので、そう言う。ここで黙ると人は
  // 「何も起きていない」と読み、置かれた承認済みチケットに気づかないまま次へ進む。
  if ("partial" in parsed) {
    return { ok: false, error: partialMessage(parsed.partial) };
  }
  const said = firstLine(ran.stderr) || firstLine(ran.stdout);
  return { ok: false, error: said === "" ? `ccnavi --approve --yes の出力を読み取れない（${parsed.error}）` : said };
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
  ], DIAGNOSE_TIMEOUT_MS);
  if (ran.killed) {
    return { ok: false, error: cutOff("ccnavi --test --json", DIAGNOSE_TIMEOUT_MS) };
  }
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
  ], DIAGNOSE_TIMEOUT_MS);
  if (ran.killed) {
    return { ok: false, error: cutOff("ccnavi --test-samples --json", DIAGNOSE_TIMEOUT_MS) };
  }
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
  const ran = await run(launcher, root, [...overrideArgs(override), "--lint"], DIAGNOSE_TIMEOUT_MS);
  if (ran.killed) {
    return { ok: false, error: cutOff("ccnavi --lint", DIAGNOSE_TIMEOUT_MS) };
  }
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
  const ran = await run(launcher, root, ["--lint", "--json"], DIAGNOSE_TIMEOUT_MS);
  if (ran.killed) {
    return { ok: false, error: cutOff("ccnavi --lint --json", DIAGNOSE_TIMEOUT_MS) };
  }
  if (ran.code < 0 || ran.code > 1) {
    return { ok: false, error: `ccnavi --lint --json が失敗した: ${firstLine(ran.stderr)}` };
  }
  const parsed = parseLintJson(ran.stdout);
  if (parsed.ok) {
    return { ok: true, value: parsed.value };
  }
  // 設定の不備などで実行ファイルが JSON ではなく人向けの文面を出したときは、JSON.parse の苦情より
  // その文面（先頭行）のほうが原因を指しているので、そちらを見せる
  const said = firstLine(ran.stdout) || firstLine(ran.stderr);
  return { ok: false, error: said === "" ? `ccnavi --lint --json の出力を読めない（${parsed.error}）` : `ccnavi --lint --json の出力: ${said}` };
}
