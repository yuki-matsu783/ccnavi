/**
 * ルール設定画面の Webview パネル。生成・更新・破棄、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。README の手動確認の手順で確かめる。
 *
 * 対象はワークスペースのルール（`.claude/ccnavi/rules.yml`）か、プロジェクト 1 つのルール
 * （`projects/<名前>/config/rules.yml`、設計 §25.4）。対象ごとに 1 パネルで、並べて開ける。
 *
 * 判定・検証は実行ファイルに任せる。編集中の内容は一時ファイルに書き、ワークスペースなら `--rules`、
 * プロジェクトなら `--project-rules-file <名前>=<パス>` で渡す。保存は、検証（`--lint`）を通り、
 * 作業中のチケットが無く（プロジェクトならそのプロジェクトの）、ファイルが外で変わっていない
 * ときだけ行う。
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import { WATCH_PATTERNS } from "./board-panel.js";
import { loadBoard, runLint, runSamples, runTest, type RulesOverride } from "./ccnavi.js";
import { envFromSettingsJson, hooksFor, parseHooks, type HookEntry } from "./core/hooks.js";
import { lockFromBoard, lockFromError, type Lock } from "./core/lock.js";
import { escapeHtml } from "./core/render.js";
import { asSections, readRules, type RuleForm, type RulesDocument, type Section } from "./core/rules-doc.js";
import { renderRulesPage } from "./core/rules-render.js";

const DEBOUNCE_MS = 120;
const DEFAULT_RULES = ".claude/ccnavi/rules.yml";
const DEFAULT_PROJECTS = "projects";
const DEFAULT_PROJECT_RULES = "config/rules.yml";
const DEFAULT_SAMPLES = "testdata/rule-samples.yml";
/** 自分の保存で監視が鳴るのを、この間だけ「外で変わった」と言わない */
const OWN_WRITE_GRACE_MS = 1500;

/** 画面が直すルールファイル。ワークスペースのものか、プロジェクト 1 つのもの */
export type RulesTarget = { readonly kind: "workspace" } | { readonly kind: "project"; readonly name: string };

type Sections = Readonly<Record<Section, readonly RuleForm[]>>;

type Message =
  | { readonly type: "reload"; readonly dirty: boolean }
  | { readonly type: "openFile"; readonly which: "rules" | "samples" }
  | { readonly type: "save"; readonly sections: Sections }
  | { readonly type: "judge"; readonly sections: Sections; readonly tool: string; readonly subject: string }
  | { readonly type: "samples"; readonly sections: Sections }
  | { readonly type: "pickFile"; readonly key: string; readonly field: FileField };

/** ファイル選択ダイアログで埋める欄 */
type FileField = "additionalContextFile" | "additionalContextOnceFile";

interface Loaded {
  readonly text: string;
  readonly mtimeMs: number;
  readonly doc: RulesDocument;
  readonly rulesPath: string;
  /** ワークスペースルートからの相対で見せる綴り。プロジェクトなら `projects/<名前>/config/rules.yml` */
  readonly rulesRel: string;
  readonly hooks: readonly HookEntry[];
  readonly hookFiles: { readonly settings: boolean; readonly settingsLocal: boolean };
  readonly mode: string;
  readonly samplesPath: string;
  readonly samplesRel: string;
}

interface PanelState {
  readonly target: RulesTarget;
  readonly panel: vscode.WebviewPanel;
  readonly folder: vscode.WorkspaceFolder;
  readonly tmpDir: string;
  watchers: vscode.FileSystemWatcher[];
  timer?: NodeJS.Timeout;
  lockTimer?: NodeJS.Timeout;
  loaded?: Loaded;
  lock: Lock;
  wroteAt: number;
}

/** 対象ごとのパネル。鍵はワークスペースが空、プロジェクトはその名前 */
const panels = new Map<string, PanelState>();

function keyOf(target: RulesTarget): string {
  return target.kind === "workspace" ? "" : target.name;
}

function projectOf(target: RulesTarget): string | undefined {
  return target.kind === "workspace" ? undefined : target.name;
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
  const key = keyOf(target);
  const existing = panels.get(key);
  if (existing !== undefined) {
    existing.panel.reveal(existing.panel.viewColumn);
    return;
  }

  const root = folder.uri.fsPath;
  let loaded: Loaded;
  try {
    loaded = readPage(root, target);
  } catch (error) {
    vscode.window.showErrorMessage(`ルール設定画面を表示できない: ${(error as Error).message}`);
    return;
  }

  const title = target.kind === "workspace" ? "ccnavi ルール設定" : `ccnavi ルール設定: ${target.name}`;
  const panel = vscode.window.createWebviewPanel("ccnaviRules", title, vscode.ViewColumn.One, {
    enableScripts: true,
    enableForms: false,
    localResourceRoots: [],
    // 編集の途中を持つので、タブを裏に回しても捨てない。
    retainContextWhenHidden: true,
  });
  const current: PanelState = {
    target,
    panel,
    folder,
    tmpDir: fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-rules-")),
    watchers: [],
    loaded,
    lock: lockFromError("まだ確認していない"),
    wroteAt: 0,
  };
  panels.set(key, current);
  registerPanelHandlers(current);
  show(current);
  void refreshLock(current);
}

function readPage(root: string, target: RulesTarget): Loaded {
  const settingsText = readText(path.join(root, ".claude", "settings.json"));
  const localText = readText(path.join(root, ".claude", "settings.local.json"));
  const env = (name: string) => (settingsText !== undefined && envFromSettingsJson(settingsText, name)) || "";
  let rulesPath: string;
  let rulesRel: string;
  if (target.kind === "workspace") {
    rulesRel = env("CCNAVI_RULES") || DEFAULT_RULES;
    rulesPath = resolveIn(root, rulesRel);
  } else {
    // プロジェクトのルールは git プロジェクトルートからの相対（既定 config/rules.yml）。
    // 作業ツリーの中の版は読まない（設計 §25.4）。置き場は CCNAVI_PROJECTS、既定 projects/。
    const projectsDir = resolveIn(root, env("CCNAVI_PROJECTS") || DEFAULT_PROJECTS);
    const projectRules = env("CCNAVI_PROJECT_RULES") || DEFAULT_PROJECT_RULES;
    rulesPath = path.join(projectsDir, target.name, ...projectRules.split("/"));
    rulesRel = path.relative(root, rulesPath).split(path.sep).join("/");
  }
  let text: string;
  let mtimeMs: number;
  try {
    text = fs.readFileSync(rulesPath, "utf8");
    mtimeMs = fs.statSync(rulesPath).mtimeMs;
  } catch (error) {
    throw new Error(`ルールファイルを読めない（${rulesRel}）: ${(error as Error).message}`);
  }
  const hooks = [
    ...(settingsText === undefined ? [] : parseHooks(settingsText, "settings")),
    ...(localText === undefined ? [] : parseHooks(localText, "settings-local")),
  ];
  const samplesRel = samplesSetting();
  return {
    text,
    mtimeMs,
    doc: readRules(text),
    rulesPath,
    rulesRel,
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
  const key = keyOf(current.target);

  panel.webview.onDidReceiveMessage((message: unknown) => {
    void handleMessage(current, asMessage(message));
  });

  panel.onDidDispose(() => {
    for (const timer of [current.timer, current.lockTimer]) {
      if (timer !== undefined) {
        clearTimeout(timer);
      }
    }
    for (const watcher of current.watchers) {
      watcher.dispose();
    }
    current.watchers = [];
    try {
      fs.rmSync(current.tmpDir, { recursive: true, force: true });
    } catch {
      // 一時ファイルの片付けに失敗しても画面の仕事には関係ない
    }
    if (panels.get(key) === current) {
      panels.delete(key);
    }
  });

  // ルールファイルと設定ファイルが変わったら「外で変わった」と伝える。自分の保存は除く。
  const rulesRel = current.loaded?.rulesRel ?? DEFAULT_RULES;
  for (const pattern of [toGlob(rulesRel), ".claude/settings.json", ".claude/settings.local.json"]) {
    const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(folder, pattern));
    const changed = () => scheduleChanged(current);
    watcher.onDidCreate(changed);
    watcher.onDidChange(changed);
    watcher.onDidDelete(changed);
    current.watchers.push(watcher);
  }
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

function toGlob(rel: string): string {
  return rel.replace(/\\/g, "/");
}

function alive(current: PanelState): boolean {
  return panels.get(keyOf(current.target)) === current;
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
      void current.panel.webview.postMessage({ type: "changed" });
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
 * ワークスペースのルールはどのツリーの doing でも止め、プロジェクトのルールはそのプロジェクトの doing だけ見る。
 */
async function refreshLock(current: PanelState): Promise<Lock> {
  const result = await loadBoard(current.folder.uri.fsPath, binSetting());
  const lock = result.ok ? lockFromBoard(result.board, projectOf(current.target)) : lockFromError(result.error);
  if (alive(current)) {
    current.lock = lock;
    void current.panel.webview.postMessage({ type: "lock", lock });
  }
  return lock;
}

function show(current: PanelState): void {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  current.panel.webview.html = renderRulesPage(
    {
      root: current.folder.uri.fsPath,
      rulesPath: loaded.rulesRel,
      mode: loaded.mode,
      model: loaded.doc.model,
      hooks: loaded.hooks,
      hookFiles: loaded.hookFiles,
      samplesPath: loaded.samplesRel,
      lock: current.lock,
    },
    { nonce: crypto.randomBytes(16).toString("base64") },
  );
}

function reload(current: PanelState): void {
  try {
    current.loaded = readPage(current.folder.uri.fsPath, current.target);
  } catch (error) {
    current.panel.webview.html = renderError((error as Error).message);
    return;
  }
  show(current);
  void refreshLock(current);
}

function renderError(error: string): string {
  return `<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none';"><title>ccnavi ルール設定</title></head><body><p>ルール設定画面を読み直せなかった。直してから「ccnavi ボード: ルール設定画面を開く」を実行し直す。</p><pre>${escapeHtml(error)}</pre></body></html>`;
}

function fail(current: PanelState, message: string): void {
  void current.panel.webview.postMessage({ type: "failed", message });
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
  return target.kind === "workspace" ? { kind: "workspace", path: tmp } : { kind: "project", name: target.name, path: tmp };
}

async function handleMessage(current: PanelState, message: Message | undefined): Promise<void> {
  if (message === undefined || !alive(current) || current.loaded === undefined) {
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
          return;
        }
      }
      reload(current);
      return;
    }
    case "openFile": {
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
      if (chosen === undefined) {
        return;
      }
      const rel = path.relative(root, chosen.fsPath);
      if (rel === "" || rel.startsWith("..") || path.isAbsolute(rel)) {
        void vscode.window.showWarningMessage(`ワークスペースの外のファイルは指定できない: ${chosen.fsPath}`);
        return;
      }
      void current.panel.webview.postMessage({
        type: "picked",
        key: message.key,
        field: message.field,
        path: rel.split(path.sep).join("/"),
      });
      return;
    }
    case "judge": {
      let rules: RulesOverride;
      try {
        rules = stage(current, message.sections);
      } catch (error) {
        fail(current, `編集中の内容を書き出せない: ${(error as Error).message}`);
        return;
      }
      const result = await runTest(root, binSetting(), rules, message.tool, message.subject);
      if (!alive(current)) {
        return;
      }
      if (!result.ok) {
        fail(current, result.error);
        return;
      }
      void current.panel.webview.postMessage({
        type: "judged",
        result: result.value,
        hooks: hooksFor(current.loaded.hooks, message.tool),
      });
      return;
    }
    case "samples": {
      let rules: RulesOverride;
      try {
        rules = stage(current, message.sections);
      } catch (error) {
        fail(current, `編集中の内容を書き出せない: ${(error as Error).message}`);
        return;
      }
      const result = await runSamples(root, binSetting(), rules, current.loaded.samplesPath);
      if (!alive(current)) {
        return;
      }
      if (!result.ok) {
        fail(current, result.error);
        return;
      }
      void current.panel.webview.postMessage({ type: "sampled", result: result.value });
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
  if (!alive(current)) {
    return;
  }
  if (!lint.ok) {
    fail(current, lint.error);
    return;
  }
  if (!lint.value.ok) {
    fail(current, `--lint が error を報告した。直してから保存する:\n${lint.value.report}`);
    return;
  }

  // 2. 作業中のチケットが無いこと。押した時点で取り直す。
  const lock = await refreshLock(current);
  if (!alive(current)) {
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
    fail(current, "ルールファイルが読み込み後に外部で変更されている。再読込してから編集し直す（この変更は上書きしない）");
    return;
  }

  try {
    current.wroteAt = Date.now();
    fs.writeFileSync(loaded.rulesPath, text, "utf8");
  } catch (error) {
    fail(current, `ルールファイルに書けない: ${(error as Error).message}`);
    return;
  }
  reload(current);
  const tail = lint.value.report.split("\n").filter((l) => l.trim() !== "").pop() ?? "";
  vscode.window.showInformationMessage(`${loaded.rulesRel} に保存した（${tail}）`);
}

function asMessage(message: unknown): Message | undefined {
  if (typeof message !== "object" || message === null) {
    return undefined;
  }
  const m = message as { type?: unknown; dirty?: unknown; which?: unknown; sections?: unknown; tool?: unknown; subject?: unknown; key?: unknown; field?: unknown };
  switch (m.type) {
    case "reload":
      return { type: "reload", dirty: m.dirty === true };
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
      if (sections === undefined || typeof m.tool !== "string" || typeof m.subject !== "string") {
        return undefined;
      }
      return { type: "judge", sections, tool: m.tool, subject: m.subject };
    }
    default:
      return undefined;
  }
}
