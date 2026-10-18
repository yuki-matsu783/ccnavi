/**
 * テンプレート管理画面の Webview パネル。生成・更新・破棄、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。README の手動確認の手順で確かめる。
 *
 * 画面は React（`src/webview/templates/`）で、ここが渡すのは「いま何を見せるか」（`TemplatesData`）だけ。
 * 渡し方は `core/screen-host.ts` の `retainedHost` が決める。この画面は開いた行を持つので
 * `retainContextWhenHidden` が真で、入れ物（HTML）は 1 度しか入らない。入れ直すと画面が作り直され、開いた行が閉じるため。
 * 中身を渡すのは、ユーザが「更新」を押したときと、ファイルが外で変わったのを読み直したときだけ。
 *
 * 対象は 3 種。共通の設定のテンプレート（`.ccnavi/config/common/templates/`）、ワークスペースの設定のテンプレート、
 * プロジェクト 1 つの設定のテンプレート。画面は一覧・検索・表示だけで、拡張はファイルを書かない。
 * 読んだ中身（一覧・層ごとの中身・統合結果の索引・下書き）は `ccnavi --templates --json` の答えをそのまま渡す。
 * 下書き（`wip/handoff/` の `template`）の取り込みと編集は「取り込み待ち」の画面が受け持つ。ここからはそちらを開く入口だけを持つ。
 * タブは 1 枚だけで、別の対象を開くとそのタブの中身を入れ替える。
 *
 * チケット制御が disable のワークスペースでは開かない。入口（サイドパネル・コマンドパレット）も同じ鍵で隠れる。
 */
import * as crypto from "node:crypto";
import * as vscode from "vscode";

import { followAppearance, postAppearance, readAppearance } from "./appearance.js";
import { checkHandoff, checkTemplates, loadBoard, runTemplates, runTemplateShow } from "./ccnavi.js";
import { loadingText } from "./core/loading-render.js";
import { MAIN_TREE_KEY } from "./core/handoffmodel.js";
import { retainedHost, type ScreenHost } from "./core/screen-host.js";
import { screens, type TemplatesTarget } from "./core/screens.js";
import { targetOptions, type TargetOption } from "./core/targets.js";
import { templatesLayerOf } from "./core/templatesmodel.js";
import { renderTemplatesPage } from "./core/templates-render.js";
import type { TemplatesData, TemplatesJson, TemplatesMessage, ToTemplates } from "./core/templates-view.js";
import { WATCH_DEBOUNCE_MS } from "./core/watch.js";
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
  /** 取り込み待ち画面を開けるか。実行ファイルが `handoff` の機能を持たなければ理由 */
  readonly handoff: { readonly enabled: boolean; readonly reason: string };
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
  timer?: NodeJS.Timeout;
  loaded?: Loaded;
  /** 読み直せなかった理由。`loaded` と排他で、どちらかは必ず入っている */
  error?: string;
  /**
   * 読み直しの番号。読むたびに 1 つ進め、読み終えたときに変わっていたら捨てる。
   * 実行ファイルに聞く間に別の対象へ切り替わると、前の対象の答えが後から届くため
   */
  seq: number;
}

/** 開いているパネル。種類ごとに 1 枚 */
let state: PanelState | undefined;

function sameTarget(a: TemplatesTarget, b: TemplatesTarget): boolean {
  return a.kind === b.kind && (a.kind !== "project" || (b.kind === "project" && a.name === b.name));
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
    // 開いたテンプレートの行や本文を持つので、タブを裏に回しても捨てない。
    retainContextWhenHidden: true,
  });
  showLoading(panel, titleOf(target), SCREEN, whatOf(target));
  const current: PanelState = {
    target,
    panel,
    folder,
    host: templatesHost(panel, folder.uri.fsPath),
    fileWatchers: [],
    seq: 0,
  };
  state = current;
  followAppearance(panel, current.host);
  registerPanelHandlers(current);
  await reload(current);
}

/**
 * 開いているタブの対象を入れ替える。読み終えるまでは前の対象の中身を消して「読み込み中」を見せ、操作も受けない
 * （`loaded` を空にする）。
 */
async function switchTarget(current: PanelState, target: TemplatesTarget): Promise<void> {
  current.target = target;
  current.panel.title = titleOf(target);
  current.loaded = undefined;
  current.error = undefined;
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
  // 取り込み待ち画面を開けるかは一覧とは別に確かめる。`handoff` を持たない古い実行ファイルでは、開く入口だけを無効にする
  const handoffReason = await checkHandoff(root, binSetting());
  const handoff = { enabled: handoffReason === undefined, reason: handoffReason ?? "" };
  return { target, data: result.value, targets, handoff, notices: [] };
}

function registerPanelHandlers(current: PanelState): void {
  const { panel } = current;

  panel.webview.onDidReceiveMessage((message: unknown) => {
    void handleMessage(current, asMessage(message));
  });

  // 保持する画面は裏でも生きている（`postMessage` は届く）が、VS Code の文書は同じ型定義の中で
  // 食い違っている（`retainContextWhenHidden` の側は「裏の画面には送れない」と言う）。
  // どちらが正しくても困らないよう、表に戻ったところで、いま出すべき知らせを送り直す。
  // 中身（`data`）は送らない。送ると、裏で開いていた行がここで閉じる。
  panel.onDidChangeViewState(() => {
    if (!panel.visible || !alive(current)) {
      return;
    }
    postAppearance(current.host);
  });

  panel.onDidDispose(() => {
    if (current.timer !== undefined) {
      clearTimeout(current.timer);
    }
    for (const watcher of current.fileWatchers) {
      watcher.dispose();
    }
    current.fileWatchers = [];
    if (state === current) {
      state = undefined;
    }
  });

  watchFiles(current);
}

/**
 * テンプレートの置き場と下書きの置き場（`wip/handoff/`）、設定ファイルが変わったら、読み直す。
 * 層の場所は読んだ答えから決まるので、再読込のたびに張り直す。
 */
function watchFiles(current: PanelState): void {
  for (const watcher of current.fileWatchers) {
    watcher.dispose();
  }
  current.fileWatchers = [];
  const globs: string[] = (current.loaded?.data.layers ?? []).map((layer) => `${layer.path}/**`);
  globs.push(
    "wip/handoff/**",
    "projects/*/wip/handoff/**",
    ".claude/worktrees/*/wip/handoff/**",
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

/** ファイルが外で変わったら、少し待ってから読み直す。この画面は書かないので、自分の書き込みを見分ける必要は無い */
function scheduleChanged(current: PanelState): void {
  if (current.timer !== undefined) {
    clearTimeout(current.timer);
  }
  current.timer = setTimeout(() => {
    current.timer = undefined;
    if (alive(current)) {
      void reload(current);
    }
  }, WATCH_DEBOUNCE_MS);
}

/** いま見せるものを渡す。呼ぶのはユーザが「更新」を押したときと、外で変わったものを読み直したときだけ。 */
function show(current: PanelState): void {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  current.error = undefined;
  current.host.send({
    kind: "page",
    page: {
      root: current.folder.uri.fsPath,
      generatedAt: new Date().toISOString(),
      target: currentKey(loaded.target),
      targets: loaded.targets,
      data: loaded.data,
      handoff: loaded.handoff,
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
  show(current);
  watchFiles(current);
}

async function handleMessage(current: PanelState, message: TemplatesMessage | undefined): Promise<void> {
  if (message === undefined || !alive(current)) {
    return;
  }
  if (message.type === "ready") {
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
  // その経路は `reload` が読み直しからやり直す）
  if (current.loaded === undefined && message.type !== "refresh") {
    return;
  }
  switch (message.type) {
    case "refresh": {
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
    case "openHandoff": {
      // 開けない理由（古い実行ファイル）は画面が出している。ここでも見て、開けないときは何もしない
      if (current.loaded?.handoff.enabled !== true) {
        return;
      }
      // プロジェクトの設定を見ているときは、そのプロジェクトのツリーの下書きを見る。それ以外はワークスペース
      const key = current.target.kind === "project" ? `project:${current.target.name}` : MAIN_TREE_KEY;
      await screens().handoff(key);
      return;
    }
  }
}

function asMessage(message: unknown): TemplatesMessage | undefined {
  if (typeof message !== "object" || message === null) {
    return undefined;
  }
  const m = message as { type?: unknown; kind?: unknown; name?: unknown; layer?: unknown };
  switch (m.type) {
    case "switchTarget":
      return typeof m.kind === "string" && typeof m.name === "string" ? { type: "switchTarget", kind: m.kind, name: m.name } : undefined;
    case "showTemplate":
      return typeof m.name === "string" && typeof m.layer === "string" ? { type: "showTemplate", name: m.name, layer: m.layer } : undefined;
    case "ready":
    case "refresh":
    case "tourDone":
    case "openHandoff":
      return { type: m.type };
    default:
      return undefined;
  }
}
