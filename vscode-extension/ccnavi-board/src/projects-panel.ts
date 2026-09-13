/**
 * プロジェクト管理画面の Webview パネル。生成・更新・破棄、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API と子プロセスに触れるので単体テストの対象外。README の手動確認の手順で確かめる。
 *
 * 一覧は実行ファイルの答え（`--explain --json` の trees と layers、`--lint --json` の苦情）を並べる。
 * 拡張が自分で見るのは、origin（ローカルの git を読み取り専用で起こす）、層のルールファイル・旧の置き場
 * `config/rules.yml`・`.claude/` の有無、`.gitignore` の本文、プロジェクトになっていない `.git` の探索だけ。
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

import { openBoard } from "./board-panel.js";
import { loadBoard, runLintJson } from "./ccnavi.js";
import { envFromSettingsJson } from "./core/hooks.js";
import { OLD_PROJECT_RULES, projectLayer, selfLayer } from "./core/layers.js";
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
import { escapeHtml } from "./core/render.js";
import { readOrigin } from "./git.js";
import { openRules } from "./rules-panel.js";
import { runInTerminal } from "./terminal.js";
import { ticketControl } from "./ticket-control.js";

const DEBOUNCE_MS = 300;
const DEFAULT_RULES = ".claude/ccnavi/rules.yml";
/** 実行ファイルが自身の層を出さない（古い版）ときに監視する層の綴り。既定の傘 `.ccnavi` の形 */
const DEFAULT_LAYER_DIR = ".ccnavi/config";

type Message =
  | { readonly type: "refresh" }
  | { readonly type: "clone"; readonly url: string; readonly name: string }
  | { readonly type: "createDir" }
  | { readonly type: "fixIgnore" }
  | { readonly type: "createRules"; readonly name: string }
  | { readonly type: "createSelfRules" }
  | { readonly type: "openRules"; readonly name: string }
  | { readonly type: "openSelfRules" }
  | { readonly type: "openBoard"; readonly name: string }
  | { readonly type: "fetch"; readonly name: string }
  | { readonly type: "pull"; readonly name: string };

interface PanelState {
  readonly panel: vscode.WebviewPanel;
  readonly folder: vscode.WorkspaceFolder;
  watchers: vscode.FileSystemWatcher[];
  timer?: NodeJS.Timeout;
  page?: ProjectsPage;
  loading: boolean;
  again: boolean;
}

let state: PanelState | undefined;

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

/** `ccnaviBoard.openProjects` の本体 */
export async function openProjects(): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    vscode.window.showInformationMessage("ワークスペースが開かれていないため、プロジェクト管理を表示できません");
    return;
  }
  if (state !== undefined) {
    state.panel.reveal(state.panel.viewColumn);
    void update();
    return;
  }

  const first = await gather(folder.uri.fsPath);
  if (!first.ok) {
    vscode.window.showErrorMessage(`プロジェクト管理を表示できません: ${first.error}`);
    return;
  }

  const panel = vscode.window.createWebviewPanel("ccnaviProjects", "ccnavi プロジェクト管理", vscode.ViewColumn.One, {
    enableScripts: true,
    enableForms: false,
    localResourceRoots: [],
    retainContextWhenHidden: false,
  });
  const current: PanelState = { panel, folder, watchers: [], loading: false, again: false };
  state = current;
  registerPanelHandlers(current, first.page.projectsRel, first.page.selfRulesRel);
  show(current, first.page);
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
  const oldRulesExists: Record<string, boolean> = {};
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
      oldRulesExists[t.name] = isFile(path.join(t.root, ...OLD_PROJECT_RULES.split("/")));
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
      projectsDirExists: projectsDir !== "" && isDir(projectsDir),
      ignored: gitignoreHasProjects(readText(path.join(root, ".gitignore")), projectsRel),
      rulesRels,
      rulesExists,
      oldRulesExists,
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

function registerPanelHandlers(current: PanelState, projectsRel: string, selfRulesRel: string): void {
  const { panel, folder } = current;

  panel.webview.onDidReceiveMessage((message: unknown) => {
    void handleMessage(current, asMessage(message));
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

  // clone の完了（`.git` の出現）、層のルールファイルと旧の置き場の出入り、origin の変化、作業ツリーの登録、`.gitignore`。
  // 層の綴り（傘の下の `config/`）は自身の層のパスから取る。プロジェクトの層も同じ形（設計 §11.2）。
  const rel = projectsRel === "" ? "projects" : projectsRel;
  const layerDir = selfRulesRel === "" ? DEFAULT_LAYER_DIR : path.posix.dirname(selfRulesRel);
  const patterns = [
    `${rel}/*/.git`,
    `${rel}/*/.git/config`,
    `${rel}/*/.git/worktrees/*`,
    `${rel}/*/${layerDir}/*`,
    `${rel}/*/${OLD_PROJECT_RULES}`,
    `${layerDir}/*`,
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
      current.page = undefined;
      current.panel.webview.html = renderError(result.error);
      return;
    }
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
  current.panel.webview.html = renderProjectsPage(page, { nonce: crypto.randomBytes(16).toString("base64") });
}

function renderError(error: string): string {
  return `<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none';"><title>ccnavi プロジェクト管理</title></head><body><p>プロジェクトの一覧を読み込めませんでした。原因を直してから「ccnavi ボード: プロジェクト管理を開く」を実行し直してください。</p><pre>${escapeHtml(error)}</pre></body></html>`;
}

function fail(current: PanelState, message: string): void {
  void current.panel.webview.postMessage({ type: "failed", message });
}

function info(current: PanelState, message: string, type: "info" | "cloned" = "info"): void {
  void current.panel.webview.postMessage({ type, message });
}

// ---- 操作

async function handleMessage(current: PanelState, message: Message | undefined): Promise<void> {
  if (message === undefined || state !== current) {
    return;
  }
  const page = current.page;
  if (page === undefined) {
    return;
  }
  const root = current.folder.uri.fsPath;
  switch (message.type) {
    case "refresh":
      void update();
      return;
    case "clone":
      clone(current, page, message.url, message.name);
      return;
    case "createDir":
      createDir(current, page);
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
      await openRules(message.name === "" ? { kind: "workspace" } : { kind: "project", name: message.name });
      return;
    case "openSelfRules":
      await openRules({ kind: "self" });
      return;
    case "openBoard":
      if (ticketControl() !== "enable") {
        fail(current, "このワークスペースではチケット制御が無効です（CCNAVI_TICKET_CONTROL=disable）");
        return;
      }
      await openBoard(message.name);
      return;
    case "fetch":
    case "pull": {
      const row = page.rows.find((r) => r.name === message.name);
      if (row === undefined) {
        fail(current, `プロジェクト ${message.name} が一覧にありません。更新してから押し直してください`);
        return;
      }
      runInTerminal(root, message.type === "fetch" ? fetchCommand(row.root) : pullCommand(row.root));
      info(current, `${message.name} で git ${message.type} をターミナルで実行しました`);
      return;
    }
  }
}

function clone(current: PanelState, page: ProjectsPage, rawUrl: string, rawName: string): void {
  const root = current.folder.uri.fsPath;
  if (page.projectsDir === "") {
    fail(current, "置き場が無効（CCNAVI_PROJECTS が空）のため、clone 先を決められません");
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
    fail(current, `${page.projectsRel}/${name.name} が既に存在し、空ではありません`);
    return;
  }
  runInTerminal(root, cloneCommand(root, page.projectsDir, remote.remote.url, name.name));
  info(
    current,
    `git clone を「ccnavi」ターミナルで実行しました（${page.projectsRel}/${name.name}）。認証が必要ならターミナルで入力してください。完了すると一覧が更新されます`,
    "cloned",
  );
}

function createDir(current: PanelState, page: ProjectsPage): void {
  if (page.projectsDir === "") {
    fail(current, "置き場が無効（CCNAVI_PROJECTS が空）のため、作成先がありません");
    return;
  }
  try {
    fs.mkdirSync(page.projectsDir, { recursive: true });
  } catch (error) {
    fail(current, `${page.projectsRel}/ を作成できません: ${(error as Error).message}`);
    return;
  }
  info(current, `${page.projectsRel}/ を作成しました`);
  void update();
}

function fixIgnore(current: PanelState, page: ProjectsPage): void {
  if (page.projectsRel === "") {
    fail(current, "置き場が無効（CCNAVI_PROJECTS が空）のため、追加する行がありません");
    return;
  }
  const file = path.join(current.folder.uri.fsPath, ".gitignore");
  const before = readText(file);
  try {
    fs.writeFileSync(file, gitignoreWithProjects(before, page.projectsRel), "utf8");
  } catch (error) {
    fail(current, `.gitignore に書き込めません: ${(error as Error).message}`);
    return;
  }
  info(current, `.gitignore に /${page.projectsRel}/ を追加しました。コミットは手動で行ってください`);
  void update();
}

function createRules(current: PanelState, page: ProjectsPage, name: string): void {
  const row = page.rows.find((r) => r.name === name);
  if (row === undefined) {
    fail(current, `プロジェクト ${name} が一覧にありません。更新してから押し直してください`);
    return;
  }
  if (row.rulesRel === "") {
    fail(current, `プロジェクト ${name} は層として数えられていないため、ルールを置く先がありません`);
    return;
  }
  copyCommonRules(current, row.rulesRel, name, "プロジェクトの git");
}

function createSelfRules(current: PanelState, page: ProjectsPage): void {
  if (page.selfRulesRel === "") {
    fail(current, "実行ファイルが自身の層を出していないため、置く先を決められません。層に対応した版の実行ファイルを使ってください");
    return;
  }
  copyCommonRules(current, page.selfRulesRel, "自身の層（self）", "ワークスペースの git");
}

/** 共通層のルールを層のルールファイル（ルートからの相対）に写す。既にあれば上書きしない */
function copyCommonRules(current: PanelState, targetRel: string, label: string, repo: string): void {
  const root = current.folder.uri.fsPath;
  const target = path.join(root, ...targetRel.split("/"));
  if (fs.existsSync(target)) {
    fail(current, `${targetRel} は既に存在するため、上書きしません`);
    return;
  }
  const settingsText = readText(path.join(root, ".claude", "settings.json"));
  const sourceRel = (settingsText !== undefined && envFromSettingsJson(settingsText, "CCNAVI_RULES")) || DEFAULT_RULES;
  const source = readText(path.isAbsolute(sourceRel) ? sourceRel : path.join(root, sourceRel));
  if (source === undefined) {
    fail(current, `ワークスペースのルール ${sourceRel} を読み込めません`);
    return;
  }
  try {
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, rewriteRulesForProject(source, sourceRel, label, new Date().toISOString().slice(0, 10)), { encoding: "utf8", flag: "wx" });
  } catch (error) {
    fail(current, `${targetRel} に書き込めません: ${(error as Error).message}`);
    return;
  }
  info(current, `${targetRel} に共通層のルールをコピーしました。内容を確認してから${repo}にコミットしてください`);
  void update();
}

function asMessage(message: unknown): Message | undefined {
  if (typeof message !== "object" || message === null) {
    return undefined;
  }
  const m = message as { type?: unknown; url?: unknown; name?: unknown };
  const named = typeof m.name === "string" ? m.name : undefined;
  switch (m.type) {
    case "refresh":
    case "createDir":
    case "fixIgnore":
    case "createSelfRules":
    case "openSelfRules":
      return { type: m.type };
    case "clone":
      return typeof m.url === "string" && named !== undefined ? { type: "clone", url: m.url, name: named } : undefined;
    case "createRules":
    case "openRules":
    case "openBoard":
    case "fetch":
    case "pull":
      return named !== undefined ? { type: m.type, name: named } : undefined;
    default:
      return undefined;
  }
}
