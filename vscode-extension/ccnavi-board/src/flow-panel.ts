/**
 * フロー編集画面の Webview パネル。生成・更新・破棄、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。README の手動確認の手順で確かめる。
 *
 * 子チケット 1 枚のフロー（設計 9.3.1、ADR-0085）を図で直す。開くのはボードのカードの「フロー」だけで、
 * **タブは子ごとに 1 枚**（同じ子をもう 1 度開けば前面に出す）。
 *
 * 画面は React（`src/webview/flow/`）で、ここが渡すのは「いま何を見せるか」（`FlowData`）だけ。
 * 渡し方はフェーズ管理と同じ `retainedHost`（編集の途中を持つ。ADR-0062）。
 *
 * 置き場と錠は実行ファイルに聞く（`--explain --json` の `tickets[].flow`）。拡張は置き場を組まず、
 * 着手中かを `started_at` から組み直さない（ADR-0035）。保存は次を全部満たすときだけ書く。
 *
 * 1. 押した時点で実行ファイルに聞き直し、`locked` が偽（着手中でない）
 * 2. 置き場が読んだときと同じ（往復の間にチケットが動いて置き場が替わっていない）
 * 3. 読み込んでから外で変わっていない（無かったファイルは、まだ無い）
 * 4. ツリーのルートからファイルまでの途中にシンボリックリンクが無い
 *
 * 書き込みは一時ファイルの入れ替えで、リンクを辿らない（`core/flow-write.ts`）。置き場は承認済みの領域
 * （既定 `.ccnavi/approved/flows/<子>.json`）で、エージェントは判定に止められて書けない。
 *
 * 残る隙間（TOCTOU）: 1 で聞き直してから書くまでの間に子が着手されると、着手の直後に書き込みが入りうる。
 * 着手は人か親のエージェントが `ccnavi-ticket.sh start` を打つ操作で、聞き直しから書き込みまでは同じ保存の
 * 1 回の中（実行ファイルを 1 度起こすぶん）。塞ぐには実行ファイルの側に錠の置き場が要るので、ここでは狭めるだけにする。
 *
 * 取り込み（`.vscode/workflows/*.json`）は画面の編集中のフローを置き換えるだけで、書くのは保存を押したとき。
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

import { followAppearance, postAppearance, readAppearance } from "./appearance.js";
import { loadBoard } from "./ccnavi.js";
import { importFlow, parseFlow, serializeFlow, templateFlow, type FlowDoc } from "./core/flow-doc.js";
import { renderFlowPage } from "./core/flow-render.js";
import { readFlowFile, writeFlowFile } from "./core/flow-write.js";
import {
  asFlowMessage,
  flowTargetOf,
  lockFromFailure,
  type FlowData,
  type FlowLock,
  type FlowMessage,
  type FlowTarget,
  type ToFlow,
} from "./core/flow-view.js";
import { loadingText } from "./core/loading-render.js";
import { retainedHost, type ScreenHost } from "./core/screen-host.js";
import { WATCH_PATTERNS } from "./core/watch.js";
import { showLoading } from "./loading.js";
import { requireTickets } from "./ticket-control.js";
import { markTourSeen, tourSeen } from "./tour.js";
import { webviewScript, webviewStyle } from "./webview-asset.js";

const DEBOUNCE_MS = 120;
/** 画面の名前。束ねの綴りは `src/webview/<名前>/main.tsx` → `out/webview/<名前>.js` */
const SCREEN = "flow";
/** 自分の保存で監視が鳴るのを、この間だけ「外で変わった」と言わない */
const OWN_WRITE_GRACE_MS = 1500;
/** 取り込みの候補の置き場（cc-wf-studio が保存する場所） */
const WORKFLOWS_GLOB = ".vscode/workflows/*.json";

interface Loaded {
  readonly target: FlowTarget;
  readonly exists: boolean;
  /** 無いときは 0 */
  readonly mtimeMs: number;
  readonly doc: FlowDoc;
  /** 画面に見せる綴り（ワークスペースルートからの相対。外なら絶対） */
  readonly shown: string;
}

interface PanelState {
  readonly ticket: string;
  readonly panel: vscode.WebviewPanel;
  readonly folder: vscode.WorkspaceFolder;
  readonly host: ScreenHost<FlowData>;
  fileWatchers: vscode.FileSystemWatcher[];
  watchers: vscode.FileSystemWatcher[];
  timer?: NodeJS.Timeout;
  lockTimer?: NodeJS.Timeout;
  loaded?: Loaded;
  error?: string;
  lock: FlowLock;
  changedPending: boolean;
  wroteAt: number;
  dirty: boolean;
  seq: number;
}

/** 開いているパネル。子の識別子ごとに 1 枚 */
const panels = new Map<string, PanelState>();

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

function titleOf(ticket: string): string {
  return `ccnavi フロー: ${ticket}`;
}

/** ボードのカードの「フロー」の本体 */
export async function openFlow(ticket: string): Promise<void> {
  if (!requireTickets("フロー編集画面")) {
    return;
  }
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    vscode.window.showInformationMessage("ワークスペースが開かれていないため、フロー編集画面を表示できない");
    return;
  }
  const open = panels.get(ticket);
  if (open !== undefined) {
    open.panel.reveal(open.panel.viewColumn);
    return;
  }
  try {
    webviewScript(SCREEN);
    webviewStyle(SCREEN);
  } catch (error) {
    vscode.window.showErrorMessage(`フロー編集画面を表示できない: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }
  const panel = vscode.window.createWebviewPanel("ccnaviFlow", titleOf(ticket), vscode.ViewColumn.One, {
    enableScripts: true,
    enableForms: false,
    localResourceRoots: [],
    // 編集の途中を持つので、タブを裏に回しても捨てない
    retainContextWhenHidden: true,
  });
  showLoading(panel, titleOf(ticket), SCREEN, `${ticket} のフロー`);
  const current: PanelState = {
    ticket,
    panel,
    folder,
    host: flowHost(panel),
    fileWatchers: [],
    watchers: [],
    lock: lockFromFailure("確認中…"),
    changedPending: false,
    wroteAt: 0,
    dirty: false,
    seq: 0,
  };
  panels.set(ticket, current);
  followAppearance(panel, current.host);
  registerPanelHandlers(current);
  await reload(current);
}

function alive(current: PanelState): boolean {
  return panels.get(current.ticket) === current;
}

function shownPath(root: string, filePath: string): string {
  const rel = path.relative(root, filePath);
  if (rel === "" || rel.startsWith("..") || path.isAbsolute(rel)) {
    return filePath;
  }
  return rel.split(path.sep).join("/");
}

/** 実行ファイルに聞いて、この子のフローの置き場と錠を引く */
async function lookUp(root: string, ticket: string): Promise<FlowTarget> {
  const board = await loadBoard(root, binSetting());
  if (!board.ok) {
    throw new Error(`フローの置き場を実行ファイルから取得できない: ${board.error}`);
  }
  const found = flowTargetOf(board.board, ticket);
  if (!found.ok) {
    throw new Error(found.error);
  }
  return found.target;
}

async function readPage(root: string, ticket: string): Promise<Loaded> {
  const target = await lookUp(root, ticket);
  const filePath = target.flow.path;
  const shown = shownPath(root, filePath);
  let read: { readonly text: string; readonly mtimeMs: number } | undefined;
  try {
    // リンクは辿らない（ツリーのルートからファイルまでの途中も）。読まないし書かない
    read = readFlowFile(target.flow.tree, filePath);
  } catch (error) {
    throw new Error(`フローのファイルを読めない（${shown}）: ${(error as Error).message}`);
  }
  if (read === undefined) {
    // 無いのは不備ではない（フローは任意）。雛形を見せ、保存でファイルを作る
    return { target, exists: false, mtimeMs: 0, doc: templateFlow(ticket, target.title), shown };
  }
  const { text, mtimeMs } = read;
  const parsed = parseFlow(text);
  if (!parsed.ok) {
    // 画面で直すと、読めなかった部分を落として書くことになる。エディタで直させる
    throw new Error(`フローのファイルを読めない（${shown}）: ${parsed.error}。エディタで直してから再読込する`);
  }
  return { target, exists: true, mtimeMs, doc: parsed.doc, shown };
}

function registerPanelHandlers(current: PanelState): void {
  const { panel, folder } = current;
  panel.webview.onDidReceiveMessage((message: unknown) => {
    void handleMessage(current, asFlowMessage(message));
  });
  // フェーズ管理と同じ。表に戻ったら、いま出すべき知らせと見た目を送り直す（中身は送らない）
  panel.onDidChangeViewState(() => {
    if (!panel.visible || !alive(current)) {
      return;
    }
    current.host.post({ type: "lock", lock: current.lock } satisfies ToFlow);
    postAppearance(current.host);
    if (current.changedPending) {
      current.host.post({ type: "changed" } satisfies ToFlow);
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
    if (alive(current)) {
      panels.delete(current.ticket);
    }
  });
  // チケットが動いたら（着手・終わり・取り消し）、錠を取り直す
  for (const pattern of WATCH_PATTERNS) {
    const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(folder, pattern));
    const moved = () => scheduleLock(current);
    watcher.onDidCreate(moved);
    watcher.onDidChange(moved);
    watcher.onDidDelete(moved);
    current.watchers.push(watcher);
  }
}

/** フローのファイルが外で変わったら「外で変わった」を出す。置き場は読み直すたびに変わりうるので張り直す */
function watchFile(current: PanelState): void {
  for (const watcher of current.fileWatchers) {
    watcher.dispose();
  }
  current.fileWatchers = [];
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  const filePath = loaded.target.flow.path;
  const watcher = vscode.workspace.createFileSystemWatcher(
    new vscode.RelativePattern(vscode.Uri.file(path.dirname(filePath)), path.basename(filePath)),
  );
  const changed = () => scheduleChanged(current);
  watcher.onDidCreate(changed);
  watcher.onDidChange(changed);
  watcher.onDidDelete(changed);
  current.fileWatchers.push(watcher);
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
      current.changedPending = true;
      current.host.post({ type: "changed" } satisfies ToFlow);
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
 * 錠を実行ファイルに聞き直す。確かめられなければ閉じる側。置き場も一緒に返す（保存が見比べる）。
 */
async function refreshLock(current: PanelState): Promise<{ readonly lock: FlowLock; readonly target?: FlowTarget }> {
  let lock: FlowLock;
  let target: FlowTarget | undefined;
  try {
    target = await lookUp(current.folder.uri.fsPath, current.ticket);
    lock = target.lock;
  } catch (error) {
    lock = lockFromFailure((error as Error).message);
  }
  if (alive(current)) {
    current.lock = lock;
    current.host.post({ type: "lock", lock } satisfies ToFlow);
  }
  return { lock, target };
}

/** いま見せるものを渡す。**画面の編集はここで捨てられる**（読み直し・保存が通ったときだけ呼ぶ） */
function show(current: PanelState): void {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  current.error = undefined;
  current.changedPending = false;
  const { target } = loaded;
  current.host.send({
    kind: "page",
    page: {
      root: current.folder.uri.fsPath,
      ticket: target.ticket,
      title: target.title,
      parent: target.parent,
      flowPath: loaded.shown,
      flowRel: target.flow.rel,
      exists: loaded.exists,
      doc: loaded.doc,
      lock: current.lock,
    },
  });
}

function showError(current: PanelState, error: string): void {
  current.loaded = undefined;
  current.error = error;
  current.host.send({ kind: "error", error });
}

function redraw(current: PanelState): void {
  if (current.loaded !== undefined) {
    show(current);
    return;
  }
  if (current.error !== undefined) {
    showError(current, current.error);
    return;
  }
  current.host.send({ kind: "loading", text: loadingText(`${current.ticket} のフロー`) });
}

function flowHost(panel: vscode.WebviewPanel): ScreenHost<FlowData> {
  if (panel.options.retainContextWhenHidden !== true) {
    console.error("フロー編集の画面は retainContextWhenHidden が真であることを前提にしている（retainedHost）");
  }
  return retainedHost<FlowData>(
    {
      html(text: string): void {
        panel.webview.html = text;
      },
      post(message: unknown): void {
        void panel.webview.postMessage(message);
      },
    },
    (data) =>
      renderFlowPage(data, {
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
    loaded = await readPage(current.folder.uri.fsPath, current.ticket);
  } catch (error) {
    if (alive(current) && current.seq === seq) {
      current.lock = lockFromFailure((error as Error).message);
      showError(current, (error as Error).message);
    }
    return;
  }
  if (!alive(current) || current.seq !== seq) {
    return;
  }
  current.loaded = loaded;
  current.lock = loaded.target.lock;
  show(current);
  watchFile(current);
}

function fail(current: PanelState, message: string): void {
  current.host.post({ type: "failed", message } satisfies ToFlow);
}

/** 往復の間に読み直されていたら、この操作は捨てる（捨てた編集をファイルに入れない） */
function stale(current: PanelState, loaded: Loaded): boolean {
  if (current.loaded === loaded) {
    return false;
  }
  fail(current, "読み直したので、この保存は捨てた。いまのフローで編集し直す");
  return true;
}

async function handleMessage(current: PanelState, message: FlowMessage | undefined): Promise<void> {
  if (message === undefined || !alive(current)) {
    return;
  }
  switch (message.type) {
    case "dirty":
      current.dirty = message.dirty;
      return;
    case "ready":
      current.dirty = false;
      current.host.ready();
      redraw(current);
      postAppearance(current.host);
      if (!tourSeen(SCREEN)) {
        current.host.post({ type: "tour" } satisfies ToFlow);
      }
      return;
    case "tourDone":
      markTourSeen(SCREEN);
      return;
    case "reload": {
      if (message.dirty) {
        const choice = await vscode.window.showWarningMessage("未保存の変更がある。破棄して読み直す？", { modal: true }, "読み直す");
        if (choice !== "読み直す") {
          current.host.post({ type: "cancelled" } satisfies ToFlow);
          return;
        }
      }
      await reload(current);
      return;
    }
    case "openFile": {
      const loaded = current.loaded;
      if (loaded === undefined || !loaded.exists) {
        return;
      }
      const target = loaded.target.flow.path;
      void vscode.workspace.openTextDocument(target).then(
        (document) => vscode.window.showTextDocument(document),
        () => vscode.window.showInformationMessage(`ファイルを開けなかった: ${target}`),
      );
      return;
    }
    case "import":
      await importInto(current, message.dirty);
      return;
    case "save":
      await save(current, message.doc);
      return;
    default: {
      const unhandled: never = message;
      void unhandled;
      return;
    }
  }
}

interface Candidate extends vscode.QuickPickItem {
  readonly uri?: vscode.Uri;
}

/**
 * 取り込む。候補はワークスペースの `.vscode/workflows/*.json`（cc-wf-studio の置き場）で、
 * ほかのファイルも選べる。読めたら画面の編集中のフローを置き換える（未保存になる）。**ここでは書かない。**
 */
async function importInto(current: PanelState, dirty: boolean): Promise<void> {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  if (current.lock.locked) {
    fail(current, current.lock.reason);
    return;
  }
  const found = await vscode.workspace.findFiles(new vscode.RelativePattern(current.folder, WORKFLOWS_GLOB), undefined, 200);
  const items: Candidate[] = found
    .map((uri) => ({ label: path.basename(uri.fsPath), description: shownPath(current.folder.uri.fsPath, uri.fsPath), uri }))
    .sort((a, b) => (a.label < b.label ? -1 : a.label > b.label ? 1 : 0));
  items.push({ label: "ほかのファイルを選ぶ…", description: "JSON のファイルを選ぶ" });
  const picked = await vscode.window.showQuickPick(items, {
    title: `${current.ticket} のフローに取り込む`,
    placeHolder: found.length === 0 ? ".vscode/workflows/ に JSON が無い。ほかのファイルを選ぶ" : "取り込むワークフロー（.vscode/workflows/*.json）",
  });
  if (picked === undefined) {
    current.host.post({ type: "cancelled" } satisfies ToFlow);
    return;
  }
  let uri = picked.uri;
  if (uri === undefined) {
    const chosen = await vscode.window.showOpenDialog({
      canSelectMany: false,
      defaultUri: vscode.Uri.file(path.join(current.folder.uri.fsPath, ".vscode", "workflows")),
      filters: { JSON: ["json"] },
      openLabel: "取り込む",
    });
    uri = chosen?.[0];
    if (uri === undefined) {
      current.host.post({ type: "cancelled" } satisfies ToFlow);
      return;
    }
  }
  if (!alive(current) || current.loaded !== loaded) {
    return;
  }
  if (dirty) {
    const choice = await vscode.window.showWarningMessage("未保存の変更がある。破棄して取り込む？", { modal: true }, "取り込む");
    if (choice !== "取り込む") {
      current.host.post({ type: "cancelled" } satisfies ToFlow);
      return;
    }
  }
  let text: string;
  try {
    text = fs.readFileSync(uri.fsPath, "utf8");
  } catch (error) {
    fail(current, `取り込むファイルを読めない: ${(error as Error).message}`);
    return;
  }
  const read = importFlow(text);
  if (!read.ok) {
    fail(current, `取り込めない（${shownPath(current.folder.uri.fsPath, uri.fsPath)}）: ${read.error}`);
    return;
  }
  if (!alive(current) || current.loaded !== loaded) {
    return;
  }
  current.host.post({ type: "imported", doc: read.doc, source: shownPath(current.folder.uri.fsPath, uri.fsPath) } satisfies ToFlow);
}

async function save(current: PanelState, doc: FlowDoc): Promise<void> {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  // 1. 押した時点で錠を聞き直す。着手中なら書かない（実行ファイルの答えのまま）
  const { lock, target } = await refreshLock(current);
  if (!alive(current) || stale(current, loaded)) {
    return;
  }
  if (lock.locked || target === undefined) {
    fail(current, lock.reason);
    return;
  }
  // 2. 置き場が同じ。往復の間にチケットが動くと、読む先（権威のツリー）が替わることがある
  const filePath = loaded.target.flow.path;
  if (target.flow.path !== filePath) {
    fail(current, `フローの置き場が変わった（${loaded.shown} → ${shownPath(current.folder.uri.fsPath, target.flow.path)}）。再読込してから編集し直す`);
    return;
  }
  // 3. 読み込んでから外で変わっていない（無かったファイルは、まだ無い）。4. リンクを辿らない。
  // 5. 一時ファイルに書いて入れ替える（途中で落ちても半端なファイルを残さない）。3〜5 は flow-write.ts
  current.wroteAt = Date.now();
  const written = writeFlowFile(target.flow.tree, filePath, serializeFlow(doc), { exists: loaded.exists, mtimeMs: loaded.mtimeMs });
  if (!written.ok) {
    current.wroteAt = 0;
    fail(current, written.error);
    return;
  }
  await reload(current);
  vscode.window.showInformationMessage(
    `${loaded.shown} に保存した。承認済みチケットと同じく人がコミットする（sh .ccnavi/scripts/ccnavi-push-approved.sh）`,
  );
}
