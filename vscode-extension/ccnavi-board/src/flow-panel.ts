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
 * 着手中かを `started_at` から組み直さない（ADR-0035）。フローが正しいか（読めるか・形）も実行ファイルに聞く。
 * 開くときは読んだ本文を、保存の前は書き出す本文を一時ファイルに書いて `--lint --json --flow` に掛け
 * （`core/flow-lint.ts`）、error があれば理由を出して開かない・保存しない。開くときは、画面の読みと実行ファイルが
 * 読んだ中身を見比べ、食い違えば場所と両者の値を出して開かない（`core/flow-match.ts`）。保存は次を全部満たすときだけ書く。
 *
 * 1. 実行ファイルの `--lint --flow` が書き出す本文に error を言わず、その本文を読んだ中身（`flow.data`）が
 *    画面が書こうとした中身と同じ（`core/flow-match.ts`。PyYAML で意味が変わる本文を書かない）
 * 2. 押した時点で実行ファイルに聞き直し、`locked` が偽（着手中でない）
 * 3. 置き場が読んだときと同じ（往復の間にチケットが動いて置き場が替わっていない）
 * 4. 読み込んでから外で変わっていない（無かったファイルは、まだ無い）
 * 5. ツリーのルートからファイルまでの途中にシンボリックリンクが無い
 *
 * 書き込みは一時ファイルの入れ替えで、リンクを辿らない（`core/flow-write.ts`）。置き場は承認済みの領域
 * （既定 `.ccnavi/approved/flows/<子>.yml`）で、エージェントは判定に止められて書けない。
 *
 * 残る隙間（TOCTOU）: 1 で聞き直してから書くまでの間に子が着手されると、着手の直後に書き込みが入りうる。
 * 着手はユーザか親のエージェントが `ccnavi-ticket.sh start` を打つ操作で、聞き直しから書き込みまでは同じ保存の
 * 1 回の中（実行ファイルを 1 度起こすぶん）。防ぐには実行ファイルの側に錠の置き場が要るので、ここでは狭めるだけにする。
 *
 * **未保存のまま閉じたとき。** VS Code の Webview パネルには、閉じるのを止める手段（保存・破棄・取り消しを聞いてから
 * 閉じる）が無い（`onDidDispose` は閉じた後に呼ばれる）。代わりに、未保存の間はタブの題の頭に「●」を付け、
 * 画面が送ってくる編集中の写し（`draft`）を控えておく。閉じた後に未保存だったら、「開き直して戻す」
 * 「YAML で開く」「破棄する」を聞く。開き直すときは、閉じた時点から置き場・有無・更新時刻・中身の指紋が
 * 変わっていなければ写しを未保存のまま戻し、変わっていれば戻さずに写しを名前の無い YAML のエディタで開く
 * （上書きしない）。同じ子の画面が既に開いていれば、その編集は差し替えず、戻せなかったと言って YAML で開く。
 *
 * **エージェントの下書き（ADR-0100）。** 置き場は実行ファイルに聞く（`tickets[].flow.draft`）。下書きが在り、中身が
 * いまのフローと違えば（`sameFlow` が偽）「提案あり」を出す。開くと下書きを読んだバイトのまま `--lint --json --flow` に
 * 掛け、error なら取り込めないと言う。通れば画面が文の前後まで見せる差分を出し、「取り込む」で編集中の内容に入れる
 * （書かない。保存はいつもの経路）。保存が成功したら、下書きの中身が取り込んだときの指紋と同じときだけ消し
 * （`core/flow-write.ts` の `removeDraftFile`）、保存のあとの知らせに名前を出す。依頼のボタン（着手の前だけ）は
 * 依頼の文を組み、ボードと同じ「コピー / 新しいセッションで開く」（`prompt-handover.ts`）で渡す。
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import { followAppearance, postAppearance, readAppearance } from "./appearance.js";
import { loadBoard, runC1Target, runFlowLint } from "./ccnavi.js";
import { PUSH_APPROVED_SCRIPT, pushApprovedCommand, scriptCommand, shellQuote, toPosixPath } from "./core/commands.js";
import { sameFlow } from "./core/flow-diff.js";
import { flowMismatch, openMismatchText, saveMismatchText } from "./core/flow-match.js";
import { asFlowDoc, parseFlowValue, serializeFlow, templateFlow, type FlowDoc } from "./core/flow-doc.js";
import { lintFlowText } from "./core/flow-lint.js";
import { renderFlowPage } from "./core/flow-render.js";
import { decodeFlowBytes, readFlowFile, removeDraftFile, writeFlowFile } from "./core/flow-write.js";
import {
  asFlowMessage,
  flowRequestPrompt,
  flowTargetOf,
  lockFromFailure,
  requestLabel,
  type FlowChecks,
  type FlowData,
  type FlowLock,
  type FlowMessage,
  type FlowOffer,
  type FlowTarget,
  type ToFlow,
} from "./core/flow-view.js";
import { loadingText } from "./core/loading-render.js";
import { retainedHost, type ScreenHost } from "./core/screen-host.js";
import { WATCH_PATTERNS } from "./core/watch.js";
import { showLoading } from "./loading.js";
import * as diaglog from "./log.js";
import { copyPrompt, openPromptInSession } from "./prompt-handover.js";
import { runInTerminal } from "./terminal.js";
import { requireTickets } from "./ticket-control.js";
import { markTourSeen, tourSeen } from "./tour.js";
import { webviewScript, webviewStyle } from "./webview-asset.js";

const DEBOUNCE_MS = 120;
/** 画面の名前。束ねの綴りは `src/webview/<名前>/main.tsx` → `out/webview/<名前>.js` */
const SCREEN = "flow";
/** 自分の保存で監視が反応するのを、この間だけ「外で変わった」と言わない */
const OWN_WRITE_GRACE_MS = 1500;

interface Loaded {
  readonly target: FlowTarget;
  readonly exists: boolean;
  /** 無いときは 0 */
  readonly mtimeMs: number;
  /** 読んだバイトの指紋（sha256 の 16 進）。無いときは空 */
  readonly hash: string;
  readonly doc: FlowDoc;
  /** 画面に見せる綴り（ワークスペースルートからの相対。外なら絶対） */
  readonly shown: string;
  /** 開くときに実行ファイルが言ったこと（warn・渡る手順・候補）。ファイルが無ければ無い */
  readonly checks?: FlowChecks;
  /** 「提案あり」。下書きが無いか、いまのフローと同じなら無い */
  offer?: FlowOffer;
}

/** 未保存のまま閉じた画面から戻す写し。読み込んだときのファイルの様子が今と同じときだけ戻す */
interface Restore {
  readonly draft: FlowDoc;
  /** 閉じたときのフローのファイルの置き場 */
  readonly path: string;
  readonly exists: boolean;
  readonly mtimeMs: number;
  /** 閉じたときに読んであったバイトの指紋。置き場・有無・時刻・指紋が今と全部同じときだけ戻す */
  readonly hash: string;
}

/** 読んだバイトの指紋 */
function hashOf(bytes: Uint8Array): string {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

interface PanelState {
  readonly ticket: string;
  readonly panel: vscode.WebviewPanel;
  readonly folder: vscode.WorkspaceFolder;
  readonly host: ScreenHost<FlowData>;
  /** 編集中の本文を `--lint --flow` に渡す一時ファイルの置き場。画面を閉じたら消す */
  readonly tmpDir: string;
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
  /** 画面が控えさせた、未保存の写し（未保存でなければ無い） */
  draft?: FlowDoc;
  /** 開き直したときに戻す写し。最初の読み込みで確かめて `shownDraft` に移す */
  restore?: Restore;
  /** 画面に渡している、戻した写し（次に読み直すまで） */
  shownDraft?: FlowDoc;
  seq: number;
  /** 画面に渡した下書きの指紋。保存のときに届いた `imported` がこの中にあるときだけ、下書きを消しに行く */
  offered: Set<string>;
  /** 画面に見せている依頼の文（コピー / 新しいセッションで開く はこれを渡す） */
  requestPrompt?: string;
  offerTimer?: NodeJS.Timeout;
}

/** 開いているパネル。子の識別子ごとに 1 枚 */
const panels = new Map<string, PanelState>();

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

function titleOf(ticket: string, dirty = false): string {
  return `${dirty ? "● " : ""}ccnavi フロー: ${ticket}`;
}

/** 保存の前に差分を確かめるか（設定。既定は確かめる） */
function reviewSetting(): boolean {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<boolean>("flowSaveReview", true);
}

/**
 * 保存前の確かめの設定を書く。いま有効な範囲に書く（フォルダの設定があればそこ、次にワークスペースの設定、
 * どちらも無ければユーザの設定）。上の範囲に値があると、下に書いても反映されないため
 */
async function updateReviewSetting(folder: vscode.WorkspaceFolder, value: boolean): Promise<void> {
  const config = vscode.workspace.getConfiguration("ccnaviBoard", folder.uri);
  const seen = config.inspect<boolean>("flowSaveReview");
  const target =
    seen?.workspaceFolderValue !== undefined
      ? vscode.ConfigurationTarget.WorkspaceFolder
      : seen?.workspaceValue !== undefined
        ? vscode.ConfigurationTarget.Workspace
        : vscode.ConfigurationTarget.Global;
  await config.update("flowSaveReview", value, target);
}

/** ボードのカードの「フロー」の本体 */
export async function openFlow(ticket: string): Promise<void> {
  await openFlowPanel(ticket, undefined);
}

async function openFlowPanel(ticket: string, restore: Restore | undefined): Promise<void> {
  if (!requireTickets("フロー編集画面")) {
    return;
  }
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    vscode.window.showInformationMessage("ワークスペースが開かれていないため、フロー編集画面を表示できません");
    return;
  }
  const open = panels.get(ticket);
  if (open !== undefined) {
    open.panel.reveal(open.panel.viewColumn);
    if (restore !== undefined) {
      // 既に開いている画面の編集を何も言わずに差し替えない。戻せなかったと言い、写しは YAML で見せる
      void vscode.window.showWarningMessage(`${ticket} のフローは既に開いているため、閉じる前の編集を戻せませんでした。閉じる前の編集は、無題の YAML ファイルとして開きます。`);
      void openDraftAsYaml(restore.draft);
    }
    return;
  }
  try {
    webviewScript(SCREEN);
    webviewStyle(SCREEN);
  } catch (error) {
    vscode.window.showErrorMessage(`フロー編集画面を表示できません: ${error instanceof Error ? error.message : String(error)}`);
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
    host: flowHost(panel, folder.uri.fsPath),
    tmpDir: fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-flow-")),
    fileWatchers: [],
    watchers: [],
    lock: lockFromFailure("確認中…"),
    changedPending: false,
    wroteAt: 0,
    dirty: false,
    restore,
    seq: 0,
    offered: new Set(),
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
    throw new Error(`フローの置き場を実行ファイルから取得できません: ${board.error}`);
  }
  const found = flowTargetOf(board.board, ticket);
  if (!found.ok) {
    throw new Error(found.error);
  }
  return found.target;
}

/** 本文を実行ファイルに確かめさせる。error があれば理由を返す（`core/flow-lint.ts`） */
function lintText(root: string, tmpDir: string, text: string | Uint8Array, shown: string) {
  return lintFlowText(text, tmpDir, shown, (file) => runFlowLint(root, binSetting(), file));
}

async function readPage(root: string, ticket: string, tmpDir: string): Promise<Loaded> {
  const target = await lookUp(root, ticket);
  const filePath = target.flow.path;
  const shown = shownPath(root, filePath);
  let read: { readonly bytes: Uint8Array; readonly mtimeMs: number } | undefined;
  try {
    // リンクは辿らない（ツリーのルートからファイルまでの途中も）。読まないし書かない
    read = readFlowFile(target.flow.tree, filePath);
  } catch (error) {
    throw new Error(`フローのファイルを読めません（${shown}）: ${(error as Error).message}`);
  }
  if (read === undefined) {
    // 無いのは不備ではない（フローは任意）。雛形を見せ、保存でファイルを作る
    const doc = templateFlow(ticket, target.title);
    return { target, exists: false, mtimeMs: 0, hash: "", doc, shown, offer: readOffer(root, target, doc) };
  }
  const { bytes, mtimeMs } = read;
  const refuse = (why: string): Error => new Error(`フローのファイルを開きません（${shown}）: ${why}。エディタで直してから再読込してください`);
  // 正しいかは実行ファイルに聞く（SubagentStart と同じ読み）。読んだバイトのまま渡す（UTF-8 として不正かどうかも
  // 実行ファイルが言う）。読めないフローを画面で直すと、読めなかった部分を落として書くことになる。エディタで直させる
  const verdict = await lintText(root, tmpDir, bytes, shown);
  if (!verdict.ok) {
    throw refuse(verdict.error);
  }
  const decoded = decodeFlowBytes(bytes);
  if (!decoded.ok) {
    throw refuse(decoded.error);
  }
  const value = parseFlowValue(decoded.text);
  if (!value.ok) {
    throw refuse(value.error);
  }
  // 画面の読み（YAML 1.2）が実行ファイルの読み（PyYAML）と同じときだけ開く。違えば、画面で保存しただけで
  // 値の意味が変わる（`0755` `yes` `1:30` マージキー など）。意味の答えは実行ファイルが持つ（core/flow-match.ts）
  const mismatch = flowMismatch(value.value, verdict.data);
  if (mismatch !== undefined) {
    throw new Error(`フローのファイルを開きません（${shown}）: ${openMismatchText(mismatch)}。直したら再読込してください`);
  }
  const doc = asFlowDoc(value.value);
  if (doc === undefined) {
    throw refuse("ノードの並び（id が文字列のノード）を取り出せないため、図を描けません");
  }
  return { target, exists: true, mtimeMs, hash: hashOf(bytes), doc, shown, checks: verdict.checks, offer: readOffer(root, target, doc) };
}

/**
 * 「提案あり」を出すか。下書きが在り、中身がいまのフロー（`current`）と違えば出す。読めない（リンク・ハードリンク・
 * 大きすぎる・YAML として読めない）ときも出し、開いたときに理由を言う。ここでは実行ファイルに掛けない（開いたときに掛ける）
 */
function readOffer(root: string, target: FlowTarget, current: FlowDoc): FlowOffer | undefined {
  const draft = target.flow.draft;
  if (draft === null) {
    return undefined;
  }
  const draftPath = shownPath(root, draft.path);
  let read: { readonly bytes: Uint8Array } | undefined;
  try {
    read = readFlowFile(target.flow.tree, draft.path);
  } catch (error) {
    return { draftPath, problem: (error as Error).message };
  }
  if (read === undefined) {
    return undefined;
  }
  const decoded = decodeFlowBytes(read.bytes);
  const value = decoded.ok ? parseFlowValue(decoded.text) : undefined;
  const doc = value?.ok === true ? asFlowDoc(value.value) : undefined;
  if (doc !== undefined && sameFlow(doc, current)) {
    return undefined;
  }
  return { draftPath };
}

/** 下書きが在るか（リンクでも在るとする）。依頼のボタンの言葉に使う */
function draftExists(target: FlowTarget): boolean {
  const draft = target.flow.draft;
  if (draft === null) {
    return false;
  }
  try {
    fs.lstatSync(draft.path);
    return true;
  } catch {
    return false;
  }
}

/** 依頼のボタンの言葉。下書きの置き場を答えない古い実行ファイルなら出さない */
function requestOf(target: FlowTarget, lock: FlowLock, flowExists: boolean): string | undefined {
  if (target.flow.draft === null) {
    return undefined;
  }
  return requestLabel({ beforeStart: target.beforeStart, locked: lock.locked, flowExists, draftExists: draftExists(target) });
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
    askRestore(current);
    for (const timer of [current.timer, current.lockTimer, current.offerTimer]) {
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
      // 消せなければ OS の一時ディレクトリに残る（承認済みの領域には書いていない）
    }
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

/**
 * 未保存のまま閉じた。閉じるのは止められないので、閉じた後に戻すかを聞く（頭のコメント）。
 * 控えた写しが無い（打ってすぐ閉じた）か、読み込めていなければ何も聞かない
 */
function askRestore(current: PanelState): void {
  const loaded = current.loaded;
  const draft = current.draft;
  if (!current.dirty || draft === undefined || loaded === undefined) {
    return;
  }
  const restore: Restore = { draft, path: loaded.target.flow.path, exists: loaded.exists, mtimeMs: loaded.mtimeMs, hash: loaded.hash };
  const back = "開き直して戻す";
  const asYaml = "YAML で開く";
  void vscode.window
    .showWarningMessage(`${current.ticket} のフローを未保存のまま閉じました。編集を戻しますか？`, { modal: true, detail: "「破棄する」を選ぶと、閉じる前の編集は残りません。" }, back, asYaml, "破棄する")
    .then((choice) => {
      if (choice === back) {
        void openFlowPanel(current.ticket, restore);
      } else if (choice === asYaml) {
        void openDraftAsYaml(draft);
      }
    });
}

/** 写しを名前の無い YAML のエディタで開く（ファイルには書かない） */
async function openDraftAsYaml(draft: FlowDoc): Promise<void> {
  const document = await vscode.workspace.openTextDocument({ language: "yaml", content: serializeFlow(draft) });
  await vscode.window.showTextDocument(document);
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
  // 下書きはエージェントが書く。動いたら「提案あり」を出し直す（編集は捨てない）
  const draft = loaded.target.flow.draft;
  if (draft !== null) {
    const drafts = vscode.workspace.createFileSystemWatcher(
      new vscode.RelativePattern(vscode.Uri.file(path.dirname(draft.path)), path.basename(draft.path)),
    );
    const moved = () => scheduleOffer(current);
    drafts.onDidCreate(moved);
    drafts.onDidChange(moved);
    drafts.onDidDelete(moved);
    current.fileWatchers.push(drafts);
  }
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
    // 着手・終わり・取り消しで、依頼のボタンを出すかが変わる
    if (target !== undefined && current.loaded !== undefined) {
      const request = requestOf({ ...current.loaded.target, beforeStart: target.beforeStart }, lock, current.loaded.exists);
      current.host.post({ type: "offer", ...(request === undefined ? {} : { request }), ...(current.loaded.offer === undefined ? {} : { offer: current.loaded.offer }) } satisfies ToFlow);
    }
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
      reviewSave: reviewSetting(),
      ...(loaded.checks === undefined ? {} : { checks: loaded.checks }),
      ...(current.shownDraft === undefined ? {} : { draft: current.shownDraft }),
      ...requestAndOffer(current, loaded),
    },
  });
}

/** 画面に渡す依頼のボタンの言葉と「提案あり」。無いものは欄ごと省く */
function requestAndOffer(current: PanelState, loaded: Loaded): { request?: string; offer?: FlowOffer } {
  const request = requestOf(loaded.target, current.lock, loaded.exists);
  return { ...(request === undefined ? {} : { request }), ...(loaded.offer === undefined ? {} : { offer: loaded.offer }) };
}

/** 下書きか錠が動いた。読み込んだフローと比べ直して、依頼のボタンと「提案あり」を出し直す（編集は捨てない） */
function postOffer(current: PanelState): void {
  const loaded = current.loaded;
  if (loaded === undefined || !alive(current)) {
    return;
  }
  loaded.offer = readOffer(current.folder.uri.fsPath, loaded.target, loaded.doc);
  current.host.post({ type: "offer", ...requestAndOffer(current, loaded) } satisfies ToFlow);
}

function scheduleOffer(current: PanelState): void {
  if (current.offerTimer !== undefined) {
    clearTimeout(current.offerTimer);
  }
  current.offerTimer = setTimeout(() => {
    current.offerTimer = undefined;
    postOffer(current);
  }, DEBOUNCE_MS);
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

function flowHost(panel: vscode.WebviewPanel, root: string): ScreenHost<FlowData> {
  if (panel.options.retainContextWhenHidden !== true) {
    // retainedHost は retainContextWhenHidden が真であることを前提にしている。偽のままだと、裏に回った画面へ
    // 送り続けて中身が古いまま止まる。診断ログにだけ残す（console には出さない。docs/claude/logging.md）
    diaglog.get("ccnavi-board", root).error("画面の前提が崩れている", { screen: "flow", retainContextWhenHidden: false });
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
  const restore = current.restore;
  current.restore = undefined;
  current.shownDraft = undefined;
  let loaded: Loaded;
  try {
    loaded = await readPage(current.folder.uri.fsPath, current.ticket, current.tmpDir);
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
  if (restore !== undefined) {
    const same =
      restore.path === loaded.target.flow.path && restore.exists === loaded.exists && restore.mtimeMs === loaded.mtimeMs && restore.hash === loaded.hash;
    if (same) {
      current.shownDraft = restore.draft;
    } else {
      // 閉じた後にファイルが変わった。写しを戻すと、変わった中身を気づかないうちに上書きしうる。写しは YAML で見せる
      void vscode.window.showWarningMessage(`${current.ticket} のフローは閉じた後に変わったので、編集を戻しませんでした。閉じる前の編集は、無題の YAML ファイルとして開きます。`);
      void openDraftAsYaml(restore.draft);
    }
  }
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
  fail(current, "フローを読み直したため、この保存は取りやめました。読み直したフローで編集し直してください");
  return true;
}

async function handleMessage(current: PanelState, message: FlowMessage | undefined): Promise<void> {
  if (message === undefined || !alive(current)) {
    return;
  }
  switch (message.type) {
    case "dirty":
      current.dirty = message.dirty;
      current.panel.title = titleOf(current.ticket, message.dirty);
      if (!message.dirty) {
        current.draft = undefined;
      }
      return;
    case "draft":
      current.draft = message.doc ?? undefined;
      return;
    case "reviewSave":
      void updateReviewSetting(current.folder, message.value);
      return;
    case "ready":
      current.dirty = false;
      current.draft = undefined;
      current.panel.title = titleOf(current.ticket);
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
        const choice = await vscode.window.showWarningMessage("未保存の変更があります。破棄して読み直しますか？", { modal: true }, "読み直す");
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
        () => vscode.window.showInformationMessage(`ファイルを開けませんでした: ${target}`),
      );
      return;
    }
    case "save":
      await save(current, message.doc, message.imported);
      return;
    case "openProposal":
      await openProposal(current);
      return;
    case "request":
      request(current);
      return;
    case "requestCopy":
      if (current.requestPrompt !== undefined) {
        await copyPrompt(current.requestPrompt, "フローの依頼の文");
      }
      return;
    case "requestOpen":
      if (current.requestPrompt !== undefined) {
        await openPromptInSession(current.requestPrompt);
      }
      return;
    case "check":
      await check(current, message.seq, message.doc);
      return;
    default: {
      const unhandled: never = message;
      void unhandled;
      return;
    }
  }
}

/**
 * 編集中の写しを実行ファイルに確かめさせ、言ったこと（warn・渡る手順・候補）を画面に返す。書きはしない。
 * 読み直しの後に届いた答えは画面が番号（`seq`）で捨てる
 */
async function check(current: PanelState, seq: number, doc: FlowDoc): Promise<void> {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  const verdict = await lintText(current.folder.uri.fsPath, current.tmpDir, serializeFlow(doc), loaded.shown);
  if (!alive(current)) {
    return;
  }
  current.host.post((verdict.ok ? { type: "checked", seq, checks: verdict.checks } : { type: "checked", seq, error: verdict.error }) satisfies ToFlow);
}

/**
 * 「提案あり」を開く。下書きを読んだバイトのまま実行ファイルに確かめさせ（`--lint --json --flow`。SubagentStart と同じ読み）、
 * error なら取り込めないと言う。通れば画面の読みと実行ファイルの読みを見比べ（開くときと同じ）、中身と指紋を返す
 */
async function openProposal(current: PanelState): Promise<void> {
  const loaded = current.loaded;
  const draft = loaded?.target.flow.draft ?? null;
  if (loaded === undefined || draft === null) {
    return;
  }
  const root = current.folder.uri.fsPath;
  const shown = shownPath(root, draft.path);
  const answer = (message: ToFlow): void => {
    if (alive(current) && current.loaded === loaded) {
      current.host.post(message);
    }
  };
  const refuse = (why: string): void => answer({ type: "proposal", error: `下書きを取り込めません（${shown}）: ${why}` });
  let read: { readonly bytes: Uint8Array } | undefined;
  try {
    read = readFlowFile(loaded.target.flow.tree, draft.path);
  } catch (error) {
    refuse((error as Error).message);
    return;
  }
  if (read === undefined) {
    refuse("下書きはもうありません");
    postOffer(current);
    return;
  }
  const bytes = read.bytes;
  const verdict = await lintText(root, current.tmpDir, bytes, shown);
  if (!verdict.ok) {
    refuse(verdict.error);
    return;
  }
  const decoded = decodeFlowBytes(bytes);
  if (!decoded.ok) {
    refuse(decoded.error);
    return;
  }
  const value = parseFlowValue(decoded.text);
  if (!value.ok) {
    refuse(value.error);
    return;
  }
  const mismatch = flowMismatch(value.value, verdict.data);
  if (mismatch !== undefined) {
    refuse(openMismatchText(mismatch));
    return;
  }
  const doc = asFlowDoc(value.value);
  if (doc === undefined) {
    refuse("ノードの並び（id が文字列のノード）を取り出せないため、図を描けません");
    return;
  }
  const hash = hashOf(bytes);
  current.offered.add(hash);
  answer({ type: "proposal", proposal: { doc, hash, draftPath: shown } });
}

/** 綴りがそのままシェルに渡せなければ引用する */
function shellWord(text: string): string {
  return /^[^\s'"\\$`!*?\[\]{}()<>|&;#~]+$/.test(text) ? text : shellQuote(text);
}

/** 依頼の文を組んで画面に渡す。出すかは画面のボタンと同じ条件で、ここでも確かめる */
function request(current: PanelState): void {
  const loaded = current.loaded;
  const draft = loaded?.target.flow.draft ?? null;
  if (loaded === undefined || draft === null || requestOf(loaded.target, current.lock, loaded.exists) === undefined) {
    fail(current, "いまはエージェントにフローを頼めません（着手の前の子だけで頼めます）");
    return;
  }
  const root = current.folder.uri.fsPath;
  const draftPath = toPosixPath(draft.path);
  const prompt = flowRequestPrompt({
    ticket: loaded.target.ticket,
    title: loaded.target.title,
    parent: loaded.target.parent,
    draftPath,
    ...(loaded.exists ? { flowPath: toPosixPath(loaded.target.flow.path) } : {}),
    redo: draftExists(loaded.target),
    lintCommand: `${scriptCommand(root, "ccnavi-launcher.sh")} --lint --flow ${shellWord(draftPath)}`,
  });
  current.requestPrompt = prompt;
  current.host.post({ type: "requestText", prompt } satisfies ToFlow);
}

/**
 * 取り込んだ下書きを消す（保存が成功したあとだけ呼ぶ）。消した・消さなかったことを、保存のあとの知らせに足す文で返す。
 * 取り込んでいなければ空
 */
function removeImported(current: PanelState, target: FlowTarget, imported: string | undefined): string {
  const draft = target.flow.draft;
  if (imported === undefined || draft === null || !current.offered.has(imported)) {
    return "";
  }
  const shown = shownPath(current.folder.uri.fsPath, draft.path);
  const removed = removeDraftFile(target.flow.tree, draft.path, imported);
  if (!removed.ok) {
    return `取り込んだ下書き ${shown} は消していません（${removed.error}）。`;
  }
  return removed.removed ? `取り込んだ下書き ${shown} を消しました（削除もコミットに入ります）。` : "";
}

async function save(current: PanelState, doc: FlowDoc, imported?: string): Promise<void> {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  // 1. 書き出す本文が正しいかを実行ファイルに聞く（SubagentStart と同じ読み）。error なら書かない
  const text = serializeFlow(doc);
  const verdict = await lintText(current.folder.uri.fsPath, current.tmpDir, text, loaded.shown);
  if (!alive(current) || stale(current, loaded)) {
    return;
  }
  if (!verdict.ok) {
    fail(current, `保存しません: ${verdict.error}`);
    return;
  }
  // 書き出す本文を実行ファイルが、画面が書こうとした中身と同じに読むときだけ書く（core/flow-match.ts）
  const mismatch = flowMismatch(doc, verdict.data);
  if (mismatch !== undefined) {
    fail(current, `保存しません: ${saveMismatchText(mismatch)}`);
    return;
  }
  // 2. 押した時点で錠を聞き直す。着手中なら書かない（実行ファイルの答えのまま）
  const { lock, target } = await refreshLock(current);
  if (!alive(current) || stale(current, loaded)) {
    return;
  }
  if (lock.locked || target === undefined) {
    fail(current, lock.reason);
    return;
  }
  // 3. 置き場が同じ。往復の間にチケットが動くと、読む先（権威のツリー）が替わることがある
  const filePath = loaded.target.flow.path;
  if (target.flow.path !== filePath) {
    fail(current, `フローの置き場が変わりました（${loaded.shown} → ${shownPath(current.folder.uri.fsPath, target.flow.path)}）。再読込してから編集し直してください`);
    return;
  }
  // 4. 読み込んでから外で変わっていない（無かったファイルは、まだ無い）。5. リンクを辿らない。
  // 6. 一時ファイルに書いて入れ替える（途中で落ちても半端なファイルを残さない）。4〜6 は flow-write.ts
  current.wroteAt = Date.now();
  const written = writeFlowFile(target.flow.tree, filePath, text, { exists: loaded.exists, mtimeMs: loaded.mtimeMs });
  if (!written.ok) {
    current.wroteAt = 0;
    fail(current, written.error);
    return;
  }
  // 取り込んだ下書きは、保存が成功したこの時点で消す（取り込んだときと同じ中身のときだけ）
  const drafted = removeImported(current, target, imported);
  await reload(current);
  // 取り込み済みの家族（C1 の対象）だけ、運ぶ処理を送る（ADR-0093 の 4.6）。ターミナルは対話中のことがあるので、
  // 勝手に打ち込まず、ユーザがボタンを押したときだけ送る（段階 2d のレビューの決定 E）。それ以外の家族は今どおり
  // ユーザがコミットする。
  const root = current.folder.uri.fsPath;
  const carrier =
    target.parent !== "" &&
    isFile(path.join(root, PUSH_APPROVED_SCRIPT)) &&
    (await runC1Target(root, binSetting(), target.parent)) === "yes";
  if (!carrier) {
    vscode.window.showInformationMessage(
      `${loaded.shown} に保存しました。${drafted}コミットは、承認済みチケットと同じくユーザが行います（sh ${PUSH_APPROVED_SCRIPT}）`,
    );
    return;
  }
  const send = "ターミナルで送る";
  const picked = await vscode.window.showInformationMessage(
    `${loaded.shown} に保存しました。${drafted}親 ${target.parent} とその子は取り込み済みのため、コミットと push は、「${send}」を押すと ${PUSH_APPROVED_SCRIPT} ${target.parent} で送れます`,
    send,
  );
  if (picked === send) {
    runInTerminal(root, pushApprovedCommand(root, [target.parent]));
  }
}

/** 普通のファイルが在るか（運ぶ sh が配られているか） */
function isFile(filePath: string): boolean {
  try {
    return fs.statSync(filePath).isFile();
  } catch {
    return false;
  }
}
