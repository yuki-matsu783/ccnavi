/**
 * リスク管理画面の Webview パネル。生成・更新・破棄、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。README の手動確認の手順で確かめる。
 *
 * 画面は React（`src/webview/risk/`）で、ここが渡すのは「いま何を見せるか」（`RiskData`）だけ。
 * 渡し方は `core/screen-host.ts` の `retainedHost` が決める。この画面は編集の途中を持つので
 * `retainContextWhenHidden` が真で、入れ物（HTML）は 1 度しか入らない。入れ直すと画面が作り直され、打ちかけの編集が消えるため。
 * 中身を渡すのは、画面の編集を捨ててよいときだけ（ユーザが「更新」を押した、保存や作成が通った）。
 * ファイルが外で変わっただけのときは `changed` を送り、捨てるかどうかはユーザが決める。
 *
 * 対象は 3 種。共通の設定の配点（`.ccnavi/common/risks.yml`。場所は固定）、
 * ワークスペースの設定の配点（既定 `.ccnavi/config/risks.yml`）、プロジェクト 1 つの設定の配点
 * （既定 `projects/<名前>/.ccnavi/config/risks.yml`）。タブは 1 枚だけで、別の対象を開くとそのタブの
 * 中身を入れ替える（未保存の変更があれば、破棄して切り替えるかを聞く）。
 * 設定ファイルの場所は実行ファイルが解いたもの（`--explain --json` の `layers[].risk`）を使い、拡張は組まない。
 * 判定は共通の設定と、親の `project:` が指す設定の和で行う。編集する 1 本とは別に、読み取り専用の足し算
 * （共通の設定 + ワークスペース、共通の設定 + 各プロジェクト。`sums[]`）を出す。足し算は実行ファイルが出した結果で、拡張は合成しない。
 *
 * チケット制御が disable のワークスペースでは開かない。配点は子チケットを閉じるときにしか
 * 読まれないので、disable の間は何も動かさない。入口（サイドパネル・コマンドパレット）も同じ鍵で隠れる。
 *
 * 検証は実行ファイルに任せる。編集中の内容は一時ファイルに書き、`--lint` に渡す。共通の設定は `--risk <パス>`、ワークスペースの設定は `--project-risk-file self=<パス>`、
 * プロジェクトは `--project-risk-file <名前>=<パス>`（レイヤーの配点は共通の設定と合わせて検証される）。
 * 保存は、検証（`--lint`）を通り、作業中のチケットが無く（共通の設定とワークスペースの設定はどのツリーでも、
 * プロジェクトの設定はそのプロジェクトの分だけ。配点は子を閉じるときに読まれるので、走っている最中に変えない）、
 * ファイルが外で変わっていないときだけ行う。
 * ファイルが無いのは「設定が無い」正常な状態。共通の設定にもワークスペースの設定にも無いときは、組み込みの配点を読み取り専用で見せ、
 * 「作る」で同じ値のファイルを書き出してから直す。それ以外（プロジェクトの設定など）は空として出し、欄は触れて、
 * 検証を通った最初の保存でファイルを作る。
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import { followAppearance, postAppearance, readAppearance } from "./appearance.js";
import { loadBoard, runLint, type LintOverride } from "./ccnavi.js";
import { LAYER_SELF, projectLayer, selfLayer } from "./core/layers.js";
import { loadingText } from "./core/loading-render.js";
import { lockFromBoard, lockFromError, type Lock } from "./core/lock.js";
import { asRiskForm, BUILTIN_RISK_TEXT, readRisk, type RiskDocument } from "./core/risk-doc.js";
import { renderRiskPage } from "./core/risk-render.js";
import type { RiskData, RiskForm, RiskMessage, ToRisk } from "./core/risk-view.js";
import { retainedHost, type ScreenHost } from "./core/screen-host.js";
import type { RiskTarget } from "./core/screens.js";
import { riskSums, type RiskSum } from "./core/sums.js";
import { targetOptions, type TargetOption } from "./core/targets.js";
import { WATCH_PATTERNS } from "./core/watch.js";
import { showLoading } from "./loading.js";
import * as diaglog from "./log.js";
import { requireTickets } from "./ticket-control.js";
import { markTourSeen, tourSeen } from "./tour.js";
import { webviewScript, webviewStyle } from "./webview-asset.js";

const DEBOUNCE_MS = 120;
const DEFAULT_RISK = ".ccnavi/common/risks.yml";
/** 画面の名前。バンドルのパスは `src/webview/<名前>/main.tsx` → `out/webview/<名前>.js`、`style.css` → `<名前>.css` */
const SCREEN = "risk";
/** 自分の保存で監視が反応するのを、この間だけ「ファイルの変更を検知しました」と言わない */
const OWN_WRITE_GRACE_MS = 1500;

interface Loaded {
  /** 読んだ対象。往復の間に切り替わったかを `stale` が見る */
  readonly target: RiskTarget;
  /** ファイルの本文。無ければ、組み込みの配点を見せるときはその本文、そうでなければ空 */
  readonly text: string;
  readonly exists: boolean;
  /** 組み込みの配点を読み取り専用で見せている（「作る」だけができる） */
  readonly builtin: boolean;
  /** 無いときは 0 */
  readonly mtimeMs: number;
  readonly doc: RiskDocument;
  readonly riskPath: string;
  /** ワークスペースルートからの相対で見せるパス */
  readonly riskRel: string;
  /** 上部に出す注意。実行ファイルがこの設定を読めていない、など */
  readonly notices: readonly string[];
  /** 切り替えられる対象（共通・ワークスペース・設定のあるプロジェクト） */
  readonly targets: readonly TargetOption[];
  /** 読み取り専用の足し算（実行ファイルの `sums[]`） */
  readonly sums: readonly RiskSum[];
}

interface PanelState {
  /** いま見せている対象。別の対象を開くと入れ替わる（タブは 1 枚） */
  target: RiskTarget;
  readonly panel: vscode.WebviewPanel;
  readonly folder: vscode.WorkspaceFolder;
  readonly tmpDir: string;
  readonly host: ScreenHost<RiskData>;
  /** 編集対象と設定ファイルの監視。対象のパスが変わるので、再読込のたびに張り直す */
  fileWatchers: vscode.FileSystemWatcher[];
  /** チケットの置き場の監視。開いている間ずっと同じ */
  watchers: vscode.FileSystemWatcher[];
  timer?: NodeJS.Timeout;
  lockTimer?: NodeJS.Timeout;
  loaded?: Loaded;
  /** 読み直せなかった理由。`loaded` と排他で、どちらかは必ず入っている */
  error?: string;
  lock: Lock;
  /** 「ファイルの変更を検知しました」を出したまま、まだ読み直していない。表に戻ったときに送り直す */
  changedPending: boolean;
  wroteAt: number;
  /** 画面に未保存の変更があるか（画面が `dirty` で知らせる）。別の対象へ切り替えるときに聞くかを決める */
  dirty: boolean;
  /**
   * 読み直しの番号。読むたびに 1 つ進め、読み終えたときに変わっていたら捨てる。
   * 設定ファイルの場所を実行ファイルに聞く間に別の対象へ切り替わると、前の対象の答えが後から届くため
   */
  seq: number;
  /** 「破棄して切り替える？」を出している間は真。重ねて開かれても 2 枚目の問いを出さない */
  asking: boolean;
}

/** 開いているパネル。種類ごとに 1 枚 */
let state: PanelState | undefined;

function sameTarget(a: RiskTarget, b: RiskTarget): boolean {
  return a.kind === b.kind && (a.kind !== "project" || (b.kind === "project" && a.name === b.name));
}

/** 保存を止めるチケットを絞るプロジェクト。共通の設定とワークスペースの設定は絞らない（どのツリーの子を閉じるときにも読まれる） */
function projectOf(target: RiskTarget): string | undefined {
  return target.kind === "project" ? target.name : undefined;
}

function titleOf(target: RiskTarget): string {
  switch (target.kind) {
    case "workspace":
      return "ccnavi リスク管理";
    case "self":
      return "ccnavi リスク管理: ワークスペース";
    case "project":
      return `ccnavi リスク管理: プロジェクト ${target.name}`;
  }
}

/** 読み込み中の一言で「何を」読んでいるか */
function whatOf(target: RiskTarget): string {
  switch (target.kind) {
    case "workspace":
      return "リスク";
    case "self":
      return "ワークスペースの設定のリスク";
    case "project":
      return `${target.name} のリスク`;
  }
}

/** 開いている対象の欄の値（`targets.ts` の `kind` と `name`） */
function currentKey(target: RiskTarget): { kind: string; name: string } {
  return { kind: target.kind, name: target.kind === "project" ? target.name : "" };
}

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

/** `ccnaviBoard.openRisk` の本体。引数なしは共通の設定の配点 */
export async function openRisk(target: RiskTarget = { kind: "workspace" }): Promise<void> {
  if (!requireTickets("リスク管理画面")) {
    return;
  }
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    vscode.window.showInformationMessage("ワークスペースが開かれていないため、リスク管理画面を表示できません");
    return;
  }
  if (state !== undefined) {
    state.panel.reveal(state.panel.viewColumn);
    if (!sameTarget(state.target, target)) {
      await switchTarget(state, target);
    }
    return;
  }

  // 画面と CSS はバンドルしたものを読んで流し込む。無ければ開かずに言う（パネルだけ出しても白いまま）
  try {
    webviewScript(SCREEN);
    webviewStyle(SCREEN);
  } catch (error) {
    vscode.window.showErrorMessage(`リスク管理画面を表示できません: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }

  // タブは読む前に作る。設定ファイルの場所を実行ファイルに聞く間に、押しても何も起きないように見えるのを避けるため。
  // `state` を先に設定するので、読んでいる間に押し直しても上の `reveal` に入る。
  // 読めなかったときもタブは閉じず、中にエラーを出す（`reload` の `showError`）
  const panel = vscode.window.createWebviewPanel("ccnaviRisk", titleOf(target), vscode.ViewColumn.One, {
    enableScripts: true,
    enableForms: false,
    localResourceRoots: [],
    // 編集の途中を持つので、タブを裏に回しても捨てない。
    retainContextWhenHidden: true,
  });
  showLoading(panel, titleOf(target), SCREEN, whatOf(target));
  const current: PanelState = {
    target,
    panel,
    folder,
    tmpDir: fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-risk-")),
    host: riskHost(panel, folder.uri.fsPath),
    fileWatchers: [],
    watchers: [],
    lock: lockFromError("確認中…"),
    changedPending: false,
    wroteAt: 0,
    dirty: false,
    seq: 0,
    asking: false,
  };
  state = current;
  followAppearance(panel, current.host);
  registerPanelHandlers(current);
  await reload(current);
}

/**
 * 開いているタブの対象を入れ替える。未保存の変更があれば、破棄して切り替えるかを先に聞く。
 * やめたら何もしない（タブは前面に出たまま、前の対象と編集が残る）。
 *
 * 読み終えるまでは前の対象の中身を消して「読み込み中」を見せ、操作も受けない（`loaded` を空にする）。
 * 前の対象の保存が往復の途中なら、`stale` が捨てる。
 */
async function switchTarget(current: PanelState, target: RiskTarget): Promise<void> {
  if (current.dirty) {
    // 問いを出している間に別の対象を開かれたら、前面に出すだけにする（問いは先に出したものに答えてもらう）
    if (current.asking) {
      return;
    }
    current.asking = true;
    const choice = await vscode.window.showWarningMessage(
      `未保存の変更があります。破棄して「${titleOf(target)}」に切り替えますか？`,
      { modal: true },
      "切り替える",
    );
    current.asking = false;
    // 問いを出している間にパネルを閉じられる。切り替え先が同じになっていることもある（2 度押した）
    if (choice !== "切り替える" || !alive(current) || sameTarget(current.target, target)) {
      return;
    }
  }
  current.target = target;
  current.panel.title = titleOf(target);
  current.loaded = undefined;
  current.error = undefined;
  current.dirty = false;
  current.changedPending = false;
  current.lock = lockFromError("確認中…");
  // 前の対象のファイルの監視は外す。切り替え先が読めたら `reload` が張り直す。読めずにエラーのままなら、
  // 前の対象の変化を「ファイルの変更を検知しました」と拾い続けない
  if (current.timer !== undefined) {
    clearTimeout(current.timer);
    current.timer = undefined;
  }
  for (const watcher of current.fileWatchers) {
    watcher.dispose();
  }
  current.fileWatchers = [];
  current.host.send({ kind: "loading", text: loadingText(whatOf(target)) });
  await reload(current);
}

async function readPage(root: string, target: RiskTarget): Promise<Loaded> {
  const notices: string[] = [];
  // 切り替えの一覧と足し算のために、共通の設定でも実行ファイルに聞く。読めなくても共通の設定は開けるので、一覧と足し算が縮むだけ
  const board = await loadBoard(root, binSetting());
  const targets = targetOptions(board.ok ? board.board : undefined, "workspace", currentKey(target));
  const sums = riskSums(board.ok ? board.board : undefined);
  let riskRel: string;
  let riskPath: string;
  /** 共通の設定とワークスペースの設定の、もう一方の配点のファイル。組み込みの配点を見せるかどうかの判断に使う */
  let other: string | undefined;
  if (target.kind === "workspace") {
    // 共通の設定の場所は `.ccnavi/common/` 固定で、env（`CCNAVI_RISK` など）では動かせない。
    riskRel = DEFAULT_RISK;
    riskPath = resolveIn(root, riskRel);
    const self = board.ok ? selfLayer(board.board) : undefined;
    other = self === undefined || self.risk.path === "" ? undefined : resolveIn(root, self.risk.path);
  } else {
    // 設定ファイルの場所は実行ファイルに聞く。`.ccnavi` から自分で組むと、組み方が実行ファイルと
    // 食い違ったときに、この画面で保存した配点が判定に使われなくなる。答えは元リポジトリの版。
    if (!board.ok) {
      throw new Error(`設定ファイルの場所を実行ファイルから取得できません: ${board.error}`);
    }
    const layer = target.kind === "self" ? selfLayer(board.board) : projectLayer(board.board, target.name);
    if (layer === undefined || layer.risk.path === "") {
      throw new Error(
        target.kind === "self"
          ? "ccnavi の出力にワークスペースの設定がありません"
          : `プロジェクト ${target.name} は設定の対象になっていません（プロジェクトのフォルダの直下に無いか、名前が予約名の common か self です）`,
      );
    }
    riskPath = resolveIn(root, layer.risk.path);
    riskRel = path.relative(root, riskPath).split(path.sep).join("/");
    if (target.kind === "self") {
      other = resolveIn(root, DEFAULT_RISK);
    }
    if (layer.risk.unreadable !== "") {
      notices.push(`実行ファイルはこのファイルを読めず、この設定を空として扱っています（このファイルの配点は 1 件も数えられていません）: ${layer.risk.unreadable}`);
    }
  }
  let text: string;
  let mtimeMs: number;
  let exists: boolean;
  try {
    text = fs.readFileSync(riskPath, "utf8");
    mtimeMs = fs.statSync(riskPath).mtimeMs;
    exists = true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      throw new Error(`配点のファイルを読めません（${riskRel}）: ${(error as Error).message}`);
    }
    // 無いのは不備ではない（「設定が無い」正常な状態）。
    text = "";
    mtimeMs = 0;
    exists = false;
  }
  // 共通の設定にもワークスペースの設定にも無ければ、適用されているのは組み込みの配点。共通かワークスペースの設定を開いたときだけ、
  // それを読み取り専用で見せ、「作る」で同じ値のファイルを書き出させる。もう一方が分からないとき（ボードを読めない）も組み込みを見せる。
  const builtin = !exists && (target.kind === "workspace" || target.kind === "self") && (other === undefined || !fs.existsSync(other));
  if (builtin) {
    text = BUILTIN_RISK_TEXT;
  }
  // 無いときの苦情（version が無い、など）は画面に出さない。無いことは不備ではない。
  const parsed = readRisk(text);
  const doc = exists || builtin ? parsed : { apply: parsed.apply, model: { ...parsed.model, problems: [] } };
  return { target, text, exists, builtin, mtimeMs, doc, riskPath, riskRel, notices, targets, sums };
}

function resolveIn(root: string, filePath: string): string {
  return path.isAbsolute(filePath) ? filePath : path.join(root, filePath);
}

function registerPanelHandlers(current: PanelState): void {
  const { panel, folder } = current;

  panel.webview.onDidReceiveMessage((message: unknown) => {
    void handleMessage(current, asMessage(message));
  });

  // 保持する画面は裏でも生きている（`postMessage` は届く）が、VS Code の文書は同じ型定義の中で
  // 食い違っている（`retainContextWhenHidden` の側は「裏の画面には送れない」と言う）。
  // どちらが正しくても困らないよう、表に戻ったところで、いま出すべき知らせを送り直す。
  // 中身（`data`）は送らない。送ると、裏で打っていた編集がここで消える。
  // 見た目（`appearance`）も同じ扱い。保持しない画面は入れ物から作り直されるので `ready` で渡るが、
  // 保持する画面は作り直されないので、裏にいる間の切り替えが届いていなかったらここでしか拾えない。
  panel.onDidChangeViewState(() => {
    if (!panel.visible || !alive(current)) {
      return;
    }
    current.host.post({ type: "lock", lock: current.lock } satisfies ToRisk);
    postAppearance(current.host);
    if (current.changedPending) {
      current.host.post({ type: "changed" } satisfies ToRisk);
    }
  });

  panel.onDidDispose(() => {
    for (const timer of [current.timer, current.lockTimer]) {
      if (timer !== undefined) {
        clearTimeout(timer);
      }
    }
    for (const watcher of [...current.fileWatchers, ...current.watchers]) {
      watcher.dispose();
    }
    current.fileWatchers = [];
    current.watchers = [];
    try {
      fs.rmSync(current.tmpDir, { recursive: true, force: true });
    } catch {
      // 一時ファイルの片付けに失敗しても画面の仕事には関係ない
    }
    if (state === current) {
      state = undefined;
    }
  });

  watchFiles(current);
  // チケットが動いたら、保存できるかを取り直す。
  for (const pattern of WATCH_PATTERNS) {
    const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(folder, pattern));
    const moved = () => scheduleLock(current);
    watcher.onDidCreate(moved);
    watcher.onDidChange(moved);
    watcher.onDidDelete(moved);
    current.watchers.push(watcher);
  }
}

/**
 * 配点のファイルと設定ファイルが変わったら「ファイルの変更を検知しました」と伝える。自分の保存は除く。
 * 対象のパスは設定で変わるので、再読込のたびに張り直す。絶対パスはワークスペース相対の glob に
 * ならないので、そのディレクトリを起点にする。
 */
function watchFiles(current: PanelState): void {
  for (const watcher of current.fileWatchers) {
    watcher.dispose();
  }
  current.fileWatchers = [];
  const riskRel = current.loaded?.riskRel ?? DEFAULT_RISK;
  const patterns: vscode.RelativePattern[] = [
    patternFor(current.folder, riskRel),
    new vscode.RelativePattern(current.folder, ".claude/settings.json"),
    new vscode.RelativePattern(current.folder, ".claude/settings.local.json"),
  ];
  for (const pattern of patterns) {
    const watcher = vscode.workspace.createFileSystemWatcher(pattern);
    const changed = () => scheduleChanged(current);
    watcher.onDidCreate(changed);
    watcher.onDidChange(changed);
    watcher.onDidDelete(changed);
    current.fileWatchers.push(watcher);
  }
}

function patternFor(folder: vscode.WorkspaceFolder, filePath: string): vscode.RelativePattern {
  if (path.isAbsolute(filePath)) {
    return new vscode.RelativePattern(vscode.Uri.file(path.dirname(filePath)), path.basename(filePath));
  }
  return new vscode.RelativePattern(folder, toGlob(filePath));
}

function toGlob(rel: string): string {
  return rel.replace(/\\/g, "/");
}

function alive(current: PanelState): boolean {
  return state === current;
}

function scheduleChanged(current: PanelState): void {
  if (Date.now() - current.wroteAt < OWN_WRITE_GRACE_MS) {
    return;
  }
  if (current.timer !== undefined) {
    clearTimeout(current.timer);
  }
  current.timer = setTimeout(() => {
    current.timer = undefined;
    if (alive(current)) {
      current.changedPending = true;
      current.host.post({ type: "changed" } satisfies ToRisk);
    }
  }, DEBOUNCE_MS);
}

function scheduleLock(current: PanelState): void {
  if (current.lockTimer !== undefined) {
    clearTimeout(current.lockTimer);
  }
  current.lockTimer = setTimeout(() => {
    current.lockTimer = undefined;
    if (alive(current)) {
      void refreshLock(current);
    }
  }, DEBOUNCE_MS);
}

/**
 * 保存できるかを実行ファイルに聞く。確かめられなければ閉じる側。
 * 共通の設定とワークスペースの設定の配点は、どのツリーの子を閉じるときにも読まれるので、どのツリーの doing でも止める。
 * プロジェクトの設定の配点は、そのプロジェクトの doing だけを見る。
 */
async function refreshLock(current: PanelState): Promise<Lock> {
  const result = await loadBoard(current.folder.uri.fsPath, binSetting());
  const lock = result.ok ? lockFromBoard(result.board, projectOf(current.target)) : lockFromError(result.error);
  if (alive(current)) {
    current.lock = lock;
    current.host.post({ type: "lock", lock } satisfies ToRisk);
  }
  return lock;
}

/**
 * いま見せるものを渡す。画面の編集はここで捨てられるので、呼ぶのはユーザが「更新」を押した
 * ときと、保存・作成が通って中身が入れ替わったときだけ。
 */
function show(current: PanelState): void {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  current.error = undefined;
  // 読み直したので、「ファイルの変更を検知しました」はもう今のことではない
  current.changedPending = false;
  current.host.send({
    kind: "page",
    page: {
      root: current.folder.uri.fsPath,
      riskPath: loaded.riskRel,
      exists: loaded.exists,
      builtin: loaded.builtin,
      model: loaded.doc.model,
      lock: current.lock,
      notices: loaded.notices,
      target: currentKey(loaded.target),
      targets: loaded.targets,
      sums: loaded.sums,
    },
  });
}

/** 読み直せなかったことを見せる。理由は覚えておく（渡せなかったときに `ready` で渡し直すため） */
function showError(current: PanelState, error: string): void {
  current.loaded = undefined;
  current.error = error;
  const target = currentKey(current.target);
  current.host.send({ kind: "error", error, target, targets: targetOptions(undefined, "workspace", target) });
}

async function reload(current: PanelState): Promise<void> {
  current.seq += 1;
  const seq = current.seq;
  let loaded: Loaded;
  try {
    loaded = await readPage(current.folder.uri.fsPath, current.target);
  } catch (error) {
    if (alive(current) && current.seq === seq) {
      showError(current, (error as Error).message);
    }
    return;
  }
  // 読んでいる間に別の対象へ切り替わった（または読み直しが重なった）なら、後から来たほうに任せる
  if (!alive(current) || current.seq !== seq) {
    return;
  }
  current.loaded = loaded;
  show(current);
  watchFiles(current);
  void refreshLock(current);
}

/**
 * いまの状態で渡し直す。1 枚目を読み込んでいる間に見送られたもの（`deferred`）は、画面が
 * 組み上がった（`ready`）ところでここから渡る。
 */
function redraw(current: PanelState): void {
  if (current.loaded !== undefined) {
    show(current);
    return;
  }
  if (current.error !== undefined) {
    showError(current, current.error);
    return;
  }
  // 切り替え先を読んでいる最中
  current.host.send({ kind: "loading", text: loadingText(whatOf(current.target)) });
}

/**
 * リスク管理の画面に渡す手段。VS Code のパネルを `retainedHost` の形に合わせる。
 * 入れ物は 1 度しか入らないので、表裏は渡さない（保持する画面は裏でも生きている）。
 * パネルの `retainContextWhenHidden` を偽に変えると、送った先が捨てられていても気づけなくなる。
 * 型では止まらないので、ここで見て言う。
 */
function riskHost(panel: vscode.WebviewPanel, root: string): ScreenHost<RiskData> {
  if (panel.options.retainContextWhenHidden !== true) {
    // retainedHost は retainContextWhenHidden が真であることを前提にしている。偽のままだと、裏に回った画面へ
    // 送り続けて中身が古いまま止まる。診断ログにだけ残す（console には出さない。docs/claude/logging.md）
    diaglog.get("ccnavi-board", root).error("画面の前提が崩れている", { screen: "risk", retainContextWhenHidden: false });
  }
  return retainedHost<RiskData>(
    {
      html(text: string): void {
        panel.webview.html = text;
      },
      post(message: unknown): void {
        void panel.webview.postMessage(message);
      },
    },
    (data) =>
      renderRiskPage(data, {
        nonce: crypto.randomBytes(16).toString("base64"),
        script: webviewScript(SCREEN),
        style: webviewStyle(SCREEN),
        appearance: readAppearance(),
      }),
  );
}

/**
 * 保存を始めたときに読んでいたものが、往復の間に入れ替わっていないか。
 *
 * 保存は実行ファイルへ 2 度出る（`--lint` と錠の取り直し）。その間にユーザが「更新」を押せば、
 * 画面の編集は捨てられ、新しい中身が出ている。そこへ古い編集を書くと、捨てたはずのものが
 * ファイルに入る。 読み直されていたら、この保存はもう無かったことにする。
 */
function stale(current: PanelState, loaded: Loaded): boolean {
  if (current.loaded === loaded) {
    return false;
  }
  // 往復の間に別の対象へ切り替わった。捨てるのは同じだが、切り替え先の画面に前の対象の話を出さない
  if (!sameTarget(loaded.target, current.target)) {
    return true;
  }
  fail(current, "画面を更新したので、この保存は取りやめました。更新後の配点で編集し直してください");
  return true;
}

/** 操作の結果の一言。1 枚目を読み込んでいる間だけ届かずに捨てられる（裏に回っていても届く） */
function fail(current: PanelState, message: string): void {
  current.host.post({ type: "failed", message } satisfies ToRisk);
}

async function handleMessage(current: PanelState, message: RiskMessage | undefined): Promise<void> {
  if (message === undefined || !alive(current)) {
    return;
  }
  if (message.type === "dirty") {
    current.dirty = message.dirty;
    return;
  }
  if (message.type === "ready") {
    // 組み上がったばかりの画面は必ず「変更なし」で始まる（作り直された画面は前の編集を持たない）
    current.dirty = false;
    // 画面が組み上がった。1 枚目を読み込んでいる間に見送った中身は、ここで渡る。
    // 見送るものが無くても渡し直す（同じ中身がもう 1 度届く）。VS Code が画面を作り直す経路
    // （`Developer: Reload Webviews`）では、入れてある HTML の中身が古いことがあるため
    current.host.ready();
    redraw(current);
    postAppearance(current.host);
    // 初回だけ吹き出しの案内を頼む。画面は指す先が出てから始め、閉じたら `tourDone` を返す。
    // 閉じずにタブを閉じたら見た記録は残らないので、次に開いたときにもう 1 度出る
    if (!tourSeen(SCREEN)) {
      current.host.post({ type: "tour" } satisfies ToRisk);
    }
    return;
  }
  if (message.type === "tourDone") {
    markTourSeen(SCREEN);
    return;
  }
  // 読み直せていない画面では、配点に当たる操作はどれも行き先が無い（「更新」は
  // 押せるが、その経路は `reload` が読み直しからやり直す）
  if (current.loaded === undefined && message.type !== "reload") {
    return;
  }
  switch (message.type) {
    case "reload": {
      if (message.dirty) {
        const choice = await vscode.window.showWarningMessage(
          "未保存の変更があります。破棄して更新しますか？",
          { modal: true },
          "更新",
        );
        if (choice !== "更新") {
          // 画面は「更新」を押した時点で欄を止めている。やめたことを伝えないと止まったままになる
          // 問いを出している間にパネルを閉じられるので、送る前に生きているかを見る
          if (alive(current)) {
            current.host.post({ type: "cancelled" } satisfies ToRisk);
          }
          return;
        }
      }
      await reload(current);
      return;
    }
    case "switchTarget": {
      // 一覧にある対象だけを受ける。画面が古いまま、消えたプロジェクトを指していても開かない
      const option = (current.loaded?.targets ?? targetOptions(undefined, "workspace", currentKey(current.target))).find((t) => t.kind === message.kind && t.name === message.name);
      if (option === undefined) {
        return;
      }
      const target: RiskTarget =
        option.kind === "project" ? { kind: "project", name: option.name } : option.kind === "self" ? { kind: "self" } : { kind: "workspace" };
      if (!sameTarget(current.target, target)) {
        await switchTarget(current, target);
      }
      return;
    }
    case "openFile": {
      if (current.loaded === undefined) {
        return;
      }
      const target = current.loaded.riskPath;
      void vscode.workspace.openTextDocument(target).then(
        (document) => vscode.window.showTextDocument(document),
        () => vscode.window.showInformationMessage(`ファイルを開けませんでした: ${target}`),
      );
      return;
    }
    case "create": {
      await create(current);
      return;
    }
    case "save": {
      await save(current, message.form);
      return;
    }
  }
}

/** 組み込みと同じ値のファイルを書き出す。既にあれば上書きしない（値は同じなので数え方は変わらない） */
async function create(current: PanelState): Promise<void> {
  const loaded = current.loaded;
  if (loaded === undefined || !loaded.builtin) {
    return;
  }
  if (fs.existsSync(loaded.riskPath)) {
    fail(current, `${loaded.riskRel} は既に存在するため、上書きしません。更新してください`);
    return;
  }
  try {
    fs.mkdirSync(path.dirname(loaded.riskPath), { recursive: true });
    current.wroteAt = Date.now();
    fs.writeFileSync(loaded.riskPath, BUILTIN_RISK_TEXT, { encoding: "utf8", flag: "wx" });
  } catch (error) {
    // 書けなかったのに猶予を残したままだと、その間の実際の外部変更が知らされない。
    current.wroteAt = 0;
    fail(current, `${loaded.riskRel} に書けません: ${(error as Error).message}`);
    return;
  }
  await reload(current);
  vscode.window.showInformationMessage(`${loaded.riskRel} を組み込みの配点で作りました。コミットは自分でしてください`);
}

/** 保存の前の検証に渡す差し替え。共通の設定は `--risk`、ほかは名前を付けて `--project-risk-file` */
function overrideFor(target: RiskTarget, tmp: string): LintOverride {
  switch (target.kind) {
    case "workspace":
      return { kind: "risk", path: tmp };
    case "self":
      return { kind: "layerRisk", name: LAYER_SELF, path: tmp };
    case "project":
      return { kind: "layerRisk", name: target.name, path: tmp };
  }
}

async function save(current: PanelState, form: RiskForm): Promise<void> {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  if (loaded.builtin) {
    fail(current, `${loaded.riskRel} がありません。先に「組み込みの配点でファイルを作る」を押してください`);
    return;
  }
  const root = current.folder.uri.fsPath;
  let tmp: string;
  let text: string;
  try {
    text = loaded.doc.apply(form);
    tmp = path.join(current.tmpDir, "risks.yml");
    fs.writeFileSync(tmp, text, "utf8");
  } catch (error) {
    fail(current, `編集中の内容を書き出せません: ${(error as Error).message}`);
    return;
  }

  // 1. 検証。error が 1 件でもあれば保存しない。
  const lint = await runLint(root, binSetting(), overrideFor(loaded.target, tmp));
  if (!alive(current)) {
    return;
  }
  if (stale(current, loaded)) {
    return;
  }
  if (!lint.ok) {
    fail(current, lint.error);
    return;
  }
  if (!lint.value.ok) {
    // 苦情は渡した一時ファイルのパスで出るので、画面では対象のファイルのパスに直す。
    fail(current, `--lint が error を報告しました。直してから保存してください:\n${lint.value.report.split(tmp).join(loaded.riskRel)}`);
    return;
  }

  // 2. 作業中のチケットが無いこと。押した時点で取り直す。
  const lock = await refreshLock(current);
  if (!alive(current)) {
    return;
  }
  if (stale(current, loaded)) {
    return;
  }
  if (lock.locked) {
    fail(current, lock.reason);
    return;
  }

  // 3. 読み込んでから外で変わっていないこと。無かったファイルは、まだ無いこと。
  if (loaded.exists) {
    let mtimeMs: number;
    try {
      mtimeMs = fs.statSync(loaded.riskPath).mtimeMs;
    } catch (error) {
      fail(current, `配点のファイルを確かめられません: ${(error as Error).message}`);
      return;
    }
    if (mtimeMs !== loaded.mtimeMs) {
      fail(current, "配点のファイルは、読み込んだあとに画面の外で変更されています。更新してから編集し直してください（この変更は上書きしません）");
      return;
    }
  } else if (fs.existsSync(loaded.riskPath)) {
    fail(current, "配点のファイルは、読み込んだあとに画面の外で作られています。更新してから編集し直してください（上書きしません）");
    return;
  }

  try {
    current.wroteAt = Date.now();
    if (loaded.exists) {
      fs.writeFileSync(loaded.riskPath, text, "utf8");
    } else {
      fs.mkdirSync(path.dirname(loaded.riskPath), { recursive: true });
      fs.writeFileSync(loaded.riskPath, text, { encoding: "utf8", flag: "wx" });
    }
  } catch (error) {
    current.wroteAt = 0;
    fail(current, `配点のファイルに書けません: ${(error as Error).message}`);
    return;
  }
  await reload(current);
  const tail = lint.value.report.split("\n").filter((l) => l.trim() !== "").pop() ?? "";
  vscode.window.showInformationMessage(`${loaded.riskRel} に保存しました（${tail}）`);
}

function asMessage(message: unknown): RiskMessage | undefined {
  if (typeof message !== "object" || message === null) {
    return undefined;
  }
  const m = message as { type?: unknown; dirty?: unknown; form?: unknown; kind?: unknown; name?: unknown };
  switch (m.type) {
    case "switchTarget":
      return typeof m.kind === "string" && typeof m.name === "string" ? { type: "switchTarget", kind: m.kind, name: m.name } : undefined;
    case "reload":
      return { type: "reload", dirty: m.dirty === true };
    case "dirty":
      return typeof m.dirty === "boolean" ? { type: "dirty", dirty: m.dirty } : undefined;
    case "ready":
    case "openFile":
    case "create":
    case "tourDone":
      return { type: m.type };
    case "save": {
      const form = asRiskForm(m.form);
      return form === undefined ? undefined : { type: "save", form };
    }
    default:
      return undefined;
  }
}
