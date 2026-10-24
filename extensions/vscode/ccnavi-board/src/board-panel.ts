/**
 * Webview パネルの生成・更新・破棄と、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。docs/design/manual-checks.md の手動確認の手順で確かめる。
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

import { followAppearance, postAppearance, readAppearance } from "./appearance.js";
import { loadBoard, runApprovePreview, runApproveYes, runChildWorktreeAdd, runDecidePreview, runDecideYes, runMrState, runSyncRetired, runTemplateDraft } from "./ccnavi.js";
import { buildBoard, flowCardOf, isKnownPath, parentTreeOf, phaseChipOf, templateDraftCardOf, todoParentsOf, workflowCardOf, type Board } from "./core/board.js";
import { flowTicketOf } from "./core/flow-view.js";
import { screens } from "./core/screens.js";
import { movedStep, NOTHING_MOVED, type MovedState } from "./core/board-moved.js";
import {
  abandonCommand,
  gitScriptCommand,
  PUSH_APPROVED_SCRIPT,
  pushApprovedCommand,
  retireCommand,
  retireConfirmText,
  type Launcher,
} from "./core/commands.js";
import { MERGE_IDLE, mergeStep, mergeView, type MergeEffect, type MergeInput, type MergeState } from "./core/merge.js";
import { repoDirOf } from "./core/mrstate.js";
import {
  approvalStep,
  blockedBeforeApprove,
  CLOSED,
  type ApprovalEffect,
  type ApprovalInput,
  type ApprovalState,
} from "./core/approval-machine.js";
import type { BoardData, BoardMessage, ToBoard } from "./core/board-view.js";
import { renderBoardPage } from "./core/render.js";
import { showLoading } from "./loading.js";
import { copyCommand, copyPrompt, openPromptInSession, runPromptInTerminal } from "./prompt-handover.js";
import { screenHost, type ScreenHost } from "./core/screen-host.js";
import { ticketControlOf } from "./core/ticket-control.js";
import { WATCH_DEBOUNCE_MS, WATCH_PATTERNS } from "./core/watch.js";
import { isTicketId } from "./core/ticket-id.js";
import { forgetSaved, onWorkflowTabs, staleSaved, workflowTabs } from "./core/workflow-tabs.js";
import { childWorktreeFailureText, unsavedText, type ApproveWorktree, type ChildWorktreeFailure } from "./core/approvemodel.js";
import { runInTerminal, scriptShell } from "./terminal.js";
import { markTourSeen, tourDue, tourPanelClosed } from "./tour.js";
import { webviewScript, webviewStyle } from "./webview-asset.js";
import { noteTicketControl, requireTickets } from "./ticket-control.js";
import * as diaglog from "./log.js";

/** 画面の名前。バンドルのパスは `src/webview/<名前>/main.tsx` → `out/webview/<名前>.js`、`style.css` → `<名前>.css` */
const SCREEN = "board";
const TITLE = "ccnavi チケット管理";

interface PanelState {
  readonly panel: vscode.WebviewPanel;
  readonly folder: vscode.WorkspaceFolder;
  watchers: vscode.FileSystemWatcher[];
  /** 画面に中身を渡す段取り（`core/screen-host.ts`）。いつ送れるかはここが持つ */
  readonly host: ScreenHost<BoardData>;
  timer?: NodeJS.Timeout;
  board?: Board;
  /** 読み直せなかったときの文面。描き直しでオーバーレイだけを載せ替えるために覚えておく */
  error?: string;
  launcher?: Launcher;
  /** 読み直しの最中か。最中にもう 1 回頼まれたら、終わってからもう 1 回だけ走らせる */
  loading: boolean;
  again: boolean;
  wasVisible: boolean;
  /** 次に描いたときに選ぶ絞り込み。1 度使ったら消す（以後は Webview の state が覚える） */
  filter?: string;
  /**
   * 承認のオーバーレイ。あれば描くたびにボードの上に被せる（監視の更新で消えない）。
   * 遷移の規則は `core/approval-machine.ts` が持ち、ここは持ち直すだけ
   */
  approval: ApprovalState;
  /**
   * 前の読み直しから動いたカード。画面ではなくここが持つ。画面は非表示になると破棄され、
   * 再表示されると作り直されるので（`retainContextWhenHidden` は偽）、そちらに持たせると
   * 承認の文を渡してボードに戻った時点で動いた表示が消える。決めるのは `core/board-moved.ts`
   */
  moved: MovedState;
  /**
   * マージ状況の確認の結果（`core/merge.ts`）。ボードが開いている間だけここに持つ（タブを隠して画面が作り直されても残り、
   * ボードを閉じて開き直すと消える）。遅れて返った結果は、世代が違えば遷移の側が破棄する
   */
  merge: MergeState;
  /** 計画編集タブの帳面（`core/workflow-tabs.ts`）を見るのをやめる */
  stopTabs?: () => void;
}

let state: PanelState | undefined;

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

/**
 * 承認した子のワークツリーを作る（計画の `create` の子だけ）。作れなかったものは、名前と手で打つ
 * コマンドを付けて知らせる。親のワークツリーが無い子は計画に入らないので、ここでは作らない。
 */
async function createChildWorktrees(root: string, worktrees: readonly ApproveWorktree[]): Promise<void> {
  const failed: ChildWorktreeFailure[] = [];
  for (const w of worktrees.filter((x) => x.state === "create")) {
    const reason = await runChildWorktreeAdd(scriptShell(), root, w);
    diaglog.get("ccnavi-board", root).info("子のワークツリーの作成結果", { ticket: w.ticket, ok: reason === "" });
    if (reason !== "") {
      failed.push({ worktree: w, reason });
    }
  }
  if (failed.length > 0) {
    void vscode.window.showWarningMessage(
      `承認した子チケットのワークツリーを作成できませんでした。\n${childWorktreeFailureText(failed, gitScriptCommand(root))}`,
    );
  }
}

/**
 * `ccnaviBoard.open` の本体。`project` を渡すと、開いたボードの絞り込みをそのプロジェクトにする
 * （`""` はワークスペース（プロジェクト外）、`"*"` は全部）。いまは呼び出し元が無い（プロジェクト管理画面の入口を外した）。
 */
export async function openBoard(project?: string): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    vscode.window.showInformationMessage("ワークスペースが開かれていないため、チケット管理画面を表示できません");
    return;
  }
  if (!requireTickets("チケット管理画面")) {
    return;
  }
  if (state !== undefined) {
    state.wasVisible = true;
    state.panel.reveal(state.panel.viewColumn);
    state.filter = project;
    void update();
    // 開いたままのタブで、左パネルの案内から案内を頼まれたとき。組み上がりの `ready` は来ない
    if (tourDue(SCREEN)) {
      state.host.post({ type: "tour" } satisfies ToBoard);
    }
    return;
  }

  // 画面と CSS はバンドルしたものを読んで流し込む。無ければ開かずに言う（パネルだけ出しても白いまま）
  try {
    webviewScript(SCREEN);
    webviewStyle(SCREEN);
  } catch (error) {
    vscode.window.showErrorMessage(`チケット管理画面を表示できません: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }

  // タブは読む前に作る。実行ファイルの答えを待ってから作ると、押しても何も起きないように見え、
  // 押し直した分だけタブが増える（`state` を先に設定するので、2 度目の押下は下の `reveal` に入る）。
  // 読めなかったときもタブは閉じず、中にエラーを出す（`update` の `showError`）
  const panel = vscode.window.createWebviewPanel("ccnaviBoard", TITLE, vscode.ViewColumn.One, {
    enableScripts: true,
    enableForms: false,
    localResourceRoots: [],
    retainContextWhenHidden: false,
  });
  if (panel.options.retainContextWhenHidden !== false) {
    // screenHost は retainContextWhenHidden が偽であることを前提にしている。真だと、非表示になっても画面が破棄されず、表示の有無の読み直しが働かない。診断ログにだけ残す（docs/claude/logging.md）
    diaglog.get("ccnavi-board", folder.uri.fsPath).error("画面の前提が崩れている", { screen: SCREEN, retainContextWhenHidden: true });
  }
  showLoading(panel, TITLE, SCREEN, "チケット");
  const current: PanelState = {
    panel,
    folder,
    watchers: [],
    host: boardHost(panel),
    loading: false,
    again: false,
    wasVisible: panel.visible,
    filter: project,
    approval: CLOSED,
    moved: NOTHING_MOVED,
    merge: MERGE_IDLE,
  };
  state = current;
  followAppearance(panel, current.host);
  registerPanelHandlers(current);
  void update();
}

/** `ccnaviBoard.refresh` の本体 */
export function refreshBoard(): void {
  if (state === undefined) {
    vscode.window.showInformationMessage("チケット管理画面が開かれていません");
    return;
  }
  void update();
}

function registerPanelHandlers(current: PanelState): void {
  const { panel, folder } = current;

  panel.webview.onDidReceiveMessage((message: unknown) => {
    handleMessage(asMessage(message));
  });

  panel.onDidChangeViewState(() => {
    const becameVisible = panel.visible && !current.wasVisible;
    current.wasVisible = panel.visible;
    if (!panel.visible) {
      // `retainContextWhenHidden` は偽なので、非表示になった画面は破棄される。再表示されると
      // ここで入れてある HTML から作り直され、組み上がったら `ready` が届く
      current.host.hidden();
    }
    if (becameVisible) {
      void update();
    }
  });

  // 計画編集タブが未保存になった・未保存でなくなったら描き直す（承認のボタンを押せる・押せない）
  current.stopTabs = onWorkflowTabs(() => {
    if (state === current) {
      redraw(current);
    }
  });

  panel.onDidDispose(() => {
    tourPanelClosed(SCREEN);
    current.stopTabs?.();
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

  for (const pattern of WATCH_PATTERNS) {
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
  }, WATCH_DEBOUNCE_MS);
}

/** 実行ファイルを走らせ直して Webview の内容を差し替える */
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
    const result = await loadBoard(current.folder.uri.fsPath, binSetting());
    if (state !== current) {
      return;
    }
    current.launcher = result.launcher;
    if (result.ok) {
      // 読み直した答えでサイドパネルの入口もそろえる。答えるのは実行ファイルだけ
      noteTicketControl(ticketControlOf(result.board));
    }
    if (!result.ok) {
      // 開いている間の失敗は閉じない。前の表示を消して、何が起きたかを見せる。
      // 承認のオーバーレイは引き継いで残す。承認した文は取り返しがつかないので、
      // ボードが描けないことを理由に消さない。
      current.board = undefined;
      showError(current, result.error);
      return;
    }
    const board = buildBoard(result.board);
    current.board = board;
    // 承認待ちの親の提案に無い親の、計画編集タブの保存のハッシュを忘れる。ターミナルや Chrome 拡張で承認した・
    // 取り下げた親を、あとで出す改版の提案と比べない（ボードから承認したときは承認の結果でも忘れる）
    forgetSaved(staleSaved(workflowTabs().saved, todoParentsOf(board)));
    show(current, board);
  } finally {
    current.loading = false;
    if (current.again) {
      current.again = false;
      void update();
    }
  }
}

/**
 * ボードを見せる。中身を渡すのは `send`。
 *
 * 読めたボードはここを必ず通るので、動いたカードもここで数え直す。同じボードを渡し直すだけの
 * 描き直し（オーバーレイの出し入れ）でも通るが、列が動いていなければ `movedStep` が前の動いた表示を
 * そのまま返すので、承認の文を閉じた拍子に動いた表示が消えることはない。
 */
function show(current: PanelState, board: Board): void {
  current.moved = movedStep(current.moved, board);
  current.board = board;
  current.error = undefined;
  send(current, {
    kind: "board",
    board,
    approval: current.approval.overlay,
    moved: current.moved.moved,
    unsaved: workflowTabs().unsaved,
    merge: mergeView(current.merge, (parent) => abandonCommand(realRoot(current.folder.uri.fsPath), parent)),
  });
}

/** 読み直せなかったことを見せる。承認のオーバーレイがあれば、ボードのときと同じように被せる */
function showError(current: PanelState, error: string): void {
  current.error = error;
  send(current, { kind: "error", error, approval: current.approval.overlay, unsaved: workflowTabs().unsaved });
}

/**
 * いま見せるものを画面に渡す。どう渡るか（送る・入れ物ごと・作り直し中で持ち越し）は
 * `screenHost` が決めて返す。ここが持つのは「1 度きりの絞り込みをいつ消すか」だけ。
 */
function send(current: PanelState, data: BoardData): void {
  const filter = current.filter;
  // 入れ物ごと入れ直す経路になったときだけ、絞り込みを埋めたほうが使われる
  const delivery = current.host.send(data, withFilter(data, filter));
  if (delivery === "rebuilt") {
    current.filter = undefined;
    return;
  }
  if (delivery === "deferred") {
    // 作り直している最中。組み上がったら `ready` が届くので、そこで渡し直す（絞り込みも持ち越す）
    return;
  }
  // 届いたときだけ消す。届かないまま消すと「このプロジェクトで絞って開く」が二度と渡らない
  if (filter !== undefined && current.host.post({ type: "filter", project: filter } satisfies ToBoard)) {
    current.filter = undefined;
  }
}

/** 開いた直後に選ぶ絞り込み。入れ物に埋めて渡すときだけ使う */
function withFilter(data: BoardData, filter: string | undefined): BoardData {
  return filter === undefined ? data : { ...data, filter };
}

/**
 * ボードの画面に渡す手段。VS Code のパネルを `screenHost` の形に合わせる。
 * nonce は呼ぶたびに変える（同じ文字列を `webview.html` に入れても VS Code は何もしない）。
 */
function boardHost(panel: vscode.WebviewPanel): ScreenHost<BoardData> {
  return screenHost<BoardData>(
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
      renderBoardPage(data, {
        nonce: crypto.randomBytes(16).toString("base64"),
        script: webviewScript(SCREEN),
        style: webviewStyle(SCREEN),
        appearance: readAppearance(),
      }),
  );
}

function handleMessage(message: BoardMessage | undefined): void {
  const current = state;
  if (message === undefined || current === undefined) {
    return;
  }
  const root = current.folder.uri.fsPath;
  switch (message.type) {
    case "ready":
      // 画面が組み上がった。入れてある HTML は少し古いことがあるので、いまの中身を渡し直す。
      // 作り直している間に見送った更新（send）も、絞り込みも、ここで届く
      current.host.ready();
      redraw(current);
      // 非表示の間に見た目が変わっていたら、入れてある HTML の body のクラスは古い。
      // `followAppearance` がそのとき送ったものは、段取りが「送れない」と見て破棄している
      postAppearance(current.host);
      // 初回だけ吹き出しの案内を頼む。画面は指す先が出てから始め、閉じたら `tourDone` を返す。
      // 閉じずにタブを閉じたら見た記録は残らないので、次に開いたときにもう 1 度出る
      if (tourDue(SCREEN)) {
        current.host.post({ type: "tour" } satisfies ToBoard);
      }
      return;
    case "tourDone":
      markTourSeen(SCREEN);
      return;
    case "refresh":
      void update();
      return;
    case "open":
      openTicket(current, message.filePath);
      return;
    case "approve":
      // 絞り込んでいなければ承認待ち全部。絞り込んでいれば見えている分だけを承認の対象にする。
      // カードの「この 1 件を承認」は、そのカードの識別子だけを絞りとして送ってくる。
      // 突き合わせる相手（いまのボードの承認待ち）をつけて渡し、決めるのは遷移の側
      dispatch(current, {
        kind: "approve",
        tickets: message.tickets,
        filtered: message.filtered,
        pending: current.board?.pendingApproval ?? [],
      });
      return;
    case "approveConfirm":
      // 押した時点の計画編集タブの様子を添える。未保存のタブがある親を含む一覧は遷移が打たない
      dispatch(current, {
        kind: "confirm",
        tickets: message.tickets,
        digest: message.digest,
        unsaved: workflowTabs().unsaved,
      });
      return;
    case "approveCancel":
      dispatch(current, { kind: "cancel" });
      return;
    case "promptCopy":
    case "promptOpen":
    case "promptRun":
      dispatch(current, { kind: "handOver", how: message.type });
      return;
    case "decide":
      // ［レビュー完了］。残った指摘を読むオーバーレイを開く。sh が指摘を取ってきて、0 件ならレビュー済みにする画面、
      // 1 件以上ならユーザが指摘ごとに選ぶ画面になる。
      // ボードから引くもの（親のワークツリー・フェーズ）をつけて渡し、開いてよいかは遷移の側が決める
      dispatch(current, {
        kind: "decide",
        parent: message.parent,
        phase: message.phase,
        tree: current.board ? parentTreeOf(current.board, message.parent) : undefined,
        chip: current.board ? phaseChipOf(current.board, message.parent, message.phase) : undefined,
      });
      return;
    case "decideConfirm":
      dispatch(current, { kind: "decideConfirm", choices: message.choices });
      return;
    case "mergeCheck":
      dispatchMerge(current, { kind: "check" });
      return;
    case "mergeRetire":
      // 画面が言った親をそのまま信頼しない。マージ済みと確認できた親かは遷移の側が決める
      dispatchMerge(current, { kind: "retire", parent: message.parent });
      return;
    case "mergeCopyAbandon":
      void copyAbandon(current, message.parent);
      return;
    case "decideConsult":
      // 相談のプロンプトを組むのは遷移の側。文面に書くワークスペースルートは、実行ファイルの案内と同じ realpath で渡す
      dispatch(current, { kind: "consult", root: realRoot(root) });
      return;
    case "flow":
      // 画面が言った識別子をそのまま信頼せず、いまのボードに子のカードとして在るものだけを開く
      if (current.board !== undefined && flowCardOf(current.board, message.ticket) !== undefined) {
        void screens().flow(message.ticket);
      }
      return;
    case "workflow":
      // 画面が言った識別子をそのまま信頼せず、いまのボードで入口を出しているカード（計画を持つ承認待ちの親の
      // 提案）だけを開く。開くのはコマンド ccnaviBoard.openWorkflow と同じ入口
      if (current.board !== undefined && workflowCardOf(current.board, message.ticket) !== undefined) {
        void vscode.commands.executeCommand("ccnaviBoard.openWorkflow", message.ticket);
      }
      return;
    case "editWorkflow":
      // 閉じてよいか・一覧の図にある親かは遷移が決める。開くのは遷移が返す openWorkflow
      dispatch(current, { kind: "editWorkflow", ticket: message.ticket });
      return;
    case "templateDraft":
      // 画面が言った識別子をそのまま信頼せず、いまのボードで入口を出しているカード（完了した計画付きの親）だけを扱う
      if (current.board !== undefined && templateDraftCardOf(current.board, message.ticket) !== undefined) {
        void makeTemplateDraft(current, message.ticket);
      }
      return;
    default: {
      // `BoardMessage` に操作を足したのに、ここに処理を書いていなければ型が合わなくなる。
      // 画面のボタンだけ足して受け側を書き忘れるのを防ぐ
      const unhandled: never = message;
      void unhandled;
      return;
    }
  }
}

/**
 * 文面に書くワークスペースルート。実行ファイルの案内（`settings.script_command`）は realpath で解いたパスを出すので、
 * 同じパスにする（macOS の /tmp → /private/tmp など）。解けなければ渡されたパスのまま
 */
function realRoot(root: string): string {
  try {
    return fs.realpathSync.native(root);
  } catch {
    return root;
  }
}

/**
 * いまの状態で描き直す（オーバーレイの出し入れ）。読み直せていないときはエラー画面のほうに
 * 載せ替える。ボードが無いことを理由にここで破棄すると、承認した文がユーザに届かない。
 */
function redraw(current: PanelState): void {
  if (current.board !== undefined) {
    show(current, current.board);
    return;
  }
  if (current.error !== undefined) {
    showError(current, current.error);
  }
}

/**
 * 承認のオーバーレイに 1 つ入力を入れる。遷移の規則はここに書かない
 * （`core/approval-machine.ts`。VS Code に触れないので単体で試せる）。
 *
 * ここがするのは 3 つだけ。返った状態を持ち直す、描き直す、やることを行う。順を変えない。
 * 描き直しが先で、やることが後（逆にすると、文を渡すときに画面が古いまま残る）。
 */
function dispatch(current: PanelState, input: ApprovalInput): void {
  const step = approvalStep(current.approval, input);
  current.approval = step.state;
  if (step.redraw) {
    redraw(current);
  }
  for (const effect of step.effects) {
    void runEffect(current, effect);
  }
}

/**
 * マージ状況の確認に 1 つ入力を入れる。遷移の規則は `core/merge.ts` にある（VS Code に触れないので単体で試せる）。
 * ここがするのは 3 つだけ。返った状態を持ち直す、描き直す、やることを行う。描き直しが先で、やることが後。
 */
function dispatchMerge(current: PanelState, input: MergeInput): void {
  const step = mergeStep(current.merge, input);
  current.merge = step.state;
  if (step.redraw) {
    redraw(current);
  }
  for (const effect of step.effects) {
    void runMergeEffect(current, effect);
  }
}

/** 同時に走らせる `mr-state` の数。ホストの API を叩くので、親が多くても一度に押し寄せない */
const MR_STATE_SLOTS = 4;
let mrStateRunning = 0;
const mrStateQueue: (() => void)[] = [];

async function withMrStateSlot<T>(job: () => Promise<T>): Promise<T> {
  if (mrStateRunning >= MR_STATE_SLOTS) {
    await new Promise<void>((resolve) => mrStateQueue.push(resolve));
  }
  mrStateRunning += 1;
  try {
    return await job();
  } finally {
    mrStateRunning -= 1;
    mrStateQueue.shift()?.();
  }
}

/** 確認した時刻の表示（ローカル時刻） */
function nowText(): string {
  const d = new Date();
  const two = (n: number): string => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
}

/** マージ状況の確認が遷移から頼まれた仕事を行う。返事が要るものは、パネルがまだ同じなら `dispatchMerge` で戻す */
async function runMergeEffect(current: PanelState, effect: MergeEffect): Promise<void> {
  const root = current.folder.uri.fsPath;
  switch (effect.kind) {
    case "loadList": {
      const result = await runSyncRetired(root, binSetting());
      if (state === current) {
        dispatchMerge(current, { kind: "listed", token: effect.token, result, at: nowText() });
      }
      return;
    }
    case "loadState": {
      // 1 回ごとに期限を付け（`runMrState`）、失敗はその親だけに留める。cwd は片付けの記録が指すリポジトリ
      const cwd = repoDirOf(root, current.board?.projectsDir ?? "", effect.repo);
      const result = await withMrStateSlot(() => runMrState(root, scriptShell(), cwd, effect.mr));
      if (state === current) {
        dispatchMerge(current, { kind: "stated", token: effect.token, parent: effect.parent, result, at: nowText() });
      }
      return;
    }
    case "confirmRetire": {
      const run = "実行する";
      const picked = await vscode.window.showWarningMessage(retireConfirmText(effect.parent), { modal: true }, run);
      if (state === current) {
        dispatchMerge(current, { kind: "retireAnswered", parent: effect.parent, accepted: picked === run });
      }
      return;
    }
    case "sendRetire": {
      // 送れたこと＝実行の成功ではない（完了は検出しない）。送れなかったときだけ理由を持って戻す
      let error: string | undefined;
      try {
        runInTerminal(root, retireCommand(root, effect.parent));
      } catch (caught) {
        error = caught instanceof Error && caught.message !== "" ? caught.message : "ターミナルに送れませんでした";
      }
      diaglog.get("ccnavi-board", root).info("片付けのコマンドを送った", { sent: error === undefined });
      if (state === current) {
        dispatchMerge(current, { kind: "retireSent", parent: effect.parent, error });
      }
      return;
    }
    case "warn":
      vscode.window.showWarningMessage(effect.text);
      return;
    default: {
      const unhandled: never = effect;
      void unhandled;
      return;
    }
  }
}

/**
 * マージせずに閉じた親の、記録を消すコマンドをコピーする。ボードからは送らない（ユーザがターミナルで実行する）。
 * 画面が言った親をそのまま信頼せず、いまの結果で「マージせず閉じた」と確認できている親だけを扱う
 */
async function copyAbandon(current: PanelState, parent: string): Promise<void> {
  const list = current.merge.list;
  const row = list.kind === "rows" ? list.rows.find((r) => r.parent === parent) : undefined;
  if (row?.result?.kind !== "closed") {
    return;
  }
  await copyCommand(abandonCommand(realRoot(current.folder.uri.fsPath), parent), "片付けの記録を消すコマンド");
}

/**
 * 遷移が返した「やること」を行う。外へ出るのはここだけ（実行ファイル・ターミナル・クリップボード・
 * 新しいセッション・通知）。返事が要るもの（一覧と承認の結果）は、返ってきたらまた `dispatch` に入れる。
 *
 * 返事を入れる前に、パネルがまだ同じかを見る。 開き直された後のパネルに、前のパネルの
 * 承認の結果を入れない。
 */
async function runEffect(current: PanelState, effect: ApprovalEffect): Promise<void> {
  const root = current.folder.uri.fsPath;
  switch (effect.kind) {
    case "loadPreview": {
      const result = await runApprovePreview(root, binSetting(), effect.only);
      if (state === current) {
        // 計画編集タブが保存したときのハッシュは、一覧が返ったこの時点のものと比べる
        dispatch(current, { kind: "previewed", result, saved: workflowTabs().saved });
      }
      return;
    }
    case "approve": {
      // 打つ直前にもう 1 度、計画編集タブに未保存の変更がある親を含まないかを見る（遷移が見たあとに
      // 未保存になっていれば打たない）。一覧の親の識別子と、帳面の親の識別子を突き合わせる
      const blocked = blockedBeforeApprove(effect.tickets, workflowTabs().unsaved);
      if (blocked.length > 0) {
        dispatch(current, { kind: "approved", outcome: { ok: false, error: unsavedText(blocked) }, carrier: false });
        return;
      }
      // 見せたときと同じ絞りを渡す。渡さないと、実行ファイルは絞らないときの対象と比べて食い違いにする。
      // 見せたダイジェスト（提案の識別子の一覧と各提案ファイルのバイト列全体）も渡す。識別子が同じでも、
      // 見せたあとに提案ファイルの中身（本文・改行・BOM）が変われば承認せず、最新を表示し直す
      const outcome = await runApproveYes(root, binSetting(), effect.tickets, effect.digest, effect.only);
      // 承認済みチケットを置くのは取り返しがつかないので、受け付けたことと結果を 1 行残す
      diaglog.get("ccnavi-board", root).info("承認を打った", {
        result: outcome.ok ? "approved" : "mismatch" in outcome ? "mismatch" : "error",
        requested: effect.tickets.length,
        approved: outcome.ok ? outcome.value.approved.length : 0,
      });
      if (outcome.ok) {
        // 承認した親は承認待ちから無くなる。あとで出す改版の提案を、前の保存と比べない
        forgetSaved(outcome.value.approved);
        // 承認した子のワークツリーを作る。待たずに、作れなかったものだけ後で名指しで知らせる
        void createChildWorktrees(root, outcome.value.worktrees);
      }
      if (state === current) {
        // 承認の push の sh があるかは、承認が返ったこの時点で見る
        dispatch(current, { kind: "approved", outcome, carrier: isFile(path.join(root, PUSH_APPROVED_SCRIPT)) });
      }
      return;
    }
    // 承認の実行ファイルは承認済みチケットを置くだけで、コミットして push するのはこの sh
    case "carry":
      runInTerminal(root, pushApprovedCommand(root, [], effect.paths));
      return;
    case "copy":
      await copyPrompt(effect.prompt, effect.what);
      return;
    case "openSession":
      await openPromptInSession(effect.prompt);
      return;
    case "runClaude":
      await runPromptInTerminal(root, effect.prompt);
      return;
    case "loadDecide": {
      const result = await runDecidePreview(root, scriptShell(), effect.tree, effect.phase, {
        parent: effect.parent,
        phase: effect.phase,
        mr: effect.mr,
      });
      if (state === current) {
        dispatch(current, { kind: "decidePreviewed", result });
      }
      return;
    }
    case "copyConsult": {
      const error = await copyPrompt(effect.prompt, "相談のプロンプト");
      if (state === current) {
        dispatch(current, { kind: "consultCopied", error });
      }
      return;
    }
    case "decide": {
      const outcome = await runDecideYes(
        root,
        binSetting(),
        scriptShell(),
        effect.tree,
        effect.phase,
        effect.choices,
        effect.digest,
      );
      // 行き先を置くのは取り返しがつかない（続きの子が起きる）ので、受け付けたことと結果を 1 行残す
      diaglog.get("ccnavi-board", root).info("残った指摘の対応方針を置いた", {
        result: outcome.ok ? "decided" : "mismatch" in outcome ? "mismatch" : "error",
        phase: effect.phase,
      });
      if (state === current) {
        dispatch(current, { kind: "decided", outcome });
      }
      return;
    }
    case "openWorkflow":
      // オーバーレイは閉じて描き直してある（描き直しが先、やることが後）
      void screens().workflow(effect.ticket);
      return;
    case "warn":
      vscode.window.showWarningMessage(effect.text);
      return;
    case "refresh":
      void update();
      return;
    default: {
      // `ApprovalEffect` に足したのに、ここに書いていなければ型が合わなくなる
      const unhandled: never = effect;
      void unhandled;
      return;
    }
  }
}

/** ファイルとして在るか。無いものを読もうとして投げるのは「無い」として扱う */
function isFile(filePath: string): boolean {
  try {
    return fs.statSync(filePath).isFile();
  } catch {
    return false;
  }
}

function openTicket(current: PanelState, filePath: string): void {
  if (current.board === undefined || !isKnownPath(current.board, filePath)) {
    return;
  }
  void showTicketPreview(filePath).catch(() => {
    vscode.window.showInformationMessage(`チケットのファイルを開けませんでした: ${filePath}`);
    void update();
  });
}

/**
 * チケットは Markdown なので、素のテキストではなくプレビューで見せる。
 * プレビューは組み込みの Markdown 拡張のもので、無いファイルを渡しても失敗を返さない前提で書いている
 * （ここからは確かめられないので、docs/design/manual-checks.md の手動確認で見る）。だから先に在るかを確かめ、無ければ
 * 呼び手に失敗を返す（通知して、ボードを読み直す）。確かめてから描くまでに消えた場合は拾えない。
 * プレビューのコマンドが無い環境（組み込みの Markdown 拡張が無効）では、これまでどおりエディタで開く。
 */
async function showTicketPreview(filePath: string): Promise<void> {
  const uri = vscode.Uri.file(filePath);
  await vscode.workspace.fs.stat(uri);
  try {
    await vscode.commands.executeCommand("markdown.showPreview", uri);
  } catch {
    const document = await vscode.workspace.openTextDocument(uri);
    await vscode.window.showTextDocument(document);
  }
}

/**
 * 完了した親から、テンプレートの下書きを作る（`ccnavi --templates --draft`）。下書きは効力が無く、書くだけなので
 * ユーザの許可は取らない。作れたら知らせ、そこから取り込み待ち画面を開けるようにする。
 */
async function makeTemplateDraft(current: PanelState, ticket: string): Promise<void> {
  const result = await runTemplateDraft(current.folder.uri.fsPath, binSetting(), ticket);
  if (!result.ok) {
    void vscode.window.showErrorMessage(`テンプレートの下書きを作れませんでした: ${result.error}`);
    return;
  }
  const open = "取り込み待ちを開く";
  const picked = await vscode.window.showInformationMessage(
    `テンプレートの下書きを作りました: ${result.value.path}。効力は無く、取り込み待ち画面で差分を読んで取り込むまで使われません。`,
    open,
  );
  if (picked === open) {
    await vscode.commands.executeCommand("ccnaviBoard.openHandoff");
  }
}

/**
 * 形を確かめる操作の一覧。`BoardMessage` に足したのにここへ足していなければ、型が合わなくなる。
 * `handleMessage` の網羅検査（`never`）は処理の書き忘れしか止めないので、入口の側でも同じことをする。
 * 足し忘れると、画面のボタンは押せるのに、届いたものが何も出さずに破棄される。
 */
const KNOWN: Readonly<Record<BoardMessage["type"], true>> = {
  ready: true,
  refresh: true,
  open: true,
  approve: true,
  approveConfirm: true,
  approveCancel: true,
  promptCopy: true,
  promptOpen: true,
  promptRun: true,
  decide: true,
  decideConfirm: true,
  decideConsult: true,
  mergeCheck: true,
  mergeRetire: true,
  mergeCopyAbandon: true,
  flow: true,
  editWorkflow: true,
  workflow: true,
  templateDraft: true,
  tourDone: true,
};

function asMessage(message: unknown): BoardMessage | undefined {
  if (typeof message !== "object" || message === null) {
    return undefined;
  }
  const m = message as {
    type?: unknown;
    filePath?: unknown;
    parent?: unknown;
    phase?: unknown;
    tickets?: unknown;
    filtered?: unknown;
    digest?: unknown;
    choices?: unknown;
    ticket?: unknown;
    family?: unknown;
    project?: unknown;
  };
  if (typeof m.type !== "string" || !(m.type in KNOWN)) {
    return undefined;
  }
  switch (m.type) {
    case "ready":
    case "refresh":
    case "tourDone":
    case "approveCancel":
    case "promptCopy":
    case "promptOpen":
    case "promptRun":
    case "decideConsult":
    case "mergeCheck":
      return { type: m.type };
    case "approve":
      // 形が不正なら破棄する。「全部承認」として扱うと、検証に失敗したときに承認の範囲が広がってしまう。
      return Array.isArray(m.tickets) &&
        m.tickets.every((t) => typeof t === "string") &&
        typeof m.filtered === "boolean"
        ? { type: "approve", tickets: m.tickets, filtered: m.filtered }
        : undefined;
    case "approveConfirm":
      // ダイジェストの無い押下は破棄する。どの一覧を見て押したかが分からないまま打たない
      return Array.isArray(m.tickets) && m.tickets.every((t) => typeof t === "string") && typeof m.digest === "string"
        ? { type: "approveConfirm", tickets: m.tickets as string[], digest: m.digest }
        : undefined;
    case "open":
      return typeof m.filePath === "string" ? { type: "open", filePath: m.filePath } : undefined;
    case "decideConfirm":
      // 値が文字列でない選択は破棄する。行き先が見せた指摘の全部に付いているかは遷移の側が見る
      return typeof m.choices === "object" &&
        m.choices !== null &&
        !Array.isArray(m.choices) &&
        Object.values(m.choices).every((v) => typeof v === "string")
        ? { type: "decideConfirm", choices: { ...(m.choices as Record<string, string>) } }
        : undefined;
    case "decide":
      return typeof m.parent === "string" && typeof m.phase === "number" && Number.isInteger(m.phase)
        ? { type: m.type, parent: m.parent, phase: m.phase }
        : undefined;
    case "flow": {
      const ticket = flowTicketOf(m);
      return ticket === undefined ? undefined : { type: "flow", ticket };
    }
    case "editWorkflow":
    case "workflow":
    case "templateDraft":
      return isTicketId(m.ticket) ? { type: m.type, ticket: m.ticket } : undefined;
    case "mergeRetire":
    case "mergeCopyAbandon":
      // 親の識別子の形でないものは破棄する（コマンド行に入る文字列なので、ここで形を確かめる）
      return isTicketId(m.parent) ? { type: m.type, parent: m.parent } : undefined;
    default:
      return undefined;
  }
}
