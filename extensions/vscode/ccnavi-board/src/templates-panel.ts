/**
 * テンプレート管理画面の Webview パネル。生成・更新・破棄、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。docs/design/manual-checks.md の手動確認の手順で確かめる。
 *
 * 画面は React（`src/webview/templates/`）で、ここが渡すのは「いま何を見せるか」（`TemplatesData`）だけ。
 * 渡し方は `core/screen-host.ts` の `retainedHost` が決める。この画面は開いた行を持つので
 * `retainContextWhenHidden` が真で、入れ物（HTML）は 1 度しか入らない。入れ直すと画面が作り直され、開いた行が閉じるため。
 * 中身を渡すのは、ユーザが「更新」を押したときと、ファイルが外で変わったのを読み直したときだけ。
 *
 * 対象は 3 種。共通の設定のテンプレート（`.ccnavi/config/common/templates/`）、ワークスペースの設定のテンプレート、
 * プロジェクト 1 つの設定のテンプレート。画面は一覧・検索・表示と、開いている対象の層のテンプレートの編集（計画の項と線・子のフロー）。
 * 読んだ中身（一覧・層ごとの中身・統合結果の索引・下書き）は `ccnavi --templates --json` の答えをそのまま渡す。
 *
 * **編集。** 編集中の中身は画面が持つ。拡張ホストが受け持つのは、実行ファイルでの検査（`--templates --check-file`）と保存だけ。
 * 読めるか・計画の順序・フローの形・図に描く中身は、実行ファイルの答えのまま。保存は、(1) 書き出す本文を実行ファイルで検査して error が無い、
 * (2) 実行ファイルが読み返した中身が、画面が書こうとした中身と同じ（食い違えば書かない）、(3) 開いたときからファイルが外で変わっていない
 * （ハッシュが同じ。新規はまだ無い）、(4) リンクを辿らず一時ファイルの入れ替えで書く（`core/flow-write.ts`）、を満たしたときだけ書く。
 * 編集できるのは、開いている対象の層で、実行ファイルが書ける（`writable`）と答えた層のテンプレートだけ。
 * **作業中の錠。** 作業中のチケットがある間は、編集はできるが保存できない（取り込み待ち画面がテンプレートの取り込みを止めるのと同じ決まり。
 * 共通の設定とワークスペースはどのツリーの作業中でも、プロジェクトはそのプロジェクトの作業中だけ。確かめられなければ止める）。
 * 錠は編集を始めたときと、チケットが動いたときと、保存を押した時点で取り直す。実行ファイルには錠を足さない。
 * 改名・移動・削除は持たない（ユーザがファイルを直接操作する）。
 * 下書き（`wip/handoff/` の `template`）の取り込みと編集は「取り込み待ち」の画面が受け持つ。ここからはそちらを開く入口だけを持つ。
 * タブは 1 枚だけで、別の対象を開くとそのタブの中身を入れ替える。
 *
 * チケット制御が disable のワークスペースでは開かない。入口（サイドパネル・コマンドパレット）も同じ鍵で隠れる。
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import { followAppearance, postAppearance, readAppearance } from "./appearance.js";
import { checkTemplateEdit, checkTemplates, loadBoard, runTemplateCheck, runTemplates, runTemplateShow } from "./ccnavi.js";
import { openHandoffFrom, readHandoffEntry } from "./handoff-entry.js";
import type { HandoffEntry } from "./core/handoff-entry.js";
import { yamlText } from "./core/flow-doc.js";
import { decodeFlowBytes, readFlowFile, writeFlowFile } from "./core/flow-write.js";
import { sameData } from "./core/template-edit.js";
import { loadingText } from "./core/loading-render.js";
import { lockFromBoard, lockFromError, type Lock } from "./core/lock.js";
import { retainedHost, type ScreenHost } from "./core/screen-host.js";
import type { TemplatesTarget } from "./core/screens.js";
import { targetOptions, type TargetOption } from "./core/targets.js";
import { shownLayerOf, templateNameTaken, templatesLayerOf, validTemplateName } from "./core/templatesmodel.js";
import { renderTemplatesPage } from "./core/templates-render.js";
import type { TemplateCheck, TemplateLayer, TemplatesData, TemplatesJson, TemplatesMessage, ToTemplates } from "./core/templates-view.js";
import { WATCH_DEBOUNCE_MS, WATCH_PATTERNS } from "./core/watch.js";
import { showLoading } from "./loading.js";
import * as diaglog from "./log.js";
import { requireTickets } from "./ticket-control.js";
import { markTourSeen, tourDue, tourPanelClosed } from "./tour.js";
import { webviewScript, webviewStyle } from "./webview-asset.js";
import { confirmDuringTurn } from "./turn-confirm.js";

/** 画面の名前。バンドルのパスは `src/webview/<名前>/main.tsx` → `out/webview/<名前>.js`、`style.css` → `<名前>.css` */
const SCREEN = "templates";
const TITLE = "ccnavi テンプレート管理";

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
  readonly handoff: HandoffEntry;
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
  /** 編集中の本文を実行ファイルに読ませるための一時の置き場。最初に読ませるときに作り、閉じるときに消す */
  tmpDir?: string;
  /** 編集欄を開いている番号。閉じていれば undefined。開いている間だけ、チケットが動いたら錠を取り直して知らせる */
  editing?: number;
  /** 作業中の錠の監視（チケットの置き場）と、取り直しの待ち・番号 */
  lockWatchers: vscode.FileSystemWatcher[];
  lockTimer?: NodeJS.Timeout;
  lockSeq: number;
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

/** 読み込み中の一言で使う対象の名前 */
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
    // 開いたままのタブに、左パネルの案内から案内を頼まれたとき。タブは組み直さないので `ready` は来ない
    if (tourDue(SCREEN) && state !== undefined) {
      state.host.post({ type: "tour" } satisfies ToTemplates);
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
  const panel = vscode.window.createWebviewPanel("ccnaviTemplates", TITLE, vscode.ViewColumn.One, {
    enableScripts: true,
    enableForms: false,
    localResourceRoots: [],
    // 開いたテンプレートの行や本文を持つので、タブを非表示にしても捨てない。
    retainContextWhenHidden: true,
  });
  showLoading(panel, TITLE, SCREEN, whatOf(target));
  const current: PanelState = {
    target,
    panel,
    folder,
    host: templatesHost(panel, folder.uri.fsPath),
    fileWatchers: [],
    lockWatchers: [],
    lockSeq: 0,
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
  const handoff = await readHandoffEntry(root, binSetting());
  return { target, data: result.value, targets, handoff, notices: [] };
}

function registerPanelHandlers(current: PanelState): void {
  const { panel } = current;

  panel.webview.onDidReceiveMessage((message: unknown) => {
    void handleMessage(current, asMessage(message));
  });

  // 保持する画面は非表示でも生きている（`postMessage` は届く）が、VS Code の文書は同じ型定義の中で
  // 記述が一致していない（`retainContextWhenHidden` の側は「非表示の画面には送れない」と言う）。
  // どちらが正しくても困らないよう、再表示されたところで、いま出すべき知らせを送り直す。
  // 中身（`data`）は送らない。送ると、非表示の間に開いていた行がここで閉じる。
  panel.onDidChangeViewState(() => {
    if (!panel.visible || !alive(current)) {
      return;
    }
    postAppearance(current.host);
  });

  panel.onDidDispose(() => {
    tourPanelClosed(SCREEN);
    if (current.timer !== undefined) {
      clearTimeout(current.timer);
    }
    if (current.tmpDir !== undefined) {
      fs.rmSync(current.tmpDir, { recursive: true, force: true });
    }
    if (current.lockTimer !== undefined) {
      clearTimeout(current.lockTimer);
    }
    for (const watcher of current.lockWatchers) {
      watcher.dispose();
    }
    current.lockWatchers = [];
    for (const watcher of current.fileWatchers) {
      watcher.dispose();
    }
    current.fileWatchers = [];
    if (state === current) {
      state = undefined;
    }
  });

  watchFiles(current);
  // チケットが動いたら、編集欄を開いている間だけ、保存を止めるかを取り直す
  for (const pattern of WATCH_PATTERNS) {
    const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(current.folder, pattern));
    const moved = () => scheduleLock(current);
    watcher.onDidCreate(moved);
    watcher.onDidChange(moved);
    watcher.onDidDelete(moved);
    current.lockWatchers.push(watcher);
  }
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

/** ファイルが外で変わったら、少し待ってから読み直す。自分の保存でも読み直す（編集欄は中身を替えない） */
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
 * 入れ物は 1 度しか入らないので、表示の有無は渡さない（保持する画面は非表示でも生きている）。
 * パネルの `retainContextWhenHidden` を偽に変えると、送った先が捨てられていても気づけなくなる。
 * 型では止まらないので、ここで見て言う。
 */
function templatesHost(panel: vscode.WebviewPanel, root: string): ScreenHost<TemplatesData> {
  if (panel.options.retainContextWhenHidden !== true) {
    // retainedHost は retainContextWhenHidden が真であることを前提にしている。偽のままだと、非表示になった画面へ
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
    if (tourDue(SCREEN)) {
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
    case "editOpen": {
      await attempt(() => editOpen(current, message.session, message.name, message.layer), (e) => current.host.post({ type: "editOpened", session: message.session, opened: null, error: e } satisfies ToTemplates));
      return;
    }
    case "editNew": {
      await attempt(() => editNew(current, message.session, message.name), (e) => current.host.post({ type: "editOpened", session: message.session, opened: null, error: e } satisfies ToTemplates));
      return;
    }
    case "editClose": {
      if (current.editing === message.session) {
        current.editing = undefined;
      }
      return;
    }
    case "editCheck": {
      await attempt(() => editCheck(current, message.session, message.seq, message.data), (e) => current.host.post({ type: "editChecked", session: message.session, seq: message.seq, check: null, error: e } satisfies ToTemplates));
      return;
    }
    case "editSave": {
      await attempt(() => editSave(current, message), (e) => current.host.post({ type: "editSaved", session: message.session, sha: "", error: e } satisfies ToTemplates));
      return;
    }
    case "openHandoff": {
      // プロジェクトの設定を見ているときは、そのプロジェクトのツリーの下書きを見る。それ以外はワークスペース
      await openHandoffFrom(current.loaded?.handoff, current.loaded?.target ?? current.target);
      return;
    }
  }
}

function asMessage(message: unknown): TemplatesMessage | undefined {
  if (typeof message !== "object" || message === null) {
    return undefined;
  }
  const m = message as { type?: unknown; kind?: unknown; name?: unknown; layer?: unknown; session?: unknown; seq?: unknown; expect?: unknown; data?: unknown };
  switch (m.type) {
    case "switchTarget":
      return typeof m.kind === "string" && typeof m.name === "string" ? { type: "switchTarget", kind: m.kind, name: m.name } : undefined;
    case "showTemplate":
      return typeof m.name === "string" && typeof m.layer === "string" ? { type: "showTemplate", name: m.name, layer: m.layer } : undefined;
    case "editOpen":
      return typeof m.session === "number" && typeof m.name === "string" && typeof m.layer === "string"
        ? { type: "editOpen", session: m.session, name: m.name, layer: m.layer }
        : undefined;
    case "editNew":
      return typeof m.session === "number" && typeof m.name === "string" ? { type: "editNew", session: m.session, name: m.name } : undefined;
    case "editClose":
      return typeof m.session === "number" ? { type: "editClose", session: m.session } : undefined;
    case "editCheck":
      return typeof m.session === "number" && typeof m.seq === "number" ? { type: "editCheck", session: m.session, seq: m.seq, data: m.data } : undefined;
    case "editSave":
      return typeof m.session === "number" && typeof m.name === "string" && typeof m.layer === "string" && typeof m.expect === "string"
        ? { type: "editSave", session: m.session, name: m.name, layer: m.layer, expect: m.expect, data: m.data }
        : undefined;
    case "ready":
    case "refresh":
    case "tourDone":
    case "openHandoff":
      return { type: m.type };
    default:
      return undefined;
  }
}

// ---- 編集

/** 編集の処理を走らせる。思いがけない例外（一時ファイルが書けない・実行ファイルが落ちるなど）でも、画面が待ち続けないよう理由を返す */
async function attempt(run: () => Promise<void>, fail: (error: string) => void): Promise<void> {
  try {
    await run();
  } catch (error) {
    fail(`編集の処理で失敗しました: ${error instanceof Error ? error.message : String(error)}`);
  }
}

/** 編集の写しを読ませるテンプレートの形。画面が送ってくる `data` は、名前を持つマッピングでなければ受けない */
function dataOf(data: unknown): { readonly name: string } | undefined {
  if (typeof data !== "object" || data === null || Array.isArray(data)) {
    return undefined;
  }
  const name = (data as { name?: unknown }).name;
  return typeof name === "string" ? { name: name.trim() } : undefined;
}

/** 開いている対象に掛ける作業中の錠。確かめられなければ掛かった錠にする */
async function lockOf(current: PanelState, target: TemplatesTarget = current.target): Promise<Lock> {
  const result = await loadBoard(current.folder.uri.fsPath, binSetting());
  if (!result.ok) {
    return lockFromError(result.error);
  }
  return lockFromBoard(result.board, target.kind === "project" ? target.name : undefined);
}

function scheduleLock(current: PanelState): void {
  if (current.editing === undefined) {
    return;
  }
  if (current.lockTimer !== undefined) {
    clearTimeout(current.lockTimer);
  }
  current.lockTimer = setTimeout(() => {
    current.lockTimer = undefined;
    void refreshLock(current);
  }, WATCH_DEBOUNCE_MS);
}

/** 錠を取り直して、編集欄に知らせる。読んでいる間に別の読み直しが始まっていた、または編集欄が閉じていたら、この答えは捨てる */
async function refreshLock(current: PanelState): Promise<void> {
  const session = current.editing;
  if (session === undefined) {
    return;
  }
  current.lockSeq += 1;
  const seq = current.lockSeq;
  const lock = await lockOf(current);
  if (alive(current) && current.lockSeq === seq && current.editing === session) {
    current.host.post({ type: "editLock", session, lock } satisfies ToTemplates);
  }
}

/** 編集の答え（開く）を画面に返す */
function postOpened(current: PanelState, session: number, opened: Extract<ToTemplates, { type: "editOpened" }>["opened"], error = ""): void {
  current.host.post({ type: "editOpened", session, opened, error } satisfies ToTemplates);
}

/** 編集してよい層。開いている対象の層で、実行ファイルが書けると答えたものだけ。理由は画面にそのまま出す */
function editableLayer(current: PanelState, layerName: string): { readonly ok: true; readonly layer: TemplateLayer } | { readonly ok: false; readonly error: string } {
  const data = current.loaded?.data;
  if (data === undefined) {
    return { ok: false, error: "一覧を読めていないため、編集できません" };
  }
  const shown = shownLayerOf(currentKey(current.target));
  if (layerName !== shown) {
    return { ok: false, error: "開いている対象の層のテンプレートだけ編集できます。対象を切り替えてください" };
  }
  const layer = data.layers.find((l) => l.name === shown);
  if (layer === undefined) {
    return { ok: false, error: "この設定のテンプレートの置き場を、実行ファイルが返していません" };
  }
  if (layer.unreadable !== "") {
    return { ok: false, error: `この層の置き場を読めないため、編集できません: ${layer.unreadable}` };
  }
  if (!layer.writable) {
    return { ok: false, error: "この層は読み取り専用です。実行ファイルが書けないと答えたため、置き場の権限を確かめてください" };
  }
  return { ok: true, layer };
}

/** 層の置き場のテンプレートのファイル。名前は形を確かめてから使う（ファイル名になるため） */
function templateFile(root: string, layer: TemplateLayer, name: string): string | undefined {
  if (!validTemplateName(name)) {
    return undefined;
  }
  const dir = path.resolve(root, layer.path);
  const file = path.join(dir, `${name}.yml`);
  const rel = path.relative(dir, file);
  return rel === `${name}.yml` ? file : undefined;
}

/** 一時の置き場に `<名前>.yml` として書き、実行ファイルに読ませる。ファイル名が名前の期待値になる */
async function checkBytes(
  current: PanelState,
  target: TemplatesTarget,
  name: string,
  bytes: string | Uint8Array,
): Promise<{ readonly ok: true; readonly check: TemplateCheck } | { readonly ok: false; readonly error: string }> {
  // 呼び出しごとに別の置き場を使う（保存中の検査と、編集中の検査が同じ名前のファイルを奪い合わない）。
  // 一時の置き場が消えていても作り直す。書けないときは理由にして返す
  let dir: string;
  try {
    if (current.tmpDir === undefined || !fs.existsSync(current.tmpDir)) {
      current.tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-templates-"));
    }
    dir = fs.mkdtempSync(path.join(current.tmpDir, "check-"));
    fs.writeFileSync(path.join(dir, `${name}.yml`), bytes);
  } catch (error) {
    return { ok: false, error: `検査のための一時ファイルを書けません: ${(error as Error).message}` };
  }
  try {
    const result = await runTemplateCheck(current.folder.uri.fsPath, binSetting(), path.join(dir, `${name}.yml`), templatesLayerOf(currentKey(target)));
    return result.ok ? { ok: true, check: result.value } : { ok: false, error: result.error };
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function sha256(bytes: string | Uint8Array): string {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

/**
 * 読ませている間に、対象が別に切り替わった（またはパネルが閉じた）か。切り替わっていたら `reply` で理由を返して真を返す。
 * 層・錠・検査に使う対象は入口で控えたものに揃っているので、途中で変わった答えは使わない（別の層の錠で通さない）
 */
function abandoned(current: PanelState, target: TemplatesTarget, reply: (error: string) => void): boolean {
  if (!alive(current)) {
    return true;
  }
  if (!sameTarget(current.target, target)) {
    reply("対象が切り替わったため、中止しました。開き直してください");
    return true;
  }
  return false;
}

async function editOpen(current: PanelState, session: number, name: string, layerName: string): Promise<void> {
  const target = current.target;
  const unsupported = await checkTemplateEdit(current.folder.uri.fsPath, binSetting());
  if (abandoned(current, target, (e) => postOpened(current, session, null, e))) {
    return;
  }
  if (unsupported !== undefined) {
    postOpened(current, session, null, unsupported);
    return;
  }
  const where = editableLayer(current, layerName);
  const file = where.ok ? templateFile(current.folder.uri.fsPath, where.layer, name) : undefined;
  if (!where.ok || file === undefined) {
    postOpened(current, session, null, where.ok ? `テンプレートの名前 ${name} は使えません` : where.error);
    return;
  }
  let read: ReturnType<typeof readFlowFile>;
  try {
    read = readFlowFile(current.folder.uri.fsPath, file);
  } catch (error) {
    postOpened(current, session, null, (error as Error).message);
    return;
  }
  if (read === undefined) {
    postOpened(current, session, null, `${name}.yml がありません。更新してから開き直してください`);
    return;
  }
  const decoded = decodeFlowBytes(read.bytes);
  if (!decoded.ok) {
    postOpened(current, session, null, decoded.error);
    return;
  }
  const checked = await checkBytes(current, target, name, read.bytes);
  if (abandoned(current, target, (e) => postOpened(current, session, null, e))) {
    return;
  }
  if (!checked.ok) {
    postOpened(current, session, null, checked.error);
    return;
  }
  if (!checked.check.valid) {
    const why = checked.check.problems.map((p) => p.detail).join(" / ");
    postOpened(current, session, null, `読み込めないため、画面では編集できません。ファイルを直接直してください: ${why}`);
    return;
  }
  const lock = await lockOf(current, target);
  if (abandoned(current, target, (e) => postOpened(current, session, null, e))) {
    return;
  }
  current.editing = session;
  postOpened(current, session, { name, layer: layerName, created: false, sha: sha256(read.bytes), data: checked.check.data, check: checked.check, lock });
}

async function editNew(current: PanelState, session: number, name: string): Promise<void> {
  const target = current.target;
  const unsupported = await checkTemplateEdit(current.folder.uri.fsPath, binSetting());
  if (abandoned(current, target, (e) => postOpened(current, session, null, e))) {
    return;
  }
  if (unsupported !== undefined) {
    postOpened(current, session, null, unsupported);
    return;
  }
  const layerName = shownLayerOf(currentKey(current.target));
  const where = editableLayer(current, layerName);
  if (!where.ok) {
    postOpened(current, session, null, where.error);
    return;
  }
  const file = templateFile(current.folder.uri.fsPath, where.layer, name);
  if (file === undefined) {
    postOpened(current, session, null, `テンプレートの名前 ${name} は使えません（英数字で始め、英数字と . _ - のみ、64 字まで）`);
    return;
  }
  // 大文字小文字だけが違う名前は同じものとして扱う（別の機械で 2 本が 1 本に重なるため）。共通の層にある名前も、
  // 同名で中身が違うと統合結果で捨てられるので作らない
  const taken = current.loaded === undefined ? undefined : templateNameTaken(current.loaded.data.layers, layerName, name);
  if (taken !== undefined) {
    postOpened(current, session, null, `名前 ${name} は、${taken.layer === "common" ? "共通" : "この層"}の ${taken.name} と同じです（大文字小文字は区別しません）。別の名前にしてください`);
    return;
  }
  try {
    if (readFlowFile(current.folder.uri.fsPath, file) !== undefined) {
      postOpened(current, session, null, `${name}.yml は既にあります。一覧から開いて編集してください`);
      return;
    }
  } catch (error) {
    postOpened(current, session, null, (error as Error).message);
    return;
  }
  // 型の選択肢を知るために、仮の 1 項で読ませる。画面はこの答えの `types` から最初の項の型を決める
  const data = { name, description: "", plan: ["implement"] };
  const checked = await checkBytes(current, target, name, yamlText(data));
  if (abandoned(current, target, (e) => postOpened(current, session, null, e))) {
    return;
  }
  if (!checked.ok) {
    postOpened(current, session, null, checked.error);
    return;
  }
  const lock = await lockOf(current, target);
  if (abandoned(current, target, (e) => postOpened(current, session, null, e))) {
    return;
  }
  current.editing = session;
  postOpened(current, session, { name, layer: layerName, created: true, sha: "", data, check: checked.check, lock });
}

async function editCheck(current: PanelState, session: number, seq: number, data: unknown): Promise<void> {
  const target = current.target;
  const reply = (check: TemplateCheck | null, error: string): void => {
    current.host.post({ type: "editChecked", session, seq, check, error } satisfies ToTemplates);
  };
  const named = dataOf(data);
  if (named === undefined || !validTemplateName(named.name)) {
    reply(null, "テンプレートの名前が使えません（英数字で始め、英数字と . _ - のみ、64 字まで）");
    return;
  }
  const checked = await checkBytes(current, target, named.name, yamlText(data));
  if (abandoned(current, target, (e) => reply(null, e))) {
    return;
  }
  reply(checked.ok ? checked.check : null, checked.ok ? "" : checked.error);
}

async function editSave(current: PanelState, message: Extract<TemplatesMessage, { type: "editSave" }>): Promise<void> {
  const target = current.target;
  const { session, name, layer: layerName, expect, data } = message;
  const reply = (sha: string, error: string): void => {
    current.host.post({ type: "editSaved", session, sha, error } satisfies ToTemplates);
  };
  const root = current.folder.uri.fsPath;
  if (session !== current.editing) {
    reply("", "保存しません: この編集は開いていません。開き直してください");
    return;
  }
  const where = editableLayer(current, layerName);
  if (!where.ok) {
    reply("", where.error);
    return;
  }
  // 作業中の錠は押した時点で取り直す（開いたあとにチケットが着手されていることがある）
  const lock = await lockOf(current, target);
  if (abandoned(current, target, (e) => reply("", e))) {
    return;
  }
  if (lock.locked) {
    current.host.post({ type: "editLock", session, lock } satisfies ToTemplates);
    reply("", `保存しません: ${lock.reason}`);
    return;
  }
  if (!(await confirmDuringTurn(lock))) {
    reply("", "保存しません。ターン中です。ターンが終わってから保存してください");
    return;
  }
  if (abandoned(current, target, (e) => reply("", e))) {
    return;
  }
  const file = templateFile(root, where.layer, name);
  const named = dataOf(data);
  if (file === undefined || named === undefined || named.name !== name) {
    reply("", "保存しません: テンプレートの名前が使えないか、中身の名前と食い違っています");
    return;
  }
  // 1. 書き出す本文を実行ファイルに読ませる。error が 1 件でもあれば書かない
  const text = yamlText(data);
  const checked = await checkBytes(current, target, name, text);
  if (abandoned(current, target, (e) => reply("", e))) {
    return;
  }
  if (!checked.ok) {
    reply("", `保存しません: ${checked.error}`);
    return;
  }
  const errors = checked.check.problems.filter((p) => p.severity === "error");
  if (!checked.check.valid || errors.length > 0) {
    reply("", `保存しません: ${errors.map((p) => p.detail).join(" / ") || "実行ファイルが読み込めませんでした"}`);
    return;
  }
  // 2. 実行ファイルが読み返した中身が、画面が書こうとした中身と同じときだけ書く
  if (!sameData(data, checked.check.data)) {
    reply("", "保存しません: 実行ファイルが読み返した中身が、画面の中身と食い違いました（YAML の読み方の違い）。食い違う欄を直してから、保存し直してください");
    return;
  }
  // 錠は、読ませている間に着手されたチケットを見逃さないよう、読んで書く直前（同期の区間の手前）にもう一度取り直す
  const final = await lockOf(current, target);
  if (abandoned(current, target, (e) => reply("", e))) {
    return;
  }
  if (final.locked) {
    current.host.post({ type: "editLock", session, lock: final } satisfies ToTemplates);
    reply("", `保存しません: ${final.reason}`);
    return;
  }
  // 3. 開いたときからファイルが外で変わっていない（新規はまだ無い）
  let read: ReturnType<typeof readFlowFile>;
  try {
    read = readFlowFile(root, file);
  } catch (error) {
    reply("", (error as Error).message);
    return;
  }
  if (expect === "" ? read !== undefined : read === undefined || sha256(read.bytes) !== expect) {
    reply("", expect === "" ? `${name}.yml は、開いたあとに画面の外で作られています。更新してからやり直してください（上書きしません）` : `${name}.yml は、開いたあとに画面の外で変わっています。この編集は上書きしません。更新して開き直してください`);
    return;
  }
  // 4. リンクを辿らず、一時ファイルの入れ替えで書く
  const written = writeFlowFile(root, file, text, { exists: read !== undefined, mtimeMs: read?.mtimeMs ?? 0 }, "テンプレート");
  if (!written.ok) {
    reply("", written.error);
    return;
  }
  reply(sha256(text), "");
  await reload(current);
}
