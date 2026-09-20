/**
 * リスク管理画面の Webview パネル。生成・更新・破棄、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。README の手動確認の手順で確かめる。
 *
 * 画面は React（`src/webview/risk/`）で、ここが渡すのは「いま何を見せるか」（`RiskData`）だけ。
 * 渡し方は `core/screen-host.ts` の `retainedHost` が決める。この画面は編集の途中を持つので
 * `retainContextWhenHidden` が真で、**入れ物（HTML）は 1 度しか入らない**（ADR-0062）。
 * 中身を渡すのは、画面の編集を捨ててよいときだけ（人が「再読込」を押した、保存や作成が通った）。
 * ファイルが外で変わっただけのときは `changed` を送り、捨てるかどうかは人が決める。
 *
 * 対象は共通層の配点（`.ccnavi/common/risks.yml`。置き場は固定）の 1 本だけ。自身の層とプロジェクトの層も
 * 配点を持ち、判定は共通層と親の `project:` の層の和で行う（設計 §11.4.2）が、この画面ではそれらを開かない
 * （設計 §11.11）。パネルは 1 つ。
 *
 * チケット制御が disable のワークスペースでは開かない。配点は子チケットを閉じるときにしか
 * 読まれないので、disable の間は何も動かさない。入口（サイドパネル・コマンドパレット）も同じ鍵で隠れる。
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

import { followAppearance, readAppearance } from "./appearance.js";
import { loadBoard, runLint } from "./ccnavi.js";
import { lockFromBoard, lockFromError, type Lock } from "./core/lock.js";
import { asRiskForm, BUILTIN_RISK_TEXT, readRisk, type RiskDocument } from "./core/risk-doc.js";
import { renderRiskPage } from "./core/risk-render.js";
import type { RiskData, RiskForm, RiskMessage, ToRisk } from "./core/risk-view.js";
import { retainedHost, type ScreenHost } from "./core/screen-host.js";
import { WATCH_PATTERNS } from "./core/watch.js";
import { requireTickets } from "./ticket-control.js";
import { webviewScript, webviewStyle } from "./webview-asset.js";

const DEBOUNCE_MS = 120;
const DEFAULT_RISK = ".ccnavi/common/risks.yml";
/** 画面の名前。束ねの綴りは `src/webview/<名前>/main.tsx` → `out/webview/<名前>.js`、`style.css` → `<名前>.css` */
const SCREEN = "risk";
/** 自分の保存で監視が鳴るのを、この間だけ「外で変わった」と言わない */
const OWN_WRITE_GRACE_MS = 1500;

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
  readonly host: ScreenHost<RiskData>;
  /** 編集対象と設定ファイルの監視。対象のパスが変わるので、再読込のたびに張り直す */
  fileWatchers: vscode.FileSystemWatcher[];
  /** チケットの置き場の監視。開いている間ずっと同じ */
  watchers: vscode.FileSystemWatcher[];
  timer?: NodeJS.Timeout;
  lockTimer?: NodeJS.Timeout;
  loaded?: Loaded;
  /** 読み直せなかった理由。`loaded` と排他で、どちらかは必ず入っている */
  error?: string;
  lock: Lock;
  /** 「外で変わった」を出したまま、まだ読み直していない。表に戻ったときに送り直す */
  changedPending: boolean;
  wroteAt: number;
}

let state: PanelState | undefined;

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

/** `ccnaviBoard.openRisk` の本体 */
export async function openRisk(): Promise<void> {
  if (!requireTickets("リスク管理画面")) {
    return;
  }
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

  // 画面と CSS は束ねたものを読んで流し込む。無ければ開かずに言う（パネルだけ出しても白いまま）
  try {
    webviewScript(SCREEN);
    webviewStyle(SCREEN);
  } catch (error) {
    vscode.window.showErrorMessage(`リスク管理画面を表示できない: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }

  const panel = vscode.window.createWebviewPanel("ccnaviRisk", "ccnavi リスク管理", vscode.ViewColumn.One, {
    enableScripts: true,
    enableForms: false,
    localResourceRoots: [],
    // 編集の途中を持つので、タブを裏に回しても捨てない。
    retainContextWhenHidden: true,
  });
  followAppearance(panel);
  const current: PanelState = {
    panel,
    folder,
    tmpDir: fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-risk-")),
    host: riskHost(panel),
    fileWatchers: [],
    watchers: [],
    loaded,
    lock: lockFromError("まだ確認していない"),
    changedPending: false,
    wroteAt: 0,
  };
  state = current;
  registerPanelHandlers(current);
  show(current);
  void refreshLock(current);
}

function readPage(root: string): Loaded {
  // 共通層の置き場は `.ccnavi/common/` 固定（ADR-0052）。
  const riskRel = DEFAULT_RISK;
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

function resolveIn(root: string, filePath: string): string {
  return path.isAbsolute(filePath) ? filePath : path.join(root, filePath);
}

function registerPanelHandlers(current: PanelState): void {
  const { panel, folder } = current;

  panel.webview.onDidReceiveMessage((message: unknown) => {
    void handleMessage(current, asMessage(message));
  });

  // 保持する画面は裏でも生きている（`postMessage` は届く）が、VS Code の文書は同じ型定義の中で
  // 食い違っている（`retainContextWhenHidden` の側は「裏の画面には送れない」と言う）。
  // どちらが正しくても壊れないよう、表に戻ったところで、いま出すべき知らせを送り直す。
  // 中身（`data`）は送らない。送ると、裏で打っていた編集がここで消える。
  panel.onDidChangeViewState(() => {
    if (!panel.visible || !alive(current)) {
      return;
    }
    current.host.post({ type: "lock", lock: current.lock } satisfies ToRisk);
    if (current.changedPending) {
      current.changedPending = true;
      current.host.post({ type: "changed" } satisfies ToRisk);
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
    try {
      fs.rmSync(current.tmpDir, { recursive: true, force: true });
    } catch {
      // 一時ファイルの片付けに失敗しても画面の仕事には関係ない
    }
    if (state === current) {
      state = undefined;
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
 * 配点のファイルと設定ファイルが変わったら「外で変わった」と伝える。自分の保存は除く。
 * 対象のパスは設定で変わるので、再読込のたびに張り直す。絶対パスはワークスペース相対の glob に
 * ならないので、そのディレクトリを起点にする。
 */
function watchFiles(current: PanelState): void {
  for (const watcher of current.fileWatchers) {
    watcher.dispose();
  }
  current.fileWatchers = [];
  const riskRel = current.loaded?.riskRel ?? DEFAULT_RISK;
  const patterns: vscode.RelativePattern[] = [
    patternFor(current.folder, riskRel),
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
      current.host.post({ type: "changed" } satisfies ToRisk);
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
    current.host.post({ type: "lock", lock } satisfies ToRisk);
  }
  return lock;
}

/**
 * いま見せるものを渡す。**画面の編集はここで捨てられる**ので、呼ぶのは人が「再読込」を押した
 * ときと、保存・作成が通って中身が入れ替わったときだけ（ADR-0062）。
 */
function show(current: PanelState): void {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  current.error = undefined;
  // 読み直したので、「外で変わった」はもう今のことではない
  current.changedPending = false;
  current.host.send({
    kind: "page",
    page: {
      root: current.folder.uri.fsPath,
      riskPath: loaded.riskRel,
      exists: loaded.exists,
      model: loaded.doc.model,
      lock: current.lock,
    },
  });
}

/** 読み直せなかったことを見せる。理由は覚えておく（渡せなかったときに `ready` で渡し直すため） */
function showError(current: PanelState, error: string): void {
  current.loaded = undefined;
  current.error = error;
  current.host.send({ kind: "error", error });
}

function reload(current: PanelState): void {
  try {
    current.loaded = readPage(current.folder.uri.fsPath);
  } catch (error) {
    showError(current, (error as Error).message);
    return;
  }
  show(current);
  watchFiles(current);
  void refreshLock(current);
}

/**
 * いまの状態で渡し直す。1 枚目を読み込んでいる間に見送られたもの（`deferred`）は、画面が
 * 組み上がった（`ready`）ところでここから渡る。
 */
function redraw(current: PanelState): void {
  if (current.loaded !== undefined) {
    show(current);
    return;
  }
  if (current.error !== undefined) {
    showError(current, current.error);
  }
}

/**
 * リスク管理の画面に渡す口。VS Code のパネルを `retainedHost` の形に合わせる。
 * **入れ物は 1 度しか入らない**ので、表裏は渡さない（保持する画面は裏でも生きている）。
 * パネルの `retainContextWhenHidden` を偽に変えると、送った先が捨てられていても気づけなくなる。
 * 型では止まらないので、ここで見て言う。
 */
function riskHost(panel: vscode.WebviewPanel): ScreenHost<RiskData> {
  if (panel.options.retainContextWhenHidden !== true) {
    console.error(`リスク管理の画面は retainContextWhenHidden が真であることを前提にしている（retainedHost）。偽のままだと、裏に回った画面へ送り続けて中身が古いまま止まる`);
  }
  return retainedHost<RiskData>(
    {
      html(text: string): void {
        panel.webview.html = text;
      },
      post(message: unknown): void {
        void panel.webview.postMessage(message);
      },
    },
    (data) =>
      renderRiskPage(data, {
        nonce: crypto.randomBytes(16).toString("base64"),
        script: webviewScript(SCREEN),
        style: webviewStyle(SCREEN),
        appearance: readAppearance(),
      }),
  );
}

/**
 * 保存を始めたときに読んでいたものが、往復の間に入れ替わっていないか。
 *
 * 保存は実行ファイルへ 2 度出る（`--lint` と錠の取り直し）。その間に人が「再読込」を押せば、
 * 画面の編集は捨てられ、新しい中身が出ている。**そこへ古い編集を書くと、捨てたはずのものが
 * ファイルに入る。** 読み直されていたら、この保存はもう無かったことにする。
 */
function stale(current: PanelState, loaded: Loaded): boolean {
  if (current.loaded === loaded) {
    return false;
  }
  fail(current, `読み直したので、この保存は捨てた。いまの${"配点"}で編集し直す`);
  return true;
}

/** 操作の結果の一言。1 枚目を読み込んでいる間だけ落ちる（裏に回っていても届く） */
function fail(current: PanelState, message: string): void {
  current.host.post({ type: "failed", message } satisfies ToRisk);
}

async function handleMessage(current: PanelState, message: RiskMessage | undefined): Promise<void> {
  if (message === undefined || !alive(current)) {
    return;
  }
  if (message.type === "ready") {
    // 画面が組み上がった。1 枚目を読み込んでいる間に見送った中身は、ここで渡る。
    // 見送るものが無くても渡し直す（同じ中身がもう 1 度届く）。VS Code が画面を作り直す道
    // （`Developer: Reload Webviews`）では、入れてある HTML の中身が古いことがあるため
    current.host.ready();
    redraw(current);
    current.host.post({ type: "appearance", value: readAppearance() } satisfies ToRisk);
    return;
  }
  // 読み直せていない画面では、配点に当たる操作はどれも行き先が無い（「再読込」は
  // 押せるが、その道は `reload` が読み直しからやり直す）
  if (current.loaded === undefined && message.type !== "reload") {
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
          // 画面は「再読込」を押した時点で欄を止めている。やめたことを伝えないと止まったままになる
          current.host.post({ type: "cancelled" } satisfies ToRisk);
          return;
        }
      }
      reload(current);
      return;
    }
    case "openFile": {
      if (current.loaded === undefined) {
        return;
      }
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
    // 書けなかったのに猶予を立てたままだと、その間の本物の外部変更を握りつぶす。
    current.wroteAt = 0;
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
    tmp = path.join(current.tmpDir, "risks.yml");
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
  if (stale(current, loaded)) {
    return;
  }
  if (!lint.ok) {
    fail(current, lint.error);
    return;
  }
  if (!lint.value.ok) {
    // 苦情は渡した一時ファイルのパスを名乗るので、画面では対象のファイルの綴りに直す。
    fail(current, `--lint が error を報告した。直してから保存する:\n${lint.value.report.split(tmp).join(loaded.riskRel)}`);
    return;
  }

  // 2. 作業中のチケットが無いこと。押した時点で取り直す。
  const lock = await refreshLock(current);
  if (!alive(current)) {
    return;
  }
  if (stale(current, loaded)) {
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
    fail(current, "配点のファイルが読み込んだあとに外で変更されている。再読込してから編集し直す（この変更は上書きしない）");
    return;
  }

  try {
    current.wroteAt = Date.now();
    fs.writeFileSync(loaded.riskPath, text, "utf8");
  } catch (error) {
    current.wroteAt = 0;
    fail(current, `配点のファイルに書けない: ${(error as Error).message}`);
    return;
  }
  reload(current);
  const tail = lint.value.report.split("\n").filter((l) => l.trim() !== "").pop() ?? "";
  vscode.window.showInformationMessage(`${loaded.riskRel} に保存した（${tail}）`);
}

function asMessage(message: unknown): RiskMessage | undefined {
  if (typeof message !== "object" || message === null) {
    return undefined;
  }
  const m = message as { type?: unknown; dirty?: unknown; form?: unknown };
  switch (m.type) {
    case "reload":
      return { type: "reload", dirty: m.dirty === true };
    case "ready":
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
