/**
 * プロジェクト管理画面の Webview パネル。生成・更新・破棄、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API と子プロセスに触れるので単体テストの対象外。README の手動確認の手順で確かめる。
 *
 * 画面は React（`src/webview/projects/`）で、ここが渡すのは「いま何を見せるか」（`ProjectsData`）だけ。
 * 渡し方（送る・入れ物ごと・作り直し中で見送る）は `core/screen-host.ts` が決める。この画面は
 * `retainContextWhenHidden` が偽なので、そのまま当たる（打ちかけの clone の欄は Webview の state にある）。
 *
 * 一覧は実行ファイルの答え（`--explain --json` の trees と layers、`--lint --json` の苦情）を並べる。
 * 拡張が自分で見るのは、origin（ローカルの git を読み取り専用で起こす）、層のルールファイル・
 * `.claude/` の有無、`.gitignore` の本文、プロジェクトになっていない `.git` の探索だけ。
 * 層のルールファイルの置き場は layers の答えを使い、`CCNAVI_PROJECT_HOME` から自分で組まない。
 *
 * clone / fetch / pull は統合ターミナルへ送る。認証の対話はそこで人が行い、完了は `projects/<名前>/.git`
 * の出現を監視して拾う。書くのは、人がボタンを押したときの `.gitignore`、置き場のディレクトリ、
 * 層（プロジェクトか自身の層）のルールファイル（無いときだけ）の 3 つ。
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

import { followAppearance, postAppearance, readAppearance } from "./appearance.js";
import { loadBoard, runLintJson } from "./ccnavi.js";
import { projectLayer, selfLayer } from "./core/layers.js";
import {
  buildProjectsPage,
  checkName,
  checkRemote,
  cloneCommand,
  duplicateOf,
  fetchCommand,
  findStrayGitDirs,
  gitignoreHasProjects,
  gitignoreWithProjects,
  pullCommand,
  rewriteRulesForProject,
  type DirEntry,
  type ProjectsPage,
} from "./core/projects.js";
import { renderProjectsPage } from "./core/projects-render.js";
import { showLoading } from "./loading.js";
import type { ProjectsData, ProjectsMessage, ToProjects } from "./core/projects-view.js";
import { screenHost, type ScreenHost } from "./core/screen-host.js";
import { screens } from "./core/screens.js";
import { readOrigin } from "./git.js";
import { runInTerminal } from "./terminal.js";
import { ticketControl } from "./ticket-control.js";
import { markTourSeen, tourSeen } from "./tour.js";
import { webviewScript, webviewStyle } from "./webview-asset.js";

const DEBOUNCE_MS = 300;
const DEFAULT_RULES = ".ccnavi/common/rules.yml";
/** 画面の名前。束ねの綴りは `src/webview/<名前>/main.tsx` → `out/webview/<名前>.js`、`style.css` → `<名前>.css` */
const SCREEN = "projects";
const TITLE = "ccnavi プロジェクト管理";

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
  /** 直前に見た表裏。表へ戻ったら読み直す（裏にいる間の変化は監視が拾っても渡せていない） */
  wasVisible: boolean;
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
    // `reveal` の前に表へ出たことにする。立てずに出すと、下の `update()` と
    // `onDidChangeViewState` の `becameVisible` からの `update()` で、実行ファイルを 2 度起こす
    state.wasVisible = true;
    state.panel.reveal(state.panel.viewColumn);
    void update();
    return;
  }

  // 画面と CSS は束ねたものを読んで流し込む。無ければ開かずに言う（パネルだけ出しても白いまま）
  try {
    webviewScript(SCREEN);
    webviewStyle(SCREEN);
  } catch (error) {
    vscode.window.showErrorMessage(`プロジェクト管理画面を表示できません: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }

  // タブは読む前に作る。実行ファイルの答えを待ってから作ると、押しても何も起きないように見え、
  // 押し直した分だけタブが増える（`state` を先に立てるので、2 度目の押下は上の `reveal` に入る）。
  // 読めなかったときもタブは閉じず、中にエラーを出す（`update` の `showError`）
  const panel = vscode.window.createWebviewPanel("ccnaviProjects", TITLE, vscode.ViewColumn.One, {
    enableScripts: true,
    enableForms: false,
    localResourceRoots: [],
    retainContextWhenHidden: false,
  });
  showLoading(panel, TITLE, SCREEN, "プロジェクト");
  const current: PanelState = { panel, folder, host: projectsHost(panel), watchers: [], loading: false, again: false, wasVisible: panel.visible };
  state = current;
  followAppearance(panel, current.host);
  registerPanelHandlers(current);
  void update();
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
  const hasClaudeDir: Record<string, boolean> = {};
  await Promise.all(
    projects.map(async (t) => {
      origins[t.name] = await readOrigin(t.root);
      const layer = projectLayer(board, t.name);
      if (layer !== undefined && layer.rules.path !== "") {
        const rulesPath = resolveIn(root, layer.rules.path);
        rulesRels[t.name] = toPosix(path.relative(root, rulesPath));
        rulesExists[t.name] = isFile(rulesPath);
      }
      hasClaudeDir[t.name] = isDir(path.join(t.root, ".claude"));
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
      hasClaudeDir,
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

function isDir(dirPath: string): boolean {
  try {
    return fs.statSync(dirPath).isDirectory();
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
      // `retainContextWhenHidden` は偽なので、裏に回った画面は捨てられる。表に戻ると
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
 * 監視を張る。置き場の綴りは実行ファイルの答えから取るので、最初に読めたときに 1 度だけ張る
 * （読めないまま開いたタブは、「更新」で読めたところで張る）。
 */
function watchProjects(current: PanelState, projectsRel: string, selfRulesRel: string): void {
  if (current.watchers.length > 0) {
    return;
  }
  const { folder } = current;
  // clone の完了（`.git` の出現）、層のルールファイルの出入り、origin の変化、ワークツリーの登録、`.gitignore`。
  // 層の綴り（ccnavi ディレクトリの下の `config/`）は自身の層のパスから取る。プロジェクトの層も同じ形（設計 11.2）。
  // 自身の層のパスが取れない（壊れた JSON）なら、層の監視は張らない。
  const rel = projectsRel === "" ? "projects" : projectsRel;
  const layerDir = selfRulesRel === "" ? "" : path.posix.dirname(selfRulesRel);
  const patterns = [
    `${rel}/*/.git`,
    `${rel}/*/.git/config`,
    `${rel}/*/.git/worktrees/*`,
    ...(layerDir === "" ? [] : [`${rel}/*/${layerDir}/*`, `${layerDir}/*`]),
    `${rel}/*/.claude`,
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
  current.host.send({ kind: "page", page });
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
 * **読み直せなかったことも渡し直す。** ここで落とすと、入れてある HTML（古い一覧）が出たまま
 * 失敗が人に届かず、`page` が無いので以後のボタンも効かない。
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
 * プロジェクト管理の画面に渡す口。VS Code のパネルを `screenHost` の形に合わせる。
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
 * 操作の結果の一言。生きている画面にしか届かない（作り直している最中と裏にいる間は落ちる）。
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
    // 裏にいる間に見た目が変わっていたら、入れてある HTML の body のクラスは古い。
    // `followAppearance` がそのとき送ったものは、段取りが「送れない」と見て落としている
    postAppearance(current.host);
    // 初回だけ吹き出しの案内を頼む。画面は指す先が出てから始め、閉じたら `tourDone` を返す。
    // 閉じずにタブを閉じたら印は残らないので、次に開いたときにもう 1 度出る
    if (!tourSeen(SCREEN)) {
      current.host.post({ type: "tour" } satisfies ToProjects);
    }
    return;
  }
  if (message.type === "tourDone") {
    markTourSeen(SCREEN);
    return;
  }
  // 「更新」は一覧が無くても通す。読み直せなかったところから人が抜け出す道がこれしかない
  if (message.type === "refresh") {
    void update();
    return;
  }
  const page = current.page;
  if (page === undefined) {
    return;
  }
  const root = current.folder.uri.fsPath;
  switch (message.type) {
    case "clone":
      clone(current, page, message.url, message.name);
      return;
    case "fixIgnore":
      fixIgnore(current, page);
      return;
    case "createRules":
      createRules(current, page, message.name);
      return;
    case "createSelfRules":
      createSelfRules(current, page);
      return;
    case "openRules":
      await screens().rules(message.name === "" ? { kind: "workspace" } : { kind: "project", name: message.name });
      return;
    case "openSelfRules":
      await screens().rules({ kind: "self" });
      return;
    case "openPhases":
    case "openSelfPhases":
    case "openBoard":
      // 画面のボタンは disable なら描かれないが、古い画面が開いたままの間は押せる。
      // 開く側でも見るので、ここは画面の中に理由を出すためだけに見る。
      if (ticketControl() !== "enable") {
        const what = message.type === "openBoard" ? "チケット管理画面" : "フェーズ管理画面";
        fail(current, `${what}は開けません。このワークスペースはチケット制御が無効です（CCNAVI_TICKET_CONTROL=disable）。一覧が古いので「更新」を押してください`);
        return;
      }
      if (message.type === "openBoard") {
        await screens().board(message.name);
        return;
      }
      await screens().phases(message.type === "openSelfPhases" ? { kind: "self" } : { kind: "project", name: message.name });
      return;
    case "fetch":
    case "pull": {
      const row = page.rows.find((r) => r.name === message.name);
      if (row === undefined) {
        fail(current, `プロジェクト ${message.name} が一覧にありません。更新してから押し直してください`);
        return;
      }
      runInTerminal(root, message.type === "fetch" ? fetchCommand(row.root) : pullCommand(row.root));
      info(current, `${message.name} で git ${message.type} をターミナルに送りました`);
      return;
    }
  }
}

function clone(current: PanelState, page: ProjectsPage, rawUrl: string, rawName: string): void {
  const root = current.folder.uri.fsPath;
  if (page.projectsDir === "") {
    fail(current, "置き場が無効（CCNAVI_PROJECTS が空）なので、clone 先を決められません");
    return;
  }
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
    `git clone を「ccnavi」ターミナルに送りました（${page.projectsRel}/${name.name}）。認証が要るならターミナルで入力してください。終わると一覧が更新されます`,
    "cloned",
  );
}

function fixIgnore(current: PanelState, page: ProjectsPage): void {
  if (page.projectsRel === "") {
    fail(current, "置き場が無効（CCNAVI_PROJECTS が空）なので、足す行がありません");
    return;
  }
  const file = path.join(current.folder.uri.fsPath, ".gitignore");
  const before = readText(file);
  try {
    fs.writeFileSync(file, gitignoreWithProjects(before, page.projectsRel), "utf8");
  } catch (error) {
    fail(current, `.gitignore に書けません: ${(error as Error).message}`);
    return;
  }
  info(current, `.gitignore に /${page.projectsRel}/ を足しました。コミットは自分でしてください`);
  void update();
}

function createRules(current: PanelState, page: ProjectsPage, name: string): void {
  const row = page.rows.find((r) => r.name === name);
  if (row === undefined) {
    fail(current, `プロジェクト ${name} が一覧にありません。更新してから押し直してください`);
    return;
  }
  if (row.rulesRel === "") {
    fail(current, `プロジェクト ${name} は設定の対象になっていないので、ルールを置く先がありません`);
    return;
  }
  copyCommonRules(current, row.rulesRel, name, "プロジェクトの git");
}

function createSelfRules(current: PanelState, page: ProjectsPage): void {
  if (page.selfRulesRel === "") {
    fail(current, "実行ファイルの答えにワークスペースの設定が無いので、置く先を決められません。更新してから押し直してください");
    return;
  }
  copyCommonRules(current, page.selfRulesRel, "自身の層（self）", "ワークスペースの git");
}

/** 共通層のルールを層のルールファイル（ルートからの相対）に写す。既にあれば上書きしない */
function copyCommonRules(current: PanelState, targetRel: string, label: string, repo: string): void {
  const root = current.folder.uri.fsPath;
  const target = path.join(root, ...targetRel.split("/"));
  if (fs.existsSync(target)) {
    fail(current, `${targetRel} は既にあるので、上書きしません`);
    return;
  }
  // 共通層の置き場は `.ccnavi/common/` 固定。env では動かない（ADR-0052）。
  const sourceRel = DEFAULT_RULES;
  const source = readText(path.isAbsolute(sourceRel) ? sourceRel : path.join(root, sourceRel));
  if (source === undefined) {
    fail(current, `共通の設定のルール ${sourceRel} を読めません`);
    return;
  }
  try {
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, rewriteRulesForProject(source, sourceRel, label, new Date().toISOString().slice(0, 10)), { encoding: "utf8", flag: "wx" });
  } catch (error) {
    fail(current, `${targetRel} に書けません: ${(error as Error).message}`);
    return;
  }
  info(current, `${targetRel} に共通の設定のルールをコピーしました。中身を確かめてから${repo}にコミットしてください`);
  void update();
}

function asMessage(message: unknown): ProjectsMessage | undefined {
  if (typeof message !== "object" || message === null) {
    return undefined;
  }
  const m = message as { type?: unknown; url?: unknown; name?: unknown };
  const named = typeof m.name === "string" ? m.name : undefined;
  switch (m.type) {
    case "ready":
    case "refresh":
    case "tourDone":
    case "fixIgnore":
    case "createSelfRules":
    case "openSelfRules":
    case "openSelfPhases":
      return { type: m.type };
    case "clone":
      return typeof m.url === "string" && named !== undefined ? { type: "clone", url: m.url, name: named } : undefined;
    case "createRules":
    case "openRules":
    case "openPhases":
    case "openBoard":
    case "fetch":
    case "pull":
      return named !== undefined ? { type: m.type, name: named } : undefined;
    default:
      return undefined;
  }
}
