/**
 * ルール管理画面の Webview パネル。生成・更新・破棄、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。docs/design/manual-checks.md の手動確認の手順で確かめる。
 *
 * 画面は React（`src/webview/rules/`）で、ここが渡すのは「いま何を見せるか」（`RulesData`）だけ。
 * 渡し方は `core/screen-host.ts` の `retainedHost` が決める。この画面は編集の途中を持つので
 * `retainContextWhenHidden` が真で、入れ物（HTML）は 1 度しか入らない。入れ直すと画面が作り直され、打ちかけの編集が消えるため。
 * 中身を渡すのは、画面の編集を破棄してよいときだけ（ユーザが「更新」を押した、保存が通った）。
 * ファイルが外で変わっただけのときは `changed` を送り、破棄するかどうかはユーザが決める。
 *
 * 対象は 3 種。共通の設定のルール（`.ccnavi/config/common/rules.yml`）、
 * ワークスペースの設定のルール（既定 `.ccnavi/config/project/rules.yml`）、プロジェクト 1 つの設定のルール
 * （既定 `projects/<名前>/.ccnavi/config/project/rules.yml`）。タブは 1 枚だけで、別の対象を開くとそのタブの
 * 中身を入れ替える（未保存の変更があれば、破棄して切り替えるかを聞く）。
 * 設定ファイルの場所は実行ファイルが解いたもの（`--explain --json` の `layers[]`）を使い、拡張は組まない。
 * ファイルが無いのは「設定が無い」正常な状態で、空として出す（保存でファイルを作る）。
 * 編集する 1 本とは別に、読み取り専用の統合結果（共通の設定 + ワークスペース、共通の設定 + 各プロジェクト。`sums[]`）を出す。
 * 統合結果は実行ファイルが出した結果をそのまま見せる。
 *
 * 判定・検証は実行ファイルに任せる。編集中の内容は一時ファイルに書き、共通の設定なら `--rules`、
 * ワークスペースかプロジェクトの設定なら `--project-rules-file <名前>=<パス>`（ワークスペースの設定は名前が `self`）で渡す。保存は、検証（`--lint`）を通り、
 * 作業中のチケットが無く（プロジェクトならそのプロジェクトの）、ファイルが外で変わっていない
 * ときだけ行う。
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import { followAppearance, postAppearance, readAppearance } from "./appearance.js";
import { loadBoard, runExpectations, runLint, runSuggest, runTest, type RulesOverride } from "./ccnavi.js";
import { hooksFor, parseHooks, type HookEntry } from "./core/hooks.js";
import { projectLayer, selfLayer } from "./core/layers.js";
import { loadingText } from "./core/loading-render.js";
import { lockFromBoard, lockFromError, type Lock } from "./core/lock.js";
import { asSections, readRules, type RulesDocument } from "./core/rules-doc.js";
import { renderRulesPage } from "./core/rules-render.js";
import { isToolName, type RulesData, type RulesMessage, type Sections, type ToRules } from "./core/rules-view.js";
import { retainedHost, type ScreenHost } from "./core/screen-host.js";
import { rulesSums, type RulesSum } from "./core/sums.js";
import { showLoading } from "./loading.js";
import type { HandoffEntry } from "./core/handoff-entry.js";
import { openHandoffFrom, readHandoffEntry } from "./handoff-entry.js";
import type { RulesTarget } from "./core/screens.js";
import { targetOptions, type TargetOption } from "./core/targets.js";
import { assertRetained, watchGlobs } from "./panel-common.js";
import { settingsPanel, type SettingsState } from "./settings-panel.js";
import { markTourSeen, tourSeen } from "./tour.js";
import { webviewScript, webviewStyle } from "./webview-asset.js";
import { confirmDuringTurn } from "./turn-confirm.js";

const DEFAULT_RULES = ".ccnavi/config/common/rules.yml";
const DEFAULT_EXPECTATIONS =".ccnavi/config/common/rule-expectations.yml";
/** 画面の名前。バンドルのパスは `src/webview/<名前>/main.tsx` → `out/webview/<名前>.js`、`style.css` → `<名前>.css` */
const SCREEN = "rules";
const TITLE = "ccnavi ルール管理";

interface Loaded {
  /** 読んだ対象。往復の間に切り替わったかを `stale` が見る */
  readonly target: RulesTarget;
  /** ファイルが在るか。無ければ空として出し、保存で作る */
  readonly exists: boolean;
  /** 無いときは 0 */
  readonly mtimeMs: number;
  readonly doc: RulesDocument;
  readonly rulesPath: string;
  /** ワークスペースルートからの相対で見せるパス。プロジェクトなら `projects/<名前>/.ccnavi/config/project/rules.yml` */
  readonly rulesRel: string;
  /** 上部に出す注意。実行ファイルがこの設定を読めていない、など */
  readonly notices: readonly string[];
  /** 取り込み待ち画面を開けるか */
  readonly handoff: HandoffEntry;
  /** 切り替えられる対象（共通・ワークスペース・設定のあるプロジェクト） */
  readonly targets: readonly TargetOption[];
  /** 読み取り専用の統合結果（実行ファイルの `sums[]`） */
  readonly sums: readonly RulesSum[];
  readonly hooks: readonly HookEntry[];
  readonly hookFiles: { readonly settings: boolean; readonly settingsLocal: boolean };
  readonly mode: string;
  readonly expectationsPath: string;
  readonly expectationsRel: string;
}

type PanelState = SettingsState<RulesData, RulesTarget, Loaded>;

/** 開いているパネル。種類ごとに 1 枚 */
let state: PanelState | undefined;

function sameTarget(a: RulesTarget, b: RulesTarget): boolean {
  return a.kind === b.kind && (a.kind !== "project" || (b.kind === "project" && a.name === b.name));
}

/** 保存を止めるチケットを絞るプロジェクト。共通の設定とワークスペースの設定は絞らない（どちらも全ツリーの Bash に影響する） */
function projectOf(target: RulesTarget): string | undefined {
  return target.kind === "project" ? target.name : undefined;
}

/** 確認の文で使う対象の名前。「設定」欄の表示（core/targets.ts の label）と揃える */
function targetLabelOf(target: RulesTarget): string {
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
function whatOf(target: RulesTarget): string {
  switch (target.kind) {
    case "workspace":
      return "ルール";
    case "self":
      return "ワークスペースの設定のルール";
    case "project":
      return `${target.name} のルール`;
  }
}

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

function expectationsSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("expectationsPath", DEFAULT_EXPECTATIONS);
}

/** `ccnaviBoard.openRules` の本体。引数なしは共通の設定のルール */
export async function openRules(target: RulesTarget = { kind: "workspace" }): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    vscode.window.showInformationMessage("ワークスペースが開かれていないため、ルール管理画面を表示できません");
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
    vscode.window.showErrorMessage(`ルール管理画面を表示できません: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }

  // タブは読む前に作る。設定ファイルの場所を実行ファイルに聞く間に、押しても何も起きないように見えるのを避けるため。
  // `state` を先に設定するので、読んでいる間に押し直しても上の `reveal` に入る。
  // 読めなかったときもタブは閉じず、中にエラーを出す（`reload` の `showError`）
  const panel = vscode.window.createWebviewPanel("ccnaviRules", TITLE, vscode.ViewColumn.One, {
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
    tmpDir: fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-rules-")),
    host: rulesHost(panel, folder.uri.fsPath),
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

async function readPage(root: string, target: RulesTarget): Promise<Loaded> {
  const settingsText = readText(path.join(root, ".claude", "settings.json"));
  const localText = readText(path.join(root, ".claude", "settings.local.json"));
  let rulesPath: string;
  let rulesRel: string;
  const notices: string[] = [];
  // 切り替えの一覧のために、共通の設定でも実行ファイルに聞く。読めなくても共通の設定は開けるので、一覧が縮むだけ
  const board = await loadBoard(root, binSetting());
  if (!board.ok) {
    notices.push(`実行ファイルから動作モードを取得できないため、判定が実行を止めるかを表示していません: ${board.error}`);
  }
  const targets = targetOptions(board.ok ? board.board : undefined, "workspace", { kind: target.kind, name: target.kind === "project" ? target.name : "" });
  if (target.kind === "workspace") {
    // 共通の設定の場所は `.ccnavi/config/common/` 固定。env（`CCNAVI_RULES` など）では動かせないので、設定ファイルは読まない。
    rulesRel = DEFAULT_RULES;
    rulesPath = resolveIn(root, rulesRel);
  } else {
    // 設定ファイルの場所は実行ファイルに聞く。`.ccnavi` から自分で組むと、組み方が実行ファイルと
    // 食い違ったときに、この画面で保存したルールが判定に使われなくなる。答えは元リポジトリの版で、
    // ワークツリーの中の版は指さない。
    if (!board.ok) {
      throw new Error(`設定ファイルの場所を実行ファイルから取得できません: ${board.error}`);
    }
    const layer = target.kind === "self" ? selfLayer(board.board) : projectLayer(board.board, target.name);
    if (layer === undefined || layer.rules.path === "") {
      throw new Error(
        target.kind === "self"
          ? "ccnavi の出力にワークスペースの設定がありません"
          : `プロジェクト ${target.name} は設定の対象になっていません（プロジェクトのフォルダの直下にないか、名前が予約名の common か self です）`,
      );
    }
    rulesPath = resolveIn(root, layer.rules.path);
    rulesRel = path.relative(root, rulesPath).split(path.sep).join("/");
    if (layer.rules.unreadable !== "") {
      notices.push(`実行ファイルはこのファイルを読み込めず、この設定を空として扱っています（このファイルのルールは 1 件も判定に使われていません）: ${layer.rules.unreadable}`);
    }
  }
  let text: string;
  let mtimeMs: number;
  let exists: boolean;
  try {
    text = fs.readFileSync(rulesPath, "utf8");
    mtimeMs = fs.statSync(rulesPath).mtimeMs;
    exists = true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      throw new Error(`ルールファイルを読み込めません（${rulesRel}）: ${(error as Error).message}`);
    }
    // 無いのは不備ではない（「設定が無い」正常な状態）。空として出し、ルールを足して保存すればファイルが作られる。
    text = "";
    mtimeMs = 0;
    exists = false;
  }
  const hooks = [
    ...(settingsText === undefined ? [] : parseHooks(settingsText, "settings")),
    ...(localText === undefined ? [] : parseHooks(localText, "settings-local")),
  ];
  const expectationsRel = expectationsSetting();
  // 取り込み待ち画面を開けるかは、設定の読み込みとは別に確かめる（古い実行ファイルでは入口だけを無効にする）
  const handoff = await readHandoffEntry(root, binSetting());
  return {
    target,
    handoff,
    exists,
    mtimeMs,
    // 無いファイルは、書式の版だけを持つ空のルールとして読む（版が無いファイルは実行ファイルが読めないので、保存で作るときに先頭へ置く）
    doc: readRules(exists ? text : "version: 1\n"),
    rulesPath,
    rulesRel,
    notices,
    targets,
    sums: rulesSums(board.ok ? board.board : undefined),
    hooks,
    hookFiles: { settings: settingsText !== undefined, settingsLocal: localText !== undefined },
    // 動作モードは実行ファイルに聞く。設定ファイルの env から自分で読むと、settings.local.json・環境・
    // 読めない値の扱いが判定と食い違う
    mode: board.ok ? board.board.settings.mode : "",
    expectationsPath: resolveIn(root, expectationsRel),
    expectationsRel,
  };
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

/**
 * ルールファイルと設定ファイルが変わったら「ファイルの変更を検知しました」と伝える。自分の保存は除く。
 * 対象のパスは設定ファイルの場所で変わるので、再読み込みのたびに張り直す。
 */
function watchFiles(current: PanelState): void {
  for (const watcher of current.fileWatchers) {
    watcher.dispose();
  }
  current.fileWatchers = [];
  const rulesRel = current.loaded?.rulesRel ?? DEFAULT_RULES;
  current.fileWatchers.push(...watchGlobs(current.folder, [toGlob(rulesRel), ".claude/settings.json", ".claude/settings.local.json"], () => scheduleChanged(current)));
}

function toGlob(rel: string): string {
  return rel.replace(/\\/g, "/");
}

function alive(current: PanelState): boolean {
  return state === current;
}

/** この画面の仕事を、3 画面で共通の段取り（`settings-panel.ts`）に渡す */
const { registerPanelHandlers, scheduleChanged, reload, redraw, switchTarget } = settingsPanel<RulesData, RulesTarget, Loaded, ToRules>({
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
 * 共通の設定とワークスペースの設定のルールはどのツリーの doing でも止め、プロジェクトのルールはそのプロジェクトの doing だけ見る。
 */
async function refreshLock(current: PanelState): Promise<Lock> {
  const result = await loadBoard(current.folder.uri.fsPath, binSetting());
  const lock = result.ok ? lockFromBoard(result.board, projectOf(current.target)) : lockFromError(result.error);
  if (alive(current)) {
    current.lock = lock;
    current.host.post({ type: "lock", lock } satisfies ToRules);
  }
  return lock;
}

/**
 * いま見せるものを渡す。画面の編集はここで破棄されるので、呼ぶのはユーザが「更新」を押した
 * ときと、保存が通って中身が入れ替わったときだけ。
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
      rulesPath: loaded.rulesRel,
      exists: loaded.exists,
      mode: loaded.mode,
      model: loaded.doc.model,
      hooks: loaded.hooks,
      hookFiles: loaded.hookFiles,
      expectationsPath: loaded.expectationsRel,
      lock: current.lock,
      notices: loaded.notices,
      handoff: loaded.handoff,
      target: { kind: loaded.target.kind, name: loaded.target.kind === "project" ? loaded.target.name : "" },
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

/** 開いている対象の欄の値（`targets.ts` の `kind` と `name`） */
function currentKey(target: RulesTarget): { kind: string; name: string } {
  return { kind: target.kind, name: target.kind === "project" ? target.name : "" };
}

/**
 * ルール管理の画面に渡す手段。VS Code のパネルを `retainedHost` の形に合わせる。
 * 入れ物は 1 度しか入らないので、表示の有無は渡さない（保持する画面は非表示でも生きている）。
 * パネルの `retainContextWhenHidden` を偽に変えると、送った先が破棄されていても気づけなくなる。
 * 型では止まらないので、ここで見て言う。
 */
function rulesHost(panel: vscode.WebviewPanel, root: string): ScreenHost<RulesData> {
  assertRetained(panel, root, "rules");
  return retainedHost<RulesData>(
    {
      html(text: string): void {
        panel.webview.html = text;
      },
      post(message: unknown): void {
        void panel.webview.postMessage(message);
      },
    },
    (data) =>
      renderRulesPage(data, {
        nonce: crypto.randomBytes(16).toString("base64"),
        script: webviewScript(SCREEN),
        style: webviewStyle(SCREEN),
        appearance: readAppearance(),
      }),
  );
}

/**
 * 頼まれたときに読んでいたものが、往復の間に入れ替わっていないか。`what` は破棄するもの。
 *
 * 保存は実行ファイルへ 2 度出る（`--lint` と錠の取り直し）。その間にユーザが「更新」を押せば、
 * 画面の編集は破棄され、新しい中身が出ている。そこへ古い編集を書くと、破棄したはずのものが
 * ファイルに入る。 判定と期待する判定の確認はファイルに触らないが、破棄した編集で出した答えを
 * 「いまのルールの判定」として見せることになるので、同じく無かったことにする。
 */
function stale(current: PanelState, loaded: Loaded, what: string): boolean {
  if (current.loaded === loaded) {
    return false;
  }
  // 往復の間に別の対象へ切り替わった。破棄するのは同じだが、切り替え先の画面に前の対象の話を出さない
  if (!sameTarget(loaded.target, current.target)) {
    return true;
  }
  fail(current, `画面を更新したので、${what}は取りやめました。更新後のルールでやり直してください`);
  return true;
}

/** 操作の結果の一言。1 枚目を読み込んでいる間だけ届かずに破棄される（非表示になっていても届く） */
function fail(current: PanelState, message: string): void {
  current.host.post({ type: "failed", message } satisfies ToRules);
}

/** 編集中の内容を一時ファイルに書き、実行ファイルへ渡す差し替えを返す */
function stage(current: PanelState, sections: Sections): RulesOverride {
  const loaded = current.loaded;
  if (loaded === undefined) {
    throw new Error("ルールが読み込まれていません");
  }
  const tmp = path.join(current.tmpDir, "rules.yml");
  fs.writeFileSync(tmp, loaded.doc.apply(sections), "utf8");
  return overrideFor(current.target, tmp);
}

function overrideFor(target: RulesTarget, tmp: string): RulesOverride {
  switch (target.kind) {
    case "workspace":
      return { kind: "workspace", path: tmp };
    case "self":
      return { kind: "self", path: tmp };
    case "project":
      return { kind: "project", name: target.name, path: tmp };
  }
}

async function handleMessage(current: PanelState, message: RulesMessage | undefined): Promise<void> {
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
      current.host.post({ type: "tour" } satisfies ToRules);
    }
    return;
  }
  if (message.type === "tourDone") {
    markTourSeen(SCREEN);
    return;
  }
  // 読み直せていない画面では、ルールにヒットする操作はどれも行き先が無い（「更新」は
  // 押せるが、その経路は `reload` が読み直しからやり直す）
  if (current.loaded === undefined && message.type !== "reload") {
    return;
  }
  const root = current.folder.uri.fsPath;
  switch (message.type) {
    case "reload": {
      if (message.dirty) {
        const choice = await vscode.window.showWarningMessage(
          "未保存の変更があります。破棄して更新しますか？",
          { modal: true },
          "更新",
        );
        if (choice !== "更新") {
          // 画面は「更新」を押した時点でボタンを止めている。やめたことを伝えないと止まったままになる。
          // 問いを出している間にパネルを閉じられるので、送る前に生きているかを見る
          if (alive(current)) {
            current.host.post({ type: "cancelled" } satisfies ToRules);
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
      const target: RulesTarget =
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
      const target = message.which === "rules" ? current.loaded.rulesPath : current.loaded.expectationsPath;
      void vscode.workspace.openTextDocument(target).then(
        (document) => vscode.window.showTextDocument(document),
        () => vscode.window.showInformationMessage(`ファイルを開けませんでした: ${target}`),
      );
      return;
    }
    case "pickFile": {
      // 選んだファイルはワークスペースルートからの相対で欄に入れる。外を選んだら入れない。
      // 実行ファイルが読むのはルートの中だけで、lint も外を指すパスを error にする。
      const picked = await vscode.window.showOpenDialog({
        defaultUri: current.folder.uri,
        canSelectFiles: true,
        canSelectFolders: false,
        canSelectMany: false,
        openLabel: "このファイルを渡す",
        title: `${message.field}: モデルへ渡すファイル`,
      });
      const chosen = picked?.[0];
      if (chosen === undefined || !alive(current)) {
        return;
      }
      const rel = path.relative(root, chosen.fsPath);
      if (rel === "" || rel.startsWith("..") || path.isAbsolute(rel)) {
        void vscode.window.showWarningMessage(`ワークスペースの外のファイルは指定できません: ${chosen.fsPath}`);
        return;
      }
      current.host.post({
        type: "picked",
        key: message.key,
        field: message.field,
        path: rel.split(path.sep).join("/"),
      } satisfies ToRules);
      return;
    }
    case "judge": {
      const loaded = current.loaded;
      if (loaded === undefined) {
        return;
      }
      let rules: RulesOverride;
      try {
        rules = stage(current, message.sections);
      } catch (error) {
        fail(current, `編集中の内容を保存できません: ${(error as Error).message}`);
        return;
      }
      const result = await runTest(root, binSetting(), rules, message.tool, message.subject);
      if (!alive(current) || stale(current, loaded, "この判定")) {
        return;
      }
      if (!result.ok) {
        fail(current, result.error);
        return;
      }
      current.host.post({ type: "judged", result: result.value, hooks: hooksFor(loaded.hooks, message.tool) } satisfies ToRules);
      return;
    }
    case "expectations": {
      const loaded = current.loaded;
      if (loaded === undefined) {
        return;
      }
      if (!fs.existsSync(loaded.expectationsPath)) {
        current.host.post({
          type: "notice",
          message: `期待する判定のファイルがまだありません: ${loaded.expectationsRel}。作ると、ここで一括で確かめられます。書き方は /ccnavi-config（.claude/skills/ccnavi-config/references/common/check.md）`,
        } satisfies ToRules);
        return;
      }
      let rules: RulesOverride;
      try {
        rules = stage(current, message.sections);
      } catch (error) {
        fail(current, `編集中の内容を保存できません: ${(error as Error).message}`);
        return;
      }
      const result = await runExpectations(root, binSetting(), rules, loaded.expectationsPath);
      if (!alive(current) || stale(current, loaded, "この期待する判定の確認")) {
        return;
      }
      if (!result.ok) {
        fail(current, result.error);
        return;
      }
      current.host.post({ type: "expectationsChecked", result: result.value } satisfies ToRules);
      return;
    }
    case "suggest": {
      const loaded = current.loaded;
      if (loaded === undefined) {
        return;
      }
      const result = await runSuggest(root, binSetting());
      if (!alive(current) || stale(current, loaded, "この候補")) {
        return;
      }
      if (!result.ok) {
        fail(current, result.error);
        return;
      }
      current.host.post({ type: "suggested", result: result.value } satisfies ToRules);
      return;
    }
    case "save": {
      await save(current, message.sections);
      return;
    }
  }
}

async function save(current: PanelState, sections: Sections): Promise<void> {
  const loaded = current.loaded;
  if (loaded === undefined) {
    return;
  }
  const root = current.folder.uri.fsPath;
  let tmp: string;
  let text: string;
  try {
    text = loaded.doc.apply(sections);
    tmp = path.join(current.tmpDir, "rules.yml");
    fs.writeFileSync(tmp, text, "utf8");
  } catch (error) {
    fail(current, `編集中の内容を保存できません: ${(error as Error).message}`);
    return;
  }

  // 1. 検証。error が 1 件でもあれば保存しない。
  const lint = await runLint(root, binSetting(), overrideFor(current.target, tmp));
  if (!alive(current) || stale(current, loaded, "この保存")) {
    return;
  }
  if (!lint.ok) {
    fail(current, lint.error);
    return;
  }
  if (!lint.value.ok) {
    // 苦情は渡した一時ファイルのパスで出るので、画面では対象のファイルのパスに直す。
    fail(current, `設定の検証（--lint）でエラーが見つかりました。内容を修正してから保存してください:\n${lint.value.report.split(tmp).join(loaded.rulesRel)}`);
    return;
  }

  // 2. 作業中のチケットが無いこと。押した時点で取り直す。
  const lock = await refreshLock(current);
  if (!alive(current) || stale(current, loaded, "この保存")) {
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
      mtimeMs = fs.statSync(loaded.rulesPath).mtimeMs;
    } catch (error) {
      fail(current, `ルールファイルを確認できません: ${(error as Error).message}`);
      return;
    }
    if (mtimeMs !== loaded.mtimeMs) {
      fail(current, "ルールファイルは、読み込んだあとに画面の外で変更されています。更新してから編集し直してください（この変更は上書きしません）");
      return;
    }
  } else if (fs.existsSync(loaded.rulesPath)) {
    fail(current, "ルールファイルは、読み込んだあとに画面の外で作られています。更新してから編集し直してください（上書きしません）");
    return;
  }

  try {
    current.wroteAt = Date.now();
    if (loaded.exists) {
      fs.writeFileSync(loaded.rulesPath, text, "utf8");
    } else {
      fs.mkdirSync(path.dirname(loaded.rulesPath), { recursive: true });
      fs.writeFileSync(loaded.rulesPath, text, { encoding: "utf8", flag: "wx" });
    }
  } catch (error) {
    // 書けなかったのに猶予を残したままだと、その間の実際の外部変更が知らされない。
    current.wroteAt = 0;
    fail(current, `ルールファイルに保存できません: ${(error as Error).message}`);
    return;
  }
  await reload(current);
  const tail = lint.value.report.split("\n").filter((l) => l.trim() !== "").pop() ?? "";
  vscode.window.showInformationMessage(`${loaded.rulesRel} に保存しました（${tail}）`);
}

function asMessage(message: unknown): RulesMessage | undefined {
  if (typeof message !== "object" || message === null) {
    return undefined;
  }
  const m = message as { type?: unknown; dirty?: unknown; which?: unknown; sections?: unknown; tool?: unknown; subject?: unknown; key?: unknown; field?: unknown; kind?: unknown; name?: unknown };
  switch (m.type) {
    case "switchTarget":
      return typeof m.kind === "string" && typeof m.name === "string" ? { type: "switchTarget", kind: m.kind, name: m.name } : undefined;
    case "ready":
    case "tourDone":
    case "openHandoff":
    case "suggest":
      return { type: m.type };
    case "reload":
      return { type: "reload", dirty: m.dirty === true };
    case "dirty":
      return typeof m.dirty === "boolean" ? { type: "dirty", dirty: m.dirty } : undefined;
    case "pickFile":
      if (typeof m.key !== "string" || (m.field !== "additionalContextFile" && m.field !== "additionalContextOnceFile")) {
        return undefined;
      }
      return { type: "pickFile", key: m.key, field: m.field };
    case "openFile":
      return m.which === "rules" || m.which === "expectations" ? { type: "openFile", which: m.which } : undefined;
    case "save": {
      const sections = asSections(m.sections);
      return sections === undefined ? undefined : { type: "save", sections };
    }
    case "expectations": {
      const sections = asSections(m.sections);
      return sections === undefined ? undefined : { type: "expectations", sections };
    }
    case "judge": {
      const sections = asSections(m.sections);
      if (sections === undefined || typeof m.subject !== "string") {
        return undefined;
      }
      // 一覧にない名前は通す（判定が対象を見ないと結果に出る）。ただし引数に渡す形は決めておく
      if (typeof m.tool !== "string" || !isToolName(m.tool)) {
        return undefined;
      }
      return { type: "judge", sections, tool: m.tool, subject: m.subject };
    }
    default:
      return undefined;
  }
}
