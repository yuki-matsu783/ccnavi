/**
 * リスク管理画面の Webview パネル。生成・更新・破棄、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。README の手動確認の手順で確かめる。
 *
 * 対象はワークスペースの配点（`.claude/ccnavi/risk.yml`、`CCNAVI_RISK`）の 1 本だけ。配点は
 * プロジェクトごとには持たない（設計 §25）。パネルは 1 つ。
 *
 * 検証は実行ファイルに任せる。編集中の内容は一時ファイルに書き、`--lint --risk <パス>` で渡す。
 * 保存は、検証（`--lint`）を通り、作業中のチケットが無く（どのツリーでも。配点は子を閉じる
 * ときに読まれるので、走っている最中に変えない）、ファイルが外で変わっていないときだけ行う。
 * ファイルが無いときは組み込みの配点を見せ、「作る」で同じ値のファイルを書き出してから直す。
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import { WATCH_PATTERNS } from "./board-panel.js";
import { loadBoard, runLint } from "./ccnavi.js";
import { envFromSettingsJson } from "./core/hooks.js";
import { lockFromBoard, lockFromError, type Lock } from "./core/lock.js";
import { escapeHtml } from "./core/render.js";
import { asRiskForm, BUILTIN_RISK_TEXT, readRisk, type RiskDocument, type RiskForm } from "./core/risk-doc.js";
import { renderRiskPage } from "./core/risk-render.js";
import { ticketControl } from "./ticket-control.js";

const DEBOUNCE_MS = 120;
const DEFAULT_RISK = ".claude/ccnavi/risk.yml";
/** 自分の保存で監視が鳴るのを、この間だけ「外で変わった」と言わない */
const OWN_WRITE_GRACE_MS = 1500;

type Message =
  | { readonly type: "reload"; readonly dirty: boolean }
  | { readonly type: "openFile" }
  | { readonly type: "create" }
  | { readonly type: "save"; readonly form: RiskForm };

interface Loaded {
  /** ファイルの本文。無ければ組み込みの配点の本文 */
  readonly text: string;
  readonly exists: boolean;
  /** 無いときは 0 */
  readonly mtimeMs: number;
  readonly doc: RiskDocument;
  readonly riskPath: string;
  /** ワークスペースルートからの相対で見せる綴り */
  readonly riskRel: string;
}

interface PanelState {
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

let state: PanelState | undefined;

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

/** `ccnaviBoard.openRisk` の本体 */
export async function openRisk(): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    vscode.window.showInformationMessage("ワークスペースが開かれていないため、リスク管理画面を表示できない");
    return;
  }
  if (state !== undefined) {
    state.panel.reveal(state.panel.viewColumn);
    return;
  }

  const root = folder.uri.fsPath;
  let loaded: Loaded;
  try {
    loaded = readPage(root);
  } catch (error) {
    vscode.window.showErrorMessage(`リスク管理画面を表示できない: ${(error as Error).message}`);
    return;
  }

  const panel = vscode.window.createWebviewPanel("ccnaviRisk", "ccnavi リスク管理", vscode.ViewColumn.One, {
    enableScripts: true,
    enableForms: false,
    localResourceRoots: [],
    // 編集の途中を持つので、タブを裏に回しても捨てない。
    retainContextWhenHidden: true,
  });
  const current: PanelState = {
    panel,
    folder,
    tmpDir: fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-risk-")),
    watchers: [],
    loaded,
    lock: lockFromError("まだ確認していない"),
    wroteAt: 0,
  };
  state = current;
  registerPanelHandlers(current);
  show(current);
  void refreshLock(current);
}

function riskRelOf(root: string): string {
  const settingsText = readText(path.join(root, ".claude", "settings.json"));
  return (settingsText !== undefined && envFromSettingsJson(settingsText, "CCNAVI_RISK")) || DEFAULT_RISK;
}

function readPage(root: string): Loaded {
  const riskRel = riskRelOf(root);
  const riskPath = resolveIn(root, riskRel);
  let text: string;
  let mtimeMs: number;
  let exists: boolean;
  try {
    text = fs.readFileSync(riskPath, "utf8");
    mtimeMs = fs.statSync(riskPath).mtimeMs;
    exists = true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      throw new Error(`配点のファイルを読めない（${riskRel}）: ${(error as Error).message}`);
    }
    // 無いのは不備ではない（組み込みの配点）。画面は組み込みを見せ、「作る」だけができる。
    text = BUILTIN_RISK_TEXT;
    mtimeMs = 0;
    exists = false;
  }
  return { text, exists, mtimeMs, doc: readRisk(text), riskPath, riskRel };
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
    if (state === current) {
      state = undefined;
    }
  });

  // 配点のファイルと設定ファイルが変わったら「外で変わった」と伝える。自分の保存は除く。
  const riskRel = current.loaded?.riskRel ?? DEFAULT_RISK;
  for (const pattern of [toGlob(riskRel), ".claude/settings.json", ".claude/settings.local.json"]) {
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
 * 配点はワークスペースに 1 本で、どのツリーの子を閉じるときにも読まれるので、どのツリーの doing でも止める。
 */
async function refreshLock(current: PanelState): Promise<Lock> {
  const result = await loadBoard(current.folder.uri.fsPath, binSetting());
  const lock = result.ok ? lockFromBoard(result.board) : lockFromError(result.error);
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
  current.panel.webview.html = renderRiskPage(
    {
      root: current.folder.uri.fsPath,
      riskPath: loaded.riskRel,
      exists: loaded.exists,
      ticketControl: ticketControl(),
      model: loaded.doc.model,
      lock: current.lock,
    },
    { nonce: crypto.randomBytes(16).toString("base64") },
  );
}

function reload(current: PanelState): void {
  try {
    current.loaded = readPage(current.folder.uri.fsPath);
  } catch (error) {
    current.panel.webview.html = renderError((error as Error).message);
    return;
  }
  show(current);
  void refreshLock(current);
}

function renderError(error: string): string {
  return `<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none';"><title>ccnavi リスク管理</title></head><body><p>リスク管理画面を読み直せなかった。直してから「ccnavi ボード: リスク管理画面を開く」を実行し直す。</p><pre>${escapeHtml(error)}</pre></body></html>`;
}

function fail(current: PanelState, message: string): void {
  void current.panel.webview.postMessage({ type: "failed", message });
}

async function handleMessage(current: PanelState, message: Message | undefined): Promise<void> {
  if (message === undefined || !alive(current) || current.loaded === undefined) {
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
          return;
        }
      }
      reload(current);
      return;
    }
    case "openFile": {
      const target = current.loaded.riskPath;
      void vscode.workspace.openTextDocument(target).then(
        (document) => vscode.window.showTextDocument(document),
        () => vscode.window.showInformationMessage(`ファイルを開けなかった: ${target}`),
      );
      return;
    }
    case "create": {
      create(current);
      return;
    }
    case "save": {
      await save(current, message.form);
      return;
    }
  }
}

/** 組み込みと同じ値のファイルを書き出す。既にあれば上書きしない（値は同じなので数え方は変わらない） */
function create(current: PanelState): void {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  if (fs.existsSync(loaded.riskPath)) {
    fail(current, `${loaded.riskRel} は既に存在するため、上書きしない。再読込する`);
    return;
  }
  try {
    fs.mkdirSync(path.dirname(loaded.riskPath), { recursive: true });
    current.wroteAt = Date.now();
    fs.writeFileSync(loaded.riskPath, BUILTIN_RISK_TEXT, { encoding: "utf8", flag: "wx" });
  } catch (error) {
    fail(current, `${loaded.riskRel} に書けない: ${(error as Error).message}`);
    return;
  }
  reload(current);
  vscode.window.showInformationMessage(`${loaded.riskRel} を組み込みの配点で作った。コミットは人が行う`);
}

async function save(current: PanelState, form: RiskForm): Promise<void> {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  if (!loaded.exists) {
    fail(current, `${loaded.riskRel} が無い。先に「組み込みの配点でファイルを作る」を押す`);
    return;
  }
  const root = current.folder.uri.fsPath;
  let tmp: string;
  let text: string;
  try {
    text = loaded.doc.apply(form);
    tmp = path.join(current.tmpDir, "risk.yml");
    fs.writeFileSync(tmp, text, "utf8");
  } catch (error) {
    fail(current, `編集中の内容を書き出せない: ${(error as Error).message}`);
    return;
  }

  // 1. 検証。error が 1 件でもあれば保存しない。
  const lint = await runLint(root, binSetting(), { kind: "risk", path: tmp });
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
    mtimeMs = fs.statSync(loaded.riskPath).mtimeMs;
  } catch (error) {
    fail(current, `配点のファイルを確かめられない: ${(error as Error).message}`);
    return;
  }
  if (mtimeMs !== loaded.mtimeMs) {
    fail(current, "配点のファイルが読み込み後に外部で変更されている。再読込してから編集し直す（この変更は上書きしない）");
    return;
  }

  try {
    current.wroteAt = Date.now();
    fs.writeFileSync(loaded.riskPath, text, "utf8");
  } catch (error) {
    fail(current, `配点のファイルに書けない: ${(error as Error).message}`);
    return;
  }
  reload(current);
  const tail = lint.value.report.split("\n").filter((l) => l.trim() !== "").pop() ?? "";
  vscode.window.showInformationMessage(`${loaded.riskRel} に保存した（${tail}）`);
}

function asMessage(message: unknown): Message | undefined {
  if (typeof message !== "object" || message === null) {
    return undefined;
  }
  const m = message as { type?: unknown; dirty?: unknown; form?: unknown };
  switch (m.type) {
    case "reload":
      return { type: "reload", dirty: m.dirty === true };
    case "openFile":
    case "create":
      return { type: m.type };
    case "save": {
      const form = asRiskForm(m.form);
      return form === undefined ? undefined : { type: "save", form };
    }
    default:
      return undefined;
  }
}
