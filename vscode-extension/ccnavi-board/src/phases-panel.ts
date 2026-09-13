/**
 * フェーズ管理画面の Webview パネル。生成・更新・破棄、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。README の手動確認の手順で確かめる。
 *
 * 対象は 3 種（設計 §11.2、§11.4.1）。共通層の種類（`.claude/ccnavi/phases.yml`、`CCNAVI_PHASES`）、
 * ワークスペース自身の層（既定 `.ccnavi/config/phases.yml`）、プロジェクト 1 つの層
 * （既定 `projects/<名前>/.ccnavi/config/phases.yml`）。対象ごとに 1 パネルで、並べて開ける。
 * 層の置き場は実行ファイルが解いたもの（`--explain --json` の `layers[].phases_file`）を使い、拡張は組まない。
 *
 * 検証は実行ファイルに任せる。編集中の内容は一時ファイルに書き、共通層なら `--lint --phases <パス>`、
 * 層なら `--lint --project-phases-file <名前>=<パス>`（自身の層は名前が `self`）で渡す。層の種類は
 * 共通層と合成して確かめられる（同じ id で中身が違う、表示名の重なり、overlap / requires の指す先）。
 * 保存は、検証（`--lint`）を通り、作業中のチケットが無く、ファイルが外で変わっていないときだけ行う。
 * 作業中のチケットは、共通層と自身の層ならどのツリーでも、プロジェクトの層ならそのプロジェクトの分を見る
 * （種類は承認・着手・閉じるときに読まれるので、走っている最中に変えない）。
 * ファイルが無いとき、共通層は空の画面と「雛形で作る」を見せる。層は雛形を置かない（雛形の id は共通層の種類と
 * 重なりやすく、中身が違えばその層が空として扱われる）。代わりに画面で種類を足させ、検証を通った最初の保存で
 * ファイルを作る。種類の無いファイル（`phases: {}`）は実行ファイルが error にするので、先に書き出さない。
 * 組み込みの既定は無い（実行ファイルも持たない。既定を組み込むと、意図せずレビューの要否が決まる）。
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import { WATCH_PATTERNS } from "./board-panel.js";
import { loadBoard, runLint, type LintOverride } from "./ccnavi.js";
import { envFromSettingsJson } from "./core/hooks.js";
import { LAYER_SELF, projectLayer, selfLayer } from "./core/layers.js";
import { lockFromBoard, lockFromError, type Lock } from "./core/lock.js";
import { asPhasesForm, readPhases, TEMPLATE_PHASES_TEXT, type PhasesDocument, type PhasesForm } from "./core/phases-doc.js";
import { renderPhasesPage } from "./core/phases-render.js";
import { escapeHtml } from "./core/render.js";
import { ticketControl } from "./ticket-control.js";

const DEBOUNCE_MS = 120;
const DEFAULT_PHASES = ".claude/ccnavi/phases.yml";
/** 自分の保存で監視が鳴るのを、この間だけ「外で変わった」と言わない */
const OWN_WRITE_GRACE_MS = 1500;
/** 層のファイルを最初の保存で作るときに、先頭へ置く説明 */
const LAYER_HEADER = [
  "# この層のフェーズの種類。共通層の種類に足して使う（設計 §11.4.1）。",
  "# 共通層と同じ id を書くなら中身も同じにする。違えば --lint が error を出し、この層は空として扱われる。",
  "",
].join("\n");

/** 画面が直す種類のファイル。共通層、自身の層、プロジェクト 1 つの層 */
export type PhasesTarget =
  | { readonly kind: "common" }
  | { readonly kind: "self" }
  | { readonly kind: "project"; readonly name: string };

type Message =
  | { readonly type: "reload"; readonly dirty: boolean }
  | { readonly type: "openFile" }
  | { readonly type: "create" }
  | { readonly type: "save"; readonly form: PhasesForm };

interface Loaded {
  /** ファイルの本文。無ければ空 */
  readonly text: string;
  readonly exists: boolean;
  /** 無いときは 0 */
  readonly mtimeMs: number;
  readonly doc: PhasesDocument;
  readonly phasesPath: string;
  /** ワークスペースルートからの相対で見せる綴り */
  readonly phasesRel: string;
  /** 上部に出す注意。実行ファイルがこの層を読めていない、など */
  readonly notices: readonly string[];
}

interface PanelState {
  readonly target: PhasesTarget;
  readonly panel: vscode.WebviewPanel;
  readonly folder: vscode.WorkspaceFolder;
  readonly tmpDir: string;
  /** 編集対象と設定ファイルの監視。対象のパスが変わるので、再読込のたびに張り直す */
  fileWatchers: vscode.FileSystemWatcher[];
  /** チケットの置き場の監視。開いている間ずっと同じ */
  watchers: vscode.FileSystemWatcher[];
  timer?: NodeJS.Timeout;
  lockTimer?: NodeJS.Timeout;
  loaded?: Loaded;
  lock: Lock;
  wroteAt: number;
}

/** 対象ごとのパネル。鍵は共通層が空、自身の層が `self`、プロジェクトは `project/<名前>` */
const panels = new Map<string, PanelState>();
/** 開いている途中の鍵。層の置き場を実行ファイルに聞く間に、同じ対象をもう 1 枚開かない */
const opening = new Set<string>();

function keyOf(target: PhasesTarget): string {
  switch (target.kind) {
    case "common":
      return "";
    case "self":
      return "self";
    case "project":
      return `project/${target.name}`;
  }
}

/** 保存を止めるチケットを絞るプロジェクト。共通層と自身の層は絞らない */
function projectOf(target: PhasesTarget): string | undefined {
  return target.kind === "project" ? target.name : undefined;
}

function titleOf(target: PhasesTarget): string {
  switch (target.kind) {
    case "common":
      return "ccnavi フェーズ管理";
    case "self":
      return "ccnavi フェーズ管理: 自身の層";
    case "project":
      return `ccnavi フェーズ管理: ${target.name}`;
  }
}

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

/** `ccnaviBoard.openPhases` の本体。引数なしは共通層の種類 */
export async function openPhases(target: PhasesTarget = { kind: "common" }): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    vscode.window.showInformationMessage("ワークスペースが開かれていないため、フェーズ管理画面を表示できない");
    return;
  }
  const key = keyOf(target);
  const existing = panels.get(key);
  if (existing !== undefined) {
    existing.panel.reveal(existing.panel.viewColumn);
    return;
  }
  if (opening.has(key)) {
    return;
  }

  const root = folder.uri.fsPath;
  let loaded: Loaded;
  opening.add(key);
  try {
    loaded = await readPage(root, target);
  } catch (error) {
    vscode.window.showErrorMessage(`フェーズ管理画面を表示できない: ${(error as Error).message}`);
    return;
  } finally {
    opening.delete(key);
  }

  const panel = vscode.window.createWebviewPanel("ccnaviPhases", titleOf(target), vscode.ViewColumn.One, {
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
    tmpDir: fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-phases-")),
    fileWatchers: [],
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

/** `.claude/settings.local.json` が先、無ければ `.claude/settings.json`。実行ファイルが読む env の重なりと同じ */
function phasesRelOf(root: string): string {
  for (const name of ["settings.local.json", "settings.json"]) {
    const settingsText = readText(path.join(root, ".claude", name));
    const found = settingsText === undefined ? "" : envFromSettingsJson(settingsText, "CCNAVI_PHASES");
    if (found !== "") {
      return found;
    }
  }
  return DEFAULT_PHASES;
}

async function readPage(root: string, target: PhasesTarget): Promise<Loaded> {
  let phasesRel: string;
  let phasesPath: string;
  const notices: string[] = [];
  if (target.kind === "common") {
    phasesRel = phasesRelOf(root);
    phasesPath = resolveIn(root, phasesRel);
  } else {
    // 層の置き場は実行ファイルに聞く。CCNAVI_PROJECT_HOME から自分で組むと、組み方がずれたときに
    // この画面で保存した種類が承認と着手に効かなくなる。答えは git プロジェクトルートの版（設計 §11.2）。
    const board = await loadBoard(root, binSetting());
    if (!board.ok) {
      throw new Error(`層の置き場を実行ファイルから取得できない: ${board.error}`);
    }
    const layer = target.kind === "self" ? selfLayer(board.board) : projectLayer(board.board, target.name);
    if (layer === undefined || layer.phasesFile.path === "") {
      throw new Error(
        target.kind === "self"
          ? "実行ファイルが自身の層を出していない（層に対応していない古い版）"
          : `プロジェクト ${target.name} は層として数えられていない（置き場の直下に無いか、予約名 common / self）`,
      );
    }
    phasesPath = resolveIn(root, layer.phasesFile.path);
    phasesRel = path.relative(root, phasesPath).split(path.sep).join("/");
    if (layer.phasesFile.unreadable !== "") {
      notices.push(`実行ファイルはこのファイルを読めず、層の種類を空として扱っている（共通層の種類だけで進む）: ${layer.phasesFile.unreadable}`);
    }
  }
  let text: string;
  let mtimeMs: number;
  let exists: boolean;
  try {
    text = fs.readFileSync(phasesPath, "utf8");
    mtimeMs = fs.statSync(phasesPath).mtimeMs;
    exists = true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      throw new Error(`フェーズの種類のファイルを読めない（${phasesRel}）: ${(error as Error).message}`);
    }
    // 無いのは不備ではない（番号だけのフェーズ、無い層は空）。画面は空を見せ、「作る」だけができる。
    text = "";
    mtimeMs = 0;
    exists = false;
  }
  if (target.kind === "common" && !exists) {
    notices.push(
      "種類は自身の層とプロジェクトの層にも置ける（プロジェクト管理画面から開く）。共通層に置いた種類は全プロジェクトに効き、層に同じ id で中身の違う種類があるとその層が空として扱われる",
    );
  }
  // 無いときの苦情（version が無い、phases が無い）は画面に出さない。無いことは帯で言う。
  const parsed = readPhases(text);
  const doc = exists ? parsed : { apply: parsed.apply, model: { ...parsed.model, problems: [] } };
  return { text, exists, mtimeMs, doc, phasesPath, phasesRel, notices };
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
    if (panels.get(key) === current) {
      panels.delete(key);
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
 * 種類のファイルと設定ファイルが変わったら「外で変わった」と伝える。自分の保存は除く。
 * 対象のパスは設定で変わるので、再読込のたびに張り直す。絶対パスはワークスペース相対の glob に
 * ならないので、そのディレクトリを起点にする。
 */
function watchFiles(current: PanelState): void {
  for (const watcher of current.fileWatchers) {
    watcher.dispose();
  }
  current.fileWatchers = [];
  const phasesRel = current.loaded?.phasesRel ?? DEFAULT_PHASES;
  const patterns: vscode.RelativePattern[] = [
    patternFor(current.folder, phasesRel),
    new vscode.RelativePattern(current.folder, ".claude/settings.json"),
    new vscode.RelativePattern(current.folder, ".claude/settings.local.json"),
  ];
  for (const pattern of patterns) {
    const watcher = vscode.workspace.createFileSystemWatcher(pattern);
    const changed = () => scheduleChanged(current);
    watcher.onDidCreate(changed);
    watcher.onDidChange(changed);
    watcher.onDidDelete(changed);
    current.fileWatchers.push(watcher);
  }
}

function patternFor(folder: vscode.WorkspaceFolder, filePath: string): vscode.RelativePattern {
  if (path.isAbsolute(filePath)) {
    return new vscode.RelativePattern(vscode.Uri.file(path.dirname(filePath)), path.basename(filePath));
  }
  return new vscode.RelativePattern(folder, toGlob(filePath));
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
 * 共通層の種類はどのツリーの承認・着手・閉じるときにも読まれるので、どのツリーの doing でも止める。
 * 自身の層も同じに止める（ワークスペース自身のチケットだけに効くが、絞らずに止める側に倒す）。
 * プロジェクトの層は、そのプロジェクトのチケットにしか足されないので、そのプロジェクトの doing だけを見る。
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
  current.panel.webview.html = renderPhasesPage(
    {
      root: current.folder.uri.fsPath,
      phasesPath: loaded.phasesRel,
      exists: loaded.exists,
      ticketControl: ticketControl(),
      model: loaded.doc.model,
      lock: current.lock,
      layer: current.target.kind !== "common",
      notices: loaded.notices,
    },
    { nonce: crypto.randomBytes(16).toString("base64") },
  );
}

async function reload(current: PanelState): Promise<void> {
  let loaded: Loaded;
  try {
    loaded = await readPage(current.folder.uri.fsPath, current.target);
  } catch (error) {
    if (alive(current)) {
      current.panel.webview.html = renderError((error as Error).message);
    }
    return;
  }
  if (!alive(current)) {
    return;
  }
  current.loaded = loaded;
  show(current);
  watchFiles(current);
  void refreshLock(current);
}

function renderError(error: string): string {
  return `<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none';"><title>ccnavi フェーズ管理</title></head><body><p>フェーズ管理画面を読み直せなかった。直してから「ccnavi ボード: フェーズ管理画面を開く」を実行し直す。</p><pre>${escapeHtml(error)}</pre></body></html>`;
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
      await reload(current);
      return;
    }
    case "openFile": {
      const target = current.loaded.phasesPath;
      void vscode.workspace.openTextDocument(target).then(
        (document) => vscode.window.showTextDocument(document),
        () => vscode.window.showInformationMessage(`ファイルを開けなかった: ${target}`),
      );
      return;
    }
    case "create": {
      await create(current);
      return;
    }
    case "save": {
      await save(current, message.form);
      return;
    }
  }
}

/** 共通層に雛形を書き出す。既にあれば上書きしない。層には雛形を置かない（最初の保存で作る） */
async function create(current: PanelState): Promise<void> {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  if (current.target.kind !== "common") {
    fail(current, "層には雛形を置かない。種類を足して保存すると、ファイルが作られる");
    return;
  }
  if (fs.existsSync(loaded.phasesPath)) {
    fail(current, `${loaded.phasesRel} は既に存在するため、上書きしない。再読込する`);
    return;
  }
  try {
    fs.mkdirSync(path.dirname(loaded.phasesPath), { recursive: true });
    current.wroteAt = Date.now();
    fs.writeFileSync(loaded.phasesPath, TEMPLATE_PHASES_TEXT, { encoding: "utf8", flag: "wx" });
  } catch (error) {
    // 書けなかったのに猶予を立てたままだと、その間の本物の外部変更を握りつぶす。
    current.wroteAt = 0;
    fail(current, `${loaded.phasesRel} に書けない: ${(error as Error).message}`);
    return;
  }
  await reload(current);
  vscode.window.showInformationMessage(
    `${loaded.phasesRel} を雛形で作った。scope の綴りをこのプロジェクトの置き場に直す。コミットは人が行う`,
  );
}

function overrideFor(target: PhasesTarget, tmp: string): LintOverride {
  switch (target.kind) {
    case "common":
      return { kind: "phases", path: tmp };
    case "self":
      return { kind: "layerPhases", name: LAYER_SELF, path: tmp };
    case "project":
      return { kind: "layerPhases", name: target.name, path: tmp };
  }
}

async function save(current: PanelState, form: PhasesForm): Promise<void> {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  const layer = current.target.kind !== "common";
  if (!loaded.exists && !layer) {
    fail(current, `${loaded.phasesRel} が無い。先に「雛形でファイルを作る」を押す`);
    return;
  }
  const root = current.folder.uri.fsPath;
  let tmp: string;
  let text: string;
  try {
    // 層のファイルを初めて作るときは、先頭に説明を置く。検証にも同じ本文を掛ける。
    text = (loaded.exists ? "" : LAYER_HEADER) + loaded.doc.apply(form);
    tmp = path.join(current.tmpDir, "phases.yml");
    fs.writeFileSync(tmp, text, "utf8");
  } catch (error) {
    fail(current, `編集中の内容を書き出せない: ${(error as Error).message}`);
    return;
  }

  // 1. 検証。error が 1 件でもあれば保存しない。承認済みの計画がこの種類で読めるかもここで分かる。
  //    層なら共通層との合成もここで確かめる。
  const lint = await runLint(root, binSetting(), overrideFor(current.target, tmp));
  if (!alive(current)) {
    return;
  }
  if (!lint.ok) {
    fail(current, lint.error);
    return;
  }
  if (!lint.value.ok) {
    // 苦情は渡した一時ファイルのパスを名乗るので、画面では対象のファイルの綴りに直す。
    fail(current, `--lint が error を報告した。直してから保存する:\n${lint.value.report.split(tmp).join(loaded.phasesRel)}`);
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

  // 3. 読み込んでから外で変わっていないこと。無かったファイルは、まだ無いこと。
  if (loaded.exists) {
    let mtimeMs: number;
    try {
      mtimeMs = fs.statSync(loaded.phasesPath).mtimeMs;
    } catch (error) {
      fail(current, `フェーズの種類のファイルを確かめられない: ${(error as Error).message}`);
      return;
    }
    if (mtimeMs !== loaded.mtimeMs) {
      fail(current, "フェーズの種類のファイルが読み込み後に外部で変更されている。再読込してから編集し直す（この変更は上書きしない）");
      return;
    }
  } else if (fs.existsSync(loaded.phasesPath)) {
    fail(current, "フェーズの種類のファイルが読み込み後に外部で作られている。再読込してから編集し直す（上書きしない）");
    return;
  }

  try {
    current.wroteAt = Date.now();
    if (loaded.exists) {
      fs.writeFileSync(loaded.phasesPath, text, "utf8");
    } else {
      fs.mkdirSync(path.dirname(loaded.phasesPath), { recursive: true });
      fs.writeFileSync(loaded.phasesPath, text, { encoding: "utf8", flag: "wx" });
    }
  } catch (error) {
    current.wroteAt = 0;
    fail(current, `フェーズの種類のファイルに書けない: ${(error as Error).message}`);
    return;
  }
  await reload(current);
  const tail = lint.value.report.split("\n").filter((l) => l.trim() !== "").pop() ?? "";
  vscode.window.showInformationMessage(`${loaded.phasesRel} に保存した（${tail}）`);
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
      const form = asPhasesForm(m.form);
      return form === undefined ? undefined : { type: "save", form };
    }
    default:
      return undefined;
  }
}
