/**
 * フェーズ管理画面の Webview パネル。生成・更新・破棄、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。README の手動確認の手順で確かめる。
 *
 * 画面は React（`src/webview/phases/`）で、ここが渡すのは「いま何を見せるか」（`PhasesData`）だけ。
 * 渡し方は `core/screen-host.ts` の `retainedHost` が決める。この画面は編集の途中を持つので
 * `retainContextWhenHidden` が真で、**入れ物（HTML）は 1 度しか入らない**（ADR-0062）。
 * 中身を渡すのは、画面の編集を捨ててよいときだけ（人が「再読込」を押した、保存や作成が通った）。
 *
 * 対象は 3 種（設計 §11.2、§11.4.1）。共通層の種類（`.ccnavi/common/phases.yml`。置き場は固定）、
 * ワークスペース自身の層（既定 `.ccnavi/config/phases.yml`）、プロジェクト 1 つの層
 * （既定 `projects/<名前>/.ccnavi/config/phases.yml`）。**タブは 1 枚だけ**で、別の対象を開くとそのタブの
 * 中身を入れ替える（未保存の変更があれば、破棄して切り替えるかを聞く）。
 * 層の置き場は実行ファイルが解いたもの（`--explain --json` の `layers[].phases_file`）を使い、拡張は組まない。
 *
 * 検証は実行ファイルに任せる。編集中の内容は一時ファイルに書き、共通層なら `--lint --phases <パス>`、
 * 層なら `--lint --project-phases-file <名前>=<パス>`（自身の層は名前が `self`）で渡す。層の種類は
 * 共通層と合成して確かめられる（同じ id で中身が違う、表示名の重なり、overlap / requires の指す先）。
 * 保存は、検証（`--lint`）を通り、作業中のチケットが無く、ファイルが外で変わっていないときだけ行う。
 * 作業中のチケットは、共通層と自身の層ならどのツリーでも、プロジェクトの層ならそのプロジェクトの分を見る
 * （種類は承認・着手・閉じるときに読まれるので、走っている最中に変えない）。
 * ファイルが無いとき、共通層は空の画面と「雛形で作る」を見せる。層は雛形を置かない（雛形の id は共通層の種類と
 * 重なりやすく、中身が違えばその層が空として扱われる）。代わりに画面で種類を足させ、検証を通った最初の保存で
 * ファイルを作る。種類の無いファイル（`phases: {}`）は実行ファイルが error にするので、先に書き出さない。
 * 組み込みの既定は無い（実行ファイルも持たない。既定を組み込むと、意図せずレビューの要否が決まる）。
 *
 * チケット制御が disable のワークスペースでは、対象がどれでも開かない。種類は親チケットの計画と
 * 子の範囲にしか読まれないので、disable の間は何も動かさない。入口（サイドパネル・コマンドパレット・
 * プロジェクト管理画面のボタン）も同じ鍵で隠れる。
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import { followAppearance, postAppearance, readAppearance } from "./appearance.js";
import { loadBoard, runLint, type LintOverride } from "./ccnavi.js";
import { LAYER_SELF, projectLayer, selfLayer } from "./core/layers.js";
import { lockFromBoard, lockFromError, type Lock } from "./core/lock.js";
import { asPhasesForm, readPhases, TEMPLATE_PHASES_TEXT, type PhasesDocument } from "./core/phases-doc.js";
import { renderPhasesPage } from "./core/phases-render.js";
import type { PhasesData, PhasesForm, PhasesMessage, ToPhases } from "./core/phases-view.js";
import { retainedHost, type ScreenHost } from "./core/screen-host.js";
import { showLoading } from "./loading.js";
import type { PhasesTarget } from "./core/screens.js";
import { WATCH_PATTERNS } from "./core/watch.js";
import { requireTickets } from "./ticket-control.js";
import { markTourSeen, tourSeen } from "./tour.js";
import { webviewScript, webviewStyle } from "./webview-asset.js";

const DEBOUNCE_MS = 120;
const DEFAULT_PHASES = ".ccnavi/common/phases.yml";
/** 画面の名前。束ねの綴りは `src/webview/<名前>/main.tsx` → `out/webview/<名前>.js`、`style.css` → `<名前>.css` */
const SCREEN = "phases";
/** 自分の保存で監視が鳴るのを、この間だけ「外で変わった」と言わない */
const OWN_WRITE_GRACE_MS = 1500;
/** 層のファイルを最初の保存で作るときに、先頭へ置く説明 */
const LAYER_HEADER = [
  "# この層のフェーズの種類。共通層の種類に足して使う（設計 §11.4.1）。",
  "# 共通層と同じ id を書くなら中身も同じにする。違えば --lint が error を出し、この層は空として扱われる。",
  "",
].join("\n");

interface Loaded {
  /** 読んだ対象。往復の間に切り替わったかを `stale` が見る */
  readonly target: PhasesTarget;
  /** ファイルの本文。無ければ空 */
  readonly text: string;
  readonly exists: boolean;
  /** 無いときは 0 */
  readonly mtimeMs: number;
  readonly doc: PhasesDocument;
  readonly phasesPath: string;
  /** ワークスペースルートからの相対で見せる綴り */
  readonly phasesRel: string;
  /** 上部に出す注意。実行ファイルがこの層を読めていない、など */
  readonly notices: readonly string[];
}

interface PanelState {
  /** いま見せている対象。別の対象を開くと入れ替わる（タブは 1 枚） */
  target: PhasesTarget;
  readonly panel: vscode.WebviewPanel;
  readonly folder: vscode.WorkspaceFolder;
  readonly tmpDir: string;
  readonly host: ScreenHost<PhasesData>;
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
  /** 「外で変わった」を出したまま、まだ読み直していない。表に戻ったときに送り直す */
  changedPending: boolean;
  wroteAt: number;
  /** 画面に未保存の変更があるか（画面が `dirty` で知らせる）。別の対象へ切り替えるときに聞くかを決める */
  dirty: boolean;
  /**
   * 読み直しの番号。読むたびに 1 つ進め、読み終えたときに変わっていたら捨てる。
   * 層の置き場を実行ファイルに聞く間に別の対象へ切り替わると、前の対象の答えが後から届くため
   */
  seq: number;
  /** 「破棄して切り替える？」を出している間は真。重ねて開かれても 2 枚目の問いを出さない */
  asking: boolean;
}

/** 開いているパネル。種類ごとに 1 枚 */
let state: PanelState | undefined;

function sameTarget(a: PhasesTarget, b: PhasesTarget): boolean {
  return a.kind === b.kind && (a.kind !== "project" || (b.kind === "project" && a.name === b.name));
}

/** 保存を止めるチケットを絞るプロジェクト。共通層と自身の層は絞らない */
function projectOf(target: PhasesTarget): string | undefined {
  return target.kind === "project" ? target.name : undefined;
}

function titleOf(target: PhasesTarget): string {
  switch (target.kind) {
    case "common":
      return "ccnavi フェーズ管理";
    case "self":
      return "ccnavi フェーズ管理: 自身の層";
    case "project":
      return `ccnavi フェーズ管理: ${target.name}`;
  }
}

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

/** `ccnaviBoard.openPhases` の本体。引数なしは共通層の種類 */
export async function openPhases(target: PhasesTarget = { kind: "common" }): Promise<void> {
  if (!requireTickets("フェーズ管理画面")) {
    return;
  }
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    vscode.window.showInformationMessage("ワークスペースが開かれていないため、フェーズ管理画面を表示できない");
    return;
  }
  if (state !== undefined) {
    state.panel.reveal(state.panel.viewColumn);
    if (!sameTarget(state.target, target)) {
      await switchTarget(state, target);
    }
    return;
  }

  // 画面と CSS は束ねたものを読んで流し込む。無ければ開かずに言う（パネルだけ出しても白いまま）
  try {
    webviewScript(SCREEN);
    webviewStyle(SCREEN);
  } catch (error) {
    vscode.window.showErrorMessage(`フェーズ管理画面を表示できない: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }

  // タブは読む前に作る。層の置き場を実行ファイルに聞く間、押しても何も起きないように見えないように。
  // `state` を先に立てるので、読んでいる間に押し直しても上の `reveal` に入る。
  // 読めなかったときもタブは閉じず、中にエラーを出す（`reload` の `showError`）
  const panel = vscode.window.createWebviewPanel("ccnaviPhases", titleOf(target), vscode.ViewColumn.One, {
    enableScripts: true,
    enableForms: false,
    localResourceRoots: [],
    // 編集の途中を持つので、タブを裏に回しても捨てない。
    retainContextWhenHidden: true,
  });
  showLoading(panel, titleOf(target), SCREEN);
  const current: PanelState = {
    target,
    panel,
    folder,
    tmpDir: fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-phases-")),
    host: phasesHost(panel),
    fileWatchers: [],
    watchers: [],
    lock: lockFromError("まだ確認していない"),
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
async function switchTarget(current: PanelState, target: PhasesTarget): Promise<void> {
  if (current.dirty) {
    // 問いを出している間に別の対象を開かれたら、前面に出すだけにする（問いは先に出したものに答えてもらう）
    if (current.asking) {
      return;
    }
    current.asking = true;
    const choice = await vscode.window.showWarningMessage(
      `未保存の変更がある。破棄して「${titleOf(target)}」に切り替える？`,
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
  current.lock = lockFromError("まだ確認していない");
  // 前の対象のファイルの監視は外す。切り替え先が読めたら `reload` が張り直す。読めずにエラーのままなら、
  // 前の対象の変化を「外で変わった」と拾い続けない
  if (current.timer !== undefined) {
    clearTimeout(current.timer);
    current.timer = undefined;
  }
  for (const watcher of current.fileWatchers) {
    watcher.dispose();
  }
  current.fileWatchers = [];
  current.host.send({ kind: "loading", title: titleOf(target) });
  await reload(current);
}

async function readPage(root: string, target: PhasesTarget): Promise<Loaded> {
  let phasesRel: string;
  let phasesPath: string;
  const notices: string[] = [];
  if (target.kind === "common") {
    // 共通層の置き場は `.ccnavi/common/` 固定（ADR-0052）。
    phasesRel = DEFAULT_PHASES;
    phasesPath = resolveIn(root, phasesRel);
  } else {
    // 層の置き場は実行ファイルに聞く。CCNAVI_PROJECT_HOME から自分で組むと、組み方がずれたときに
    // この画面で保存した種類が承認と着手に効かなくなる。答えは元リポジトリの版（設計 §11.2）。
    const board = await loadBoard(root, binSetting());
    if (!board.ok) {
      throw new Error(`層の置き場を実行ファイルから取得できない: ${board.error}`);
    }
    const layer = target.kind === "self" ? selfLayer(board.board) : projectLayer(board.board, target.name);
    if (layer === undefined || layer.phasesFile.path === "") {
      throw new Error(
        target.kind === "self"
          ? "実行ファイルの答えに自身の層が無い"
          : `プロジェクト ${target.name} は層として数えられていない（置き場の直下に無いか、予約名 common / self）`,
      );
    }
    phasesPath = resolveIn(root, layer.phasesFile.path);
    phasesRel = path.relative(root, phasesPath).split(path.sep).join("/");
    if (layer.phasesFile.unreadable !== "") {
      notices.push(`実行ファイルはこのファイルを読めず、層の種類を空として扱っている（共通層の種類だけで進む）: ${layer.phasesFile.unreadable}`);
    }
  }
  let text: string;
  let mtimeMs: number;
  let exists: boolean;
  try {
    text = fs.readFileSync(phasesPath, "utf8");
    mtimeMs = fs.statSync(phasesPath).mtimeMs;
    exists = true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      throw new Error(`フェーズの種類のファイルを読めない（${phasesRel}）: ${(error as Error).message}`);
    }
    // 無いのは不備ではない（番号だけのフェーズ、無い層は空）。画面は空を見せ、「作る」だけができる。
    text = "";
    mtimeMs = 0;
    exists = false;
  }
  if (target.kind === "common" && !exists) {
    notices.push(
      "種類は自身の層とプロジェクトの層にも置ける（プロジェクト管理画面から開く）。共通層に置いた種類は全プロジェクトに効き、層に同じ id で中身の違う種類があるとその層が空として扱われる",
    );
  }
  // 無いときの苦情（version が無い、phases が無い）は画面に出さない。無いことは帯で言う。
  const parsed = readPhases(text);
  const doc = exists ? parsed : { apply: parsed.apply, model: { ...parsed.model, problems: [] } };
  return { target, text, exists, mtimeMs, doc, phasesPath, phasesRel, notices };
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
  // どちらが正しくても壊れないよう、表に戻ったところで、いま出すべき知らせを送り直す。
  // 中身（`data`）は送らない。送ると、裏で打っていた編集がここで消える。
  // 見た目（`appearance`）も同じ扱い。保持しない画面は入れ物から作り直されるので `ready` で渡るが、
  // 保持する画面は作り直されないので、裏にいる間の切り替えが落ちていたらここでしか拾えない（issue #87）。
  panel.onDidChangeViewState(() => {
    if (!panel.visible || !alive(current)) {
      return;
    }
    current.host.post({ type: "lock", lock: current.lock } satisfies ToPhases);
    postAppearance(current.host);
    if (current.changedPending) {
      current.host.post({ type: "changed" } satisfies ToPhases);
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
 * 種類のファイルと設定ファイルが変わったら「外で変わった」と伝える。自分の保存は除く。
 * 対象のパスは設定で変わるので、再読込のたびに張り直す。絶対パスはワークスペース相対の glob に
 * ならないので、そのディレクトリを起点にする。
 */
function watchFiles(current: PanelState): void {
  for (const watcher of current.fileWatchers) {
    watcher.dispose();
  }
  current.fileWatchers = [];
  const phasesRel = current.loaded?.phasesRel ?? DEFAULT_PHASES;
  const patterns: vscode.RelativePattern[] = [
    patternFor(current.folder, phasesRel),
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
      current.host.post({ type: "changed" } satisfies ToPhases);
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
 * 共通層の種類はどのツリーの承認・着手・閉じるときにも読まれるので、どのツリーの doing でも止める。
 * 自身の層も同じに止める（ワークスペース自身のチケットだけに効くが、絞らずに止める側に倒す）。
 * プロジェクトの層は、そのプロジェクトのチケットにしか足されないので、そのプロジェクトの doing だけを見る。
 */
async function refreshLock(current: PanelState): Promise<Lock> {
  const result = await loadBoard(current.folder.uri.fsPath, binSetting());
  const lock = result.ok ? lockFromBoard(result.board, projectOf(current.target)) : lockFromError(result.error);
  if (alive(current)) {
    current.lock = lock;
    current.host.post({ type: "lock", lock } satisfies ToPhases);
  }
  return lock;
}

/**
 * いま見せるものを渡す。**画面の編集はここで捨てられる**ので、呼ぶのは人が「再読込」を押した
 * ときと、保存・作成が通って中身が入れ替わったときだけ（ADR-0062）。
 */
function show(current: PanelState): void {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  current.error = undefined;
  // 読み直したので、「外で変わった」はもう今のことではない
  current.changedPending = false;
  current.host.send({
    kind: "page",
    page: {
      root: current.folder.uri.fsPath,
      phasesPath: loaded.phasesRel,
      exists: loaded.exists,
      model: loaded.doc.model,
      lock: current.lock,
      layer: current.target.kind !== "common",
      notices: loaded.notices,
    },
  });
}

/** 読み直せなかったことを見せる。理由は覚えておく（渡せなかったときに `ready` で渡し直すため） */
function showError(current: PanelState, error: string): void {
  current.loaded = undefined;
  current.error = error;
  current.host.send({ kind: "error", error });
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
  current.host.send({ kind: "loading", title: titleOf(current.target) });
}

/**
 * フェーズ管理の画面に渡す口。VS Code のパネルを `retainedHost` の形に合わせる。
 * **入れ物は 1 度しか入らない**ので、表裏は渡さない（保持する画面は裏でも生きている）。
 * パネルの `retainContextWhenHidden` を偽に変えると、送った先が捨てられていても気づけなくなる。
 * 型では止まらないので、ここで見て言う。
 */
function phasesHost(panel: vscode.WebviewPanel): ScreenHost<PhasesData> {
  if (panel.options.retainContextWhenHidden !== true) {
    console.error(`フェーズ管理の画面は retainContextWhenHidden が真であることを前提にしている（retainedHost）。偽のままだと、裏に回った画面へ送り続けて中身が古いまま止まる`);
  }
  return retainedHost<PhasesData>(
    {
      html(text: string): void {
        panel.webview.html = text;
      },
      post(message: unknown): void {
        void panel.webview.postMessage(message);
      },
    },
    (data) =>
      renderPhasesPage(data, {
        nonce: crypto.randomBytes(16).toString("base64"),
        script: webviewScript(SCREEN),
        style: webviewStyle(SCREEN),
        appearance: readAppearance(),
      }),
  );
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
 * 保存を始めたときに読んでいたものが、往復の間に入れ替わっていないか。
 *
 * 保存は実行ファイルへ 2 度出る（`--lint` と錠の取り直し）。その間に人が「再読込」を押せば、
 * 画面の編集は捨てられ、新しい中身が出ている。**そこへ古い編集を書くと、捨てたはずのものが
 * ファイルに入る。** 読み直されていたら、この保存はもう無かったことにする。
 */
function stale(current: PanelState, loaded: Loaded): boolean {
  if (current.loaded === loaded) {
    return false;
  }
  // 往復の間に別の対象へ切り替わった。捨てるのは同じだが、切り替え先の画面に前の対象の話を出さない
  if (!sameTarget(loaded.target, current.target)) {
    return true;
  }
  fail(current, "読み直したので、この保存は捨てた。いまの種類で編集し直す");
  return true;
}

/** 操作の結果の一言。1 枚目を読み込んでいる間だけ落ちる（裏に回っていても届く） */
function fail(current: PanelState, message: string): void {
  current.host.post({ type: "failed", message } satisfies ToPhases);
}

async function handleMessage(current: PanelState, message: PhasesMessage | undefined): Promise<void> {
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
    // 見送るものが無くても渡し直す（同じ中身がもう 1 度届く）。VS Code が画面を作り直す道
    // （`Developer: Reload Webviews`）では、入れてある HTML の中身が古いことがあるため
    current.host.ready();
    redraw(current);
    postAppearance(current.host);
    // 初回だけ吹き出しの案内を頼む。画面は種類の中身が出てから始め、閉じたら `tourDone` を返す。
    // 閉じずにタブを閉じたら印は残らないので、次に開いたときにもう 1 度出る
    if (!tourSeen(SCREEN)) {
      current.host.post({ type: "tour" } satisfies ToPhases);
    }
    return;
  }
  if (message.type === "tourDone") {
    markTourSeen(SCREEN);
    return;
  }
  // 読み直せていない画面では、種類に当たる操作はどれも行き先が無い（「再読込」は
  // 押せるが、その道は `reload` が読み直しからやり直す）
  if (current.loaded === undefined && message.type !== "reload") {
    return;
  }
  switch (message.type) {
    case "reload": {
      if (message.dirty) {
        const choice = await vscode.window.showWarningMessage(
          "未保存の変更がある。破棄して読み直す？",
          { modal: true },
          "読み直す",
        );
        if (choice !== "読み直す") {
          // 画面は「再読込」を押した時点で欄を止めている。やめたことを伝えないと止まったままになる
          current.host.post({ type: "cancelled" } satisfies ToPhases);
          return;
        }
      }
      await reload(current);
      return;
    }
    case "openFile": {
      if (current.loaded === undefined) {
        return;
      }
      const target = current.loaded.phasesPath;
      void vscode.workspace.openTextDocument(target).then(
        (document) => vscode.window.showTextDocument(document),
        () => vscode.window.showInformationMessage(`ファイルを開けなかった: ${target}`),
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

/** 共通層に雛形を書き出す。既にあれば上書きしない。層には雛形を置かない（最初の保存で作る） */
async function create(current: PanelState): Promise<void> {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  if (current.target.kind !== "common") {
    fail(current, "層には雛形を置かない。種類を足して保存すると、ファイルが作られる");
    return;
  }
  if (fs.existsSync(loaded.phasesPath)) {
    fail(current, `${loaded.phasesRel} は既に存在するため、上書きしない。再読込する`);
    return;
  }
  try {
    fs.mkdirSync(path.dirname(loaded.phasesPath), { recursive: true });
    current.wroteAt = Date.now();
    fs.writeFileSync(loaded.phasesPath, TEMPLATE_PHASES_TEXT, { encoding: "utf8", flag: "wx" });
  } catch (error) {
    // 書けなかったのに猶予を立てたままだと、その間の本物の外部変更を握りつぶす。
    current.wroteAt = 0;
    fail(current, `${loaded.phasesRel} に書けない: ${(error as Error).message}`);
    return;
  }
  await reload(current);
  if (!alive(current)) {
    return;
  }
  vscode.window.showInformationMessage(
    `${loaded.phasesRel} を雛形から作成しました。修正してコミットを行ってください。`,
  );
}

function overrideFor(target: PhasesTarget, tmp: string): LintOverride {
  switch (target.kind) {
    case "common":
      return { kind: "phases", path: tmp };
    case "self":
      return { kind: "layerPhases", name: LAYER_SELF, path: tmp };
    case "project":
      return { kind: "layerPhases", name: target.name, path: tmp };
  }
}

async function save(current: PanelState, form: PhasesForm): Promise<void> {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  const layer = current.target.kind !== "common";
  if (!loaded.exists && !layer) {
    fail(current, `${loaded.phasesRel} が無い。先に「雛形でファイルを作る」を押す`);
    return;
  }
  const root = current.folder.uri.fsPath;
  let tmp: string;
  let text: string;
  try {
    // 層のファイルを初めて作るときは、先頭に説明を置く。検証にも同じ本文を掛ける。
    text = (loaded.exists ? "" : LAYER_HEADER) + loaded.doc.apply(form);
    tmp = path.join(current.tmpDir, "phases.yml");
    fs.writeFileSync(tmp, text, "utf8");
  } catch (error) {
    fail(current, `編集中の内容を書き出せない: ${(error as Error).message}`);
    return;
  }

  // 1. 検証。error が 1 件でもあれば保存しない。承認済みの計画がこの種類で読めるかもここで分かる。
  //    層なら共通層との合成もここで確かめる。
  const lint = await runLint(root, binSetting(), overrideFor(current.target, tmp));
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
    // 苦情は渡した一時ファイルのパスを名乗るので、画面では対象のファイルの綴りに直す。
    fail(current, `--lint が error を報告した。直してから保存する:\n${lint.value.report.split(tmp).join(loaded.phasesRel)}`);
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
      mtimeMs = fs.statSync(loaded.phasesPath).mtimeMs;
    } catch (error) {
      fail(current, `フェーズの種類のファイルを確かめられない: ${(error as Error).message}`);
      return;
    }
    if (mtimeMs !== loaded.mtimeMs) {
      fail(current, "フェーズの種類のファイルが読み込んだあとに外で変更されている。再読込してから編集し直す（この変更は上書きしない）");
      return;
    }
  } else if (fs.existsSync(loaded.phasesPath)) {
    fail(current, "フェーズの種類のファイルが読み込んだあとに外で作られている。再読込してから編集し直す（上書きしない）");
    return;
  }

  try {
    current.wroteAt = Date.now();
    if (loaded.exists) {
      fs.writeFileSync(loaded.phasesPath, text, "utf8");
    } else {
      fs.mkdirSync(path.dirname(loaded.phasesPath), { recursive: true });
      fs.writeFileSync(loaded.phasesPath, text, { encoding: "utf8", flag: "wx" });
    }
  } catch (error) {
    current.wroteAt = 0;
    fail(current, `フェーズの種類のファイルに書けない: ${(error as Error).message}`);
    return;
  }
  await reload(current);
  const tail = lint.value.report.split("\n").filter((l) => l.trim() !== "").pop() ?? "";
  vscode.window.showInformationMessage(`${loaded.phasesRel} に保存した（${tail}）`);
}

function asMessage(message: unknown): PhasesMessage | undefined {
  if (typeof message !== "object" || message === null) {
    return undefined;
  }
  const m = message as { type?: unknown; dirty?: unknown; form?: unknown };
  switch (m.type) {
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
      const form = asPhasesForm(m.form);
      return form === undefined ? undefined : { type: "save", form };
    }
    default:
      return undefined;
  }
}
