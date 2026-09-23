/**
 * ルール設定画面の Webview パネル。生成・更新・破棄、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。README の手動確認の手順で確かめる。
 *
 * 画面は React（`src/webview/rules/`）で、ここが渡すのは「いま何を見せるか」（`RulesData`）だけ。
 * 渡し方は `core/screen-host.ts` の `retainedHost` が決める。この画面は編集の途中を持つので
 * `retainContextWhenHidden` が真で、**入れ物（HTML）は 1 度しか入らない**（ADR-0062）。
 * 中身を渡すのは、画面の編集を捨ててよいときだけ（人が「再読込」を押した、保存が通った）。
 * ファイルが外で変わっただけのときは `changed` を送り、捨てるかどうかは人が決める。
 *
 * 対象は 3 種（設計 §11.2）。ワークスペースのルール（共通層、`.ccnavi/common/rules.yml`）、
 * ワークスペース自身の層（既定 `.ccnavi/config/rules.yml`）、プロジェクト 1 つの層
 * （既定 `projects/<名前>/.ccnavi/config/rules.yml`）。**タブは 1 枚だけ**で、別の対象を開くとそのタブの
 * 中身を入れ替える（未保存の変更があれば、破棄して切り替えるかを聞く）。
 * 層の置き場は実行ファイルが解いたもの（`--explain --json` の `layers[]`）を使い、拡張は組まない。
 *
 * 判定・検証は実行ファイルに任せる。編集中の内容は一時ファイルに書き、ワークスペースなら `--rules`、
 * 層なら `--project-rules-file <名前>=<パス>`（自身の層は名前が `self`）で渡す。保存は、検証（`--lint`）を通り、
 * 作業中のチケットが無く（プロジェクトならそのプロジェクトの）、ファイルが外で変わっていない
 * ときだけ行う。
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import { followAppearance, postAppearance, readAppearance } from "./appearance.js";
import { loadBoard, runLint, runSamples, runTest, type RulesOverride } from "./ccnavi.js";
import { envFromSettingsJson, hooksFor, parseHooks, type HookEntry } from "./core/hooks.js";
import { projectLayer, selfLayer } from "./core/layers.js";
import { loadingText } from "./core/loading-render.js";
import { lockFromBoard, lockFromError, type Lock } from "./core/lock.js";
import { asSections, readRules, type RulesDocument } from "./core/rules-doc.js";
import { renderRulesPage } from "./core/rules-render.js";
import { KNOWN_TOOLS, type RulesData, type RulesMessage, type Sections, type ToRules } from "./core/rules-view.js";
import { retainedHost, type ScreenHost } from "./core/screen-host.js";
import { showLoading } from "./loading.js";
import type { RulesTarget } from "./core/screens.js";
import { WATCH_PATTERNS } from "./core/watch.js";
import { webviewScript, webviewStyle } from "./webview-asset.js";

const DEBOUNCE_MS = 120;
const DEFAULT_RULES = ".ccnavi/common/rules.yml";
const DEFAULT_SAMPLES = ".ccnavi/common/rule-samples.yml";
/** 画面の名前。束ねの綴りは `src/webview/<名前>/main.tsx` → `out/webview/<名前>.js`、`style.css` → `<名前>.css` */
const SCREEN = "rules";
/** 自分の保存で監視が鳴るのを、この間だけ「外で変わった」と言わない */
const OWN_WRITE_GRACE_MS = 1500;

interface Loaded {
  /** 読んだ対象。往復の間に切り替わったかを `stale` が見る */
  readonly target: RulesTarget;
  readonly mtimeMs: number;
  readonly doc: RulesDocument;
  readonly rulesPath: string;
  /** ワークスペースルートからの相対で見せる綴り。プロジェクトなら `projects/<名前>/.ccnavi/config/rules.yml` */
  readonly rulesRel: string;
  /** 上部に出す注意。実行ファイルがこの層を読めていない、など */
  readonly notices: readonly string[];
  readonly hooks: readonly HookEntry[];
  readonly hookFiles: { readonly settings: boolean; readonly settingsLocal: boolean };
  readonly mode: string;
  readonly samplesPath: string;
  readonly samplesRel: string;
}

interface PanelState {
  /** いま見せている対象。別の対象を開くと入れ替わる（タブは 1 枚） */
  target: RulesTarget;
  readonly panel: vscode.WebviewPanel;
  readonly folder: vscode.WorkspaceFolder;
  readonly tmpDir: string;
  readonly host: ScreenHost<RulesData>;
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

function sameTarget(a: RulesTarget, b: RulesTarget): boolean {
  return a.kind === b.kind && (a.kind !== "project" || (b.kind === "project" && a.name === b.name));
}

/** 保存を止めるチケットを絞るプロジェクト。共通層と自身の層は絞らない（どちらも全ツリーの Bash に効く） */
function projectOf(target: RulesTarget): string | undefined {
  return target.kind === "project" ? target.name : undefined;
}

function titleOf(target: RulesTarget): string {
  switch (target.kind) {
    case "workspace":
      return "ccnavi ルール設定";
    case "self":
      return "ccnavi ルール設定: 自身の層";
    case "project":
      return `ccnavi ルール設定: ${target.name}`;
  }
}

/** 読み込み中の一言で「何を」読んでいるか */
function whatOf(target: RulesTarget): string {
  switch (target.kind) {
    case "workspace":
      return "ルール";
    case "self":
      return "自身の層のルール";
    case "project":
      return `${target.name} のルール`;
  }
}

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

function samplesSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("samplesPath", DEFAULT_SAMPLES);
}

/** `ccnaviBoard.openRules` の本体。引数なしはワークスペースのルール */
export async function openRules(target: RulesTarget = { kind: "workspace" }): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    vscode.window.showInformationMessage("ワークスペースが開かれていないため、ルール設定画面を表示できない");
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
    vscode.window.showErrorMessage(`ルール設定画面を表示できない: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }

  // タブは読む前に作る。層の置き場を実行ファイルに聞く間、押しても何も起きないように見えないように。
  // `state` を先に立てるので、読んでいる間に押し直しても上の `reveal` に入る。
  // 読めなかったときもタブは閉じず、中にエラーを出す（`reload` の `showError`）
  const panel = vscode.window.createWebviewPanel("ccnaviRules", titleOf(target), vscode.ViewColumn.One, {
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
    tmpDir: fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-rules-")),
    host: rulesHost(panel),
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
async function switchTarget(current: PanelState, target: RulesTarget): Promise<void> {
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
  current.host.send({ kind: "loading", text: loadingText(whatOf(target)) });
  await reload(current);
}

async function readPage(root: string, target: RulesTarget): Promise<Loaded> {
  const settingsText = readText(path.join(root, ".claude", "settings.json"));
  const localText = readText(path.join(root, ".claude", "settings.local.json"));
  const env = (name: string) => (settingsText !== undefined && envFromSettingsJson(settingsText, name)) || "";
  let rulesPath: string;
  let rulesRel: string;
  const notices: string[] = [];
  if (target.kind === "workspace") {
    // 共通層の置き場は `.ccnavi/common/` 固定。env では動かないので設定ファイルは読まない（ADR-0052）。
    rulesRel = DEFAULT_RULES;
    rulesPath = resolveIn(root, rulesRel);
  } else {
    // 層の置き場は実行ファイルに聞く。CCNAVI_PROJECT_HOME を読んで自分で組むと、組み方が実行ファイルと
    // ずれたときに、この画面で保存したルールが判定に効かなくなる。答えは元リポジトリの版で、
    // ワークツリーの中の版は指さない（設計 §11.2）。
    const board = await loadBoard(root, binSetting());
    if (!board.ok) {
      throw new Error(`層の置き場を実行ファイルから取得できない: ${board.error}`);
    }
    const layer = target.kind === "self" ? selfLayer(board.board) : projectLayer(board.board, target.name);
    if (layer === undefined || layer.rules.path === "") {
      throw new Error(
        target.kind === "self"
          ? "実行ファイルの答えに自身の層が無い"
          : `プロジェクト ${target.name} は層として数えられていない（置き場の直下に無いか、予約名 common / self）`,
      );
    }
    rulesPath = resolveIn(root, layer.rules.path);
    rulesRel = path.relative(root, rulesPath).split(path.sep).join("/");
    if (layer.rules.unreadable !== "") {
      notices.push(`実行ファイルはこのファイルを読めず、層を空として扱っている（ここのルールは 1 件も効いていない）: ${layer.rules.unreadable}`);
    }
  }
  let text: string;
  let mtimeMs: number;
  try {
    text = fs.readFileSync(rulesPath, "utf8");
    mtimeMs = fs.statSync(rulesPath).mtimeMs;
  } catch (error) {
    const hint = target.kind === "workspace" ? "" : "。無いならプロジェクト管理画面の「共通層からコピー」で作る";
    throw new Error(`ルールファイルを読めない（${rulesRel}）: ${(error as Error).message}${hint}`);
  }
  const hooks = [
    ...(settingsText === undefined ? [] : parseHooks(settingsText, "settings")),
    ...(localText === undefined ? [] : parseHooks(localText, "settings-local")),
  ];
  const samplesRel = samplesSetting();
  return {
    target,
    mtimeMs,
    doc: readRules(text),
    rulesPath,
    rulesRel,
    notices,
    hooks,
    hookFiles: { settings: settingsText !== undefined, settingsLocal: localText !== undefined },
    mode: env("CCNAVI_MODE"),
    samplesPath: resolveIn(root, samplesRel),
    samplesRel,
  };
}

function readText(filePath: string): string | undefined {
  try {
    return fs.readFileSync(filePath, "utf8");
  } catch {
    return undefined;
  }
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
    current.host.post({ type: "lock", lock: current.lock } satisfies ToRules);
    postAppearance(current.host);
    if (current.changedPending) {
      current.host.post({ type: "changed" } satisfies ToRules);
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
 * ルールファイルと設定ファイルが変わったら「外で変わった」と伝える。自分の保存は除く。
 * 対象のパスは層の置き場で変わるので、再読込のたびに張り直す。
 */
function watchFiles(current: PanelState): void {
  for (const watcher of current.fileWatchers) {
    watcher.dispose();
  }
  current.fileWatchers = [];
  const rulesRel = current.loaded?.rulesRel ?? DEFAULT_RULES;
  for (const pattern of [toGlob(rulesRel), ".claude/settings.json", ".claude/settings.local.json"]) {
    const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(current.folder, pattern));
    const changed = () => scheduleChanged(current);
    watcher.onDidCreate(changed);
    watcher.onDidChange(changed);
    watcher.onDidDelete(changed);
    current.fileWatchers.push(watcher);
  }
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
      current.host.post({ type: "changed" } satisfies ToRules);
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
 * ワークスペースのルールと自身の層はどのツリーの doing でも止め、プロジェクトのルールはそのプロジェクトの doing だけ見る。
 */
async function refreshLock(current: PanelState): Promise<Lock> {
  const result = await loadBoard(current.folder.uri.fsPath, binSetting());
  const lock = result.ok ? lockFromBoard(result.board, projectOf(current.target)) : lockFromError(result.error);
  if (alive(current)) {
    current.lock = lock;
    current.host.post({ type: "lock", lock } satisfies ToRules);
  }
  return lock;
}

/**
 * いま見せるものを渡す。**画面の編集はここで捨てられる**ので、呼ぶのは人が「再読込」を押した
 * ときと、保存が通って中身が入れ替わったときだけ（ADR-0062）。
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
      rulesPath: loaded.rulesRel,
      mode: loaded.mode,
      model: loaded.doc.model,
      hooks: loaded.hooks,
      hookFiles: loaded.hookFiles,
      samplesPath: loaded.samplesRel,
      lock: current.lock,
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
 * ルール設定の画面に渡す口。VS Code のパネルを `retainedHost` の形に合わせる。
 * **入れ物は 1 度しか入らない**ので、表裏は渡さない（保持する画面は裏でも生きている）。
 * パネルの `retainContextWhenHidden` を偽に変えると、送った先が捨てられていても気づけなくなる。
 * 型では止まらないので、ここで見て言う。
 */
function rulesHost(panel: vscode.WebviewPanel): ScreenHost<RulesData> {
  if (panel.options.retainContextWhenHidden !== true) {
    console.error(`ルール設定の画面は retainContextWhenHidden が真であることを前提にしている（retainedHost）。偽のままだと、裏に回った画面へ送り続けて中身が古いまま止まる`);
  }
  return retainedHost<RulesData>(
    {
      html(text: string): void {
        panel.webview.html = text;
      },
      post(message: unknown): void {
        void panel.webview.postMessage(message);
      },
    },
    (data) =>
      renderRulesPage(data, {
        nonce: crypto.randomBytes(16).toString("base64"),
        script: webviewScript(SCREEN),
        style: webviewStyle(SCREEN),
        appearance: readAppearance(),
      }),
  );
}

/**
 * 頼まれたときに読んでいたものが、往復の間に入れ替わっていないか。`what` は捨てるもの。
 *
 * 保存は実行ファイルへ 2 度出る（`--lint` と錠の取り直し）。その間に人が「再読込」を押せば、
 * 画面の編集は捨てられ、新しい中身が出ている。**そこへ古い編集を書くと、捨てたはずのものが
 * ファイルに入る。** 判定とサンプルはファイルに触らないが、捨てた編集で出した答えを
 * 「いまのルールの判定」として見せることになるので、同じく無かったことにする。
 */
function stale(current: PanelState, loaded: Loaded, what: string): boolean {
  if (current.loaded === loaded) {
    return false;
  }
  // 往復の間に別の対象へ切り替わった。捨てるのは同じだが、切り替え先の画面に前の対象の話を出さない
  if (!sameTarget(loaded.target, current.target)) {
    return true;
  }
  fail(current, `読み直したので、${what}は捨てた。いまのルールでやり直す`);
  return true;
}

/** 操作の結果の一言。1 枚目を読み込んでいる間だけ落ちる（裏に回っていても届く） */
function fail(current: PanelState, message: string): void {
  current.host.post({ type: "failed", message } satisfies ToRules);
}

/** 編集中の内容を一時ファイルに書き、実行ファイルへ渡す差し替えを返す */
function stage(current: PanelState, sections: Sections): RulesOverride {
  const loaded = current.loaded;
  if (loaded === undefined) {
    throw new Error("ルールが読み込まれていない");
  }
  const tmp = path.join(current.tmpDir, "rules.yml");
  fs.writeFileSync(tmp, loaded.doc.apply(sections), "utf8");
  return overrideFor(current.target, tmp);
}

function overrideFor(target: RulesTarget, tmp: string): RulesOverride {
  switch (target.kind) {
    case "workspace":
      return { kind: "workspace", path: tmp };
    case "self":
      return { kind: "self", path: tmp };
    case "project":
      return { kind: "project", name: target.name, path: tmp };
  }
}

async function handleMessage(current: PanelState, message: RulesMessage | undefined): Promise<void> {
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
    return;
  }
  // 読み直せていない画面では、ルールに当たる操作はどれも行き先が無い（「再読込」は
  // 押せるが、その道は `reload` が読み直しからやり直す）
  if (current.loaded === undefined && message.type !== "reload") {
    return;
  }
  const root = current.folder.uri.fsPath;
  switch (message.type) {
    case "reload": {
      if (message.dirty) {
        const choice = await vscode.window.showWarningMessage(
          "未保存の変更がある。破棄して読み直す？",
          { modal: true },
          "読み直す",
        );
        if (choice !== "読み直す") {
          // 画面は「再読込」を押した時点でボタンを止めている。やめたことを伝えないと止まったままになる。
          // 問いを出している間にパネルを閉じられるので、送る前に生きているかを見る
          if (alive(current)) {
            current.host.post({ type: "cancelled" } satisfies ToRules);
          }
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
      const target = message.which === "rules" ? current.loaded.rulesPath : current.loaded.samplesPath;
      void vscode.workspace.openTextDocument(target).then(
        (document) => vscode.window.showTextDocument(document),
        () => vscode.window.showInformationMessage(`ファイルを開けなかった: ${target}`),
      );
      return;
    }
    case "pickFile": {
      // 選んだファイルはワークスペースルートからの相対で欄に入れる。外を選んだら入れない。
      // 実行ファイルが読むのはルートの中だけで、lint も外を指すパスを error にする。
      const picked = await vscode.window.showOpenDialog({
        defaultUri: current.folder.uri,
        canSelectFiles: true,
        canSelectFolders: false,
        canSelectMany: false,
        openLabel: "このファイルを渡す",
        title: `${message.field}: モデルへ渡すファイル`,
      });
      const chosen = picked?.[0];
      if (chosen === undefined || !alive(current)) {
        return;
      }
      const rel = path.relative(root, chosen.fsPath);
      if (rel === "" || rel.startsWith("..") || path.isAbsolute(rel)) {
        void vscode.window.showWarningMessage(`ワークスペースの外のファイルは指定できない: ${chosen.fsPath}`);
        return;
      }
      current.host.post({
        type: "picked",
        key: message.key,
        field: message.field,
        path: rel.split(path.sep).join("/"),
      } satisfies ToRules);
      return;
    }
    case "judge": {
      const loaded = current.loaded;
      if (loaded === undefined) {
        return;
      }
      let rules: RulesOverride;
      try {
        rules = stage(current, message.sections);
      } catch (error) {
        fail(current, `編集中の内容を書き出せない: ${(error as Error).message}`);
        return;
      }
      const result = await runTest(root, binSetting(), rules, message.tool, message.subject);
      if (!alive(current) || stale(current, loaded, "この判定")) {
        return;
      }
      if (!result.ok) {
        fail(current, result.error);
        return;
      }
      current.host.post({ type: "judged", result: result.value, hooks: hooksFor(loaded.hooks, message.tool) } satisfies ToRules);
      return;
    }
    case "samples": {
      const loaded = current.loaded;
      if (loaded === undefined) {
        return;
      }
      let rules: RulesOverride;
      try {
        rules = stage(current, message.sections);
      } catch (error) {
        fail(current, `編集中の内容を書き出せない: ${(error as Error).message}`);
        return;
      }
      const result = await runSamples(root, binSetting(), rules, loaded.samplesPath);
      if (!alive(current) || stale(current, loaded, "このサンプルの判定")) {
        return;
      }
      if (!result.ok) {
        fail(current, result.error);
        return;
      }
      current.host.post({ type: "sampled", result: result.value } satisfies ToRules);
      return;
    }
    case "save": {
      await save(current, message.sections);
      return;
    }
  }
}

async function save(current: PanelState, sections: Sections): Promise<void> {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  const root = current.folder.uri.fsPath;
  let tmp: string;
  let text: string;
  try {
    text = loaded.doc.apply(sections);
    tmp = path.join(current.tmpDir, "rules.yml");
    fs.writeFileSync(tmp, text, "utf8");
  } catch (error) {
    fail(current, `編集中の内容を書き出せない: ${(error as Error).message}`);
    return;
  }

  // 1. 検証。error が 1 件でもあれば保存しない。
  const lint = await runLint(root, binSetting(), overrideFor(current.target, tmp));
  if (!alive(current) || stale(current, loaded, "この保存")) {
    return;
  }
  if (!lint.ok) {
    fail(current, lint.error);
    return;
  }
  if (!lint.value.ok) {
    // 苦情は渡した一時ファイルのパスを名乗るので、画面では対象のファイルの綴りに直す。
    fail(current, `--lint が error を報告した。直してから保存する:\n${lint.value.report.split(tmp).join(loaded.rulesRel)}`);
    return;
  }

  // 2. 作業中のチケットが無いこと。押した時点で取り直す。
  const lock = await refreshLock(current);
  if (!alive(current) || stale(current, loaded, "この保存")) {
    return;
  }
  if (lock.locked) {
    fail(current, lock.reason);
    return;
  }

  // 3. 読み込んでから外で変わっていないこと。
  let mtimeMs: number;
  try {
    mtimeMs = fs.statSync(loaded.rulesPath).mtimeMs;
  } catch (error) {
    fail(current, `ルールファイルを確かめられない: ${(error as Error).message}`);
    return;
  }
  if (mtimeMs !== loaded.mtimeMs) {
    fail(current, "ルールファイルが読み込んだあとに外で変更されている。再読込してから編集し直す（この変更は上書きしない）");
    return;
  }

  try {
    current.wroteAt = Date.now();
    fs.writeFileSync(loaded.rulesPath, text, "utf8");
  } catch (error) {
    // 書けなかったのに猶予を立てたままだと、その間の本物の外部変更を握りつぶす。
    current.wroteAt = 0;
    fail(current, `ルールファイルに書けない: ${(error as Error).message}`);
    return;
  }
  await reload(current);
  const tail = lint.value.report.split("\n").filter((l) => l.trim() !== "").pop() ?? "";
  vscode.window.showInformationMessage(`${loaded.rulesRel} に保存した（${tail}）`);
}

function asMessage(message: unknown): RulesMessage | undefined {
  if (typeof message !== "object" || message === null) {
    return undefined;
  }
  const m = message as { type?: unknown; dirty?: unknown; which?: unknown; sections?: unknown; tool?: unknown; subject?: unknown; key?: unknown; field?: unknown };
  switch (m.type) {
    case "ready":
      return { type: "ready" };
    case "reload":
      return { type: "reload", dirty: m.dirty === true };
    case "dirty":
      return typeof m.dirty === "boolean" ? { type: "dirty", dirty: m.dirty } : undefined;
    case "pickFile":
      if (typeof m.key !== "string" || (m.field !== "additionalContextFile" && m.field !== "additionalContextOnceFile")) {
        return undefined;
      }
      return { type: "pickFile", key: m.key, field: m.field };
    case "openFile":
      return m.which === "rules" || m.which === "samples" ? { type: "openFile", which: m.which } : undefined;
    case "save": {
      const sections = asSections(m.sections);
      return sections === undefined ? undefined : { type: "save", sections };
    }
    case "samples": {
      const sections = asSections(m.sections);
      return sections === undefined ? undefined : { type: "samples", sections };
    }
    case "judge": {
      const sections = asSections(m.sections);
      if (sections === undefined || typeof m.subject !== "string") {
        return undefined;
      }
      // 画面の選択肢は KNOWN_TOOLS だけ。それ以外の名前で実行ファイルを起こさない
      if (typeof m.tool !== "string" || !(KNOWN_TOOLS as readonly string[]).includes(m.tool)) {
        return undefined;
      }
      return { type: "judge", sections, tool: m.tool, subject: m.subject };
    }
    default:
      return undefined;
  }
}
