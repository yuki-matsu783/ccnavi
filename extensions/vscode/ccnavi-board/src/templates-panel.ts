/**
 * テンプレート管理画面の Webview パネル。生成・更新・破棄、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。README の手動確認の手順で確かめる。
 *
 * 画面は React（`src/webview/templates/`）で、ここが渡すのは「いま何を見せるか」（`TemplatesData`）だけ。
 * 渡し方は `core/screen-host.ts` の `retainedHost` が決める。この画面は編集の途中を持つので
 * `retainContextWhenHidden` が真で、入れ物（HTML）は 1 度しか入らない。入れ直すと画面が作り直され、打ちかけの編集が消えるため。
 * 中身を渡すのは、画面の編集を捨ててよいときだけ（ユーザが「更新」を押した、取り込みが通った）。
 *
 * 対象は 3 種。共通の設定のテンプレート（`.ccnavi/config/common/templates/`）、ワークスペースの設定のテンプレート、
 * プロジェクト 1 つの設定のテンプレート。画面は読むことと、下書きの取り込みの下見・実行を実行ファイルに任せ、
 * 拡張はファイルを書かない。読んだ中身（一覧・層ごとの中身・統合結果の索引・下書き）は `ccnavi --templates --json` の答えをそのまま渡す。
 * タブは 1 枚だけで、別の対象を開くとそのタブの中身を入れ替える（画面に未保存の変更があれば、破棄して切り替えるかを聞く）。
 * 取り込みは実行ファイルが書き、書けたあとに一覧と下書きを読み直す。
 *
 * チケット制御が disable のワークスペースでは開かない。入口（サイドパネル・コマンドパレット）も同じ鍵で隠れる。
 */
import * as crypto from "node:crypto";
import * as vscode from "vscode";

import { followAppearance, postAppearance, readAppearance } from "./appearance.js";
import {
  checkTemplates,
  checkTemplatesEdit,
  loadBoard,
  runTemplateEdit,
  runTemplateEditYes,
  runTemplateImport,
  runTemplateImportYes,
  runTemplates,
  runTemplateShow,
} from "./ccnavi.js";
import { lockFromBoard, lockFromError, type Lock } from "./core/lock.js";
import { loadingText } from "./core/loading-render.js";
import type { BoardJson } from "./core/model.js";
import { retainedHost, type ScreenHost } from "./core/screen-host.js";
import type { TemplatesTarget } from "./core/screens.js";
import { targetOptions, type TargetOption } from "./core/targets.js";
import { templatesLayerOf } from "./core/templatesmodel.js";
import { editLockReason } from "./core/templates-lock.js";
import { renderTemplatesPage } from "./core/templates-render.js";
import type { EditRequest, ImportRequest, TemplatesData, TemplatesJson, TemplatesMessage, ToTemplates } from "./core/templates-view.js";
import { OWN_WRITE_GRACE_MS, WATCH_DEBOUNCE_MS, WATCH_PATTERNS } from "./core/watch.js";
import { showLoading } from "./loading.js";
import * as diaglog from "./log.js";
import { requireTickets } from "./ticket-control.js";
import { markTourSeen, tourSeen } from "./tour.js";
import { webviewScript, webviewStyle } from "./webview-asset.js";

/** 画面の名前。バンドルのパスは `src/webview/<名前>/main.tsx` → `out/webview/<名前>.js`、`style.css` → `<名前>.css` */
const SCREEN = "templates";
/** 1 枚の画面の「どの対象か」を画面の欄の値に写すための、対象の一覧の既定（共通の設定の欄の kind） */
const COMMON_KIND = "workspace";

interface Loaded {
  /** 読んだ対象。往復の間に切り替わったかを見る */
  readonly target: TemplatesTarget;
  /** `ccnavi --templates --json` の答え */
  readonly data: TemplatesJson;
  /** 切り替えられる対象（共通の設定・ワークスペース・設定のあるプロジェクト） */
  readonly targets: readonly TargetOption[];
  readonly lock: Lock;
  /** プロジェクト名 → そのプロジェクトの作業中だけを見た Lock。"self" も入る */
  readonly projectLocks: Record<string, Lock>;
  /** 取り込み先に選べる層の名前（`data.layers` の name のうち common 以外） */
  readonly importLayers: readonly string[];
  /** 編集の旗を実行ファイルが持つか。持たなければ理由（画面の編集の欄を無効にする） */
  readonly edit: { readonly enabled: boolean; readonly reason: string };
  /** 上部に出す注意。今は空のまま渡す（画面の欄を残すため） */
  readonly notices: readonly string[];
}

interface PanelState {
  /** いま見せている対象。別の対象を開くと入れ替わる（タブは 1 枚） */
  target: TemplatesTarget;
  readonly panel: vscode.WebviewPanel;
  readonly folder: vscode.WorkspaceFolder;
  readonly host: ScreenHost<TemplatesData>;
  /** テンプレートの置き場と設定ファイルの監視。対象の層が変わるので、再読込のたびに張り直す */
  fileWatchers: vscode.FileSystemWatcher[];
  /** チケットの置き場の監視。開いている間ずっと同じ */
  watchers: vscode.FileSystemWatcher[];
  timer?: NodeJS.Timeout;
  lockTimer?: NodeJS.Timeout;
  loaded?: Loaded;
  /** 読み直せなかった理由。`loaded` と排他で、どちらかは必ず入っている */
  error?: string;
  lock: Lock;
  projectLocks: Record<string, Lock>;
  /** 「ファイルの変更を検知しました」を出したまま、まだ読み直していない。表に戻ったときに送り直す */
  changedPending: boolean;
  /** 自分が書いた時刻。この直後の監視の反応は、外で変わったものとして扱わない */
  wroteAt: number;
  /** 取り込み・編集の実行ファイルを走らせている間は真。猶予の時間に依らず、この間の監視の反応は外で変わったものとして扱わない */
  writing: boolean;
  /** 画面に未保存の変更があるか（画面が `dirty` で知らせる）。別の対象へ切り替えるときに聞くかを決める */
  dirty: boolean;
  /**
   * 読み直しの番号。読むたびに 1 つ進め、読み終えたときに変わっていたら捨てる。
   * 実行ファイルに聞く間に別の対象へ切り替わると、前の対象の答えが後から届くため
   */
  seq: number;
  /** 「破棄して切り替える？」を出している間は真。重ねて開かれても 2 枚目の問いを出さない */
  asking: boolean;
  /**
   * 錠の読み直しの番号。読むたびに 1 つ進め、読み終えたときに変わっていたら捨てる。
   * 重なって起動した古い答えが後から届いても、新しい錠を上書きしないため
   */
  lockSeq: number;
}

/** 開いているパネル。種類ごとに 1 枚 */
let state: PanelState | undefined;

function sameTarget(a: TemplatesTarget, b: TemplatesTarget): boolean {
  return a.kind === b.kind && (a.kind !== "project" || (b.kind === "project" && a.name === b.name));
}

/** 作業中の止めを絞るプロジェクト。共通の設定とワークスペースの設定は絞らない（どのツリーの doing でも止める） */
function projectOf(target: TemplatesTarget): string | undefined {
  return target.kind === "project" ? target.name : undefined;
}

function titleOf(target: TemplatesTarget): string {
  switch (target.kind) {
    case "workspace":
      return "ccnavi テンプレート管理: 共通の設定";
    case "self":
      return "ccnavi テンプレート管理: ワークスペース";
    case "project":
      return `ccnavi テンプレート管理: プロジェクト ${target.name}`;
  }
}

/** 読み込み中の一言で「何を」読んでいるか（`titleOf` から題を外した短い言い方） */
function whatOf(target: TemplatesTarget): string {
  switch (target.kind) {
    case "workspace":
      return "共通の設定";
    case "self":
      return "ワークスペース";
    case "project":
      return `プロジェクト ${target.name}`;
  }
}

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

/** `ccnaviBoard.openTemplates` の本体。引数なしはワークスペースの設定のテンプレート */
export async function openTemplates(target: TemplatesTarget = { kind: "self" }): Promise<void> {
  if (!requireTickets("テンプレート管理画面")) {
    return;
  }
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    vscode.window.showInformationMessage("ワークスペースが開かれていないため、テンプレート管理画面を表示できません");
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
    vscode.window.showErrorMessage(`テンプレート管理画面を表示できません: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }

  // タブは読む前に作る。実行ファイルに聞く間に、押しても何も起きないように見えるのを避けるため。
  // `state` を先に設定するので、読んでいる間に押し直しても上の `reveal` に入る。
  // 読めなかったときもタブは閉じず、中にエラーを出す（`reload` の `showError`）
  const panel = vscode.window.createWebviewPanel("ccnaviTemplates", titleOf(target), vscode.ViewColumn.One, {
    enableScripts: true,
    enableForms: false,
    localResourceRoots: [],
    // 下書きの取り込みの下見や、開いている本文を持つので、タブを裏に回しても捨てない。
    retainContextWhenHidden: true,
  });
  showLoading(panel, titleOf(target), SCREEN, whatOf(target));
  const current: PanelState = {
    target,
    panel,
    folder,
    host: templatesHost(panel, folder.uri.fsPath),
    fileWatchers: [],
    watchers: [],
    lock: lockFromError("確認中…"),
    projectLocks: {},
    changedPending: false,
    wroteAt: 0,
    writing: false,
    dirty: false,
    seq: 0,
    asking: false,
    lockSeq: 0,
  };
  state = current;
  followAppearance(panel, current.host);
  registerPanelHandlers(current);
  await reload(current);
}

/**
 * 開いているタブの対象を入れ替える。未保存の変更があれば、破棄して切り替えるかを先に聞く。
 * やめたら何もしない（タブは前面に出たまま、前の対象と画面の状態が残る）。
 *
 * 読み終えるまでは前の対象の中身を消して「読み込み中」を見せ、操作も受けない（`loaded` を空にする）。
 */
async function switchTarget(current: PanelState, target: TemplatesTarget): Promise<void> {
  if (current.dirty) {
    // 問いを出している間に別の対象を開かれたら、前面に出すだけにする（問いは先に出したものに答えてもらう）
    if (current.asking) {
      return;
    }
    current.asking = true;
    const choice = await vscode.window.showWarningMessage(
      `編集またはプレビューを開いています。破棄して「${titleOf(target)}」に切り替えますか？`,
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
  current.projectLocks = {};
  // 前の対象のファイルの監視は外す。切り替え先が読めたら `reload` が張り直す。読めずにエラーのままなら、
  // 前の対象の変化を拾い続けない
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

/** プロジェクトごとの作業中の止めを、ボードの答えから作る。"self" はワークスペースの設定の欄 */
function locksOf(board: BoardJson): { readonly lock: Lock; readonly projectLocks: Record<string, Lock> } {
  const projectLocks: Record<string, Lock> = {};
  for (const tree of board.trees) {
    if (tree.kind === "project") {
      projectLocks[tree.name] = lockFromBoard(board, tree.name);
    }
  }
  projectLocks["self"] = lockFromBoard(board, projectOf({ kind: "self" }));
  return { lock: lockFromBoard(board), projectLocks };
}

async function readPage(root: string, target: TemplatesTarget): Promise<Loaded> {
  // 一覧の中身（層・統合結果・下書き）は実行ファイルの答えをそのまま使う。層の場所も拡張は組まない。
  const board = await loadBoard(root, binSetting());
  if (!board.ok) {
    throw new Error(`ボードを実行ファイルから取得できません: ${board.error}`);
  }
  // テンプレートのフラグを知らない古い実行ファイルでは、一覧を読めない。理由をそのまま出す
  const unsupported = await checkTemplates(root, binSetting());
  if (unsupported !== undefined) {
    throw new Error(unsupported);
  }
  const key = currentKey(target);
  const targets = targetOptions(board.board, COMMON_KIND, key);
  const result = await runTemplates(root, binSetting(), templatesLayerOf(key));
  if (!result.ok) {
    throw new Error(result.error);
  }
  const { lock, projectLocks } = locksOf(board.board);
  const importLayers = result.value.layers.map((l) => l.name).filter((n) => n !== "common");
  // 編集の旗は取り込みとは別に確かめる。持たない古い実行ファイルでは、編集の欄だけを無効にする
  const editReason = await checkTemplatesEdit(root, binSetting());
  const edit = { enabled: editReason === undefined, reason: editReason ?? "" };
  return { target, data: result.value, targets, lock, projectLocks, importLayers, edit, notices: [] };
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
  panel.onDidChangeViewState(() => {
    if (!panel.visible || !alive(current)) {
      return;
    }
    current.host.post({ type: "lock", lock: current.lock, projectLocks: current.projectLocks } satisfies ToTemplates);
    postAppearance(current.host);
    if (current.changedPending) {
      current.host.post({ type: "changed" } satisfies ToTemplates);
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
    if (state === current) {
      state = undefined;
    }
  });

  watchFiles(current);
  // チケットが動いたら、取り込みを止めるかを取り直す。
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
 * テンプレートの置き場と設定ファイルが変わったら、読み直す（画面に未保存の変更が無いとき）か、
 * 「ファイルの変更を検知しました」と伝える（未保存の変更があるとき）。
 * 層の場所は読んだ答えから決まるので、再読込のたびに張り直す。
 */
function watchFiles(current: PanelState): void {
  for (const watcher of current.fileWatchers) {
    watcher.dispose();
  }
  current.fileWatchers = [];
  const globs: string[] = (current.loaded?.data.layers ?? []).map((layer) => `${layer.path}/**`);
  globs.push(
    "wip/proposals/templates/**",
    "projects/*/wip/proposals/templates/**",
    ".ccnavi/config/common/templates/**",
    ".ccnavi/config/project/templates/**",
    "projects/*/.ccnavi/config/project/templates/**",
  );
  const patterns: vscode.RelativePattern[] = globs.map((glob) => new vscode.RelativePattern(current.folder, toGlob(glob)));
  patterns.push(new vscode.RelativePattern(current.folder, ".claude/settings.json"));
  patterns.push(new vscode.RelativePattern(current.folder, ".claude/settings.local.json"));
  for (const pattern of patterns) {
    const watcher = vscode.workspace.createFileSystemWatcher(pattern);
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
  if (current.writing || Date.now() - current.wroteAt < OWN_WRITE_GRACE_MS) {
    return;
  }
  if (current.timer !== undefined) {
    clearTimeout(current.timer);
  }
  current.timer = setTimeout(() => {
    current.timer = undefined;
    if (!alive(current)) {
      return;
    }
    // 画面に未保存の変更が無ければ、そのまま読み直す。あれば、画面の編集を残すために知らせるだけにする
    if (!current.dirty) {
      void reload(current);
      return;
    }
    current.changedPending = true;
    current.host.post({ type: "changed" } satisfies ToTemplates);
  }, WATCH_DEBOUNCE_MS);
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
  }, WATCH_DEBOUNCE_MS);
}

/**
 * 取り込みを止めるかを実行ファイルに聞き直して、画面に渡す。確かめられなければ閉じる側。
 * 共通の設定とワークスペースの設定は、どのツリーの doing でも止める。プロジェクトの欄は、そのプロジェクトの doing だけを見る。
 */
async function refreshLock(current: PanelState): Promise<{ lock: Lock; projectLocks: Record<string, Lock> }> {
  // 番号を進めてから読む。読み終えたときに番号が変わっていれば、後から別の読み直しが始まっているので、
  // この答えは古い。画面にも `current.lock` にも入れず、読んだ値だけ返す（新しい答えを古いもので上書きしない）
  current.lockSeq += 1;
  const seq = current.lockSeq;
  const result = await loadBoard(current.folder.uri.fsPath, binSetting());
  let lock: Lock;
  let projectLocks: Record<string, Lock>;
  if (result.ok) {
    ({ lock, projectLocks } = locksOf(result.board));
  } else {
    // プロジェクト用の欄が無いので、画面は共通のロックにそろえる
    lock = lockFromError(result.error);
    projectLocks = {};
  }
  if (alive(current) && current.lockSeq === seq) {
    current.lock = lock;
    current.projectLocks = projectLocks;
    current.host.post({ type: "lock", lock, projectLocks } satisfies ToTemplates);
  }
  return { lock, projectLocks };
}

/**
 * いま見せるものを渡す。画面の編集はここで捨てられるので、呼ぶのはユーザが「更新」を押した
 * ときと、取り込みが通って中身が入れ替わったとき、外で変わったものを読み直したときだけ。
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
      generatedAt: new Date().toISOString(),
      target: currentKey(loaded.target),
      targets: loaded.targets,
      data: loaded.data,
      lock: current.lock,
      projectLocks: current.projectLocks,
      importLayers: loaded.importLayers,
      edit: loaded.edit,
      notices: loaded.notices,
    },
  });
}

/** 読み直せなかったことを見せる。理由は覚えておく（渡せなかったときに `ready` で渡し直すため） */
function showError(current: PanelState, error: string): void {
  current.loaded = undefined;
  current.error = error;
  const target = currentKey(current.target);
  current.host.send({ kind: "error", error, target, targets: targetOptions(undefined, COMMON_KIND, target) });
}

/** 開いている対象の欄の値（`targets.ts` の `kind` と `name`） */
function currentKey(target: TemplatesTarget): { kind: string; name: string } {
  return { kind: target.kind, name: target.kind === "project" ? target.name : "" };
}

/**
 * いまの状態で渡し直す。1 枚目を読み込んでいる間に見送られたものは、画面が
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
 * テンプレート管理の画面に渡す手段。VS Code のパネルを `retainedHost` の形に合わせる。
 * 入れ物は 1 度しか入らないので、表裏は渡さない（保持する画面は裏でも生きている）。
 * パネルの `retainContextWhenHidden` を偽に変えると、送った先が捨てられていても気づけなくなる。
 * 型では止まらないので、ここで見て言う。
 */
function templatesHost(panel: vscode.WebviewPanel, root: string): ScreenHost<TemplatesData> {
  if (panel.options.retainContextWhenHidden !== true) {
    // retainedHost は retainContextWhenHidden が真であることを前提にしている。偽のままだと、裏に回った画面へ
    // 送り続けて中身が古いまま止まる。診断ログにだけ残す（console には出さない。docs/claude/logging.md）
    diaglog.get("ccnavi-board", root).error("画面の前提が崩れている", { screen: "templates", retainContextWhenHidden: false });
  }
  return retainedHost<TemplatesData>(
    {
      html(text: string): void {
        panel.webview.html = text;
      },
      post(message: unknown): void {
        void panel.webview.postMessage(message);
      },
    },
    (data) =>
      renderTemplatesPage(data, {
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
  current.lock = loaded.lock;
  current.projectLocks = loaded.projectLocks;
  show(current);
  watchFiles(current);
  void refreshLock(current);
}

async function handleMessage(current: PanelState, message: TemplatesMessage | undefined): Promise<void> {
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
    // 初回だけ吹き出しの案内を頼む。画面は中身が出てから始め、閉じたら `tourDone` を返す
    if (!tourSeen(SCREEN)) {
      current.host.post({ type: "tour" } satisfies ToTemplates);
    }
    return;
  }
  if (message.type === "tourDone") {
    markTourSeen(SCREEN);
    return;
  }
  // 読み直せていない画面では、中身に当たる操作はどれも行き先が無い（「更新」は押せるが、
  // その経路は `reload` が読み直しからやり直す）。取り込みだけは、はじかれた理由を画面に返す
  if (current.loaded === undefined && message.type !== "refresh" && message.type !== "importYes") {
    return;
  }
  switch (message.type) {
    case "refresh": {
      if (message.dirty) {
        const choice = await vscode.window.showWarningMessage(
          "編集またはプレビューを開いています。破棄して更新しますか？",
          { modal: true },
          "更新",
        );
        if (choice !== "更新") {
          // 画面は「更新」を押した時点で欄を止めている。やめたことを伝えないと止まったままになる
          current.host.post({ type: "cancelled" } satisfies ToTemplates);
          return;
        }
      }
      await reload(current);
      return;
    }
    case "switchTarget": {
      // 一覧にある対象だけを受ける。画面が古いまま、消えたプロジェクトを指していても開かない
      const option = (current.loaded?.targets ?? targetOptions(undefined, COMMON_KIND, currentKey(current.target))).find(
        (t) => t.kind === message.kind && t.name === message.name,
      );
      if (option === undefined) {
        return;
      }
      const target: TemplatesTarget =
        option.kind === "project" ? { kind: "project", name: option.name } : option.kind === "workspace" ? { kind: "workspace" } : { kind: "self" };
      if (!sameTarget(current.target, target)) {
        await switchTarget(current, target);
      }
      return;
    }
    case "showTemplate": {
      // 1 件の本文は実行ファイルに読ませる。待つ間に別の対象へ切り替わっても、画面が名前と層で突き合わせる
      const result = await runTemplateShow(current.folder.uri.fsPath, binSetting(), message.name, templatesLayerOf(currentKey(current.target)));
      if (!alive(current)) {
        return;
      }
      current.host.post({
        type: "detail",
        name: message.name,
        layer: message.layer,
        template: result.ok ? result.value : null,
        error: result.ok ? "" : result.error,
      } satisfies ToTemplates);
      return;
    }
    case "previewImport": {
      // 下見は何も書かない。答えを見せるだけ
      const result = await runTemplateImport(current.folder.uri.fsPath, binSetting(), message.request);
      if (!alive(current)) {
        return;
      }
      current.host.post({
        type: "preview",
        request: message.request,
        answer: result.ok ? result.value : null,
        error: result.ok ? "" : result.error,
      } satisfies ToTemplates);
      return;
    }
    case "importYes": {
      await importYes(current, message.request, message.expect);
      return;
    }
    case "previewEdit": {
      // 下見は何も書かない。答えを見せるだけ
      const result = await runTemplateEdit(current.folder.uri.fsPath, binSetting(), message.request);
      if (!alive(current)) {
        return;
      }
      current.host.post({
        type: "editPreview",
        request: message.request,
        answer: result.ok ? result.value : null,
        error: result.ok ? "" : result.error,
      } satisfies ToTemplates);
      return;
    }
    case "editYes": {
      await editYes(current, message.request, message.expect);
      return;
    }
  }
}

/**
 * 取り込みの実行。実行ファイルを呼ぶ前に、拡張の側で確かめられることを確かめる（はじかれたら実行ファイルは呼ばない）。
 * 書けたら一覧と下書きを読み直す（下書きは取り込みで消えることがある）。
 */
async function importYes(current: PanelState, request: ImportRequest, expect: string): Promise<void> {
  const refuse = (error: string): void => {
    current.host.post({ type: "imported", request, answer: null, error } satisfies ToTemplates);
  };
  const loaded = current.loaded;
  if (loaded === undefined) {
    refuse("画面を再読み込みしてください");
    return;
  }
  // 錠は押した時点で取り直す。キャッシュした錠は、チケットが動いてから更新されるまでの隙を通すため
  const fresh = await refreshLock(current);
  if (!alive(current)) {
    return;
  }
  const lock = request.to === "common" ? fresh.lock : (fresh.projectLocks[request.layer] ?? fresh.lock);
  if (lock.locked) {
    refuse(lock.reason);
    return;
  }
  if (request.to === "project" && !loaded.importLayers.includes(request.layer)) {
    refuse(`取り込み先の層が一覧にありません: ${request.layer}`);
    return;
  }
  if (!loaded.data.drafts.some((d) => d.name === request.draft)) {
    refuse(`下書きが一覧にありません: ${request.draft}`);
    return;
  }
  if (!/^[0-9a-f]{64}$/.test(expect)) {
    refuse("ハッシュの形が違います");
    return;
  }

  current.writing = true;
  current.wroteAt = Date.now();
  let result: Awaited<ReturnType<typeof runTemplateImportYes>>;
  try {
    result = await runTemplateImportYes(current.folder.uri.fsPath, binSetting(), request, expect);
  } finally {
    current.writing = false;
  }
  if (!alive(current)) {
    return;
  }
  current.wroteAt = Date.now();
  const answer = result.ok ? result.value : null;
  if (answer === null || !answer.written) {
    current.wroteAt = 0;
    if (answer === null) {
      // 書けたかどうか分からない。抑制した監視の反応を取り戻すため、外で変わったときと同じ道に通す
      scheduleChanged(current);
    }
  }
  if (answer !== null && answer.written) {
    // 先に読み直す。`reload` は中身を渡すと画面の状態の一言を消すので、「取り込みました」は読み直したあとに送る。
    // 読み直しの前に、画面の未保存の変更は無いものとして扱う（`reload` は確認を出さずに中身を渡すため）
    current.dirty = false;
    await reload(current);
    if (!alive(current)) {
      return;
    }
  }
  current.host.post({ type: "imported", request, answer, error: result.ok ? "" : result.error } satisfies ToTemplates);
}

/**
 * 編集の実行。取り込みと同じく、実行ファイルを呼ぶ前に拡張の側で確かめられることを確かめる。
 * 書けたら一覧と下書きを読み直す（下書きは編集で消えることがある）。
 */
async function editYes(current: PanelState, request: EditRequest, expect: string): Promise<void> {
  const refuse = (error: string): void => {
    current.host.post({ type: "edited", request, answer: null, error } satisfies ToTemplates);
  };
  const loaded = current.loaded;
  if (loaded === undefined) {
    refuse("画面を再読み込みしてください");
    return;
  }
  if (!loaded.edit.enabled) {
    refuse(loaded.edit.reason);
    return;
  }
  // 錠は押した時点で取り直す（取り込みと同じ理由）
  const fresh = await refreshLock(current);
  if (!alive(current)) {
    return;
  }
  const reason = lockReasonOf(fresh, request);
  if (reason !== "") {
    refuse(reason);
    return;
  }
  if (request.on === "project" && !loaded.importLayers.includes(request.layer)) {
    refuse(`層が一覧にありません: ${request.layer}`);
    return;
  }
  if (request.op === "move" && request.to === "project" && !loaded.importLayers.includes(request.toLayer)) {
    refuse(`層が一覧にありません: ${request.toLayer}`);
    return;
  }
  // 下書きを既存のものとして触る操作だけ、一覧に在るかを見る。保存は新しい下書きも作るので見ない
  const touchesDraft = request.op === "delete-draft" || (request.op !== "save" && request.on === "draft");
  if (touchesDraft && !loaded.data.drafts.some((d) => d.name === request.name)) {
    refuse(`下書きが一覧にありません: ${request.name}`);
    return;
  }
  if (!/^[0-9a-f]{64}$/.test(expect)) {
    refuse("ハッシュの形が違います");
    return;
  }

  current.writing = true;
  current.wroteAt = Date.now();
  let result: Awaited<ReturnType<typeof runTemplateEditYes>>;
  try {
    result = await runTemplateEditYes(current.folder.uri.fsPath, binSetting(), request, expect);
  } finally {
    current.writing = false;
  }
  if (!alive(current)) {
    return;
  }
  current.wroteAt = Date.now();
  const answer = result.ok ? result.value : null;
  if (answer === null || !(answer.written || answer.removed)) {
    current.wroteAt = 0;
    if (answer === null) {
      // 書けたかどうか分からない。抑制した監視の反応を取り戻すため、外で変わったときと同じ道に通す
      scheduleChanged(current);
    }
  }
  if (answer !== null && (answer.written || answer.removed)) {
    // 取り込みと同じく、先に読み直してから結果を送る
    current.dirty = false;
    await reload(current);
    if (!alive(current)) {
      return;
    }
  }
  current.host.post({ type: "edited", request, answer, error: result.ok ? "" : result.error } satisfies ToTemplates);
}

/**
 * 編集の主語（と移す先）の錠。下書きだけを触る操作には効力が無いので空文字。掛かっていなければ空文字、
 * 掛かっていればその理由。主語は common なら共通の錠、project なら層の錠（無ければ作業中全体の錠）
 */
function lockReasonOf(fresh: { lock: Lock; projectLocks: Record<string, Lock> }, request: EditRequest): string {
  return editLockReason(request, fresh.lock, fresh.projectLocks);
}

function asEditRequest(value: unknown): EditRequest | undefined {
  if (typeof value !== "object" || value === null) {
    return undefined;
  }
  const r = value as { op?: unknown; name?: unknown; on?: unknown; layer?: unknown; newName?: unknown; to?: unknown; toLayer?: unknown; body?: unknown };
  const op = r.op;
  const on = r.on;
  const to = r.to;
  if (op !== "save" && op !== "delete-draft" && op !== "rename" && op !== "move" && op !== "delete") {
    return undefined;
  }
  if (on !== "draft" && on !== "common" && on !== "project") {
    return undefined;
  }
  if (to !== "" && to !== "common" && to !== "project") {
    return undefined;
  }
  if (
    typeof r.name !== "string" ||
    typeof r.layer !== "string" ||
    typeof r.newName !== "string" ||
    typeof r.toLayer !== "string" ||
    typeof r.body !== "string" ||
    r.name.trim() === ""
  ) {
    return undefined;
  }
  return { op, name: r.name, on, layer: r.layer, newName: r.newName, to, toLayer: r.toLayer, body: r.body };
}

function asRequest(value: unknown): ImportRequest | undefined {
  if (typeof value !== "object" || value === null) {
    return undefined;
  }
  const r = value as { draft?: unknown; to?: unknown; layer?: unknown; replace?: unknown };
  if (typeof r.draft !== "string" || typeof r.layer !== "string" || typeof r.replace !== "boolean") {
    return undefined;
  }
  if (r.to !== "common" && r.to !== "project") {
    return undefined;
  }
  return { draft: r.draft, to: r.to, layer: r.layer, replace: r.replace };
}

function asMessage(message: unknown): TemplatesMessage | undefined {
  if (typeof message !== "object" || message === null) {
    return undefined;
  }
  const m = message as { type?: unknown; dirty?: unknown; kind?: unknown; name?: unknown; layer?: unknown; request?: unknown; expect?: unknown };
  switch (m.type) {
    case "switchTarget":
      return typeof m.kind === "string" && typeof m.name === "string" ? { type: "switchTarget", kind: m.kind, name: m.name } : undefined;
    case "refresh":
      return typeof m.dirty === "boolean" ? { type: "refresh", dirty: m.dirty } : undefined;
    case "dirty":
      return typeof m.dirty === "boolean" ? { type: "dirty", dirty: m.dirty } : undefined;
    case "showTemplate":
      return typeof m.name === "string" && typeof m.layer === "string" ? { type: "showTemplate", name: m.name, layer: m.layer } : undefined;
    case "previewImport": {
      const request = asRequest(m.request);
      return request === undefined ? undefined : { type: "previewImport", request };
    }
    case "importYes": {
      const request = asRequest(m.request);
      return request !== undefined && typeof m.expect === "string" ? { type: "importYes", request, expect: m.expect } : undefined;
    }
    case "previewEdit": {
      const request = asEditRequest(m.request);
      return request === undefined ? undefined : { type: "previewEdit", request };
    }
    case "editYes": {
      const request = asEditRequest(m.request);
      return request !== undefined && typeof m.expect === "string" ? { type: "editYes", request, expect: m.expect } : undefined;
    }
    case "ready":
    case "tourDone":
      return { type: m.type };
    default:
      return undefined;
  }
}
