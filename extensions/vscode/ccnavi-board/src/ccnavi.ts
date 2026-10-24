/**
 * 実行ファイルを探して走らせる。Node の子プロセスを使うが VS Code には依存しない。
 * 実行ファイルはネットワークに出ないので、ここで待つのはワークスペースの走査だけ。
 *
 * 走らせるのは 13 つ。`--explain --json`（ボード）、`--test --json`（1 件の判定）、
 * `--test-expectations --json`（期待する判定の一括）、`--lint`（設定の検証）、`--lint --json`（同じ苦情を
 * 機械可読で。プロジェクト管理画面が読む）、`--lint --json --flow <パス>`（子のフロー 1 本を
 * SubagentStart と同じ読みで確かめる。フロー編集画面が開くときと保存の前に読む）、`--agree --preview --json`（承認待ちの一覧を見る）、
 * `user agree --yes … --json`（見せた一覧を承認する。ユーザがオーバーレイで押したときだけ）、
 * `--suggest --json`（記録からルールの候補を起こす。ルール管理画面が読む。記録を読むので `--log ""` は付けない）、
 * `--plan-order <親> --json [--order <JSON>]`（親の提案の計画の順序を振り直した答え。計画編集タブが読む）と
 * `--plan-order <親> --order <JSON> --expect <ハッシュ> --write --json`（その保存。提案を書き換えるのは実行ファイル）と、
 * `--templates --json`（テンプレートの一覧。テンプレート管理画面が読む）、`--templates --show <名前> --json`（1 件の本文）、`--templates --check-file <パス> --json`（編集中の 1 本を読ませる）、
 * `user handoff [<パス>…] --json`（`wip/handoff/` の下書きの一覧を見る。取り込み待ち画面が読む。`--root` で指したツリー 1 本だけを見る）と
 * `user handoff <パス…> --yes --expect <ハッシュ> --via board --json`（取り込み。書くのは実行ファイル。ユーザが画面で押したときだけ。
 * `expect` は取り込むパスを指して見るだけをもう一度呼んだ答えのもの）。
 * ほかに `--version --json`（版・互換の版・受け付けるフラグ）を、起動のときと新しいフラグを使う前に聞く。
 * 判定と検証はルールファイルを差し替えられる。
 * 共通の設定のルールは `--rules`、プロジェクトのルールは `--project-rules-file <名前>=<パス>`。
 * 検証はリスクの配点も差し替えられる。共通の設定は `--risk`、プロジェクトの設定は `--project-risk-file <名前>=<パス>`
 * （ワークスペースの設定は `self=<パス>`。共通の設定と合わせて検証される）。フェーズ定義は `--project-phases-file <名前>=<パス>`
 * （同じく `self=<パス>`）で差し替えられる。共通の設定の定義は `--phases`（リスク管理画面・フェーズ管理画面）。
 * 編集中の内容を一時ファイルに置いて試すため。承認済みチケットと state の置き場は外し、記録も残さない
 * （試し打ちで記録を汚さない）。
 */
import { execFile } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";

import {
  parseApprovePreview,
  parseApproveResult,
  type ApproveMismatch,
  type ApproveOutcome,
  type ApprovePreview,
  type ApproveResult,
  type ApproveWorktree,
  failureReason,
} from "./core/approvemodel.js";
import {
  approveArgs,
  writeAndPushTargetOf,
  decideArgs,
  handoffArgs,
  handoffYesArgs,
  decidePreviewArgs,
  mrStateArgs,
  syncRetiredArgs,
  GIT_SCRIPT,
  overrideArgs,
  planOrderArgs,
  planOrderWriteArgs,
  previewArgs,
  REVIEW_SCRIPT,
  USER_DECIDE_SCRIPT,
  templatesArgs,
  templatesCheckArgs,
  templatesDraftArgs,
  templatesShowArgs,
  toPosixPath,
  type Launcher,
  type LintOverride,
  type RulesOverride,
} from "./core/commands.js";
export type { LintOverride, RulesOverride } from "./core/commands.js";
import {
  parseDecidePreview,
  parseDecideResult,
  type DecideExpect,
  type DecideOutcome,
  type DecidePreview,
} from "./core/decidemodel.js";
import { parseLintJson, type LintJson } from "./core/lintmodel.js";
import { binFromSettingsJson, hostTarget, locate } from "./core/locate.js";
import { parseBoardJson, type BoardJson } from "./core/model.js";
import { interpretMrState, parseRetired, type MrStateResult, type RetiredParse } from "./core/mrstate.js";
import { parsePlanOrder, type PlanOrder, type PlanOrderAnswer, type PlanOrderWrite } from "./core/planordermodel.js";
import { parseSuggestJson, type SuggestJson } from "./core/suggestmodel.js";
import type { HandoffList, HandoffResult } from "./core/handoff-view.js";
import { parseHandoffList, parseHandoffResult } from "./core/handoffmodel.js";
import type { TemplateCheck, TemplateDetail, TemplatesJson } from "./core/templates-view.js";
import { parseTemplateCheck, parseTemplates, parseTemplateShow } from "./core/templatesmodel.js";
import {
  parseExpectationsJson,
  parseTestJson,
  type ExpectationsJson,
  type TestJson,
} from "./core/testmodel.js";
import { missingFeatures, missingFlags, parseVersionJson, type VersionProbe } from "./core/version.js";

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

/** 子のワークツリー 1 本を作る（`git worktree add` の局所の操作だけ） */
const WORKTREE_TIMEOUT_MS = 60_000;

/**
 * ボードの読み直し（`--explain --json`）に付ける期限（ミリ秒）。返らないと画面の「更新」が
 * 押されたまま戻らず、以後の読み直しも何も出さずに捨てられる（board-panel の loading が真のまま残る）。
 * 走査は数百ミリ秒で終わるが、遅い機械と大きなリポジトリを見て承認と同じ 60 秒。
 */
const EXPLAIN_TIMEOUT_MS = 60_000;

/**
 * 診断の 4 本（`--test` / `--test-expectations` / `--lint` / `--lint --json`）に付ける期限（ミリ秒）。
 * この 4 本を待つ間、ルール管理・リスク管理・フェーズ管理の画面はボタンを非活性にし、返事が
 * 届いたときにしか活性へ戻さない。ボードと違って HTML の総取り替えも監視の読み直しも無いので、
 * 返らないと画面を閉じるまで戻れない（編集中の内容は消える）。値はボードと承認に揃えて 60 秒。
 */
const DIAGNOSE_TIMEOUT_MS = 60_000;

/** 期限で打ち切ったときの文面。標準エラーには何も残らないので、呼び手の代わりにここで組む */
function cutOff(what: string, ms: number): string {
  return `${what} を ${ms / 1000} 秒で中断しました`;
}

const NOT_FOUND =
  "ccnavi の実行ファイルが見つかりません（設定 ccnaviBoard.binPath、.claude/settings.json の CCNAVI_ENTRY_PATH、dist/ccnavi/ccnavi、.ccnavi/scripts/ccnavi-launcher.sh が起動する .ccnavi/bin/<os>-<arch>/ccnavi、src/ccnavi/__main__.py、PATH の ccnavi のどれもありません）。設定 ccnaviBoard.binPath で指定できます";

/** 見るのはルールだけ。チケット制御と state の置き場は外し、記録も残さない */
const RULES_ONLY = ["--ticket-control", "disable", "--state", "", "--log", ""] as const;

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
    pathDirs: (process.env.PATH ?? "").split(path.delimiter),
    sameFile: (a, b) => {
      try {
        return fs.realpathSync(a) === fs.realpathSync(b);
      } catch {
        return false;
      }
    },
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
  /** 期限で打ち切った（execFile が強制終了した）。標準エラーには何も残らないので、呼び手が文面を作る */
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
  return spawn(file, argv, root, timeout);
}

/** 子プロセスを 1 本走らせ、終了コード・出力・打ち切られたかを返す */
function spawn(file: string, argv: readonly string[], cwd: string, timeout?: number): Promise<Ran> {
  return new Promise((resolve) => {
    execFile(
      file,
      argv,
      {
        cwd,
        maxBuffer: MAX_OUTPUT,
        windowsHide: true,
        ...(timeout === undefined ? {} : { timeout }),
        // 標準出力は ASCII だけにしてあるが、標準エラーの日本語が化けないように。
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
      ? `${EXPLAIN_TIMEOUT_MS / 1000} 秒たっても応答がないため中断しました`
      : ran.stderr;
    return { ok: false, launcher, error: `ccnavi --explain --json が失敗しました: ${why}` };
  }
  const parsed = parseBoardJson(ran.stdout);
  if (!parsed.ok) {
    return { ok: false, launcher, error: parsed.error };
  }
  return { ok: true, launcher, board: parsed.board };
}

/** 承認の答え。形は契約の側（`core/approvemodel.ts`）が持つ */
export type { ApproveOutcome };

/**
 * 承認待ちの一覧を見る（`--agree --preview --json`）。承認済みチケットは置かれない。
 * 記録と state の置き場は外さない。承認の経路は試し打ちではないので、実運用の設定のまま走らせる。
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
      error: `${cutOff("ccnavi --agree --preview --json", APPROVE_TIMEOUT_MS)}。何も承認していません`,
    };
  }
  if (ran.code !== 0) {
    // 標準エラーは全部見せる。絞りが通らなかった理由（「親の改版が承認待ちなのに承認の対象に無い」など）は
    // 読めない提案の行より後ろに出るので、1 行目だけでは伝わらない。
    return { ok: false, error: `ccnavi --agree --preview --json が失敗しました:\n${ran.stderr.trim()}` };
  }
  const parsed = parseApprovePreview(ran.stdout);
  return parsed.ok ? { ok: true, value: parsed.value } : { ok: false, error: parsed.error };
}

/**
 * 見せた一覧をそのまま承認する（`user agree --yes <識別子,…> --digest <ダイジェスト> --json`）。
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
  const refused = await checkUserEntry(root, setting, "ccnavi user agree --yes");
  if (refused !== undefined) {
    return { ok: false, error: refused };
  }
  const ran = await run(launcher, root, approveArgs(tickets, digest, only), APPROVE_TIMEOUT_MS);
  // 打ち切りは読む前に見る。承認済みチケットは 1 件ずつ置かれる（agree.py の for cand in batch）ので、
  // 途中で強制終了されると一部だけ置かれた状態が残る。stdout も途中で切れていて「読み取れない」になるため、
  // ここで拾わないと何が起きたのか伝わらない。
  if (ran.killed) {
    return {
      ok: false,
      error:
        `${cutOff("ccnavi user agree --yes", APPROVE_TIMEOUT_MS)}。` +
        "一部だけ承認済みになっている可能性があります。承認済みチケットをコミット・pushするコマンドはターミナルに送信していません" +
        "（送るのは承認できたときだけです）。チケット管理画面を更新して、何が承認されたかを確認してください",
    };
  }
  const parsed = parseApproveResult(ran.stdout);
  if (parsed.ok) {
    return ran.code === 0
      ? parsed
      : { ok: false, error: `ccnavi user agree --yes が失敗しました: ${firstLine(ran.stderr)}` };
  }
  if ("mismatch" in parsed) {
    return parsed;
  }
  // 途中で止まった。置かれたぶんは残っているので、そう言う。ここで何も言わないとユーザは
  // 「何も起きていない」と読み、置かれた承認済みチケットに気づかないまま次へ進む。
  if ("partial" in parsed) {
    return parsed;
  }
  const said = firstLine(ran.stderr) || firstLine(ran.stdout);
  return { ok: false, error: said === "" ? `ccnavi user agree --yes の出力を読み取れません（${parsed.error}）` : said };
}

/**
 * 残った指摘の 2 本に付ける期限（ミリ秒）。sh がホストからスレッドを取ってくるので承認より長い。
 * 置いている間オーバーレイは閉じられないので、返らないとパネルが戻らなくなる
 */
const DECIDE_TIMEOUT_MS = 120_000;

/**
 * 残った指摘とダイジェストを見る（`ccnavi-review.sh decide <N> --preview`）。何も置かない。
 * ホストを読むのは sh（実行ファイルはネットワークに出ない）なので、実行ファイルではなく sh を走らせる。
 * cwd は親のワークツリー（sh はそこを親として実行ファイルに渡す）
 */
export async function runDecidePreview(
  root: string,
  shell: string,
  tree: string,
  phase: number,
  expect: DecideExpect,
): Promise<RunResult<DecidePreview>> {
  const ran = await runScript(shell, root, REVIEW_SCRIPT, tree, decidePreviewArgs(phase), DECIDE_TIMEOUT_MS);
  if (ran.killed) {
    return { ok: false, error: `${cutOff("ccnavi-review.sh decide --preview", DECIDE_TIMEOUT_MS)}。反映していません` };
  }
  if (ran.code !== 0) {
    return { ok: false, error: `ccnavi-review.sh decide --preview が失敗しました: ${ran.stderr.trim()}` };
  }
  // 頼んだ親・フェーズ・MR の番号と照らして厳しく読む。読めなければ呼び手は件数を出さない
  const parsed = parseDecidePreview(ran.stdout, expect);
  return parsed.ok ? { ok: true, value: parsed.value } : { ok: false, error: parsed.error };
}

/**
 * ユーザが選んだ行き先を置く（`ccnavi-user-decide.sh decide <N> --choices <JSON> --digest <ダイジェスト>`）。
 * 見せた指摘と今の指摘が違えば、実行ファイルは何も置かず `mismatch` を返す
 */
export async function runDecideYes(
  root: string,
  setting: string,
  shell: string,
  tree: string,
  phase: number,
  choices: Readonly<Record<string, string>>,
  digest: string,
): Promise<DecideOutcome> {
  const refused = await checkUserEntry(root, setting, "ccnavi-user-decide.sh decide");
  if (refused !== undefined) {
    return { ok: false, error: refused };
  }
  const ran = await runScript(shell, root, USER_DECIDE_SCRIPT, tree, decideArgs(phase, choices, digest), DECIDE_TIMEOUT_MS);
  if (ran.killed) {
    return {
      ok: false,
      error:
        `${cutOff("ccnavi-user-decide.sh decide", DECIDE_TIMEOUT_MS)}。` +
        "反映できたか分かりません。チケット管理画面を更新して、フェーズの状態を確認してください",
    };
  }
  const parsed = parseDecideResult(ran.stdout.trim().split("\n").pop() ?? "");
  if (parsed.ok || "mismatch" in parsed) {
    return parsed;
  }
  const said = firstLine(ran.stderr) || firstLine(ran.stdout);
  return { ok: false, error: said === "" ? `ccnavi-user-decide.sh decide の出力を読み取れません（${parsed.error}）` : said };
}

/**
 * マージ状況の 2 本に付ける期限（ミリ秒）。1 回ごとに付ける。`mr-state` はホストに聞くので、curl の上限（30 秒）に余裕を足す。
 * gh / glab の経路には sh の側の上限が無いので、ここで打ち切る（期限切れは拡張の側だけの種類 `timeout`）
 */
const MR_STATE_TIMEOUT_MS = 45_000;
const RETIRED_TIMEOUT_MS = 30_000;

/**
 * 片付けの記録を持つ親の一覧を読む（`ccnavi sync retired`）。ネットワークにも git にも触れない読み取り。
 * 失敗・期限切れ・形式の異常は、「ありません」とは言わず `ok: false` で返す（行を黙って捨てない）
 */
export async function runSyncRetired(root: string, setting: string): Promise<RetiredParse> {
  const launcher = findLauncher(root, setting);
  if (launcher === undefined) {
    return { ok: false, error: `一覧を取れない（${NOT_FOUND}）` };
  }
  const ran = await run(launcher, root, syncRetiredArgs(), RETIRED_TIMEOUT_MS);
  return parseRetired(ran);
}

/**
 * 1 件の MR の状況を聞く（`ccnavi-review.sh mr-state <番号>`。読むだけ）。cwd は片付けの記録が指すリポジトリ。
 * 答えは `interpretMrState` が厳しく検証する。実行できなかった（シェルが無いなど）・期限切れは、
 * その親の「確認できない」にとどめる（ほかの親は続ける）
 */
export async function runMrState(root: string, shell: string, cwd: string, number: number): Promise<MrStateResult> {
  const ran = await runScript(shell, root, REVIEW_SCRIPT, cwd, mrStateArgs(number), MR_STATE_TIMEOUT_MS);
  return interpretMrState(ran, number);
}

/**
 * 承認した子のワークツリーを 1 本作る（`ccnavi-git.sh worktree add <path> -b <子> <base>`）。
 * 作れたら空文字、作れなければ理由（標準エラーの 1 行目など）を返す。ワークスペースルートから打つ。
 */
export async function runChildWorktreeAdd(shell: string, root: string, w: ApproveWorktree): Promise<string> {
  const args = ["worktree", "add", w.path, "-b", w.branch, w.base];
  const ran = await spawn(shell, [toPosixPath(path.join(root, GIT_SCRIPT)), ...args], root, WORKTREE_TIMEOUT_MS);
  if (ran.killed) {
    return "時間内に終わらなかった";
  }
  if (ran.code === 0) {
    return "";
  }
  return failureReason(ran.stderr, ran.stdout) || `終了コード ${ran.code}`;
}

/**
 * sh のスクリプト（`.ccnavi/scripts/` の下）を子プロセスで走らせる。パスは `/` 区切りにする
 * （Windows の Git Bash は `C:/…` を読める）
 */
function runScript(
  shell: string,
  root: string,
  script: string,
  cwd: string,
  args: readonly string[],
  timeout: number,
): Promise<Ran> {
  return spawn(shell, [toPosixPath(path.join(root, script)), ...args], cwd, timeout);
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
    return { ok: false, error: `ccnavi --test --json が失敗しました: ${ran.stderr}` };
  }
  const parsed = parseTestJson(ran.stdout);
  return parsed.ok ? { ok: true, value: parsed.value } : { ok: false, error: parsed.error };
}

/** 期待する判定をぜんぶ判定に掛ける */
export async function runExpectations(
  root: string,
  setting: string,
  rules: RulesOverride,
  expectationsPath: string,
): Promise<RunResult<ExpectationsJson>> {
  const launcher = findLauncher(root, setting);
  if (launcher === undefined) {
    return { ok: false, error: NOT_FOUND };
  }
  const ran = await run(launcher, root, [
    ...overrideArgs(rules),
    ...RULES_ONLY,
    "--test-expectations",
    expectationsPath,
    "--json",
  ], DIAGNOSE_TIMEOUT_MS);
  if (ran.killed) {
    return { ok: false, error: cutOff("ccnavi --test-expectations --json", DIAGNOSE_TIMEOUT_MS) };
  }
  if (ran.code !== 0) {
    return { ok: false, error: `ccnavi --test-expectations --json が失敗しました: ${ran.stderr}` };
  }
  const parsed = parseExpectationsJson(ran.stdout);
  return parsed.ok ? { ok: true, value: parsed.value } : { ok: false, error: parsed.error };
}

/**
 * 記録からルールの候補を起こす（`--suggest --json`、docs/requirements/json-shapes.md「候補の JSON」）。読むのは保存済みの
 * ルールと記録で、編集中の内容は渡さない。候補は実行ファイルが `--lint` と期待する判定の確認で確かめたものだけ。
 * 記録を読むので `--log ""` は付けない（state の置き場は実行ファイルが外す）。
 */
export async function runSuggest(root: string, setting: string): Promise<RunResult<SuggestJson>> {
  const launcher = findLauncher(root, setting);
  if (launcher === undefined) {
    return { ok: false, error: NOT_FOUND };
  }
  const ran = await run(launcher, root, ["--suggest", "--json"], DIAGNOSE_TIMEOUT_MS);
  if (ran.killed) {
    return { ok: false, error: cutOff("ccnavi --suggest --json", DIAGNOSE_TIMEOUT_MS) };
  }
  if (ran.code !== 0) {
    return { ok: false, error: `ccnavi --suggest --json が失敗しました: ${firstLine(ran.stderr)}` };
  }
  const parsed = parseSuggestJson(ran.stdout);
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
    return { ok: false, error: `ccnavi --lint が失敗しました: ${ran.stderr}` };
  }
  return { ok: true, value: { ok: ran.code === 0, report: ran.stdout.trim() } };
}

/**
 * 実運用の設定をそのまま検証し、苦情を JSON で受ける（docs/requirements/json-shapes.md「lint の JSON」）。
 * プロジェクト管理画面が、置き場とプロジェクトごとの warn を拾うために呼ぶ。
 * error があると終了コードが 1 になるが、それは JSON の中身で分かるので失敗にしない。
 */
/** 出力の最初の空でない行。画面の 1 行に収める用途で、全量は要らない */
function firstLine(text: string): string {
  return text.split(/\r?\n/).map((l) => l.trim()).find((l) => l !== "") ?? "";
}

export async function runLintJson(root: string, setting: string): Promise<RunResult<LintJson>> {
  return lintJson(root, setting, [], "ccnavi --lint --json");
}

/**
 * 子チケットのフロー 1 本を実行ファイルに確かめさせる（`--lint --json --flow <パス>`）。
 * フロー編集画面が、開くときと保存の前に編集中の本文を一時ファイルに書いて渡す。読み手と検査は
 * SubagentStart と同じもので、答えは `(flow)` の苦情（`problemsOfFlow`）。
 * 先に `--version --json` で `--flow` を知っているかを見る。知らない、または確かめられないときは
 * 失敗にする（開かない・保存しない側）。
 */
export async function runFlowLint(root: string, setting: string, flowPath: string): Promise<RunResult<LintJson>> {
  const what = "ccnavi --lint --json --flow";
  const probe = await probeVersion(root, setting);
  if (probe === undefined) {
    return { ok: false, error: NOT_FOUND };
  }
  const refused = missingFlags(probe, ["--flow"], what, hasSource(root));
  if (refused !== undefined) {
    return { ok: false, error: refused };
  }
  return lintJson(root, setting, ["--flow", flowPath], what);
}

/**
 * 実行ファイルに版を聞く（`--version --json`）。実行ファイルが見つからなければ undefined。
 */
export async function probeVersion(root: string, setting: string): Promise<VersionProbe | undefined> {
  const launcher = findLauncher(root, setting);
  if (launcher === undefined) {
    return undefined;
  }
  const ran = await run(launcher, root, ["--version", "--json"], DIAGNOSE_TIMEOUT_MS);
  if (ran.killed) {
    return { kind: "failed", error: cutOff("ccnavi --version --json", DIAGNOSE_TIMEOUT_MS) };
  }
  if (ran.code !== 0) {
    return { kind: "failed", error: `ccnavi --version --json が失敗しました: ${firstLine(ran.stderr)}` };
  }
  const parsed = parseVersionJson(ran.stdout);
  return parsed.ok ? { kind: "ok", info: parsed.value } : { kind: "failed", error: parsed.error };
}

/**
 * ワークスペースが ccnavi のリポジトリ（組み立てる元の build.py とソースがある）か。
 * 実行ファイルが古いときの直し方が、組み立て直しか配り直しかで分かれる
 */
export function hasSource(root: string): boolean {
  return fs.existsSync(path.join(root, "build.py")) && fs.existsSync(path.join(root, "src", "ccnavi", "__main__.py"));
}

async function lintJson(root: string, setting: string, extra: readonly string[], what: string): Promise<RunResult<LintJson>> {
  const launcher = findLauncher(root, setting);
  if (launcher === undefined) {
    return { ok: false, error: NOT_FOUND };
  }
  const ran = await run(launcher, root, ["--lint", "--json", ...extra], DIAGNOSE_TIMEOUT_MS);
  if (ran.killed) {
    return { ok: false, error: cutOff(what, DIAGNOSE_TIMEOUT_MS) };
  }
  if (ran.code < 0 || ran.code > 1) {
    return { ok: false, error: `${what} が失敗しました: ${firstLine(ran.stderr)}` };
  }
  const parsed = parseLintJson(ran.stdout);
  if (parsed.ok) {
    return { ok: true, value: parsed.value };
  }
  // 設定の不備などで実行ファイルが JSON ではなくユーザ向けの文面を出したときは、JSON.parse の苦情より
  // その文面（先頭行）のほうが原因を指しているので、そちらを見せる
  const said = firstLine(ran.stdout) || firstLine(ran.stderr);
  return { ok: false, error: said === "" ? `${what} の出力を読み込めません（${parsed.error}）` : `${what} の出力: ${said}` };
}

/**
 * 親子のチケットが write-and-push の対象か（`ccnavi write-and-push family <親>` の `target`。`yes` / `no` / `stop`）。答えなければ空文字。
 * フローの保存の後、承認の push を送るかを決めるのに使う。
 */
export async function runWriteAndPushTarget(root: string, setting: string, parent: string): Promise<string> {
  const launcher = findLauncher(root, setting);
  if (launcher === undefined) {
    return "";
  }
  const ran = await run(launcher, root, ["write-and-push", "family", parent], EXPLAIN_TIMEOUT_MS);
  return ran.code === 0 ? writeAndPushTargetOf(ran.stdout) : "";
}

/** 計画編集タブの 2 本が求めるフラグ。知らない古い実行ファイルは、確かめられないとして失敗にする */
const PLAN_ORDER_FLAGS = ["--plan-order", "--order", "--expect", "--write"] as const;

/** テンプレート管理画面が求めるフラグ。知らない古い実行ファイルは、確かめられないとして失敗にする */
const TEMPLATES_FLAGS = ["--templates", "--show"] as const;

/** テンプレートの編集が求めるフラグ。知らない古い実行ファイルは、読ませて確かめられないので編集を始めない */
const TEMPLATE_EDIT_FLAGS = ["--templates", "--check-file"] as const;

/**
 * 取り込み待ち画面が求める機能（`ccnavi user handoff` を実行ファイルが持つ）。持たない古い実行ファイルは `user handoff` を知らず、
 * argparse の苦情（不明瞭な断り）で返すので、画面を開くときに見て「古い版です」と言い、画面を無効にする
 */
const HANDOFF_FEATURES = ["handoff"] as const;

/**
 * ユーザ専用の入口（`ccnavi user <操作>`）を実行ファイルが持つことを示す機能名。持たない古い実行ファイルは
 * `user` を知らず、argparse の苦情（不明瞭な断り）で返すので、打つ前に見て「古い版です」と言う
 */
const USER_ENTRY_FEATURES = ["user-entry"] as const;

/**
 * ユーザ専用の操作（承認・対応方針）を打つ前に、実行ファイルが `user-entry` を持つかを聞く。
 * 知っていれば undefined、知らなければ（確かめられないときも）理由。起動時の互換の版の比べ（`warnVersionSkew`）は
 * 知らせるだけで止めないので、止めるのはここ。`what` は使おうとしたもの
 */
export async function checkUserEntry(root: string, setting: string, what: string): Promise<string | undefined> {
  const probe = await probeVersion(root, setting);
  if (probe === undefined) {
    return NOT_FOUND;
  }
  return missingFeatures(probe, USER_ENTRY_FEATURES, what, hasSource(root));
}

/**
 * 実行ファイルが `--plan-order` を知っているかを聞く（`--version --json`）。知っていれば undefined、知らなければ理由。
 * 計画編集タブは読み込むたびに 1 回聞く（線を変えるたびには聞かない。往復が倍になるため）
 */
export async function checkPlanOrder(root: string, setting: string, what = "計画編集タブ"): Promise<string | undefined> {
  const probe = await probeVersion(root, setting);
  if (probe === undefined) {
    return NOT_FOUND;
  }
  return missingFlags(probe, PLAN_ORDER_FLAGS, what, hasSource(root));
}

/**
 * 親の提案の計画を、振り直した答えで読む（`--plan-order <親> --json [--order <JSON>]`）。何も書かない。
 * `order` は元の番号での直接の先行で、空なら提案のまま。番号の振り直しと検査は実行ファイルが答える。
 * `--plan-order` は `user` の外の独立したオプションなので、組み込みの止めにも端末の確かめにも当たらない。
 * 失敗（実行ファイルが無い・非 0 終了・JSON でない）は理由の文にして返し、画面は壊さない。古い実行ファイルかは
 * 呼び手が先に `checkPlanOrder` で聞く
 */
export async function runPlanOrder(root: string, setting: string, parent: string, order: PlanOrder = {}): Promise<RunResult<PlanOrderAnswer>> {
  const what = "ccnavi --plan-order --json";
  const launcher = findLauncher(root, setting);
  if (launcher === undefined) {
    return { ok: false, error: NOT_FOUND };
  }
  const ran = await run(launcher, root, planOrderArgs(parent, order), DIAGNOSE_TIMEOUT_MS);
  if (ran.killed) {
    return { ok: false, error: cutOff(what, DIAGNOSE_TIMEOUT_MS) };
  }
  if (ran.code !== 0) {
    return { ok: false, error: `${what} が失敗しました: ${ran.stderr.trim() || firstLine(ran.stdout)}` };
  }
  const parsed = parsePlanOrder(ran.stdout);
  return parsed.ok ? { ok: true, value: parsed.value } : { ok: false, error: `${what} の出力を読み込めません（${parsed.error}）` };
}

/**
 * 計画編集タブの保存（`--plan-order <親> --order <JSON> --expect <ハッシュ> --write --json`）。
 * 書くのは実行ファイルで、拡張はファイルを書かない。`expect` は開いたときの `source_sha` で、親か子の提案が外で
 * 変わっていれば実行ファイルは何も書かない（答えの `source_sha` が今の値になるので `changed` にする）。
 * 書かなかった理由は実行ファイルの標準エラーの文のまま返す
 */
export async function runPlanOrderWrite(root: string, setting: string, parent: string, order: PlanOrder, expect: string): Promise<PlanOrderWrite> {
  const what = "ccnavi --plan-order --write";
  const launcher = findLauncher(root, setting);
  if (launcher === undefined) {
    return { kind: "error", error: NOT_FOUND };
  }
  const ran = await run(launcher, root, planOrderWriteArgs(parent, order, expect), DIAGNOSE_TIMEOUT_MS);
  if (ran.killed) {
    return {
      kind: "error",
      error: `${cutOff(what, DIAGNOSE_TIMEOUT_MS)}。提案を変更したかは分かりません。再読込して確認してください`,
    };
  }
  const parsed = parsePlanOrder(ran.stdout);
  if (!parsed.ok) {
    const said = ran.stderr.trim() || firstLine(ran.stdout);
    return { kind: "error", error: `${what} が失敗しました: ${said === "" ? parsed.error : said}` };
  }
  const answer = parsed.value;
  if (answer.written === true && ran.code === 0) {
    return { kind: "written", answer };
  }
  if (answer.source_sha !== expect) {
    return { kind: "changed", answer };
  }
  if (ran.code === 0) {
    return { kind: "unchanged", answer };
  }
  return { kind: "refused", answer, reason: ran.stderr.trim() || "ccnavi が変更しませんでした" };
}

/**
 * 実行ファイルが `--templates` と `--show` を知っているかを聞く（`--version --json`）。知っていれば undefined、知らなければ理由。
 * テンプレート管理画面は読み込むたびに 1 回聞く。
 */
export async function checkTemplates(root: string, setting: string): Promise<string | undefined> {
  const probe = await probeVersion(root, setting);
  if (probe === undefined) {
    return NOT_FOUND;
  }
  return missingFlags(probe, TEMPLATES_FLAGS, "テンプレート管理画面", hasSource(root));
}

/**
 * 実行ファイルが `--templates --check-file` を知っているかを聞く（`--version --json`）。知っていれば undefined、知らなければ理由。
 * テンプレートの編集を始めるたびに 1 回聞く。知らなければ編集の入口を無効にして、この理由を言う。
 */
export async function checkTemplateEdit(root: string, setting: string): Promise<string | undefined> {
  const probe = await probeVersion(root, setting);
  if (probe === undefined) {
    return NOT_FOUND;
  }
  return missingFlags(probe, TEMPLATE_EDIT_FLAGS, "テンプレートの編集", hasSource(root));
}

/**
 * 実行ファイルが `user handoff` を持つか（`features` に `handoff`）を聞く。持っていれば undefined、持たなければ理由
 * （取り込み待ち画面を無効にして、この理由を言う）。取り込み待ち画面は読み込むたびに 1 回聞く。
 */
export async function checkHandoff(root: string, setting: string): Promise<string | undefined> {
  const probe = await probeVersion(root, setting);
  if (probe === undefined) {
    return NOT_FOUND;
  }
  return missingFeatures(probe, HANDOFF_FEATURES, "取り込み待ち画面", hasSource(root));
}

/** 失敗の答え（`{ok:false,error}`）の error。読めなければ空 */
function errorOfAnswer(stdout: string): string {
  let raw: unknown;
  try {
    raw = JSON.parse(stdout);
  } catch {
    return "";
  }
  if (typeof raw === "object" && raw !== null && "error" in raw && typeof raw.error === "string") {
    return raw.error;
  }
  return "";
}

/** 非 0 終了の理由。失敗の答えの error を優先し、無ければ標準エラーの最初の行、それも無ければ標準出力の最初の行 */
function failureOf(ran: Ran): string {
  return errorOfAnswer(ran.stdout) || firstLine(ran.stderr) || firstLine(ran.stdout);
}

/** テンプレートの一覧を読む（`--templates --json --layer <名前>`）。何も書かない */
export async function runTemplates(root: string, setting: string, layer: string): Promise<RunResult<TemplatesJson>> {
  const what = "ccnavi --templates --json";
  const launcher = findLauncher(root, setting);
  if (launcher === undefined) {
    return { ok: false, error: NOT_FOUND };
  }
  const ran = await run(launcher, root, templatesArgs(layer), DIAGNOSE_TIMEOUT_MS);
  if (ran.killed) {
    return { ok: false, error: cutOff(what, DIAGNOSE_TIMEOUT_MS) };
  }
  if (ran.code !== 0) {
    return { ok: false, error: `${what} が失敗しました: ${failureOf(ran)}` };
  }
  const parsed = parseTemplates(ran.stdout);
  return parsed.ok ? { ok: true, value: parsed.value } : { ok: false, error: `${what} の出力を読み込めません（${parsed.error}）` };
}

/** 1 件の本文を読む（`--templates --show <名前> --layer <名前> --json`）。何も書かない */
export async function runTemplateShow(root: string, setting: string, name: string, layer: string): Promise<RunResult<TemplateDetail>> {
  const what = "ccnavi --templates --show --json";
  const launcher = findLauncher(root, setting);
  if (launcher === undefined) {
    return { ok: false, error: NOT_FOUND };
  }
  const ran = await run(launcher, root, templatesShowArgs(name, layer), DIAGNOSE_TIMEOUT_MS);
  if (ran.killed) {
    return { ok: false, error: cutOff(what, DIAGNOSE_TIMEOUT_MS) };
  }
  if (ran.code !== 0) {
    return { ok: false, error: `${what} が失敗しました: ${failureOf(ran)}` };
  }
  const parsed = parseTemplateShow(ran.stdout);
  return parsed.ok ? { ok: true, value: parsed.value } : { ok: false, error: `${what} の出力を読み込めません（${parsed.error}）` };
}

/** 編集中の 1 本を実行ファイルに読ませる（`--templates --check-file <パス> --layer <名前> --json`）。何も書かない */
export async function runTemplateCheck(root: string, setting: string, file: string, layer: string): Promise<RunResult<TemplateCheck>> {
  const what = "ccnavi --templates --check-file --json";
  const launcher = findLauncher(root, setting);
  if (launcher === undefined) {
    return { ok: false, error: NOT_FOUND };
  }
  const ran = await run(launcher, root, templatesCheckArgs(file, layer), DIAGNOSE_TIMEOUT_MS);
  if (ran.killed) {
    return { ok: false, error: cutOff(what, DIAGNOSE_TIMEOUT_MS) };
  }
  if (ran.code !== 0) {
    return { ok: false, error: `${what} が失敗しました: ${failureOf(ran)}` };
  }
  const parsed = parseTemplateCheck(ran.stdout);
  return parsed.ok ? { ok: true, value: parsed.value } : { ok: false, error: `${what} の出力を読み込めません（${parsed.error}）` };
}

/** 下書きを作った答え */
export interface DraftMade {
  readonly name: string;
  /** ワークスペースルートからの相対 */
  readonly path: string;
  readonly notes: readonly string[];
}

/** 完了した親から下書きを作る（`--templates --draft <親> --json`）。失敗は理由の文にして返す */
export async function runTemplateDraft(root: string, setting: string, parent: string): Promise<RunResult<DraftMade>> {
  const what = "ccnavi --templates --draft --json";
  const launcher = findLauncher(root, setting);
  if (launcher === undefined) {
    return { ok: false, error: NOT_FOUND };
  }
  const ran = await run(launcher, root, templatesDraftArgs(parent), DIAGNOSE_TIMEOUT_MS);
  if (ran.killed) {
    return { ok: false, error: cutOff(what, DIAGNOSE_TIMEOUT_MS) };
  }
  let raw: unknown;
  try {
    raw = JSON.parse(ran.stdout);
  } catch {
    return { ok: false, error: `${what} が失敗しました: ${failureOf(ran)}` };
  }
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    return { ok: false, error: `${what} の出力を読み込めません` };
  }
  const r = raw as Record<string, unknown>;
  if (r.ok !== true) {
    return { ok: false, error: typeof r.error === "string" && r.error !== "" ? r.error : `${what} が失敗しました` };
  }
  return {
    ok: true,
    value: {
      name: typeof r.name === "string" ? r.name : "",
      path: typeof r.path === "string" ? r.path : "",
      notes: Array.isArray(r.notes) ? r.notes.filter((n): n is string => typeof n === "string") : [],
    },
  };
}

/**
 * `wip/handoff/` の下書きの一覧を見る（`user handoff [<パス>…] --json`）。何も書かない。
 * `workspace` は実行ファイルを探すワークスペースのルート、`tree` は見るツリーのルート（`--root` に渡す）。
 * ワークスペースのルートのツリーなら二つは同じ。実行ファイルは `workspace` の側で探す
 * （プロジェクトやワークツリーには `dist/ccnavi/` が無いため）。
 * 見るだけの答えは、対象に error や conflict があっても終了コード 0 で返る。
 */
export async function runHandoff(workspace: string, setting: string, tree: string, paths: readonly string[] = []): Promise<RunResult<HandoffList>> {
  const what = "ccnavi user handoff --json";
  const launcher = findLauncher(workspace, setting);
  if (launcher === undefined) {
    return { ok: false, error: NOT_FOUND };
  }
  const ran = await run(launcher, tree, handoffArgs(paths), DIAGNOSE_TIMEOUT_MS);
  if (ran.killed) {
    return { ok: false, error: cutOff(what, DIAGNOSE_TIMEOUT_MS) };
  }
  const parsed = parseHandoffList(ran.stdout);
  if (parsed.ok) {
    return { ok: true, value: parsed.value };
  }
  if (ran.code !== 0) {
    return { ok: false, error: `${what} が失敗しました: ${failureOf(ran)}` };
  }
  return { ok: false, error: `${what} の出力を読み込めません（${parsed.error}）` };
}

/**
 * 取り込みの実行（`user handoff <パス…> --yes --expect <ハッシュ> --via board --json`）。書くのは実行ファイルで、
 * 拡張はファイルを書かない。失敗は `{ok:false, error}` の結果で返し、投げない。
 * 期限切れのときは、取り込めたかが分からないことを文に足す。
 */
export async function runHandoffYes(workspace: string, setting: string, tree: string, paths: readonly string[], expect: string): Promise<HandoffResult> {
  const what = "ccnavi user handoff --yes --json";
  const launcher = findLauncher(workspace, setting);
  if (launcher === undefined) {
    return { ok: false, error: NOT_FOUND };
  }
  const ran = await run(launcher, tree, handoffYesArgs(paths, expect), DIAGNOSE_TIMEOUT_MS);
  if (ran.killed) {
    return { ok: false, error: `${cutOff(what, DIAGNOSE_TIMEOUT_MS)}（取り込めたかは分かりません。一覧を再読み込みして確認してください）` };
  }
  const parsed = parseHandoffResult(ran.stdout);
  if (parsed.ok) {
    return parsed.value;
  }
  if (ran.code !== 0) {
    return { ok: false, error: `${what} が失敗しました: ${failureOf(ran)}` };
  }
  return { ok: false, error: `${what} の出力を読み込めません（${parsed.error}）` };
}
