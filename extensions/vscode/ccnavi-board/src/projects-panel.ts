/**
 * プロジェクト管理画面の Webview パネル。生成・更新・破棄、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API と子プロセスに触れるので単体テストの対象外。docs/design/manual-checks.md の手動確認の手順で確かめる。
 *
 * 画面は React（`src/webview/projects/`）で、ここが渡すのは「いま何を見せるか」（`ProjectsData`）だけ。
 * 渡し方（送る・入れ物ごと・作り直し中で見送る）は `core/screen-host.ts` が決める。この画面は
 * `retainContextWhenHidden` が偽なので、そのまま当たる（打ちかけの clone の欄は Webview の state にある）。
 *
 * 一覧は実行ファイルの答え（`--explain --json` の trees と layers、`--lint --json` の苦情）を並べる。
 * 拡張が自分で見るのは、origin（ローカルの git を読み取り専用で起こす）、ワークスペースとプロジェクトの設定のルールファイル・
 * `.gitignore` の本文、プロジェクトになっていない `.git` の探索だけ。
 * そのルールファイルの場所は layers（レイヤー）の答えを使い、`.ccnavi` から自分で組まない（組み方を実行ファイルとずらさない）。
 *
 * clone は統合ターミナルへ送る。認証の対話はそこでユーザが行い、完了は `projects/<名前>/.git`
 * の出現を監視して拾う。拡張が書くのは、ユーザがボタンを押したときの `.gitignore` だけ。
 *
 * 共通レイヤーのミラー（各プロジェクトの `.ccnavi/config/common/`）のずれは、開いた時と 5 分ごと（`MIRROR_INTERVAL_MS`）に
 * 実行ファイルに聞く（`user mirror --json`。読むだけ）。一覧の読み直し（`update`）とは別に回し、結果は `ProjectsData` の `mirror` に載せる。
 * 処理の流れ:
 *
 * 1. `refreshMirror` が `checkMirror`（実行ファイルが `user mirror` を持つか）→ `runMirror` の順に聞き、終わったら次の回を予約する。
 *    読み込み中に呼ばれたら、終わった後にもう 1 回だけ回す（`update` と同じ重ならない作り）。パネルを閉じたら予約を消して止める
 * 2. 画面の「ミラーを同期」「すべて同期」で `mirrorSync` が届いたら `runMirrorYes` を呼ぶ。書くのは実行ファイルで、
 *    各プロジェクトの作業ツリーへ（コミットはしない）。結果は VS Code の通知で知らせ、すぐ調べ直す
 * 3. 同期している間と、同期の後の調べ直しが終わるまでは、画面のボタンを押せなくする（`busy`）。古い `expect` で押させないため
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

import { followAppearance, postAppearance, readAppearance } from "./appearance.js";
import { checkMirror, loadBoard, runLintJson, runMirror, runMirrorYes } from "./ccnavi.js";
import { projectLayer, selfLayer } from "./core/layers.js";
import { clockText, syncNotice, unknownSyncNames, type MirrorStatus } from "./core/mirrormodel.js";
import {
  buildProjectsPage,
  checkName,
  checkRemote,
  cloneCommand,
  duplicateOf,
  findStrayGitDirs,
  gitignoreHasProjects,
  gitignoreWithProjects,
  type DirEntry,
  type ProjectsPage,
} from "./core/projects.js";
import { renderProjectsPage } from "./core/projects-render.js";
import { showLoading } from "./loading.js";
import type { MirrorData, ProjectsData, ProjectsMessage, ToProjects } from "./core/projects-view.js";
import { screenHost, type ScreenHost } from "./core/screen-host.js";
import { readOrigin } from "./git.js";
import * as diaglog from "./log.js";
import { runInTerminal } from "./terminal.js";
import { markTourSeen, tourDue } from "./tour.js";
import { confirmIfTurn } from "./turn-confirm.js";
import { webviewScript, webviewStyle } from "./webview-asset.js";

/** 他の画面（WATCH_DEBOUNCE_MS = 120）より長い。理由は記録が無く未確認。変える前に、プロジェクトの一覧を読む重さを測ること */
const DEBOUNCE_MS = 300;
/** 画面の名前。バンドルのパスは `src/webview/<名前>/main.tsx` → `out/webview/<名前>.js`、`style.css` → `<名前>.css` */
const SCREEN = "projects";
const TITLE = "ccnavi プロジェクト管理";
/**
 * ミラーのずれを調べ直す間隔。ずれは共通レイヤーを直したときにしか生じず、急ぐものではない。
 * 1 回ごとに実行ファイルを 2 度（`--version --json` と `user mirror --json`）起こすので、短くしすぎない
 */
const MIRROR_INTERVAL_MS = 5 * 60 * 1000;

interface PanelState {
  readonly panel: vscode.WebviewPanel;
  readonly folder: vscode.WorkspaceFolder;
  readonly host: ScreenHost<ProjectsData>;
  watchers: vscode.FileSystemWatcher[];
  timer?: NodeJS.Timeout;
  page?: ProjectsPage;
  /** 読み直せなかった理由。`page` と排他で、どちらかは必ず入っている */
  error?: string;
  loading: boolean;
  again: boolean;
  /** 直前に見た表示の有無。再表示されたら読み直す（非表示の間の変化は監視が検知しても渡せていない） */
  wasVisible: boolean;
  /** ミラーを調べた結果。一覧とは別に回す */
  mirror: MirrorStatus;
  /** 最後にミラーを調べ終えた時刻（`HH:MM`）。まだなら空 */
  mirrorCheckedAt: string;
  /** 次にミラーを調べる予約 */
  mirrorTimer?: NodeJS.Timeout;
  mirrorLoading: boolean;
  mirrorAgain: boolean;
  /** 同期している */
  syncing: boolean;
  /** 終えた同期の回数と、最後に調べ終えた結果が、何回目の同期の後に調べ始めたものか。後者が小さい間は、結果が同期の前の古いもの */
  syncSeq: number;
  seenSeq: number;
}

let state: PanelState | undefined;

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

/** `ccnaviBoard.openProjects` の本体 */
export async function openProjects(): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    vscode.window.showInformationMessage("ワークスペースが開かれていないため、プロジェクト管理画面を表示できません");
    return;
  }
  if (state !== undefined) {
    // `reveal` の前に表示されたことにする。`wasVisible` を真にせずに出すと、下の `update()` と
    // `onDidChangeViewState` の `becameVisible` からの `update()` で、実行ファイルを 2 度起こす
    state.wasVisible = true;
    state.panel.reveal(state.panel.viewColumn);
    void update();
    // 開いたままのタブに案内を頼まれたとき（左パネルの案内から）。組み上がりの `ready` は来ない
    if (tourDue(SCREEN)) {
      state.host.post({ type: "tour" } satisfies ToProjects);
    }
    return;
  }

  // 画面と CSS はバンドルしたものを読んで流し込む。無ければ開かずに言う（パネルだけ出しても白いまま）
  try {
    webviewScript(SCREEN);
    webviewStyle(SCREEN);
  } catch (error) {
    vscode.window.showErrorMessage(`プロジェクト管理画面を表示できません: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }

  // タブは読む前に作る。実行ファイルの答えを待ってから作ると、押しても何も起きないように見え、
  // 押し直した分だけタブが増える（`state` を先に設定するので、2 度目の押下は上の `reveal` に入る）。
  // 読めなかったときもタブは閉じず、中にエラーを出す（`update` の `showError`）
  const panel = vscode.window.createWebviewPanel("ccnaviProjects", TITLE, vscode.ViewColumn.One, {
    enableScripts: true,
    enableForms: false,
    localResourceRoots: [],
    retainContextWhenHidden: false,
  });
  if (panel.options.retainContextWhenHidden !== false) {
    // screenHost は retainContextWhenHidden が偽であることを前提にしている。真だと、非表示になっても画面が捨てられず、表示の有無の読み直しが働かない。診断ログにだけ残す（docs/claude/logging.md）
    diaglog.get("ccnavi-board", folder.uri.fsPath).error("画面の前提が崩れている", { screen: SCREEN, retainContextWhenHidden: true });
  }
  showLoading(panel, TITLE, SCREEN, "プロジェクト");
  const current: PanelState = {
    panel,
    folder,
    host: projectsHost(panel),
    watchers: [],
    loading: false,
    again: false,
    wasVisible: panel.visible,
    mirror: { kind: "waiting" },
    mirrorCheckedAt: "",
    mirrorLoading: false,
    mirrorAgain: false,
    syncing: false,
    syncSeq: 0,
    seenSeq: 0,
  };
  state = current;
  followAppearance(panel, current.host);
  registerPanelHandlers(current);
  void update();
  void refreshMirror();
}

// ---- 読み取り

type Gathered = { readonly ok: true; readonly page: ProjectsPage } | { readonly ok: false; readonly error: string };

/** 実行ファイルの答えと、拡張が自分で見るものを集めて画面の中身にする */
async function gather(root: string): Promise<Gathered> {
  const loaded = await loadBoard(root, binSetting());
  if (!loaded.ok) {
    return { ok: false, error: loaded.error };
  }
  const board = loaded.board;
  const lint = await runLintJson(root, binSetting());
  const projectsDir = board.settings.projects;
  const projectsRel = projectsDir === "" ? "" : toPosix(path.relative(root, projectsDir));
  const self = selfLayer(board);
  const selfRulesPath = self === undefined || self.rules.path === "" ? "" : resolveIn(root, self.rules.path);

  const projects = board.trees.filter((t) => t.kind === "project");
  const origins: Record<string, string> = {};
  const rulesRels: Record<string, string> = {};
  const rulesExists: Record<string, boolean> = {};
  await Promise.all(
    projects.map(async (t) => {
      origins[t.name] = await readOrigin(t.root);
      const layer = projectLayer(board, t.name);
      if (layer !== undefined && layer.rules.path !== "") {
        const rulesPath = resolveIn(root, layer.rules.path);
        rulesRels[t.name] = toPosix(path.relative(root, rulesPath));
        rulesExists[t.name] = isFile(rulesPath);
      }
    }),
  );

  const knownRels = new Set(board.trees.filter((t) => t.kind !== "main").map((t) => toPosix(path.relative(root, t.root))));
  const strays = findStrayGitDirs({
    projectsRel,
    list: (rel) => listDir(path.join(root, ...rel.split("/").filter((s) => s !== ""))),
    knownRels,
  });

  return {
    ok: true,
    page: buildProjectsPage({
      board,
      lint: lint.ok ? lint.value : undefined,
      lintError: lint.ok ? "" : lint.error,
      origins,
      strays,
      projectsRel,
      ignored: gitignoreHasProjects(readText(path.join(root, ".gitignore")), projectsRel),
      rulesRels,
      rulesExists,
      selfRulesRel: selfRulesPath === "" ? "" : toPosix(path.relative(root, selfRulesPath)),
      selfRulesExists: selfRulesPath !== "" && isFile(selfRulesPath),
    }),
  };
}

function listDir(dir: string): readonly DirEntry[] {
  try {
    return fs.readdirSync(dir, { withFileTypes: true }).map((e) => ({ name: e.name, isDir: e.isDirectory() }));
  } catch {
    return [];
  }
}

function readText(filePath: string): string | undefined {
  try {
    return fs.readFileSync(filePath, "utf8");
  } catch {
    return undefined;
  }
}

function isFile(filePath: string): boolean {
  try {
    return fs.statSync(filePath).isFile();
  } catch {
    return false;
  }
}

function toPosix(p: string): string {
  return p.split(path.sep).join("/");
}

function resolveIn(root: string, filePath: string): string {
  return path.isAbsolute(filePath) ? filePath : path.join(root, filePath);
}

// ---- パネル

function registerPanelHandlers(current: PanelState): void {
  const { panel } = current;

  panel.webview.onDidReceiveMessage((message: unknown) => {
    void handleMessage(current, asMessage(message));
  });

  panel.onDidChangeViewState(() => {
    const becameVisible = panel.visible && !current.wasVisible;
    current.wasVisible = panel.visible;
    if (!panel.visible) {
      // `retainContextWhenHidden` は偽なので、非表示になった画面は捨てられる。再表示されると
      // ここで入れてある HTML から作り直され、組み上がったら `ready` が届く
      current.host.hidden();
    }
    if (becameVisible) {
      void update();
    }
  });

  panel.onDidDispose(() => {
    if (current.timer !== undefined) {
      clearTimeout(current.timer);
      current.timer = undefined;
    }
    // ミラーの次の回を止める。読み込み中の回は、終わったところで `state` が変わったのを見て予約しない
    if (current.mirrorTimer !== undefined) {
      clearTimeout(current.mirrorTimer);
      current.mirrorTimer = undefined;
    }
    for (const watcher of current.watchers) {
      watcher.dispose();
    }
    current.watchers = [];
    if (state === current) {
      state = undefined;
    }
  });
}

/**
 * 監視を張る。置き場のパスは実行ファイルの答えから取るので、最初に読めたときに 1 度だけ張る
 * （読めないまま開いたタブは、「更新」で読めたところで張る）。
 */
function watchProjects(current: PanelState, projectsRel: string, selfRulesRel: string): void {
  if (current.watchers.length > 0) {
    return;
  }
  const { folder } = current;
  // clone の完了（`.git` の出現）、ワークスペースとプロジェクトの設定のルールファイルの出入り、origin の変化、ワークツリーの登録、`.gitignore`。
  // 設定のパス（ccnavi ディレクトリの下の `config/`）はワークスペースの設定のパスから取る。プロジェクトの設定も同じ形。
  // ワークスペースの設定のパスが取れない（JSON として読めない）なら、設定のルールファイルの監視は張らない。
  const rel = projectsRel;
  const layerDir = selfRulesRel === "" ? "" : path.posix.dirname(selfRulesRel);
  const patterns = [
    `${rel}/*/.git`,
    `${rel}/*/.git/config`,
    `${rel}/*/.git/worktrees/*`,
    ...(layerDir === "" ? [] : [`${rel}/*/${layerDir}/*`, `${layerDir}/*`]),
    ".gitignore",
    ".claude/settings.json",
  ];
  for (const pattern of patterns) {
    const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(folder, pattern));
    watcher.onDidCreate(scheduleUpdate);
    watcher.onDidChange(scheduleUpdate);
    watcher.onDidDelete(scheduleUpdate);
    current.watchers.push(watcher);
  }
}

function scheduleUpdate(): void {
  const current = state;
  if (current === undefined) {
    return;
  }
  if (current.timer !== undefined) {
    clearTimeout(current.timer);
  }
  current.timer = setTimeout(() => {
    current.timer = undefined;
    if (state === current) {
      void update();
    }
  }, DEBOUNCE_MS);
}

async function update(): Promise<void> {
  const current = state;
  if (current === undefined) {
    return;
  }
  if (current.loading) {
    current.again = true;
    return;
  }
  current.loading = true;
  try {
    const result = await gather(current.folder.uri.fsPath);
    if (state !== current) {
      return;
    }
    if (!result.ok) {
      showError(current, result.error);
      return;
    }
    watchProjects(current, result.page.projectsRel, result.page.selfRulesRel);
    show(current, result.page);
  } finally {
    current.loading = false;
    if (current.again) {
      current.again = false;
      void update();
    }
  }
}

function show(current: PanelState, page: ProjectsPage): void {
  current.page = page;
  current.error = undefined;
  current.host.send({ kind: "page", page, mirror: mirrorData(current) });
}

/** 画面に渡すミラーの状態 */
function mirrorData(current: PanelState): MirrorData {
  return {
    status: current.mirror,
    busy: current.syncing || current.seenSeq < current.syncSeq,
    checkedAt: current.mirrorCheckedAt,
  };
}

/** ミラーの状態が変わったので描き直す。一覧がまだ無い（読み込み中・読めない）ときは、一覧が届くときに一緒に渡る */
function showMirror(current: PanelState): void {
  if (current.page !== undefined) {
    show(current, current.page);
  }
}

// ---- ミラー

/**
 * ミラーのずれを調べ直し、終わったら次の回を予約する。読み込み中に呼ばれたら、終わった後にもう 1 回だけ回す。
 * 読むだけで、何も書かない（同期は `syncMirror` だけ）。
 */
async function refreshMirror(): Promise<void> {
  const current = state;
  if (current === undefined) {
    return;
  }
  if (current.mirrorLoading) {
    current.mirrorAgain = true;
    return;
  }
  current.mirrorLoading = true;
  if (current.mirrorTimer !== undefined) {
    clearTimeout(current.mirrorTimer);
    current.mirrorTimer = undefined;
  }
  // 調べ始めた時点で終えていた同期の回数。この回の結果は、これより後の同期を映していない
  const seq = current.syncSeq;
  try {
    const status = await readMirror(current.folder.uri.fsPath);
    if (state !== current) {
      return;
    }
    current.mirror = status;
    current.mirrorCheckedAt = clockText(new Date());
    current.seenSeq = seq;
    showMirror(current);
  } finally {
    current.mirrorLoading = false;
    // 閉じたパネルは予約しない
    if (state === current) {
      if (current.mirrorAgain) {
        current.mirrorAgain = false;
        void refreshMirror();
      } else {
        current.mirrorTimer = setTimeout(() => {
          current.mirrorTimer = undefined;
          if (state === current) {
            void refreshMirror();
          }
        }, MIRROR_INTERVAL_MS);
      }
    }
  }
}

/** 実行ファイルに、`user mirror` を持つかとミラーのずれを聞く */
async function readMirror(root: string): Promise<MirrorStatus> {
  const unsupported = await checkMirror(root, binSetting());
  if (unsupported !== undefined) {
    return { kind: "unsupported", reason: unsupported };
  }
  const result = await runMirror(root, binSetting());
  return result.ok ? { kind: "ok", list: result.value } : { kind: "failed", error: result.error };
}

/**
 * ミラーを同期する（`names` が空なら差分のある全プロジェクト）。書くのは実行ファイルで、各プロジェクトの作業ツリーへ。
 * 結果は VS Code の通知で知らせ（成功は情報、見せた後に内容が変わっていたら警告、失敗はエラー）、すぐ調べ直す。
 */
async function syncMirror(current: PanelState, names: readonly string[], expect: string): Promise<void> {
  if (current.syncing || current.seenSeq < current.syncSeq) {
    // 画面はボタンを押せなくしているので、来るのは押した直後の二度押しくらい。黙って捨てると何も起きないように見えるので言う
    void vscode.window.showInformationMessage("ミラーを同期しています。終わるまで待ってください");
    return;
  }
  // ターン中に書くと、次のツール呼び出しの実行後チェックが戻しうる。押した時点で知らせる。
  if (!(await confirmIfTurn(current.folder.uri.fsPath, binSetting()))) {
    return;
  }
  if (state !== current) {
    return;
  }
  current.syncing = true;
  showMirror(current);
  let notice: ReturnType<typeof syncNotice>;
  try {
    notice = syncNotice(await runMirrorYes(current.folder.uri.fsPath, binSetting(), names, expect));
  } finally {
    current.syncing = false;
    current.syncSeq += 1;
  }
  if (notice.kind === "info") {
    void vscode.window.showInformationMessage(notice.message);
  } else if (notice.kind === "warn") {
    void vscode.window.showWarningMessage(notice.message);
  } else {
    void vscode.window.showErrorMessage(notice.message);
  }
  if (state === current) {
    // 同期の前に調べた結果は古い。調べ直しが終わるまで、ボタンは押せないまま（`busy`）
    showMirror(current);
    void refreshMirror();
  }
}

/** 読み直せなかったことを見せる。理由は覚えておく（渡せなかったときに `ready` で渡し直すため） */
function showError(current: PanelState, error: string): void {
  current.page = undefined;
  current.error = error;
  current.host.send({ kind: "error", error });
}

/**
 * いまの状態で描き直す。`send` が `deferred`（作り直し中）を返して捨てられたものは、
 * 画面が組み上がった（`ready`）ところでここから渡し直す。
 *
 * 読み直せなかったことも渡し直す。 ここで渡さないと、入れてある HTML（古い一覧）が出たまま
 * 失敗がユーザに届かず、`page` が無いので以後のボタンも使えない。
 */
function redraw(current: PanelState): void {
  if (current.page !== undefined) {
    show(current, current.page);
    return;
  }
  if (current.error !== undefined) {
    showError(current, current.error);
  }
}

/**
 * プロジェクト管理の画面に渡す手段。VS Code のパネルを `screenHost` の形に合わせる。
 * nonce は呼ぶたびに変える（同じ文字列を `webview.html` に入れても VS Code は何もしない）。
 */
function projectsHost(panel: vscode.WebviewPanel): ScreenHost<ProjectsData> {
  return screenHost<ProjectsData>(
    {
      get visible(): boolean {
        return panel.visible;
      },
      html(text: string): void {
        panel.webview.html = text;
      },
      post(message: unknown): void {
        void panel.webview.postMessage(message);
      },
    },
    (data) =>
      renderProjectsPage(data, {
        nonce: crypto.randomBytes(16).toString("base64"),
        script: webviewScript(SCREEN),
        style: webviewStyle(SCREEN),
        appearance: readAppearance(),
      }),
  );
}

/**
 * 操作の結果の一言。生きている画面にしか届かない（作り直している最中と非表示の間は届かない）。
 * その場で言うだけのものなので、持ち越さずに捨てる。
 */
function fail(current: PanelState, message: string): void {
  current.host.post({ type: "failed", message } satisfies ToProjects);
}

function info(current: PanelState, message: string, type: "info" | "cloned" = "info"): void {
  current.host.post({ type, message } satisfies ToProjects);
}

// ---- 操作

async function handleMessage(current: PanelState, message: ProjectsMessage | undefined): Promise<void> {
  if (message === undefined || state !== current) {
    return;
  }
  if (message.type === "ready") {
    // 画面が組み上がった。入れてある HTML は少し古いことがあるので、いまの中身を渡し直す。
    // 作り直している間に見送った更新（send）も、ここで届く
    current.host.ready();
    redraw(current);
    // 非表示の間に見た目が変わっていたら、入れてある HTML の body のクラスは古い。
    // `followAppearance` がそのとき送ったものは、段取りが「送れない」と見て捨てている
    postAppearance(current.host);
    // 初回だけ吹き出しの案内を頼む。画面は指す先が出てから始め、閉じたら `tourDone` を返す
    if (tourDue(SCREEN)) {
      current.host.post({ type: "tour" } satisfies ToProjects);
    }
    return;
  }
  if (message.type === "tourDone") {
    markTourSeen(SCREEN);
    return;
  }
  // 「更新」は一覧が無くても通す。読み直せなかったところからユーザが抜け出す方法がこれしかない
  if (message.type === "refresh") {
    void update();
    void refreshMirror();
    return;
  }
  const page = current.page;
  if (page === undefined) {
    return;
  }
  if (message.type === "clone") {
    clone(current, page, message.url, message.name);
  } else if (message.type === "fixIgnore") {
    fixIgnore(current, page);
  } else if (message.type === "mirrorSync") {
    const unknown = unknownSyncNames(current.mirror, message.names);
    if (unknown.length > 0) {
      // 画面が見せていない名前は、実行ファイルに渡さない。一覧を読み直して、もう一度押してもらう
      void vscode.window.showWarningMessage(`同期の対象を確かめられませんでした: ${unknown.join("、")}。最新の状態を読み直します`);
      void refreshMirror();
      return;
    }
    void syncMirror(current, message.names, message.expect);
  }
}

function clone(current: PanelState, page: ProjectsPage, rawUrl: string, rawName: string): void {
  const root = current.folder.uri.fsPath;
  const remote = checkRemote(rawUrl);
  if (!remote.ok) {
    fail(current, remote.error);
    return;
  }
  const name = checkName(rawName === "" ? remote.remote.name : rawName, page.existingNames);
  if (!name.ok) {
    fail(current, name.error);
    return;
  }
  const twin = duplicateOf(page.rows, remote.remote.key);
  if (twin !== undefined) {
    fail(current, `同じリポジトリを ${twin.name} として既に clone しています（origin ${twin.origin}）`);
    return;
  }
  const target = path.join(page.projectsDir, name.name);
  if (fs.existsSync(target) && listDir(target).length > 0) {
    fail(current, `${page.projectsRel}/${name.name} が既にあり、空ではありません`);
    return;
  }
  runInTerminal(root, cloneCommand(root, page.projectsDir, remote.remote.url, name.name));
  info(
    current,
    `git clone を「ccnavi」ターミナルに送りました（${page.projectsRel}/${name.name}）。認証が必要ならターミナルで入力してください。終わると一覧が更新されます`,
    "cloned",
  );
}

function fixIgnore(current: PanelState, page: ProjectsPage): void {
  const file = path.join(current.folder.uri.fsPath, ".gitignore");
  const before = readText(file);
  try {
    fs.writeFileSync(file, gitignoreWithProjects(before, page.projectsRel), "utf8");
  } catch (error) {
    fail(current, `.gitignore に保存できません: ${(error as Error).message}`);
    return;
  }
  info(current, `.gitignore に /${page.projectsRel}/ を追加しました。コミットは自分でしてください`);
  void update();
}

function asMessage(message: unknown): ProjectsMessage | undefined {
  if (typeof message !== "object" || message === null) {
    return undefined;
  }
  const m = message as { type?: unknown; url?: unknown; name?: unknown; names?: unknown; expect?: unknown };
  switch (m.type) {
    case "ready":
    case "refresh":
    case "fixIgnore":
    case "tourDone":
      return { type: m.type };
    case "clone":
      return typeof m.url === "string" && typeof m.name === "string" ? { type: "clone", url: m.url, name: m.name } : undefined;
    case "mirrorSync":
      // 名前は空でない文字列だけ。`expect` が空なら、実行ファイルに渡しても一致しないので受けない
      return Array.isArray(m.names) && m.names.every((n) => typeof n === "string" && n !== "") && typeof m.expect === "string" && m.expect !== ""
        ? { type: "mirrorSync", names: m.names as string[], expect: m.expect }
        : undefined;
    default:
      return undefined;
  }
}
