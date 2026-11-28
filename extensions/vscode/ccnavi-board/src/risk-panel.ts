/**
 * リスク管理画面のWebviewパネル。生成・更新・破棄、ファイル監視、Webviewからの操作の受け付け。
 * VS CodeのAPIに触れるので単体テストの対象外。docs/design/manual-checks.mdの手動確認の手順で確かめる。
 *
 * 画面はReact（`src/webview/risk/`）で、ここが渡すのは「いま何を見せるか」（`RiskData`）だけ。
 * 渡し方は`core/screen-host.ts`の`retainedHost`が決める。この画面は編集の途中を持つので
 * `retainContextWhenHidden`が真で、入れ物（HTML）は1度しか入らない。入れ直すと画面が作り直され、打ちかけの編集が消えるため。
 * 中身を渡すのは、画面の編集を破棄してよいときだけ（ユーザが「更新」を押した、保存や作成が通った）。
 * ファイルが外で変わっただけのときは`changed`を送り、破棄するかどうかはユーザが決める。
 *
 * 対象は3種。共通の設定の配点（`.ccnavi/config/common/risks.yml`。場所は固定）、
 * ワークスペースの設定の配点（既定`.ccnavi/config/project/risks.yml`）、プロジェクト1つの設定の配点
 * （既定`projects/<名前>/.ccnavi/config/project/risks.yml`）。タブは1枚だけで、別の対象を開くとそのタブの
 * 中身を入れ替える（未保存の変更があれば、破棄して切り替えるかを聞く）。
 * 設定ファイルの場所は実行ファイルが解いたもの（`--explain --json`の`layers[].risk`）を使い、拡張は組まない。
 * 判定は共通の設定と、親の`project:`が指す設定の和で行う。編集する1本とは別に、読み取り専用の統合結果
 * （共通の設定 + ワークスペース、共通の設定 + 各プロジェクト。`sums[]`）を出す。統合結果は実行ファイルが出した結果で、拡張は合成しない。
 *
 * チケット制御がdisableのワークスペースでは開かない。配点は子チケットをクローズするときにしか
 * 読まれないので、disableの間は何も動かさない。入口（サイドパネル・コマンドパレット）も同じキーで隠れる。
 *
 * 検証は実行ファイルに任せる。編集中の内容は一時ファイルに書き、`--lint`に渡す。共通の設定は`--risk <パス>`、ワークスペースの設定は`--project-risk-file self=<パス>`、
 * プロジェクトは`--project-risk-file <名前>=<パス>`（レイヤーの配点は共通の設定と合わせて検証される）。
 * 保存は、検証（`--lint`）を通り、作業中のチケットが無く（共通の設定とワークスペースの設定はどのツリーでも、
 * プロジェクトの設定はそのプロジェクトの分だけ。配点は子をクローズするときに読まれるので、走っている最中に変えない）、
 * ファイルが外で変わっていないときだけ行う（流れは`settings-panel.ts`の`save`）。
 * ファイルが無いのは「設定が無い」正常な状態。共通の設定にもワークスペースの設定にも無いときは、組み込みの配点を読み取り専用で見せ、
 * 「作る」で同じ値のファイルを書き出してから直す。それ以外（プロジェクトの設定など）は空として出し、欄は触れて、
 * 検証を通った最初の保存でファイルを作る。
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import { followAppearance, readAppearance } from "./appearance.js";
import { loadBoard, type LintOverride } from "./ccnavi.js";
import { writeTreeFile } from "./core/flow-write.js";
import { LAYER_SELF, projectLayer, selfLayer } from "./core/layers.js";
import { loadingText } from "./core/loading-render.js";
import { lockFromError } from "./core/lock.js";
import { asRiskForm, BUILTIN_RISK_TEXT, readRisk, type RiskDocument } from "./core/risk-doc.js";
import { renderRiskPage } from "./core/risk-render.js";
import type { RiskData, RiskMessage, ToRisk } from "./core/risk-view.js";
import { retainedHost, type ScreenHost } from "./core/screen-host.js";
import { HANDOFF_ENTRY } from "./handoff-entry.js";
import type { RiskTarget } from "./core/screens.js";
import { riskSums, type RiskSum } from "./core/sums.js";
import { targetOptions } from "./core/targets.js";
import { blockedFailure, unreadableFailure, type ErrorFile } from "./core/load-failure.js";
import { showLoading } from "./loading.js";
import { assertRetained, openInEditor, watchPattern, workspaceFolder } from "./panel-common.js";
import { binSetting, sameTarget, settingsPanel, targetKey, type SettingsLoaded, type SettingsState } from "./settings-panel.js";
import { requireTickets } from "./ticket-control.js";
import { webviewScript, webviewStyle } from "./webview-asset.js";
import { confirmIfTurn } from "./turn-confirm.js";

const DEFAULT_RISK = ".ccnavi/config/common/risks.yml";
/** 画面の名前。バンドルのパスは`src/webview/<名前>/main.tsx` → `out/webview/<名前>.js`、`style.css` → `<名前>.css` */
const SCREEN = "risk";
const TITLE = "ccnaviリスク管理";
/** 文で使う呼び名（「配点のファイル」） */
const NOUN = "配点";

interface Loaded extends SettingsLoaded {
  /** ファイルの本文。無ければ、組み込みの配点を見せるときはその本文、そうでなければ空 */
  readonly text: string;
  /** 組み込みの配点を読み取り専用で見せている（「作る」だけができる） */
  readonly builtin: boolean;
  readonly doc: RiskDocument;
  /** 上部に出す注意。実行ファイルがこの設定を読めていない、など */
  readonly notices: readonly string[];
  /** 読み取り専用の統合結果（実行ファイルの`sums[]`） */
  readonly sums: readonly RiskSum[];
}

type PanelState = SettingsState<RiskData, Loaded>;

/** 開いているパネル。種類ごとに1枚 */
let state: PanelState | undefined;

/** 読み込み中の一言で「何を」読んでいるか */
function whatOf(target: RiskTarget): string {
  switch (target.kind) {
    case "workspace":
      return "リスク";
    case "self":
      return "ワークスペースの設定のリスク";
    case "project":
      return `${target.name}のリスク`;
  }
}

/** `ccnaviBoard.openRisk`の本体。引数なしは共通の設定の配点 */
export async function openRisk(target: RiskTarget = { kind: "workspace" }): Promise<void> {
  if (!requireTickets("リスク管理画面")) {
    return;
  }
  const folder = workspaceFolder("リスク管理画面");
  if (folder === undefined) {
    return;
  }
  if (state !== undefined) {
    state.panel.reveal(state.panel.viewColumn);
    if (!sameTarget(state.target, target)) {
      await switchTarget(state, target);
    }
    return;
  }

  // 画面とCSSはバンドルしたものを読んで流し込む。無ければ開かずに言う（パネルだけ出しても白いまま）
  try {
    webviewScript(SCREEN);
    webviewStyle(SCREEN);
  } catch (error) {
    vscode.window.showErrorMessage(`リスク管理画面を表示できません: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }

  // タブは読む前に作る。設定ファイルの場所を実行ファイルに聞く間に、押しても何も起きないように見えるのを避けるため。
  // `state`を先に設定するので、読んでいる間に押し直しても上の`reveal`に入る。
  // 読めなかったときもタブは閉じず、中にエラーを出す（`reload`の`showError`）
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
  const targets = targetOptions(board.ok ? board.board : undefined, "workspace", targetKey(target));
  const sums = riskSums(board.ok ? board.board : undefined);
  let riskRel: string;
  let riskPath: string;
  /** 共通の設定とワークスペースの設定の、もう一方の配点のファイル。組み込みの配点を見せるかどうかの判断に使う */
  let other: string | undefined;
  if (target.kind === "workspace") {
    // 共通の設定の場所は`.ccnavi/config/common/`固定で、env（`CCNAVI_RISK`など）では動かせない。
    riskRel = DEFAULT_RISK;
    riskPath = resolveIn(root, riskRel);
    const self = board.ok ? selfLayer(board.board) : undefined;
    other = self === undefined || self.risk.path === "" ? undefined : resolveIn(root, self.risk.path);
  } else {
    // 設定ファイルの場所は実行ファイルに聞く。`.ccnavi`から自分で組むと、組み方が実行ファイルと
    // 食い違ったときに、この画面で保存した配点が判定に使われなくなる。答えは元リポジトリの版。
    if (!board.ok) {
      throw new Error(`設定ファイルの場所を実行ファイルから取得できません: ${board.error}`);
    }
    const layer = target.kind === "self" ? selfLayer(board.board) : projectLayer(board.board, target.name);
    if (layer === undefined || layer.risk.path === "") {
      throw new Error(
        target.kind === "self"
          ? "ccnaviの出力にワークスペースの設定がありません"
          : `プロジェクト${target.name}は設定の対象になっていません（プロジェクトのフォルダの直下にないか、名前が予約名のcommonかselfです）`,
      );
    }
    riskPath = resolveIn(root, layer.risk.path);
    riskRel = path.relative(root, riskPath).split(path.sep).join("/");
    if (target.kind === "self") {
      other = resolveIn(root, DEFAULT_RISK);
    }
    if (layer.risk.unreadable !== "") {
      throw unreadableFailure({ path: riskPath, rel: riskRel }, layer.risk.unreadable);
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
  // 無いときの苦情（versionが無い、など）は画面に出さない。無いことは不備ではない。
  const parsed = readRisk(text);
  if (parsed.blocking.length > 0 && (exists || builtin)) {
    throw blockedFailure({ path: riskPath, rel: riskRel }, parsed.blocking);
  }
  const doc = exists || builtin ? parsed : { ...parsed, model: { ...parsed.model, problems: [] } };
  return { target, text, exists, builtin, mtimeMs, doc, file: { path: riskPath, rel: riskRel }, notices, targets, sums, handoff: HANDOFF_ENTRY };
}

function resolveIn(root: string, filePath: string): string {
  return path.isAbsolute(filePath) ? filePath : path.join(root, filePath);
}

/**
 * 配点のファイルと設定ファイルが変わったら「ファイルの変更を検知しました」と伝える。自分の保存は除く。
 * 対象のパスは設定で変わるので、更新のたびに張り直す。絶対パスはワークスペース相対のglobに
 * ならないので、そのディレクトリを起点にする。
 */
function watchFiles(current: PanelState): void {
  for (const watcher of current.fileWatchers) {
    watcher.dispose();
  }
  current.fileWatchers = [];
  const riskRel = current.loaded?.file.rel ?? DEFAULT_RISK;
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

/** この画面の仕事を、3画面で共通の段取り（`settings-panel.ts`）に渡す */
const { registerPanelHandlers, scheduleChanged, reload, switchTarget, fail, save } = settingsPanel<RiskData, Loaded, ToRisk, RiskMessage>({
  accepts: true,
  screen: SCREEN,
  title: TITLE,
  words: { file: NOUN, content: NOUN },
  alive,
  release: (current) => {
    if (state === current) {
      state = undefined;
    }
  },
  parse: asMessage,
  handle,
  read: readPage,
  show,
  showError,
  watchFiles,
  loading: (current) => ({ kind: "loading", text: loadingText(whatOf(current.target)) }),
  override: overrideFor,
});

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
      riskPath: loaded.file.rel,
      exists: loaded.exists,
      builtin: loaded.builtin,
      model: loaded.doc.model,
      lock: current.lock,
      notices: loaded.notices,
      handoff: loaded.handoff,
      target: targetKey(loaded.target),
      targets: loaded.targets,
      sums: loaded.sums,
    },
  });
}

/** 読み直せなかったことを見せる。理由は覚えておく（渡せなかったときに`ready`で渡し直すため） */
function showError(current: PanelState, error: string, file?: ErrorFile): void {
  current.loaded = undefined;
  current.error = error;
  const target = targetKey(current.target);
  current.host.send({ kind: "error", error, ...(file === undefined ? {} : { file: file.rel }), target, targets: targetOptions(undefined, "workspace", target) });
}

/**
 * リスク管理の画面に渡す手段。VS Codeのパネルを`retainedHost`の形に合わせる。
 * 入れ物は1度しか入らないので、表示の有無は渡さない（保持する画面は非表示でも生きている）。
 * パネルの`retainContextWhenHidden`を偽に変えると、送った先が破棄されていても気づけなくなる。
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

/** この画面にだけある操作（3画面で同じものは`settings-panel.ts`が受ける） */
async function handle(current: PanelState, loaded: Loaded, message: RiskMessage): Promise<void> {
  switch (message.type) {
    case "openFile":
      openInEditor(loaded.file.path);
      return;
    case "create":
      await create(current, loaded);
      return;
    case "save":
      if (loaded.builtin) {
        fail(current, `${loaded.file.rel}がありません。先に「組み込みの配点でファイルを作る」を押してください`);
        return;
      }
      await save(current, loaded, () => loaded.doc.apply(message.form));
      return;
  }
}

/** 組み込みと同じ値のファイルを書き出す。既にあれば上書きしない（値は同じなので数え方は変わらない） */
async function create(current: PanelState, loaded: Loaded): Promise<void> {
  if (!loaded.builtin) {
    return;
  }
  if (fs.existsSync(loaded.file.path)) {
    fail(current, `${loaded.file.rel}は既に存在するため、上書きしません。更新してください`);
    return;
  }
  if (!(await confirmIfTurn(current.folder.uri.fsPath, binSetting()))) {
    fail(current, "エージェントの応答中のため作成を取りやめました。応答が終わってから作成してください。");
    return;
  }
  if (!alive(current)) {
    return;
  }
  // 保存と同じ守りで書く（ツリーの外とリンクを断り、一時ファイルの入れ替えで書く。まだ無いことも確かめ直す）
  current.wroteAt = Date.now();
  const written = writeTreeFile(current.folder.uri.fsPath, loaded.file.path, BUILTIN_RISK_TEXT, { exists: false, mtimeMs: 0 }, NOUN, undefined);
  if (!written.ok) {
    // 書けなかったのに猶予を残したままだと、その間の実際の外部変更が知らされない。
    current.wroteAt = 0;
    fail(current, written.error);
    return;
  }
  await reload(current);
  vscode.window.showInformationMessage(`${loaded.file.rel}を組み込みの配点で作りました。コミットは自分でしてください`);
}

/** 保存の前の検証に渡す差し替え。共通の設定は`--risk`、ほかは名前を付けて`--project-risk-file` */
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
