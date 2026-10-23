/**
 * 取り込み待ち画面の Webview パネル。生成・更新・破棄、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。docs/design/manual-checks.md の手動確認の手順で確かめる。
 *
 * `wip/handoff/<相対パス>` に置いた下書きを、同じツリーの `<相対パス>` へ取り込む画面。見るツリーは 1 本で、
 * 実行ファイルに `--root` で渡す（ワークスペース、プロジェクト、ワークツリーのどれか）。
 * 一覧は `ccnavi user handoff --json`（見るだけ）の答えをそのまま渡し、取り込みは実行ファイルが書く。拡張はファイルを書かない。
 * 取り込む手順（見るだけをもう一度呼んで `expect` を取る、錠を取り直す）は `core/handoff-import.ts` に分けてあり、
 * ここはそれに実行ファイルの呼び出しを渡すだけ。
 *
 * 渡し方は `core/screen-host.ts` の `retainedHost` が決める。この画面は選んだ対象や開いた行を持つので
 * `retainContextWhenHidden` が真で、入れ物（HTML）は 1 度しか入らない。
 *
 * `features` に `handoff` を持たない実行ファイルでは、画面を無効にして「古い版です」と言う。
 * チケット制御が disable でも開ける（作業中の錠は、チケットが無ければ掛からない）。
 */
import * as crypto from "node:crypto";
import * as vscode from "vscode";

import { followAppearance, postAppearance, readAppearance } from "./appearance.js";
import { checkHandoff, loadBoard, runHandoff, runHandoffYes } from "./ccnavi.js";
import { importSelected, terminalGuide, type HandoffDeps } from "./core/handoff-import.js";
import { renderHandoffPage } from "./core/handoff-render.js";
import type { HandoffData, HandoffList, HandoffMessage, HandoffTreeOption, ToHandoff } from "./core/handoff-view.js";
import { findTree, isTerminalCommand, keyLabel, lockForTree, MAIN_TREE_KEY, treeOptions } from "./core/handoffmodel.js";
import { lockFromBoard, lockFromError, type Lock } from "./core/lock.js";
import { loadingText } from "./core/loading-render.js";
import type { TreeJson } from "./core/model.js";
import { retainedHost, type ScreenHost } from "./core/screen-host.js";
import { OWN_WRITE_GRACE_MS, WATCH_DEBOUNCE_MS, WATCH_PATTERNS } from "./core/watch.js";
import { showLoading } from "./loading.js";
import * as diaglog from "./log.js";
import { webviewScript, webviewStyle } from "./webview-asset.js";

/** 画面の名前。バンドルのパスは `src/webview/<名前>/main.tsx` → `out/webview/<名前>.js`、`style.css` → `<名前>.css` */
const SCREEN = "handoff";

type Loaded =
  | {
      readonly kind: "page";
      readonly key: string;
      /** 見るツリー。ボードの `trees` に無いワークスペースのルートは、`main` として組む */
      readonly tree: TreeJson;
      readonly list: HandoffList;
      readonly trees: readonly HandoffTreeOption[];
      readonly lock: Lock;
    }
  | { readonly kind: "disabled"; readonly key: string; readonly reason: string };

interface PanelState {
  /** いま見ているツリーの鍵。別のツリーを開くと入れ替わる（タブは 1 枚） */
  key: string;
  readonly panel: vscode.WebviewPanel;
  readonly folder: vscode.WorkspaceFolder;
  readonly host: ScreenHost<HandoffData>;
  /** 下書きと取り込み先の監視 */
  fileWatchers: vscode.FileSystemWatcher[];
  /** チケットの置き場の監視。開いている間ずっと同じ */
  watchers: vscode.FileSystemWatcher[];
  timer?: NodeJS.Timeout;
  lockTimer?: NodeJS.Timeout;
  loaded?: Loaded;
  /** 読み直せなかった理由。`loaded` と排他で、どちらかは必ず入っている */
  error?: string;
  /** 取り込みの実行ファイルを走らせている間は真。この間の監視の反応は外で変わったものとして扱わない */
  writing: boolean;
  /** 自分が書いた時刻。この直後の監視の反応は、外で変わったものとして扱わない */
  wroteAt: number;
  /** 読み直しの番号。読み終えたときに変わっていたら捨てる */
  seq: number;
  /** 錠の読み直しの番号。読み終えたときに変わっていたら捨てる */
  lockSeq: number;
}

/** 開いているパネル。種類ごとに 1 枚 */
let state: PanelState | undefined;

function titleOf(key: string): string {
  return `ccnavi 取り込み待ち: ${keyLabel(key)}`;
}

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

/** `ccnaviBoard.openHandoff` の本体。引数なしはワークスペース */
export async function openHandoff(key: string = MAIN_TREE_KEY): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    vscode.window.showInformationMessage("ワークスペースが開かれていないため、取り込み待ち画面を表示できません");
    return;
  }
  if (state !== undefined) {
    state.panel.reveal(state.panel.viewColumn);
    if (state.key !== key) {
      await switchTree(state, key);
    }
    return;
  }

  // 画面と CSS はバンドルしたものを読んで流し込む。無ければ開かずに言う（パネルだけ出しても白いまま）
  try {
    webviewScript(SCREEN);
    webviewStyle(SCREEN);
  } catch (error) {
    vscode.window.showErrorMessage(`取り込み待ち画面を表示できません: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }

  // タブは読む前に作る（押しても何も起きないように見えて、押し直されるのを避けるため）。
  // 読めなかったときもタブは閉じず、中にエラーを出す（`reload` の `showError`）
  const panel = vscode.window.createWebviewPanel("ccnaviHandoff", titleOf(key), vscode.ViewColumn.One, {
    enableScripts: true,
    enableForms: false,
    localResourceRoots: [],
    // 選んだ対象や開いた行を持つので、タブを非表示にしても捨てない。
    retainContextWhenHidden: true,
  });
  showLoading(panel, titleOf(key), SCREEN, keyLabel(key));
  const current: PanelState = {
    key,
    panel,
    folder,
    host: handoffHost(panel, folder.uri.fsPath),
    fileWatchers: [],
    watchers: [],
    writing: false,
    wroteAt: 0,
    seq: 0,
    lockSeq: 0,
  };
  state = current;
  followAppearance(panel, current.host);
  registerPanelHandlers(current);
  await reload(current);
}

/** 開いているタブのツリーを入れ替える。読み終えるまでは前のツリーの中身を消して「読み込み中」を見せ、操作も受けない */
async function switchTree(current: PanelState, key: string): Promise<void> {
  current.key = key;
  current.panel.title = titleOf(key);
  current.loaded = undefined;
  current.error = undefined;
  if (current.timer !== undefined) {
    clearTimeout(current.timer);
    current.timer = undefined;
  }
  current.host.send({ kind: "loading", text: loadingText(keyLabel(key)) });
  await reload(current);
}

/** ボードの `main` が `trees` に無いとき（ボードが読めたのに無いことは通常ない）の、ワークスペースのツリー */
function mainTree(root: string): TreeJson {
  return { name: "", root, project: "", kind: "main" };
}

async function readPage(workspace: string, key: string): Promise<Loaded> {
  // 実行ファイルが `handoff` を持たなければ、一覧を呼ばずに画面を無効にする
  const unsupported = await checkHandoff(workspace, binSetting());
  if (unsupported !== undefined) {
    return { kind: "disabled", key, reason: unsupported };
  }
  const board = await loadBoard(workspace, binSetting());
  if (!board.ok) {
    throw new Error(`ボードを実行ファイルから取得できません: ${board.error}`);
  }
  const trees = treeOptions(board.board);
  const found = findTree(board.board, key);
  // ワークスペースは、ボードの `main` の root（実パス）ではなく、開いているフォルダを渡す。他は実行ファイルが答えた root
  const tree: TreeJson | undefined = key === MAIN_TREE_KEY ? { ...(found ?? mainTree(workspace)), root: workspace } : found;
  if (tree === undefined) {
    throw new Error(`ツリーが見つかりません: ${keyLabel(key)}`);
  }
  const result = await runHandoff(workspace, binSetting(), tree.root, []);
  if (!result.ok) {
    throw new Error(result.error);
  }
  return { kind: "page", key, tree, list: result.value, trees, lock: lockForTree(board.board, tree) };
}

function registerPanelHandlers(current: PanelState): void {
  const { panel, folder } = current;

  panel.webview.onDidReceiveMessage((message: unknown) => {
    void handleMessage(current, asMessage(message));
  });

  // 保持する画面は非表示でも生きている（`postMessage` は届く）が、VS Code の文書は同じ型定義の中で
  // 記述が一致していない。どちらが正しくても困らないよう、再表示されたところで、いま出すべき知らせを送り直す。
  // 中身（`data`）は送らない。送ると、非表示の間に選んでいた内容がここで消える。
  panel.onDidChangeViewState(() => {
    if (!panel.visible || !alive(current)) {
      return;
    }
    if (current.loaded?.kind === "page") {
      current.host.post({ type: "lock", lock: current.loaded.lock } satisfies ToHandoff);
    }
    postAppearance(current.host);
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

/** 下書きの置き場と、取り込み先になる場所（許可リストの種類）の監視。ワークスペース・プロジェクト・ワークツリーの下を見る */
const WATCH_GLOBS: readonly string[] = [
  "wip/handoff/**",
  "projects/*/wip/handoff/**",
  ".claude/worktrees/*/wip/handoff/**",
  ".ccnavi/config/**",
  "projects/*/.ccnavi/config/**",
  ".ccnavi/docs/**",
  ".ccnavi/CCNAVI.md",
  ".claude/skills/**",
  ".claude/settings.json",
  ".claude/settings.local.json",
];

function watchFiles(current: PanelState): void {
  for (const watcher of current.fileWatchers) {
    watcher.dispose();
  }
  current.fileWatchers = [];
  for (const glob of WATCH_GLOBS) {
    const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(current.folder, glob));
    const changed = () => scheduleChanged(current);
    watcher.onDidCreate(changed);
    watcher.onDidChange(changed);
    watcher.onDidDelete(changed);
    current.fileWatchers.push(watcher);
  }
}

function alive(current: PanelState): boolean {
  return state === current;
}

/** ファイルが外で変わったら、少し待ってから読み直す。自分の取り込みの最中とその直後の反応は、取り込みの側が読み直すので捨てる */
function scheduleChanged(current: PanelState): void {
  if (current.writing || Date.now() - current.wroteAt < OWN_WRITE_GRACE_MS) {
    return;
  }
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
 * 見ているツリーに掛ける錠を、今の状態で取り直す。確かめられなければ掛かった錠にする（閉じる側）。
 * 画面に出すのは読み終えたときに番号が変わっていないときだけ（後から別の読み直しが始まっていれば、この答えは古い）。
 */
async function refreshLock(current: PanelState): Promise<Lock> {
  current.lockSeq += 1;
  const seq = current.lockSeq;
  const result = await loadBoard(current.folder.uri.fsPath, binSetting());
  const loaded = current.loaded;
  let lock: Lock;
  if (!result.ok) {
    lock = lockFromError(result.error);
  } else if (loaded?.kind === "page") {
    lock = lockForTree(result.board, loaded.tree);
  } else {
    lock = lockFromBoard(result.board);
  }
  if (alive(current) && current.lockSeq === seq && loaded?.kind === "page" && current.loaded === loaded) {
    current.loaded = { ...loaded, lock };
    current.host.post({ type: "lock", lock } satisfies ToHandoff);
  }
  return lock;
}

/** いま見せるものを渡す。呼ぶのはユーザが「更新」を押したときと、外で変わったものを読み直したとき、取り込みのあとだけ */
function show(current: PanelState): void {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  current.error = undefined;
  if (loaded.kind === "disabled") {
    current.host.send({ kind: "disabled", reason: loaded.reason });
    return;
  }
  current.host.send({
    kind: "page",
    page: {
      root: current.folder.uri.fsPath,
      generatedAt: new Date().toISOString(),
      tree: loaded.key,
      trees: loaded.trees,
      list: loaded.list,
      lock: loaded.lock,
      notices: [],
    },
  });
}

/** 読み直せなかったことを見せる。理由は覚えておく（渡せなかったときに `ready` で渡し直すため） */
function showError(current: PanelState, error: string): void {
  current.loaded = undefined;
  current.error = error;
  current.host.send({ kind: "error", error, tree: current.key, trees: treeOptions(undefined) });
}

/** いまの状態で渡し直す。1 枚目を読み込んでいる間に見送られたものは、画面が組み上がった（`ready`）ところでここから渡る */
function redraw(current: PanelState): void {
  if (current.loaded !== undefined) {
    show(current);
    return;
  }
  if (current.error !== undefined) {
    showError(current, current.error);
    return;
  }
  current.host.send({ kind: "loading", text: loadingText(keyLabel(current.key)) });
}

/** 取り込み待ちの画面に渡す手段。VS Code のパネルを `retainedHost` の形に合わせる。パネルの前提が崩れていれば診断ログに残す */
function handoffHost(panel: vscode.WebviewPanel, root: string): ScreenHost<HandoffData> {
  if (panel.options.retainContextWhenHidden !== true) {
    diaglog.get("ccnavi-board", root).error("画面の前提が崩れている", { screen: "handoff", retainContextWhenHidden: false });
  }
  return retainedHost<HandoffData>(
    {
      html(text: string): void {
        panel.webview.html = text;
      },
      post(message: unknown): void {
        void panel.webview.postMessage(message);
      },
    },
    (data) =>
      renderHandoffPage(data, {
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
    loaded = await readPage(current.folder.uri.fsPath, current.key);
  } catch (error) {
    if (alive(current) && current.seq === seq) {
      showError(current, (error as Error).message);
    }
    return;
  }
  // 読んでいる間に別のツリーへ切り替わった（または読み直しが重なった）なら、後から来たほうに任せる
  if (!alive(current) || current.seq !== seq) {
    return;
  }
  current.loaded = loaded;
  show(current);
  void refreshLock(current);
}

/** 実行ファイルへの窓口。手順（`core/handoff-import.ts`）はこれを通して実行ファイルを呼ぶ */
function depsOf(current: PanelState, loaded: Extract<Loaded, { kind: "page" }>): HandoffDeps {
  const workspace = current.folder.uri.fsPath;
  return {
    view: (paths) => runHandoff(workspace, binSetting(), loaded.tree.root, paths),
    apply: (paths, expect) => runHandoffYes(workspace, binSetting(), loaded.tree.root, paths, expect),
    lock: async () => {
      const board = await loadBoard(workspace, binSetting());
      return board.ok ? lockForTree(board.board, loaded.tree) : lockFromError(board.error);
    },
  };
}

async function handleMessage(current: PanelState, message: HandoffMessage | undefined): Promise<void> {
  if (message === undefined || !alive(current)) {
    return;
  }
  if (message.type === "ready") {
    // 画面が組み上がった。1 枚目を読み込んでいる間に見送った中身は、ここで渡る。
    // 見送るものが無くても渡し直す（同じ中身がもう 1 度届く）
    current.host.ready();
    redraw(current);
    postAppearance(current.host);
    return;
  }
  if (message.type === "copy") {
    // 画面が出した端末のコマンドだけを写す
    if (isTerminalCommand(message.text)) {
      await vscode.env.clipboard.writeText(message.text);
      void vscode.window.showInformationMessage("端末で打つコマンドをコピーしました");
    }
    return;
  }
  const loaded = current.loaded;
  if (message.type === "refresh") {
    await reload(current);
    return;
  }
  if (message.type === "switchTree") {
    // 一覧にあるツリーだけを受ける。画面が古いまま、消えたツリーを指していても開かない
    const options = loaded?.kind === "page" ? loaded.trees : treeOptions(undefined);
    if (options.some((o) => o.key === message.key) && message.key !== current.key) {
      await switchTree(current, message.key);
    }
    return;
  }
  if (message.type === "import") {
    await importPaths(current, loaded, message.paths);
    return;
  }
  if (message.type === "terminal") {
    if (loaded?.kind !== "page") {
      return;
    }
    const guide = await terminalGuide(depsOf(current, loaded), loaded.list, message.path);
    if (!alive(current)) {
      return;
    }
    current.host.post({ type: "terminal", path: message.path, command: guide.command, error: guide.error } satisfies ToHandoff);
  }
}

/**
 * 取り込みの実行。手順は `importSelected` が持つ（呼ぶ前にはじくこと、見るだけをもう一度呼んで `expect` を取ること、錠）。
 * 実行ファイルを呼んだなら、成否によらず読み直す（書けたかが分からない失敗があるため）。結果は読み直したあとに送る
 * （`data` を渡すと画面の確認の帯が閉じるので、結果の知らせはその後ろに置く）。
 */
async function importPaths(current: PanelState, loaded: Loaded | undefined, paths: readonly string[]): Promise<void> {
  const answer = (result: { readonly ok: false; readonly error: string }): void => {
    current.host.post({ type: "imported", paths, result } satisfies ToHandoff);
  };
  if (loaded?.kind !== "page") {
    answer({ ok: false, error: "画面を再読み込みしてください" });
    return;
  }
  current.writing = true;
  current.wroteAt = Date.now();
  let outcome: Awaited<ReturnType<typeof importSelected>>;
  try {
    outcome = await importSelected(depsOf(current, loaded), loaded.list, paths);
  } finally {
    current.writing = false;
  }
  if (!alive(current)) {
    return;
  }
  current.wroteAt = Date.now();
  if (outcome.attempted) {
    await reload(current);
    if (!alive(current)) {
      return;
    }
  } else {
    // 書いていない。抑えていた監視の反応を取り戻すため、抑える猶予は戻す
    current.wroteAt = 0;
  }
  current.host.post({ type: "imported", paths, result: outcome.result } satisfies ToHandoff);
}

function asMessage(message: unknown): HandoffMessage | undefined {
  if (typeof message !== "object" || message === null) {
    return undefined;
  }
  const m = message as { type?: unknown; key?: unknown; paths?: unknown; path?: unknown; text?: unknown };
  switch (m.type) {
    case "ready":
    case "refresh":
      return { type: m.type };
    case "switchTree":
      return typeof m.key === "string" ? { type: "switchTree", key: m.key } : undefined;
    case "import":
      return Array.isArray(m.paths) && m.paths.every((p): p is string => typeof p === "string") ? { type: "import", paths: m.paths } : undefined;
    case "terminal":
      return typeof m.path === "string" ? { type: "terminal", path: m.path } : undefined;
    case "copy":
      return typeof m.text === "string" ? { type: "copy", text: m.text } : undefined;
    default:
      return undefined;
  }
}
