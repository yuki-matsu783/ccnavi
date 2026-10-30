/**
 * フェーズ管理画面の Webview パネル。生成・更新・破棄、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。docs/design/manual-checks.md の手動確認の手順で確かめる。
 *
 * 画面は React（`src/webview/phases/`）で、ここが渡すのは「いま何を見せるか」（`PhasesData`）だけ。
 * 渡し方は `core/screen-host.ts` の `retainedHost` が決める。この画面は編集の途中を持つので
 * `retainContextWhenHidden` が真で、入れ物（HTML）は 1 度しか入らない。入れ直すと画面が作り直され、打ちかけの編集が消えるため。
 * 中身を渡すのは、画面の編集を破棄してよいときだけ（ユーザが「更新」を押した、保存が通った）。
 *
 * 対象は 3 種。共通の設定の定義（`.ccnavi/config/common/phases.yml`。場所は固定）、ワークスペースの設定の定義
 * （既定 `.ccnavi/config/project/phases.yml`）、プロジェクト 1 つの設定の定義（既定 `projects/<名前>/.ccnavi/config/project/phases.yml`）。
 * 判定に使われるのは、共通の設定と、親チケットの `project:` が指す設定の和。共通の設定を開いたときは、編集する定義がそのまま
 * 共通の定義になるので、読み取り専用の欄（実行ファイルの統合結果のうち出どころが共通のもの）は出さない。
 * タブは 1 枚だけで、別の対象を開くとそのタブの中身を入れ替える（未保存の変更があれば、破棄して切り替えるかを聞く）。
 * 設定ファイルの場所は実行ファイルが解いたもの（`--explain --json` の `layers[].phases_file`）を使い、拡張は組まない。
 *
 * 検証は実行ファイルに任せる。編集中の内容は一時ファイルに書き、`--lint --project-phases-file <名前>=<パス>`
 * （ワークスペースの設定は名前が `self`）、共通の設定は `--phases <パス>` で渡す。実行ファイルは共通の設定と足した形で確かめる（同じ id で中身が違う、
 * 表示名の重なり、定義の形）。
 * 保存は、検証（`--lint`）を通り、作業中のチケットが無く、ファイルが外で変わっていないときだけ行う。
 * 作業中のチケットは、共通の設定とワークスペースの設定ならどのツリーでも、プロジェクトの設定ならそのプロジェクトの分だけを見る
 * （定義は承認・着手・閉じるときに読まれるので、走っている最中に変えない）。
 * ファイルが無いのは「設定が無い」正常な状態で、帯は出さない。欄は触れ、検証を通った最初の保存でファイルを作る（雛形は置かない）。
 * 定義の無いファイル（`phases: {}`）は実行ファイルが error にするので、先に書き出さない。
 * 組み込みの既定は無い（実行ファイルも持たない。既定を組み込むと、意図せずレビューの要否が決まる）。
 * 共通の設定の `phases.yml` を実行ファイルが読めないときは、共通の定義が空として扱われ、`--lint` が error で言う。画面は開き、その
 * error を上部の帯で出す。共通の設定を開いているときは、その定義を直して検証の通る形で保存できる（直さなければ、ほかの対象の `--lint` も error のまま）。
 *
 * チケット制御が disable のワークスペースでは、対象がどれでも開かない。定義は親チケットの計画と
 * 子の範囲にしか読まれないので、disable の間は何も動かさない。入口（サイドパネル・コマンドパレット）も同じキーで隠れる。
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import { followAppearance, postAppearance, readAppearance } from "./appearance.js";
import { loadBoard, runLint, type LintOverride } from "./ccnavi.js";
import { commonLayer, LAYER_SELF, projectLayer, selfLayer } from "./core/layers.js";
import { loadingText } from "./core/loading-render.js";
import { warnLinesOf } from "./core/lintmodel.js";
import { lockFromBoard, lockFromError, type Lock } from "./core/lock.js";
import { asPhasesForm, readPhases, type PhasesDocument } from "./core/phases-doc.js";
import { renderPhasesPage } from "./core/phases-render.js";
import type { CommonPhase, PhasesData, PhasesForm, PhasesMessage, ToPhases } from "./core/phases-view.js";
import { retainedHost, type ScreenHost } from "./core/screen-host.js";
import { commonPhases } from "./core/sums.js";
import { showLoading } from "./loading.js";
import type { HandoffEntry } from "./core/handoff-entry.js";
import { openHandoffFrom, readHandoffEntry } from "./handoff-entry.js";
import type { PhasesTarget } from "./core/screens.js";
import { targetOptions, type TargetOption } from "./core/targets.js";
import { lintSummary } from "./core/lint-summary.js";
import { assertRetained, watchPattern } from "./panel-common.js";
import { settingsPanel, type SettingsState } from "./settings-panel.js";
import { requireTickets } from "./ticket-control.js";
import { markTourSeen, tourSeen } from "./tour.js";
import { webviewScript, webviewStyle } from "./webview-asset.js";
import { confirmDuringTurn } from "./turn-confirm.js";

const DEFAULT_PHASES = ".ccnavi/config/project/phases.yml";
/** 共通の設定の定義の場所。ルール管理・リスク管理と同じく固定で、env では動かせない */
const DEFAULT_COMMON_PHASES = ".ccnavi/config/common/phases.yml";
/** 画面の名前。バンドルのパスは `src/webview/<名前>/main.tsx` → `out/webview/<名前>.js`、`style.css` → `<名前>.css` */
const SCREEN = "phases";
const TITLE = "ccnavi フェーズ管理";

/** ワークスペースかプロジェクトの設定のファイルを最初の保存で作るときに、先頭へ置く説明 */
const LAYER_HEADER = [
  "# このレイヤーのフェーズ定義。親チケットの project: がこのレイヤーを指すときに、共通レイヤーの定義に追加して使われる。",
  "# 共通レイヤーと同じ id で中身が違う定義や、title が重なる定義は --lint が error にし、このファイルのその定義だけが使われない。",
  "",
].join("\n");
/** 共通の設定のファイルを最初の保存で作るときに、先頭へ置く説明 */
const COMMON_HEADER = [
  "# フェーズ定義（共通レイヤー）。判定は、このファイルと親チケットの project: が指すレイヤーの phases.yml の統合結果で使う。",
  "# 同じ id で中身が違う定義や、title が重なる定義は --lint が error にする。",
  "",
].join("\n");

interface Loaded {
  /** 読んだ対象。往復の間に切り替わったかを `stale` が見る */
  readonly target: PhasesTarget;
  /** ファイルの本文。無ければ空 */
  readonly text: string;
  readonly exists: boolean;
  /** 無いときは 0 */
  readonly mtimeMs: number;
  readonly doc: PhasesDocument;
  readonly phasesPath: string;
  /** ワークスペースルートからの相対で見せるパス */
  readonly phasesRel: string;
  /** 上部に出す注意。実行ファイルがこの設定を読めていない、など */
  readonly notices: readonly string[];
  /** 取り込み待ち画面を開けるか */
  readonly handoff: HandoffEntry;
  /** 上部に error の帯で出す文。共通の設定の phases.yml を読めない、など */
  readonly errors: readonly string[];
  /** 共通の設定の定義（読み取り専用）。実行ファイルの統合結果のうち出どころが共通のもの */
  readonly common: readonly CommonPhase[];
  /** 切り替えられる対象（共通の設定・ワークスペース・設定のあるプロジェクト） */
  readonly targets: readonly TargetOption[];
}

type PanelState = SettingsState<PhasesData, PhasesTarget, Loaded>;

/** 開いているパネル。種類ごとに 1 枚 */
let state: PanelState | undefined;

function sameTarget(a: PhasesTarget, b: PhasesTarget): boolean {
  return a.kind === b.kind && (a.kind !== "project" || (b.kind === "project" && a.name === b.name));
}

/** 保存を止めるチケットを絞るプロジェクト。共通の設定とワークスペースの設定は絞らない（どのツリーの doing でも止める） */
function projectOf(target: PhasesTarget): string | undefined {
  return target.kind === "project" ? target.name : undefined;
}

/** 確認の文で使う対象の名前。「設定」欄の表示（core/targets.ts の label）と揃える */
function targetLabelOf(target: PhasesTarget): string {
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
function whatOf(target: PhasesTarget): string {
  switch (target.kind) {
    case "workspace":
      return "共通の設定のフェーズ";
    case "self":
      return "ワークスペースの設定のフェーズ";
    case "project":
      return `${target.name} のフェーズ`;
  }
}

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

/** `ccnaviBoard.openPhases` の本体。引数なしはワークスペースの設定の定義 */
export async function openPhases(target: PhasesTarget = { kind: "self" }): Promise<void> {
  if (!requireTickets("フェーズ管理画面")) {
    return;
  }
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    vscode.window.showInformationMessage("ワークスペースが開かれていないため、フェーズ管理画面を表示できません");
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
    vscode.window.showErrorMessage(`フェーズ管理画面を表示できません: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }

  // タブは読む前に作る。設定ファイルの場所を実行ファイルに聞く間に、押しても何も起きないように見えるのを避けるため。
  // `state` を先に設定するので、読んでいる間に押し直しても上の `reveal` に入る。
  // 読めなかったときもタブは閉じず、中にエラーを出す（`reload` の `showError`）
  const panel = vscode.window.createWebviewPanel("ccnaviPhases", TITLE, vscode.ViewColumn.One, {
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
    tmpDir: fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-phases-")),
    host: phasesHost(panel, folder.uri.fsPath),
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

async function readPage(root: string, target: PhasesTarget): Promise<Loaded> {
  const notices: string[] = [];
  const errors: string[] = [];
  // 設定ファイルの場所は実行ファイルに聞く。`.ccnavi` から自分で組むと、組み方が食い違ったときに
  // この画面で保存した定義が承認と着手に反映されなくなる。答えは元リポジトリの版。
  const board = await loadBoard(root, binSetting());
  if (!board.ok) {
    throw new Error(`設定ファイルの場所を実行ファイルから取得できません: ${board.error}`);
  }
  const targets = targetOptions(board.board, "workspace", currentKey(target));
  let phasesPath: string;
  let phasesRel: string;
  if (target.kind === "workspace") {
    // 共通の設定の場所は `.ccnavi/config/common/` 固定。env（`--phases` を渡す設定など）では動かせない（リスク管理と同じ）。
    phasesRel = DEFAULT_COMMON_PHASES;
    phasesPath = resolveIn(root, phasesRel);
  } else {
    const layer = target.kind === "self" ? selfLayer(board.board) : projectLayer(board.board, target.name);
    if (layer === undefined || layer.phasesFile.path === "") {
      throw new Error(
        target.kind === "self"
          ? "ccnavi の出力にワークスペースの設定がありません"
          : `プロジェクト ${target.name} は設定の対象になっていません（プロジェクトのフォルダの直下にないか、名前が予約名の common か self です）`,
      );
    }
    phasesPath = resolveIn(root, layer.phasesFile.path);
    phasesRel = path.relative(root, phasesPath).split(path.sep).join("/");
    if (layer.phasesFile.unreadable !== "") {
      notices.push(`実行ファイルはこのファイルを読み込めず、この設定の定義を空として扱っています: ${layer.phasesFile.unreadable}`);
    }
  }
  // 共通の設定の phases.yml を実行ファイルが読めなければ、共通の定義は空として扱われ、--lint が error で言う。画面は開いたまま帯で知らせる。
  const commonFile = commonLayer(board.board);
  if (commonFile !== undefined && commonFile.phasesFile.unreadable !== "") {
    errors.push(`共通の設定の phases.yml を読み込めません（${commonFile.phasesFile.path}）。共通の定義は空として扱われています: ${commonFile.phasesFile.unreadable}`);
  }
  // 判定に使われる和は実行ファイルが組む（`sums[]`）。出どころが共通の定義だけを読み取り専用で見せ、拡張は足し直さない。
  // 共通の設定を開いているときは、編集する定義がそのまま共通の定義なので、読み取り専用の欄は出さない。
  const common =
    target.kind === "workspace"
      ? []
      : commonPhases(board.board.sums.find((s) => s.name === (target.kind === "self" ? LAYER_SELF : target.name)));
  let text: string;
  let mtimeMs: number;
  let exists: boolean;
  try {
    text = fs.readFileSync(phasesPath, "utf8");
    mtimeMs = fs.statSync(phasesPath).mtimeMs;
    exists = true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      throw new Error(`フェーズ定義のファイルを読み込めません（${phasesRel}）: ${(error as Error).message}`);
    }
    // 無いのは不備ではない（無い設定は空）。画面は空を見せ、定義を足して保存すればファイルが作られる。
    text = "";
    mtimeMs = 0;
    exists = false;
  }
  // 無いときの苦情（version が無い、phases が無い）は画面に出さない。
  const parsed = readPhases(text);
  const doc = exists ? parsed : { apply: parsed.apply, model: { ...parsed.model, problems: [] } };
  // 取り込み待ち画面を開けるかは、設定の読み込みとは別に確かめる（古い実行ファイルでは入口だけを無効にする）
  const handoff = await readHandoffEntry(root, binSetting());
  return { target, text, exists, mtimeMs, doc, phasesPath, phasesRel, notices, errors, common, targets, handoff };
}

function resolveIn(root: string, filePath: string): string {
  return path.isAbsolute(filePath) ? filePath : path.join(root, filePath);
}

/**
 * 定義のファイルと設定ファイルが変わったら「ファイルの変更を検知しました」と伝える。自分の保存は除く。
 * 対象のパスは設定で変わるので、再読み込みのたびに張り直す。絶対パスはワークスペース相対の glob に
 * ならないので、そのディレクトリを起点にする。
 */
function watchFiles(current: PanelState): void {
  for (const watcher of current.fileWatchers) {
    watcher.dispose();
  }
  current.fileWatchers = [];
  // 読めていないときの仮の場所。共通の設定を開いているなら共通の場所を見る（読めなかった後も、その設定の変化を拾うため）
  const phasesRel = current.loaded?.phasesRel ?? (current.target.kind === "workspace" ? DEFAULT_COMMON_PHASES : DEFAULT_PHASES);
  const patterns: vscode.RelativePattern[] = [
    patternFor(current.folder, phasesRel),
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
const { registerPanelHandlers, scheduleChanged, reload, redraw, switchTarget } = settingsPanel<PhasesData, PhasesTarget, Loaded, ToPhases>({
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
 * 共通の設定とワークスペースの設定の定義はどのツリーの doing でも止める（プロジェクト外のチケットだけに影響するが、絞らずに止める側にする）。
 * プロジェクトの設定は、そのプロジェクトのチケットにしか足されないので、そのプロジェクトの doing だけを見る。
 */
async function refreshLock(current: PanelState): Promise<Lock> {
  const result = await loadBoard(current.folder.uri.fsPath, binSetting());
  const lock = result.ok ? lockFromBoard(result.board, projectOf(current.target)) : lockFromError(result.error);
  if (alive(current)) {
    current.lock = lock;
    current.host.post({ type: "lock", lock } satisfies ToPhases);
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
      phasesPath: loaded.phasesRel,
      exists: loaded.exists,
      model: loaded.doc.model,
      lock: current.lock,
      notices: loaded.notices,
      handoff: loaded.handoff,
      errors: loaded.errors,
      common: loaded.common,
      target: { kind: loaded.target.kind, name: loaded.target.kind === "project" ? loaded.target.name : "" },
      targets: loaded.targets,
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

/** 開いている対象の欄の値（`targets.ts` の `kind` と `name`） */
function currentKey(target: PhasesTarget): { kind: string; name: string } {
  return { kind: target.kind, name: target.kind === "project" ? target.name : "" };
}

/**
 * フェーズ管理の画面に渡す手段。VS Code のパネルを `retainedHost` の形に合わせる。
 * 入れ物は 1 度しか入らないので、表示の有無は渡さない（保持する画面は非表示でも生きている）。
 * パネルの `retainContextWhenHidden` を偽に変えると、送った先が破棄されていても気づけなくなる。
 * 型では止まらないので、ここで見て言う。
 */
function phasesHost(panel: vscode.WebviewPanel, root: string): ScreenHost<PhasesData> {
  assertRetained(panel, root, "phases");
  return retainedHost<PhasesData>(
    {
      html(text: string): void {
        panel.webview.html = text;
      },
      post(message: unknown): void {
        void panel.webview.postMessage(message);
      },
    },
    (data) =>
      renderPhasesPage(data, {
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
  fail(current, "画面を更新したので、この保存は取りやめました。更新後の定義で編集し直してください");
  return true;
}

/** 操作の結果の一言。1 枚目を読み込んでいる間だけ届かずに破棄される（非表示になっていても届く） */
function fail(current: PanelState, message: string): void {
  current.host.post({ type: "failed", message } satisfies ToPhases);
}

async function handleMessage(current: PanelState, message: PhasesMessage | undefined): Promise<void> {
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
    // 初回だけ吹き出しの案内を頼む。画面は定義の中身が出てから始め、閉じたら `tourDone` を返す。
    // 閉じずにタブを閉じたら見た記録は残らないので、次に開いたときにもう 1 度出る
    if (!tourSeen(SCREEN)) {
      current.host.post({ type: "tour" } satisfies ToPhases);
    }
    return;
  }
  if (message.type === "tourDone") {
    markTourSeen(SCREEN);
    return;
  }
  // 読み直せていない画面では、定義にヒットする操作はどれも行き先が無い（「更新」は
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
          current.host.post({ type: "cancelled" } satisfies ToPhases);
          return;
        }
      }
      await reload(current);
      return;
    }
    case "openFile": {
      if (current.loaded === undefined) {
        return;
      }
      const target = current.loaded.phasesPath;
      void vscode.workspace.openTextDocument(target).then(
        (document) => vscode.window.showTextDocument(document),
        () => vscode.window.showInformationMessage(`ファイルを開けませんでした: ${target}`),
      );
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
      const target: PhasesTarget =
        option.kind === "project" ? { kind: "project", name: option.name } : option.kind === "workspace" ? { kind: "workspace" } : { kind: "self" };
      if (!sameTarget(current.target, target)) {
        await switchTarget(current, target);
      }
      return;
    }
    case "save": {
      await save(current, message.form);
      return;
    }
  }
}

/** 保存の前の検証に渡す差し替え。共通の設定は `--phases`、ほかは名前を付けて `--project-phases-file` */
function headerOf(target: PhasesTarget): string {
  return target.kind === "workspace" ? COMMON_HEADER : LAYER_HEADER;
}

function overrideFor(target: PhasesTarget, tmp: string): LintOverride {
  switch (target.kind) {
    case "workspace":
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
  const root = current.folder.uri.fsPath;
  let tmp: string;
  let text: string;
  try {
    // ファイルを初めて作るときは、先頭に説明を置く。検証にも同じ本文を掛ける。
    text = (loaded.exists ? "" : headerOf(loaded.target)) + loaded.doc.apply(form);
    tmp = path.join(current.tmpDir, "phases.yml");
    fs.writeFileSync(tmp, text, "utf8");
  } catch (error) {
    fail(current, `編集中の内容を保存できません: ${(error as Error).message}`);
    return;
  }

  // 1. 検証。error が 1 件でもあれば保存しない。承認済みの計画がこの定義で読めるかもここで分かる。
  //    実行ファイルは共通の設定と足した形で確かめる（同じ id で中身が違う、表示名の重なり）。
  const lint = await runLint(root, binSetting(), overrideFor(current.target, tmp));
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
    fail(current, `設定の検証（--lint）でエラーが見つかりました。内容を修正してから保存してください:\n${lint.value.report.split(tmp).join(loaded.phasesRel)}`);
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
    fail(current, "エージェントの応答中のため保存を取りやめました。応答が終わってから保存してください。");
    return;
  }
  if (!alive(current)) {
    return;
  }

  // 3. 読み込んでから外で変わっていないこと。無かったファイルは、まだ無いこと。
  if (loaded.exists) {
    let mtimeMs: number;
    try {
      mtimeMs = fs.statSync(loaded.phasesPath).mtimeMs;
    } catch (error) {
      fail(current, `フェーズ定義のファイルを確認できません: ${(error as Error).message}`);
      return;
    }
    if (mtimeMs !== loaded.mtimeMs) {
      fail(current, "フェーズ定義のファイルは、読み込んだあとに画面の外で変更されています。更新してから編集し直してください（この変更は上書きしません）");
      return;
    }
  } else if (fs.existsSync(loaded.phasesPath)) {
    fail(current, "フェーズ定義のファイルは、読み込んだあとに画面の外で作られています。更新してから編集し直してください（上書きしません）");
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
    fail(current, `フェーズ定義のファイルに保存できません: ${(error as Error).message}`);
    return;
  }
  await reload(current);
  // warn の本文は画面上部の帯に出す（通知は件数だけ）。中身（data）を渡したあとに送る
  const warns = warnLinesOf(lint.value.report, tmp, loaded.phasesRel);
  if (warns.length > 0 && alive(current)) {
    current.host.post({ type: "saved", warns } satisfies ToPhases);
  }
  vscode.window.showInformationMessage(`${loaded.phasesRel} に保存しました（${lintSummary(lint.value.report)}）`);
}

function asMessage(message: unknown): PhasesMessage | undefined {
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
    case "tourDone":
    case "openHandoff":
      return { type: m.type };
    case "save": {
      const form = asPhasesForm(m.form);
      return form === undefined ? undefined : { type: "save", form };
    }
    default:
      return undefined;
  }
}
