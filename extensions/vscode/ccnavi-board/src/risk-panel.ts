/**
 * リスク管理画面の Webview パネル。生成・更新・破棄、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。docs/design/manual-checks.md の手動確認の手順で確かめる。
 *
 * 画面は React（`src/webview/risk/`）で、ここが渡すのは「いま何を見せるか」（`RiskData`）だけ。
 * 渡し方は `core/screen-host.ts` の `retainedHost` が決める。この画面は編集の途中を持つので
 * `retainContextWhenHidden` が真で、入れ物（HTML）は 1 度しか入らない。入れ直すと画面が作り直され、打ちかけの編集が消えるため。
 * 中身を渡すのは、画面の編集を破棄してよいときだけ（ユーザが「更新」を押した、保存や作成が通った）。
 * ファイルが外で変わっただけのときは `changed` を送り、破棄するかどうかはユーザが決める。
 *
 * 対象は 3 種。共通の設定の配点（`.ccnavi/config/common/risks.yml`。場所は固定）、
 * ワークスペースの設定の配点（既定 `.ccnavi/config/project/risks.yml`）、プロジェクト 1 つの設定の配点
 * （既定 `projects/<名前>/.ccnavi/config/project/risks.yml`）。タブは 1 枚だけで、別の対象を開くとそのタブの
 * 中身を入れ替える（未保存の変更があれば、破棄して切り替えるかを聞く）。
 * 設定ファイルの場所は実行ファイルが解いたもの（`--explain --json` の `layers[].risk`）を使い、拡張は組まない。
 * 判定は共通の設定と、親の `project:` が指す設定の和で行う。編集する 1 本とは別に、読み取り専用の統合結果
 * （共通の設定 + ワークスペース、共通の設定 + 各プロジェクト。`sums[]`）を出す。統合結果は実行ファイルが出した結果で、拡張は合成しない。
 *
 * チケット制御が disable のワークスペースでは開かない。配点は子チケットを閉じるときにしか
 * 読まれないので、disable の間は何も動かさない。入口（サイドパネル・コマンドパレット）も同じキーで隠れる。
 *
 * 検証は実行ファイルに任せる。編集中の内容は一時ファイルに書き、`--lint` に渡す。共通の設定は `--risk <パス>`、ワークスペースの設定は `--project-risk-file self=<パス>`、
 * プロジェクトは `--project-risk-file <名前>=<パス>`（レイヤーの配点は共通の設定と合わせて検証される）。
 * 保存は、検証（`--lint`）を通り、作業中のチケットが無く（共通の設定とワークスペースの設定はどのツリーでも、
 * プロジェクトの設定はそのプロジェクトの分だけ。配点は子を閉じるときに読まれるので、走っている最中に変えない）、
 * ファイルが外で変わっていないときだけ行う。
 * ファイルが無いのは「設定が無い」正常な状態。共通の設定にもワークスペースの設定にも無いときは、組み込みの配点を読み取り専用で見せ、
 * 「作る」で同じ値のファイルを書き出してから直す。それ以外（プロジェクトの設定など）は空として出し、欄は触れて、
 * 検証を通った最初の保存でファイルを作る。
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import { followAppearance, postAppearance, readAppearance } from "./appearance.js";
import { loadBoard, runLint, type LintOverride } from "./ccnavi.js";
import { LAYER_SELF, projectLayer, selfLayer } from "./core/layers.js";
import { loadingText } from "./core/loading-render.js";
import { warnLinesOf } from "./core/lintmodel.js";
import { lockFromBoard, lockFromError, type Lock } from "./core/lock.js";
import { asRiskForm, BUILTIN_RISK_TEXT, readRisk, type RiskDocument } from "./core/risk-doc.js";
import { renderRiskPage } from "./core/risk-render.js";
import type { RiskData, RiskForm, RiskMessage, ToRisk } from "./core/risk-view.js";
import { retainedHost, type ScreenHost } from "./core/screen-host.js";
import type { HandoffEntry } from "./core/handoff-entry.js";
import { openHandoffFrom, readHandoffEntry } from "./handoff-entry.js";
import type { RiskTarget } from "./core/screens.js";
import { riskSums, type RiskSum } from "./core/sums.js";
import { targetOptions, type TargetOption } from "./core/targets.js";
import { showLoading } from "./loading.js";
import { assertRetained, watchPattern } from "./panel-common.js";
import { settingsPanel, type SettingsState } from "./settings-panel.js";
import { requireTickets } from "./ticket-control.js";
import { markTourSeen, tourSeen } from "./tour.js";
import { webviewScript, webviewStyle } from "./webview-asset.js";
import { confirmDuringTurn, confirmIfTurn } from "./turn-confirm.js";

const DEFAULT_RISK = ".ccnavi/config/common/risks.yml";
/** 画面の名前。バンドルのパスは `src/webview/<名前>/main.tsx` → `out/webview/<名前>.js`、`style.css` → `<名前>.css` */
const SCREEN = "risk";
const TITLE = "ccnavi リスク管理";

interface Loaded {
  /** 読んだ対象。往復の間に切り替わったかを `stale` が見る */
  readonly target: RiskTarget;
  /** ファイルの本文。無ければ、組み込みの配点を見せるときはその本文、そうでなければ空 */
  readonly text: string;
  readonly exists: boolean;
  /** 組み込みの配点を読み取り専用で見せている（「作る」だけができる） */
  readonly builtin: boolean;
  /** 無いときは 0 */
  readonly mtimeMs: number;
  readonly doc: RiskDocument;
  readonly riskPath: string;
  /** ワークスペースルートからの相対で見せるパス */
  readonly riskRel: string;
  /** 上部に出す注意。実行ファイルがこの設定を読めていない、など */
  readonly notices: readonly string[];
  /** 取り込み待ち画面を開けるか */
  readonly handoff: HandoffEntry;
  /** 切り替えられる対象（共通・ワークスペース・設定のあるプロジェクト） */
  readonly targets: readonly TargetOption[];
  /** 読み取り専用の統合結果（実行ファイルの `sums[]`） */
  readonly sums: readonly RiskSum[];
}

type PanelState = SettingsState<RiskData, RiskTarget, Loaded>;

/** 開いているパネル。種類ごとに 1 枚 */
let state: PanelState | undefined;

function sameTarget(a: RiskTarget, b: RiskTarget): boolean {
  return a.kind === b.kind && (a.kind !== "project" || (b.kind === "project" && a.name === b.name));
}

/** 保存を止めるチケットを絞るプロジェクト。共通の設定とワークスペースの設定は絞らない（どのツリーの子を閉じるときにも読まれる） */
function projectOf(target: RiskTarget): string | undefined {
  return target.kind === "project" ? target.name : undefined;
}

/** 確認の文で使う対象の名前。「設定」欄の表示（core/targets.ts の label）と揃える */
function targetLabelOf(target: RiskTarget): string {
  switch (target.kind) {
    case "workspace":
      return "共通の設定";
    case "self":
      return "ワークスペース";
    case "project":
      return `プロジェクト ${target.name}`;
  }
}

/** 読み込み中の一言で「何を」読んでいるか */
function whatOf(target: RiskTarget): string {
  switch (target.kind) {
    case "workspace":
      return "リスク";
    case "self":
      return "ワークスペースの設定のリスク";
    case "project":
      return `${target.name} のリスク`;
  }
}

/** 開いている対象の欄の値（`targets.ts` の `kind` と `name`） */
function currentKey(target: RiskTarget): { kind: string; name: string } {
  return { kind: target.kind, name: target.kind === "project" ? target.name : "" };
}

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

/** `ccnaviBoard.openRisk` の本体。引数なしは共通の設定の配点 */
export async function openRisk(target: RiskTarget = { kind: "workspace" }): Promise<void> {
  if (!requireTickets("リスク管理画面")) {
    return;
  }
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    vscode.window.showInformationMessage("ワークスペースが開かれていないため、リスク管理画面を表示できません");
    return;
  }
  if (state !== undefined) {
    state.panel.reveal(state.panel.viewColumn);
    if (!sameTarget(state.target, target)) {
      await switchTarget(state, target);
    }
    return;
  }

  // 画面と CSS はバンドルしたものを読んで流し込む。無ければ開かずに言う（パネルだけ出しても白いまま）
  try {
    webviewScript(SCREEN);
    webviewStyle(SCREEN);
  } catch (error) {
    vscode.window.showErrorMessage(`リスク管理画面を表示できません: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }

  // タブは読む前に作る。設定ファイルの場所を実行ファイルに聞く間に、押しても何も起きないように見えるのを避けるため。
  // `state` を先に設定するので、読んでいる間に押し直しても上の `reveal` に入る。
  // 読めなかったときもタブは閉じず、中にエラーを出す（`reload` の `showError`）
  const panel = vscode.window.createWebviewPanel("ccnaviRisk", TITLE, vscode.ViewColumn.One, {
    enableScripts: true,
    enableForms: false,
    localResourceRoots: [],
    // 編集の途中を持つので、タブを非表示にしても破棄しない。
    retainContextWhenHidden: true,
  });
  showLoading(panel, TITLE, SCREEN, whatOf(target));
  const current: PanelState = {
    target,
    panel,
    folder,
    tmpDir: fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-risk-")),
    host: riskHost(panel, folder.uri.fsPath),
    fileWatchers: [],
    watchers: [],
    lock: lockFromError("確認中…"),
    changedPending: false,
    wroteAt: 0,
    dirty: false,
    seq: 0,
    asking: false,
  };
  state = current;
  followAppearance(panel, current.host);
  registerPanelHandlers(current);
  await reload(current);
}

async function readPage(root: string, target: RiskTarget): Promise<Loaded> {
  const notices: string[] = [];
  // 切り替えの一覧と統合結果のために、共通の設定でも実行ファイルに聞く。読めなくても共通の設定は開けるので、一覧と統合結果が縮むだけ
  const board = await loadBoard(root, binSetting());
  const targets = targetOptions(board.ok ? board.board : undefined, "workspace", currentKey(target));
  const sums = riskSums(board.ok ? board.board : undefined);
  let riskRel: string;
  let riskPath: string;
  /** 共通の設定とワークスペースの設定の、もう一方の配点のファイル。組み込みの配点を見せるかどうかの判断に使う */
  let other: string | undefined;
  if (target.kind === "workspace") {
    // 共通の設定の場所は `.ccnavi/config/common/` 固定で、env（`CCNAVI_RISK` など）では動かせない。
    riskRel = DEFAULT_RISK;
    riskPath = resolveIn(root, riskRel);
    const self = board.ok ? selfLayer(board.board) : undefined;
    other = self === undefined || self.risk.path === "" ? undefined : resolveIn(root, self.risk.path);
  } else {
    // 設定ファイルの場所は実行ファイルに聞く。`.ccnavi` から自分で組むと、組み方が実行ファイルと
    // 食い違ったときに、この画面で保存した配点が判定に使われなくなる。答えは元リポジトリの版。
    if (!board.ok) {
      throw new Error(`設定ファイルの場所を実行ファイルから取得できません: ${board.error}`);
    }
    const layer = target.kind === "self" ? selfLayer(board.board) : projectLayer(board.board, target.name);
    if (layer === undefined || layer.risk.path === "") {
      throw new Error(
        target.kind === "self"
          ? "ccnavi の出力にワークスペースの設定がありません"
          : `プロジェクト ${target.name} は設定の対象になっていません（プロジェクトのフォルダの直下にないか、名前が予約名の common か self です）`,
      );
    }
    riskPath = resolveIn(root, layer.risk.path);
    riskRel = path.relative(root, riskPath).split(path.sep).join("/");
    if (target.kind === "self") {
      other = resolveIn(root, DEFAULT_RISK);
    }
    if (layer.risk.unreadable !== "") {
      notices.push(`実行ファイルはこのファイルを読み込めず、この設定を空として扱っています（このファイルの配点は 1 件も数えられていません）: ${layer.risk.unreadable}`);
    }
  }
  let text: string;
  let mtimeMs: number;
  let exists: boolean;
  try {
    text = fs.readFileSync(riskPath, "utf8");
    mtimeMs = fs.statSync(riskPath).mtimeMs;
    exists = true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      throw new Error(`配点のファイルを読み込めません（${riskRel}）: ${(error as Error).message}`);
    }
    // 無いのは不備ではない（「設定が無い」正常な状態）。
    text = "";
    mtimeMs = 0;
    exists = false;
  }
  // 共通の設定にもワークスペースの設定にも無ければ、適用されているのは組み込みの配点。共通かワークスペースの設定を開いたときだけ、
  // それを読み取り専用で見せ、「作る」で同じ値のファイルを書き出させる。もう一方が分からないとき（ボードを読めない）も組み込みを見せる。
  const builtin = !exists && (target.kind === "workspace" || target.kind === "self") && (other === undefined || !fs.existsSync(other));
  if (builtin) {
    text = BUILTIN_RISK_TEXT;
  }
  // 無いときの苦情（version が無い、など）は画面に出さない。無いことは不備ではない。
  const parsed = readRisk(text);
  const doc = exists || builtin ? parsed : { apply: parsed.apply, model: { ...parsed.model, problems: [] } };
  // 取り込み待ち画面を開けるかは、設定の読み込みとは別に確かめる（古い実行ファイルでは入口だけを無効にする）
  const handoff = await readHandoffEntry(root, binSetting());
  return { target, text, exists, builtin, mtimeMs, doc, riskPath, riskRel, notices, targets, sums, handoff };
}

function resolveIn(root: string, filePath: string): string {
  return path.isAbsolute(filePath) ? filePath : path.join(root, filePath);
}

/**
 * 配点のファイルと設定ファイルが変わったら「ファイルの変更を検知しました」と伝える。自分の保存は除く。
 * 対象のパスは設定で変わるので、再読み込みのたびに張り直す。絶対パスはワークスペース相対の glob に
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
  current.fileWatchers.push(...patterns.map((pattern) => watchPattern(pattern, () => scheduleChanged(current))));
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

/** この画面の仕事を、3 画面で共通の段取り（`settings-panel.ts`）に渡す */
const { registerPanelHandlers, scheduleChanged, reload, redraw, switchTarget } = settingsPanel<RiskData, RiskTarget, Loaded, ToRisk>({
  accepts: true,
  alive,
  release: (current) => {
    if (state === current) {
      state = undefined;
    }
  },
  handleMessage: (current, message) => void handleMessage(current, asMessage(message)),
  read: readPage,
  show,
  showError,
  watchFiles,
  refreshLock,
  loading: (current) => ({ kind: "loading", text: loadingText(whatOf(current.target)) }),
  sameTarget,
  targetLabel: targetLabelOf,
});

/**
 * 保存できるかを実行ファイルに聞く。確かめられなければ閉じる側。
 * 共通の設定とワークスペースの設定の配点は、どのツリーの子を閉じるときにも読まれるので、どのツリーの doing でも止める。
 * プロジェクトの設定の配点は、そのプロジェクトの doing だけを見る。
 */
async function refreshLock(current: PanelState): Promise<Lock> {
  const result = await loadBoard(current.folder.uri.fsPath, binSetting());
  const lock = result.ok ? lockFromBoard(result.board, projectOf(current.target)) : lockFromError(result.error);
  if (alive(current)) {
    current.lock = lock;
    current.host.post({ type: "lock", lock } satisfies ToRisk);
  }
  return lock;
}

/**
 * いま見せるものを渡す。画面の編集はここで破棄されるので、呼ぶのはユーザが「更新」を押した
 * ときと、保存・作成が通って中身が入れ替わったときだけ。
 */
function show(current: PanelState): void {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  current.error = undefined;
  // 読み直したので、「ファイルの変更を検知しました」はもう今のことではない
  current.changedPending = false;
  current.host.send({
    kind: "page",
    page: {
      root: current.folder.uri.fsPath,
      riskPath: loaded.riskRel,
      exists: loaded.exists,
      builtin: loaded.builtin,
      model: loaded.doc.model,
      lock: current.lock,
      notices: loaded.notices,
      handoff: loaded.handoff,
      target: currentKey(loaded.target),
      targets: loaded.targets,
      sums: loaded.sums,
    },
  });
}

/** 読み直せなかったことを見せる。理由は覚えておく（渡せなかったときに `ready` で渡し直すため） */
function showError(current: PanelState, error: string): void {
  current.loaded = undefined;
  current.error = error;
  const target = currentKey(current.target);
  current.host.send({ kind: "error", error, target, targets: targetOptions(undefined, "workspace", target) });
}

/**
 * リスク管理の画面に渡す手段。VS Code のパネルを `retainedHost` の形に合わせる。
 * 入れ物は 1 度しか入らないので、表示の有無は渡さない（保持する画面は非表示でも生きている）。
 * パネルの `retainContextWhenHidden` を偽に変えると、送った先が破棄されていても気づけなくなる。
 * 型では止まらないので、ここで見て言う。
 */
function riskHost(panel: vscode.WebviewPanel, root: string): ScreenHost<RiskData> {
  assertRetained(panel, root, "risk");
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
 * 保存は実行ファイルへ 2 度出る（`--lint` と錠の取り直し）。その間にユーザが「更新」を押せば、
 * 画面の編集は破棄され、新しい中身が出ている。そこへ古い編集を書くと、破棄したはずのものが
 * ファイルに入る。 読み直されていたら、この保存はもう無かったことにする。
 */
function stale(current: PanelState, loaded: Loaded): boolean {
  if (current.loaded === loaded) {
    return false;
  }
  // 往復の間に別の対象へ切り替わった。破棄するのは同じだが、切り替え先の画面に前の対象の話を出さない
  if (!sameTarget(loaded.target, current.target)) {
    return true;
  }
  fail(current, "画面を更新したので、この保存は取りやめました。更新後の配点で編集し直してください");
  return true;
}

/** 操作の結果の一言。1 枚目を読み込んでいる間だけ届かずに破棄される（非表示になっていても届く） */
function fail(current: PanelState, message: string): void {
  current.host.post({ type: "failed", message } satisfies ToRisk);
}

async function handleMessage(current: PanelState, message: RiskMessage | undefined): Promise<void> {
  if (message === undefined || !alive(current)) {
    return;
  }
  if (message.type === "dirty") {
    current.dirty = message.dirty;
    return;
  }
  if (message.type === "ready") {
    // 組み上がったばかりの画面は必ず「変更なし」で始まる（作り直された画面は前の編集を持たない）
    current.dirty = false;
    // 画面が組み上がった。1 枚目を読み込んでいる間に見送った中身は、ここで渡る。
    // 見送るものが無くても渡し直す（同じ中身がもう 1 度届く）。VS Code が画面を作り直す経路
    // （`Developer: Reload Webviews`）では、入れてある HTML の中身が古いことがあるため
    current.host.ready();
    redraw(current);
    postAppearance(current.host);
    // 初回だけ吹き出しの案内を頼む。画面は指す先が出てから始め、閉じたら `tourDone` を返す。
    // 閉じずにタブを閉じたら見た記録は残らないので、次に開いたときにもう 1 度出る
    if (!tourSeen(SCREEN)) {
      current.host.post({ type: "tour" } satisfies ToRisk);
    }
    return;
  }
  if (message.type === "tourDone") {
    markTourSeen(SCREEN);
    return;
  }
  // 読み直せていない画面では、配点にヒットする操作はどれも行き先が無い（「更新」は
  // 押せるが、その経路は `reload` が読み直しからやり直す）
  if (current.loaded === undefined && message.type !== "reload") {
    return;
  }
  switch (message.type) {
    case "reload": {
      if (message.dirty) {
        const choice = await vscode.window.showWarningMessage(
          "未保存の変更があります。破棄して更新しますか？",
          { modal: true },
          "更新",
        );
        if (choice !== "更新") {
          // 画面は「更新」を押した時点で欄を止めている。やめたことを伝えないと止まったままになる
          // 問いを出している間にパネルを閉じられるので、送る前に生きているかを見る
          if (alive(current)) {
            current.host.post({ type: "cancelled" } satisfies ToRisk);
          }
          return;
        }
      }
      await reload(current);
      return;
    }
    case "openHandoff": {
      // 設定のプロジェクトに合わせて、そのプロジェクトのツリーの下書きを開く
      await openHandoffFrom(current.loaded?.handoff, current.loaded?.target ?? current.target);
      return;
    }
    case "switchTarget": {
      // 一覧にある対象だけを受ける。画面が古いまま、消えたプロジェクトを指していても開かない
      const option = (current.loaded?.targets ?? targetOptions(undefined, "workspace", currentKey(current.target))).find((t) => t.kind === message.kind && t.name === message.name);
      if (option === undefined) {
        return;
      }
      const target: RiskTarget =
        option.kind === "project" ? { kind: "project", name: option.name } : option.kind === "self" ? { kind: "self" } : { kind: "workspace" };
      if (!sameTarget(current.target, target)) {
        await switchTarget(current, target);
      }
      return;
    }
    case "openFile": {
      if (current.loaded === undefined) {
        return;
      }
      const target = current.loaded.riskPath;
      void vscode.workspace.openTextDocument(target).then(
        (document) => vscode.window.showTextDocument(document),
        () => vscode.window.showInformationMessage(`ファイルを開けませんでした: ${target}`),
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

/** 組み込みと同じ値のファイルを書き出す。既にあれば上書きしない（値は同じなので数え方は変わらない） */
async function create(current: PanelState): Promise<void> {
  const loaded = current.loaded;
  if (loaded === undefined || !loaded.builtin) {
    return;
  }
  if (fs.existsSync(loaded.riskPath)) {
    fail(current, `${loaded.riskRel} は既に存在するため、上書きしません。更新してください`);
    return;
  }
  if (!(await confirmIfTurn(current.folder.uri.fsPath, binSetting()))) {
    fail(current, "ターン中のため作成を取りやめました。ターンが終わってから作成してください。");
    return;
  }
  if (!alive(current)) {
    return;
  }
  try {
    fs.mkdirSync(path.dirname(loaded.riskPath), { recursive: true });
    current.wroteAt = Date.now();
    fs.writeFileSync(loaded.riskPath, BUILTIN_RISK_TEXT, { encoding: "utf8", flag: "wx" });
  } catch (error) {
    // 書けなかったのに猶予を残したままだと、その間の実際の外部変更が知らされない。
    current.wroteAt = 0;
    fail(current, `${loaded.riskRel} に保存できません: ${(error as Error).message}`);
    return;
  }
  await reload(current);
  vscode.window.showInformationMessage(`${loaded.riskRel} を組み込みの配点で作りました。コミットは自分でしてください`);
}

/** 保存の前の検証に渡す差し替え。共通の設定は `--risk`、ほかは名前を付けて `--project-risk-file` */
function overrideFor(target: RiskTarget, tmp: string): LintOverride {
  switch (target.kind) {
    case "workspace":
      return { kind: "risk", path: tmp };
    case "self":
      return { kind: "layerRisk", name: LAYER_SELF, path: tmp };
    case "project":
      return { kind: "layerRisk", name: target.name, path: tmp };
  }
}

async function save(current: PanelState, form: RiskForm): Promise<void> {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  if (loaded.builtin) {
    fail(current, `${loaded.riskRel} がありません。先に「組み込みの配点でファイルを作る」を押してください`);
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
    fail(current, `編集中の内容を保存できません: ${(error as Error).message}`);
    return;
  }

  // 1. 検証。error が 1 件でもあれば保存しない。
  const lint = await runLint(root, binSetting(), overrideFor(loaded.target, tmp));
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
    // 苦情は渡した一時ファイルのパスで出るので、画面では対象のファイルのパスに直す。
    fail(current, `設定の検証（--lint）でエラーが見つかりました。内容を修正してから保存してください:\n${lint.value.report.split(tmp).join(loaded.riskRel)}`);
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
  if (!(await confirmDuringTurn(lock))) {
    fail(current, "ターン中のため保存を取りやめました。ターンが終わってから保存してください。");
    return;
  }
  if (!alive(current)) {
    return;
  }

  // 3. 読み込んでから外で変わっていないこと。無かったファイルは、まだ無いこと。
  if (loaded.exists) {
    let mtimeMs: number;
    try {
      mtimeMs = fs.statSync(loaded.riskPath).mtimeMs;
    } catch (error) {
      fail(current, `配点のファイルを確認できません: ${(error as Error).message}`);
      return;
    }
    if (mtimeMs !== loaded.mtimeMs) {
      fail(current, "配点のファイルは、読み込んだあとに画面の外で変更されています。更新してから編集し直してください（この変更は上書きしません）");
      return;
    }
  } else if (fs.existsSync(loaded.riskPath)) {
    fail(current, "配点のファイルは、読み込んだあとに画面の外で作られています。更新してから編集し直してください（上書きしません）");
    return;
  }

  try {
    current.wroteAt = Date.now();
    if (loaded.exists) {
      fs.writeFileSync(loaded.riskPath, text, "utf8");
    } else {
      fs.mkdirSync(path.dirname(loaded.riskPath), { recursive: true });
      fs.writeFileSync(loaded.riskPath, text, { encoding: "utf8", flag: "wx" });
    }
  } catch (error) {
    current.wroteAt = 0;
    fail(current, `配点のファイルに保存できません: ${(error as Error).message}`);
    return;
  }
  await reload(current);
  // warn の本文は画面上部の帯に出す（通知は件数だけ）。中身（data）を渡したあとに送る
  const warns = warnLinesOf(lint.value.report, tmp, loaded.riskRel);
  if (warns.length > 0 && alive(current)) {
    current.host.post({ type: "saved", warns } satisfies ToRisk);
  }
  const tail = lint.value.report.split("\n").filter((l) => l.trim() !== "").pop() ?? "";
  vscode.window.showInformationMessage(`${loaded.riskRel} に保存しました（${tail}）`);
}

function asMessage(message: unknown): RiskMessage | undefined {
  if (typeof message !== "object" || message === null) {
    return undefined;
  }
  const m = message as { type?: unknown; dirty?: unknown; form?: unknown; kind?: unknown; name?: unknown };
  switch (m.type) {
    case "switchTarget":
      return typeof m.kind === "string" && typeof m.name === "string" ? { type: "switchTarget", kind: m.kind, name: m.name } : undefined;
    case "reload":
      return { type: "reload", dirty: m.dirty === true };
    case "dirty":
      return typeof m.dirty === "boolean" ? { type: "dirty", dirty: m.dirty } : undefined;
    case "ready":
    case "openFile":
    case "create":
    case "tourDone":
    case "openHandoff":
      return { type: m.type };
    case "save": {
      const form = asRiskForm(m.form);
      return form === undefined ? undefined : { type: "save", form };
    }
    default:
      return undefined;
  }
}
