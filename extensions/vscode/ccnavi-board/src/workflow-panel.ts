/**
 * 計画編集タブ（作業・レビュー計画の編集。旧称ワークフロー編集）の Webview パネル。生成・更新・破棄、提案の監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。docs/design/manual-checks.md の手動確認の手順で確かめる。
 *
 * 親の提案（承認待ちの置き場 `todo/` にある、計画を持つ親。新規と改版の両方）の計画のステップどうしの先行（線）を
 * 図で引き直す。**タブは親ごとに 1 枚**（同じ親をもう 1 度開けば前面に出す）。作りはフロー編集（`flow-panel.ts`）と
 * 同じで、違いは 3 つ。
 *
 * 1. 取り込む段が無い。開いたときの提案の中身（`--plan-order <親> --json` の答え）がそのまま初期値
 * 2. 書くのは実行ファイル（`--plan-order <親> --order <JSON> --expect <ハッシュ> --write`）
 * 3. 保存先はエージェントも書ける提案（親の提案と、番号の変わるステップを指す子の提案）。なので開いたときのハッシュ
 *    （`source_sha`）を `--expect` に渡し、外で変わっていれば実行ファイルが何も書かない
 *
 * 画面は React（`src/webview/workflow/`）で、ここが渡すのは「いま何を見せるか」（`WorkflowData`）だけ。
 * 渡し方はフロー編集と同じ `retainedHost`（線の引き直しの途中を持つので、入れ物を入れ直さない）。
 *
 * **判定は実行ファイル。** 線を変えるたびに画面が元の番号での先行（`--order`）を頼み、ここが
 * `--plan-order <親> --json --order <JSON>` の答えをそのまま返す。番号の振り直し・順序の検査・書けるか
 * （`writable`）・子の付け替えの一覧は答えのまま。画面もここも番号を振り直さない。
 *
 * **外で提案が変わったとき。** 提案の置き場（`core/watch.ts`）を見張り、動いたら `--plan-order <親> --json` を打ち直して
 * ハッシュを比べる。違えば画面に「外で変わった」と今の答えを渡し、画面が差分を見せて「取り込む」（読み直す）か
 * 「編集を続ける」（保存はできない）を選ばせる。線の答えのハッシュが開いたときと違うときも同じ。親の提案が承認待ちから
 * 無くなったら（承認・取り下げ・削除。ボードの答えで見分ける）、画面を読むだけにする。
 *
 * **未保存のまま閉じたとき。** フロー編集と同じく、閉じるのは止められない（`onDidDispose` は閉じた後）。未保存の間は
 * タブの題の頭に「●」を付け、画面が送ってくる線のコピー（`draft`）を覚える。閉じた後に未保存だったら「開き直して戻す」
 * 「破棄する」を聞く。戻すのは、提案のハッシュが閉じた時点から変わっていないときだけ（変わっていれば戻せないと言う）。
 *
 * **ボードの承認との結び付き。** タブの未保存と、最後に保存したときのハッシュ（`source_sha`）を帳面
 * （`core/workflow-tabs.ts`）に書く。ボードは、未保存のタブがある親を含む一覧の承認を打たず、承認の一覧の
 * `source_sha` が保存したときと違えば「保存したあとに提案が変わった」と出す。未保存は、タブが閉じた・破棄した
 * （読み直した・取り込んだ）・保存したときに外れる。
 *
 * 残る隙間（TOCTOU）: 実行ファイルがハッシュを確かめてから書くまでの間にエージェントが提案を書くと、どちらかが
 * 失われうる。間は実行ファイル 1 回の起動の中で、防ぐには提案の置き場に錠が要るので、ここでは狭めるだけにする
 * （書いたあとの監視で食い違いに気づけば「外で変わった」を出す）。
 */
import * as crypto from "node:crypto";
import * as vscode from "vscode";

import { followAppearance, postAppearance, readAppearance } from "./appearance.js";
import { checkPlanOrder, loadBoard, runPlanOrder, runPlanOrderWrite } from "./ccnavi.js";
import { loadingText } from "./core/loading-render.js";
import type { PlanOrder, PlanOrderAnswer } from "./core/planordermodel.js";
import { retainedHost, type ScreenHost } from "./core/screen-host.js";
import { OWN_WRITE_GRACE_MS, WATCH_PATTERNS } from "./core/watch.js";
import { markUnsaved, rememberSaved } from "./core/workflow-tabs.js";
import { renderWorkflowPage } from "./core/workflow-render.js";
import {
  asWorkflowMessage,
  goneReasonOf,
  restoreAsked,
  savedShaOf,
  workflowTicketOf,
  workflowTitle,
  type ToWorkflow,
  type WorkflowData,
  type WorkflowMessage,
} from "./core/workflow-view.js";
import { showLoading } from "./loading.js";
import * as diaglog from "./log.js";
import { requireTickets } from "./ticket-control.js";
import { markTourSeen, tourSeen } from "./tour.js";
import { webviewScript, webviewStyle } from "./webview-asset.js";

/** 他の画面（WATCH_DEBOUNCE_MS = 120）より長い。理由は記録が無く未確認。変える前に、答えを返す実行ファイルの重さを測ること */
const DEBOUNCE_MS = 200;
/** 画面の名前。バンドルのパスは `src/webview/<名前>/main.tsx` → `out/webview/<名前>.js` */
const SCREEN = "workflow";

/** 未保存のまま閉じた画面から戻す線。提案のハッシュが閉じたときと同じときだけ戻す */
interface Restore {
  readonly draft: PlanOrder;
  readonly sha: string;
}

interface PanelState {
  readonly ticket: string;
  readonly panel: vscode.WebviewPanel;
  readonly folder: vscode.WorkspaceFolder;
  readonly host: ScreenHost<WorkflowData>;
  watchers: vscode.FileSystemWatcher[];
  timer?: NodeJS.Timeout;
  /** 開いたとき（読み直したとき）の答え。`source_sha` を保存の `--expect` に使う */
  loaded?: PlanOrderAnswer;
  error?: string;
  dirty: boolean;
  /** 画面が送ってきた、未保存の線のコピー（未保存でなければ無い） */
  draft?: PlanOrder;
  /** 開き直したときに戻す線。最初の読み込みで確かめて `shownDraft` に移す */
  restore?: Restore;
  shownDraft?: PlanOrder;
  /** 外で変わったときの今の答え。再表示されたとき・作り直したときに知らせ直す */
  changed?: PlanOrderAnswer;
  /** 提案が承認待ちから無くなった理由 */
  gone?: string;
  seq: number;
  saving: boolean;
  wroteAt: number;
}

/** 開いているパネル。親の識別子ごとに 1 枚 */
const panels = new Map<string, PanelState>();

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

/**
 * 親の識別子を受けてタブを開く（コマンド `ccnaviBoard.openWorkflow` と、画面の帳面の `workflow`）。
 * コマンドパレットには出さない（開く親を選ぶ段が無いため）。識別子の形でなければ開かない
 */
export async function openWorkflow(ticket: unknown): Promise<void> {
  const parent = workflowTicketOf(ticket);
  if (parent === undefined) {
    void vscode.window.showWarningMessage("計画編集タブを開く親の識別子が渡されていません（ボードの親の提案から開きます）");
    return;
  }
  await openWorkflowPanel(parent, undefined);
}

async function openWorkflowPanel(ticket: string, restore: Restore | undefined): Promise<void> {
  if (!requireTickets("計画編集タブ")) {
    return;
  }
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    vscode.window.showInformationMessage("ワークスペースが開かれていないため、計画編集タブを表示できません");
    return;
  }
  const open = panels.get(ticket);
  if (open !== undefined) {
    open.panel.reveal(open.panel.viewColumn);
    if (restore !== undefined) {
      // 既に開いている画面の編集を何も言わずに差し替えない
      void vscode.window.showWarningMessage(`${ticket} の計画編集タブは既に開いているため、閉じる前の編集を戻せませんでした。閉じる前の編集は、無題の JSON ファイルとして開きます。`);
      void openDraftAsJson(restore.draft);
    }
    return;
  }
  try {
    webviewScript(SCREEN);
    webviewStyle(SCREEN);
  } catch (error) {
    vscode.window.showErrorMessage(`計画編集タブを表示できません: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }
  const panel = vscode.window.createWebviewPanel("ccnaviWorkflow", workflowTitle(ticket), vscode.ViewColumn.One, {
    enableScripts: true,
    enableForms: false,
    localResourceRoots: [],
    // 線の引き直しの途中を持つので、タブを非表示にしても破棄しない
    retainContextWhenHidden: true,
  });
  showLoading(panel, workflowTitle(ticket), SCREEN, `${ticket} の計画編集`);
  const current: PanelState = {
    ticket,
    panel,
    folder,
    host: workflowHost(panel, folder.uri.fsPath),
    watchers: [],
    dirty: false,
    restore,
    seq: 0,
    saving: false,
    wroteAt: 0,
  };
  panels.set(ticket, current);
  followAppearance(panel, current.host);
  registerPanelHandlers(current);
  await reload(current);
}

function alive(current: PanelState): boolean {
  return panels.get(current.ticket) === current;
}

function root(current: PanelState): string {
  return current.folder.uri.fsPath;
}

function registerPanelHandlers(current: PanelState): void {
  const { panel, folder } = current;
  panel.webview.onDidReceiveMessage((message: unknown) => {
    void handleMessage(current, asWorkflowMessage(message));
  });
  // フロー編集と同じ。再表示されたら、いま出すべき知らせと見た目を送り直す（中身は送らない）
  panel.onDidChangeViewState(() => {
    if (!panel.visible || !alive(current)) {
      return;
    }
    postAppearance(current.host);
    postNotices(current);
  });
  panel.onDidDispose(() => {
    askRestore(current);
    // 閉じたタブの未保存は、ボードの承認を止めない（戻すかを聞くのは閉じた後で、戻せば開き直したタブがまた書く）
    if (alive(current)) {
      markUnsaved(current.ticket, false);
    }
    if (current.timer !== undefined) {
      clearTimeout(current.timer);
    }
    for (const watcher of current.watchers) {
      watcher.dispose();
    }
    current.watchers = [];
    if (alive(current)) {
      panels.delete(current.ticket);
    }
  });
  // 提案（親と子）が動いたら、外で変わったかを実行ファイルに聞き直す。承認されて置き場が変わったときもここで気づく
  for (const pattern of WATCH_PATTERNS) {
    const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(folder, pattern));
    const moved = () => scheduleCheck(current);
    watcher.onDidCreate(moved);
    watcher.onDidChange(moved);
    watcher.onDidDelete(moved);
    current.watchers.push(watcher);
  }
}

/** 外で変わった・無くなったの知らせを画面に送る（作り直した画面と、再表示された画面へ） */
function postNotices(current: PanelState): void {
  if (current.gone !== undefined) {
    current.host.post({ type: "gone", reason: current.gone } satisfies ToWorkflow);
  } else if (current.changed !== undefined) {
    current.host.post({ type: "changed", answer: current.changed } satisfies ToWorkflow);
  }
}

/**
 * 未保存のまま閉じた。閉じるのは止められないので、閉じた後に戻すかを聞く（頭のコメント）。
 * 覚えたコピーが無い（線を変えてすぐ閉じた）か、読み込めていなければ何も聞かない
 */
function askRestore(current: PanelState): void {
  const loaded = current.loaded;
  const draft = current.draft;
  if (!restoreAsked({ dirty: current.dirty, draft: draft !== undefined, loaded: loaded !== undefined, saving: current.saving }) || draft === undefined || loaded === undefined) {
    return;
  }
  const restore: Restore = { draft, sha: loaded.source_sha };
  const back = "開き直して戻す";
  void vscode.window
    .showWarningMessage(
      `${current.ticket} の作業・レビュー計画の順序を未保存のまま閉じました。編集を戻しますか？`,
      { modal: true, detail: "「破棄する」を選ぶと、閉じる前の編集は残りません。提案が閉じた後に変わっていれば、戻せません。" },
      back,
      "破棄する",
    )
    .then((choice) => {
      if (choice === back) {
        void openWorkflowPanel(current.ticket, restore);
      }
    });
}

/** コピーを名前の無い JSON のエディタで開く（ファイルには書かない） */
async function openDraftAsJson(draft: PlanOrder): Promise<void> {
  try {
    const document = await vscode.workspace.openTextDocument({ language: "json", content: `${JSON.stringify(draft, null, 2)}\n` });
    await vscode.window.showTextDocument(document, { preserveFocus: true });
  } catch (error) {
    void vscode.window.showErrorMessage(`閉じる前の編集を JSON として開けませんでした: ${error instanceof Error ? error.message : String(error)}`);
  }
}

function scheduleCheck(current: PanelState): void {
  if (current.timer !== undefined) {
    clearTimeout(current.timer);
  }
  current.timer = setTimeout(() => {
    current.timer = undefined;
    if (alive(current)) {
      void checkOutside(current);
    }
  }, DEBOUNCE_MS);
}

/**
 * 提案が外で変わったかを聞き直す。自分の保存の直後と保存の途中は聞かない（保存のあとは読み直すので要らない）。
 * 答えのハッシュが開いたときと違えば「外で変わった」、答えられず、親の提案が承認待ちに無ければ「無くなった」
 */
async function checkOutside(current: PanelState): Promise<void> {
  const loaded = current.loaded;
  if (loaded === undefined || current.saving || current.gone !== undefined || Date.now() - current.wroteAt < OWN_WRITE_GRACE_MS) {
    return;
  }
  const got = await runPlanOrder(root(current), binSetting(), current.ticket);
  if (!alive(current) || current.loaded !== loaded) {
    return;
  }
  if (got.ok) {
    if (got.value.source_sha !== loaded.source_sha && current.changed?.source_sha !== got.value.source_sha) {
      current.changed = got.value;
      current.host.post({ type: "changed", answer: got.value } satisfies ToWorkflow);
    }
    return;
  }
  const gone = await goneReason(current);
  if (gone !== undefined && alive(current)) {
    current.gone = gone;
    current.host.post({ type: "gone", reason: gone } satisfies ToWorkflow);
  }
}

/**
 * 親の提案が承認待ちから無くなったか（ボードの答えで見分ける）。無くなっていれば理由、まだ承認待ちか確かめられなければ
 * undefined（そのときは何も言わない。次の変化でまた聞く）
 */
async function goneReason(current: PanelState): Promise<string | undefined> {
  const board = await loadBoard(root(current), binSetting());
  if (!board.ok) {
    return undefined;
  }
  const found = board.board.tickets.find((t) => t.ticket === current.ticket && t.parent === "");
  const revision = current.loaded?.plans.some((plan) => plan.current !== null) === true;
  return goneReasonOf(current.ticket, revision, found === undefined ? undefined : { proposal: found.proposal?.state ?? null, approved: found.copy.status !== "none" });
}

/** いま見せるものを渡す。**画面の編集はここで破棄される**（読み直し・取り込む・保存が通ったときだけ呼ぶ） */
function show(current: PanelState): void {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  current.error = undefined;
  current.host.send({
    kind: "page",
    page: {
      root: root(current),
      ticket: current.ticket,
      answer: loaded,
      ...(current.shownDraft === undefined ? {} : { draft: current.shownDraft }),
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
    postNotices(current);
    return;
  }
  if (current.error !== undefined) {
    showError(current, current.error);
    return;
  }
  current.host.send({ kind: "loading", text: loadingText(`${current.ticket} の計画編集`) });
}

function workflowHost(panel: vscode.WebviewPanel, rootPath: string): ScreenHost<WorkflowData> {
  if (panel.options.retainContextWhenHidden !== true) {
    // retainedHost は retainContextWhenHidden が真であることを前提にしている。診断ログにだけ残す
    diaglog.get("ccnavi-board", rootPath).error("画面の前提が崩れている", { screen: SCREEN, retainContextWhenHidden: false });
  }
  return retainedHost<WorkflowData>(
    {
      html(text: string): void {
        panel.webview.html = text;
      },
      post(message: unknown): void {
        void panel.webview.postMessage(message);
      },
    },
    (data) =>
      renderWorkflowPage(data, {
        nonce: crypto.randomBytes(16).toString("base64"),
        script: webviewScript(SCREEN),
        style: webviewStyle(SCREEN),
        appearance: readAppearance(),
      }),
  );
}

/** 実行ファイルに聞き直して、提案のままの答えを見せる。画面の編集は破棄する */
async function reload(current: PanelState): Promise<void> {
  current.seq += 1;
  const seq = current.seq;
  const restore = current.restore;
  current.restore = undefined;
  current.shownDraft = undefined;
  current.changed = undefined;
  current.gone = undefined;
  const rootPath = root(current);
  const refused = await checkPlanOrder(rootPath, binSetting());
  const got = refused === undefined ? await runPlanOrder(rootPath, binSetting(), current.ticket) : ({ ok: false, error: refused } as const);
  if (!alive(current) || current.seq !== seq) {
    if (restore !== undefined) {
      void vscode.window.showWarningMessage(`${current.ticket} の計画編集タブを読み直せなかったので、閉じる前の編集を戻せませんでした。閉じる前の編集は、無題の JSON ファイルとして開きます。`);
      void openDraftAsJson(restore.draft);
    }
    return;
  }
  if (!got.ok) {
    if (restore !== undefined) {
      void vscode.window.showWarningMessage(`${current.ticket} の提案を読めなかったので、閉じる前の編集を戻せませんでした。閉じる前の編集は、無題の JSON ファイルとして開きます。`);
      void openDraftAsJson(restore.draft);
    }
    showError(current, got.error);
    return;
  }
  current.loaded = got.value;
  if (restore !== undefined) {
    if (restore.sha === got.value.source_sha) {
      current.shownDraft = restore.draft;
    } else {
      // 閉じた後に提案が変わった。前の線を戻すと、変わった提案に古い順序を重ねることになる
      void vscode.window.showWarningMessage(`${current.ticket} の提案は閉じた後に変わったので、閉じる前の編集を戻しませんでした。閉じる前の編集は、無題の JSON ファイルとして開きます。`);
      void openDraftAsJson(restore.draft);
    }
  }
  show(current);
}

function fail(current: PanelState, message: string): void {
  current.host.post({ type: "failed", message } satisfies ToWorkflow);
}

async function handleMessage(current: PanelState, message: WorkflowMessage | undefined): Promise<void> {
  if (message === undefined || !alive(current)) {
    return;
  }
  switch (message.type) {
    case "dirty":
      current.dirty = message.dirty;
      // 線の答えを待つ間も、ボードの承認を止める（題の「●」は答えの未保存だけ）
      markUnsaved(current.ticket, message.dirty || message.checking);
      current.panel.title = workflowTitle(current.ticket, message.dirty);
      if (!message.dirty) {
        current.draft = undefined;
      }
      return;
    case "draft":
      current.draft = message.order ?? undefined;
      return;
    case "ready":
      current.dirty = false;
      markUnsaved(current.ticket, false);
      current.draft = undefined;
      current.panel.title = workflowTitle(current.ticket);
      current.host.ready();
      redraw(current);
      postAppearance(current.host);
      // 初回だけ吹き出しの案内を頼む。画面は図が出てから始め、閉じたら `tourDone` を返す
      if (!tourSeen(SCREEN)) {
        current.host.post({ type: "tour" } satisfies ToWorkflow);
      }
      return;
    case "tourDone":
      markTourSeen(SCREEN);
      return;
    case "reload": {
      if (message.dirty) {
        const choice = await vscode.window.showWarningMessage("未保存の順序があります。破棄して再読み込みしますか？", { modal: true }, "再読み込みする");
        if (choice !== "再読み込みする") {
          current.host.post({ type: "cancelled" } satisfies ToWorkflow);
          return;
        }
      }
      await reload(current);
      return;
    }
    case "adopt":
      // 画面のボタンが「編集を破棄する」と言っているので、ここでは聞き直さない
      await reload(current);
      return;
    case "order":
      await answer(current, message.seq, message.order);
      return;
    case "save":
      await save(current, message.order, message.expect);
      return;
    default: {
      const unhandled: never = message;
      void unhandled;
      return;
    }
  }
}

/**
 * 画面が変えた線で実行ファイルに振り直させ、答えを返す。答えのハッシュが開いたときと違えば、
 * 外で変わっているので、その知らせも出す
 */
async function answer(current: PanelState, seq: number, order: PlanOrder): Promise<void> {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  const got = await runPlanOrder(root(current), binSetting(), current.ticket, order);
  if (!alive(current) || current.loaded !== loaded) {
    return;
  }
  current.host.post((got.ok ? { type: "answer", seq, answer: got.value } : { type: "answer", seq, error: got.error }) satisfies ToWorkflow);
  if (got.ok && got.value.source_sha !== loaded.source_sha) {
    scheduleCheck(current);
  }
}

async function save(current: PanelState, order: PlanOrder, expect: string): Promise<void> {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  if (expect !== loaded.source_sha) {
    // 画面が別の読み込みのハッシュを送ってきた（読み直しと行き違った）。書かない
    fail(current, "提案を再読み込みしたため、この保存は取りやめました。再読み込みした提案で線を引き直してください");
    return;
  }
  const log = diaglog.get("ccnavi-board", root(current));
  current.saving = true;
  current.wroteAt = Date.now();
  const result = await runPlanOrderWrite(root(current), binSetting(), current.ticket, order, expect);
  current.saving = false;
  current.wroteAt = Date.now();
  log.info("計画編集の保存", { screen: SCREEN, ticket: current.ticket, result: result.kind });
  // 書けたら、タブを閉じていても書いたあとのハッシュを覚える（保存を押してすぐ閉じても、承認の注意が効く）
  const sha = savedShaOf(result);
  if (sha !== undefined) {
    rememberSaved(current.ticket, sha);
  }
  if (!alive(current) || current.loaded !== loaded) {
    return;
  }
  switch (result.kind) {
    case "written": {
      const moved = result.answer.children.filter((child) => child.ticket !== child.new_ticket).length;
      await reload(current);
      vscode.window.showInformationMessage(
        `${current.ticket} の計画の順序を提案に反映しました${moved === 0 ? "" : `（子の提案 ${moved} 本の識別子と phase: を付け替えました）`}。` +
          "承認の画面はボードから開き直してください。変更した提案はコミットしていません",
      );
      return;
    }
    case "unchanged":
      await reload(current);
      vscode.window.showInformationMessage(`${current.ticket} の計画は振り直しても変わらないので、反映しませんでした`);
      return;
    case "changed": {
      current.wroteAt = 0;
      const fresh = await runPlanOrder(root(current), binSetting(), current.ticket);
      if (!alive(current)) {
        return;
      }
      fail(current, "提案が外で変わっていたため、反映しませんでした（何も変更していません）");
      current.changed = fresh.ok ? fresh.value : result.answer;
      current.host.post({ type: "changed", answer: current.changed } satisfies ToWorkflow);
      return;
    }
    case "refused":
      current.wroteAt = 0;
      fail(current, `反映できませんでした: ${result.reason}`);
      return;
    case "error":
      current.wroteAt = 0;
      log.error("計画編集の保存に失敗", { screen: SCREEN });
      fail(current, result.error);
      return;
    default: {
      const unhandled: never = result;
      void unhandled;
      return;
    }
  }
}
