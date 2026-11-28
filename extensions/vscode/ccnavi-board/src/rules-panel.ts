/**
 * ルール管理画面のWebviewパネル。生成・更新・破棄、ファイル監視、Webviewからの操作の受け付け。
 * VS CodeのAPIに触れるので単体テストの対象外。docs/design/manual-checks.mdの手動確認の手順で確かめる。
 *
 * 画面はReact（`src/webview/rules/`）で、ここが渡すのは「いま何を見せるか」（`RulesData`）だけ。
 * 渡し方は`core/screen-host.ts`の`retainedHost`が決める。この画面は編集の途中を持つので
 * `retainContextWhenHidden`が真で、入れ物（HTML）は1度しか入らない。入れ直すと画面が作り直され、打ちかけの編集が消えるため。
 * 中身を渡すのは、画面の編集を破棄してよいときだけ（ユーザが「更新」を押した、保存が通った）。
 * ファイルが外で変わっただけのときは`changed`を送り、破棄するかどうかはユーザが決める。
 *
 * 対象は3種。共通設定のルール（`.ccnavi/config/common/rules.yml`）、
 * ワークスペースの設定のルール（既定`.ccnavi/config/project/rules.yml`）、プロジェクト1つの設定のルール
 * （既定`projects/<名前>/.ccnavi/config/project/rules.yml`）。タブは1枚だけで、別の対象を開くとそのタブの
 * 中身を入れ替える（未保存の変更があれば、破棄して切り替えるかを聞く）。
 * 設定ファイルの場所は実行ファイルが解いたもの（`--explain --json`の`layers[]`）を使い、拡張は組まない。
 * ファイルが無いのは「設定が無い」正常な状態で、空として出す（保存でファイルを作る）。
 * 編集する1本とは別に、読み取り専用の統合結果（共通設定 + ワークスペース、共通設定 + 各プロジェクト。`sums[]`）を出す。
 * 統合結果は実行ファイルが出した結果をそのまま見せる。
 *
 * 判定・検証は実行ファイルに任せる。編集中の内容は一時ファイルに書き、共通設定なら`--rules`、
 * ワークスペースかプロジェクトの設定なら`--project-rules-file <名前>=<パス>`（ワークスペースの設定は名前が`self`）で渡す。保存は、検証（`--lint`）を通り、
 * 作業中のチケットが無く（プロジェクトならそのプロジェクトの）、ファイルが外で変わっていない
 * ときだけ行う（流れは`settings-panel.ts`の`save`）。
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import { followAppearance, readAppearance } from "./appearance.js";
import { loadBoard, runExpectations, runSuggest, runTest, type RulesOverride } from "./ccnavi.js";
import { hooksFor, parseHooks, type HookEntry } from "./core/hooks.js";
import { projectLayer, selfLayer } from "./core/layers.js";
import { loadingText } from "./core/loading-render.js";
import { lockFromError } from "./core/lock.js";
import { asSections, readRules, type RulesDocument } from "./core/rules-doc.js";
import { renderRulesPage } from "./core/rules-render.js";
import { isToolName, TMP_RULES_PREFIX, type RulesData, type RulesMessage, type Sections, type ToRules } from "./core/rules-view.js";
import { retainedHost, type ScreenHost } from "./core/screen-host.js";
import { rulesSums, type RulesSum } from "./core/sums.js";
import { showLoading } from "./loading.js";
import { HANDOFF_ENTRY } from "./handoff-entry.js";
import type { SettingsTarget } from "./core/screens.js";
import { targetKey, targetOptions } from "./core/targets.js";
import { blockedFailure, unreadableFailure, type ErrorFile } from "./core/load-failure.js";
import { assertRetained, openInEditor, resolveIn, toGlob, watchGlobs, workspaceFolder } from "./panel-common.js";
import { binSetting, readSettingFile, settingsPanel, type SettingsLoaded, type SettingsState } from "./settings-panel.js";
import { targetToOpen } from "./target-memory.js";
import { webviewScript, webviewStyle } from "./webview-asset.js";

const DEFAULT_RULES = ".ccnavi/config/common/rules.yml";
const DEFAULT_EXPECTATIONS =".ccnavi/config/common/rule-expectations.yml";
/** 画面の名前。バンドルのパスは`src/webview/<名前>/main.tsx` → `out/webview/<名前>.js`、`style.css` → `<名前>.css` */
const SCREEN = "rules";
const TITLE = "ccnaviルール管理";

interface Loaded extends SettingsLoaded {
  readonly doc: RulesDocument;
  /** 上部に出す注意。実行ファイルがこの設定を読めていない、など */
  readonly notices: readonly string[];
  /** 読み取り専用の統合結果（実行ファイルの`sums[]`） */
  readonly sums: readonly RulesSum[];
  readonly hooks: readonly HookEntry[];
  readonly hookFiles: { readonly settings: boolean; readonly settingsLocal: boolean };
  readonly mode: string;
  readonly expectationsPath: string;
  readonly expectationsRel: string;
}

type PanelState = SettingsState<RulesData, Loaded>;

/** 開いているパネル。種類ごとに1枚 */
let state: PanelState | undefined;

/** 読み込み中の一言で「何を」読んでいるか */
function whatOf(target: SettingsTarget): string {
  switch (target.kind) {
    case "workspace":
      return "共通設定のルール";
    case "self":
      return "ワークスペースの設定のルール";
    case "project":
      return `プロジェクト「${target.name}」のルール`;
  }
}

function expectationsSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("expectationsPath", DEFAULT_EXPECTATIONS);
}

/** `ccnaviBoard.openRules`の本体。開いていれば前面に出すだけ。開くときは最後に選んだ対象で、無ければ共通設定で開く */
export async function openRules(): Promise<void> {
  const folder = workspaceFolder("ルール管理画面");
  if (folder === undefined) {
    return;
  }
  if (state !== undefined) {
    state.panel.reveal(state.panel.viewColumn);
    return;
  }

  // 画面とCSSはバンドルしたものを読んで流し込む。無ければ開かずに言う（パネルだけ出しても白いまま）
  try {
    webviewScript(SCREEN);
    webviewStyle(SCREEN);
  } catch (error) {
    vscode.window.showErrorMessage(`ルール管理画面を表示できません: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }

  // タブは読む前に作る。設定ファイルの場所を実行ファイルに聞く間に、押しても何も起きないように見えるのを避けるため。
  // `state`を先に設定するので、読んでいる間に押し直しても上の`reveal`に入る。
  // 読めなかったときもタブは閉じず、中にエラーを出す（`reload`の`showError`）
  const panel = vscode.window.createWebviewPanel("ccnaviRules", TITLE, vscode.ViewColumn.One, {
    enableScripts: true,
    enableForms: false,
    localResourceRoots: [],
    // 編集の途中を持つので、タブを非表示にしても破棄しない。
    retainContextWhenHidden: true,
  });
  const target = targetToOpen({ kind: "workspace" });
  showLoading(panel, TITLE, SCREEN, whatOf(target));
  const current: PanelState = {
    target,
    panel,
    folder,
    tmpDir: fs.mkdtempSync(path.join(os.tmpdir(), TMP_RULES_PREFIX)),
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

async function readPage(root: string, target: SettingsTarget): Promise<Loaded> {
  const settingsText = readText(path.join(root, ".claude", "settings.json"));
  const localText = readText(path.join(root, ".claude", "settings.local.json"));
  let rulesPath: string;
  let rulesRel: string;
  const notices: string[] = [];
  // 切り替えの一覧のために、共通設定でも実行ファイルに聞く。読めなくても共通設定は開けるので、一覧が縮むだけ
  const board = await loadBoard(root, binSetting());
  if (!board.ok) {
    notices.push(`ccnaviから動作モードを取得できないため、denyやaskの判定でツールの実行が実際に止まるかどうかを表示していません: ${board.error}`);
  }
  const targets = targetOptions(board.ok ? board.board : undefined, "workspace", targetKey(target));
  if (target.kind === "workspace") {
    // 共通設定の場所は`.ccnavi/config/common/`固定。env（`CCNAVI_RULES`など）では動かせないので、設定ファイルは読まない。
    rulesRel = DEFAULT_RULES;
    rulesPath = resolveIn(root, rulesRel);
  } else {
    // 設定ファイルの場所は実行ファイルに聞く。`.ccnavi`から自分で組むと、組み方が実行ファイルと
    // 食い違ったときに、この画面で保存したルールが判定に使われなくなる。答えは元リポジトリの版で、
    // ワークツリーの中の版は指さない。
    if (!board.ok) {
      throw new Error(`設定ファイルの場所をccnaviから取得できません: ${board.error}`);
    }
    const layer = target.kind === "self" ? selfLayer(board.board) : projectLayer(board.board, target.name);
    if (layer === undefined || layer.rules.path === "") {
      throw new Error(
        target.kind === "self"
          ? "ccnaviの出力にワークスペースの設定がありません"
          : `プロジェクト「${target.name}」は設定の対象になっていません（プロジェクトのフォルダの直下にないか、名前が予約名のcommonかselfです）`,
      );
    }
    rulesPath = resolveIn(root, layer.rules.path);
    rulesRel = path.relative(root, rulesPath).split(path.sep).join("/");
    if (layer.rules.unreadable !== "") {
      throw unreadableFailure({ path: rulesPath, rel: rulesRel }, layer.rules.unreadable);
    }
  }
  // 無いのは不備ではない（「設定が無い」正常な状態）。空として出し、ルールを足して保存すればファイルが作られる。
  const { text, mtimeMs, exists } = readSettingFile(rulesPath, rulesRel, "ルールファイル");
  const doc = readRules(exists ? text : "version: 1\n");
  if (doc.blocking.length > 0) {
    throw blockedFailure({ path: rulesPath, rel: rulesRel }, doc.blocking);
  }
  const hooks = [
    ...(settingsText === undefined ? [] : parseHooks(settingsText, "settings")),
    ...(localText === undefined ? [] : parseHooks(localText, "settings-local")),
  ];
  const expectationsRel = expectationsSetting();
  return {
    target,
    handoff: HANDOFF_ENTRY,
    exists,
    mtimeMs,
    // 無いファイルは、書式の版だけを持つ空のルールとして読む（版が無いファイルは実行ファイルが読めないので、保存で作るときに先頭へ置く）
    doc,
    file: { path: rulesPath, rel: rulesRel },
    notices,
    targets,
    sums: rulesSums(board.ok ? board.board : undefined),
    hooks,
    hookFiles: { settings: settingsText !== undefined, settingsLocal: localText !== undefined },
    // 動作モードは実行ファイルに聞く。設定ファイルのenvから自分で読むと、settings.local.json・環境・
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

/**
 * ルールファイルと設定ファイルが変わったら「ファイルの変更を検知しました」と伝える。自分の保存は除く。
 * 対象のパスは設定ファイルの場所で変わるので、更新のたびに張り直す。
 */
function watchFiles(current: PanelState): void {
  for (const watcher of current.fileWatchers) {
    watcher.dispose();
  }
  current.fileWatchers = [];
  const rulesRel = current.loaded?.file.rel ?? DEFAULT_RULES;
  current.fileWatchers.push(...watchGlobs(current.folder, [toGlob(rulesRel), ".claude/settings.json", ".claude/settings.local.json"], () => scheduleChanged(current)));
}

function alive(current: PanelState): boolean {
  return state === current;
}

/** この画面の仕事を、3画面で共通の段取り（`settings-panel.ts`）に渡す */
const { registerPanelHandlers, scheduleChanged, reload, stale, fail, save } = settingsPanel<RulesData, Loaded, ToRules, RulesMessage>({
  accepts: true,
  screen: SCREEN,
  title: TITLE,
  words: { file: "ルール", content: "ルール" },
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
      rulesPath: loaded.file.rel,
      exists: loaded.exists,
      mode: loaded.mode,
      model: loaded.doc.model,
      hooks: loaded.hooks,
      hookFiles: loaded.hookFiles,
      expectationsPath: loaded.expectationsRel,
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
 * ルール管理の画面に渡す手段。VS Codeのパネルを`retainedHost`の形に合わせる。
 * 入れ物は1度しか入らないので、表示の有無は渡さない（保持する画面は非表示でも生きている）。
 * パネルの`retainContextWhenHidden`を偽に変えると、送った先が破棄されていても気づけなくなる。
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

/** 編集中の内容を一時ファイルに書き、実行ファイルへ渡す差し替えを返す */
function stage(current: PanelState, loaded: Loaded, sections: Sections): RulesOverride {
  const tmp = path.join(current.tmpDir, "rules.yml");
  fs.writeFileSync(tmp, loaded.doc.apply(sections), "utf8");
  return overrideFor(loaded.target, tmp);
}

function overrideFor(target: SettingsTarget, tmp: string): RulesOverride {
  switch (target.kind) {
    case "workspace":
      return { kind: "workspace", path: tmp };
    case "self":
      return { kind: "self", path: tmp };
    case "project":
      return { kind: "project", name: target.name, path: tmp };
  }
}

/** この画面にだけある操作（3画面で同じものは`settings-panel.ts`が受ける） */
async function handle(current: PanelState, loaded: Loaded, message: RulesMessage): Promise<void> {
  const root = current.folder.uri.fsPath;
  switch (message.type) {
    case "openFile":
      openInEditor(message.which === "rules" ? loaded.file.path : loaded.expectationsPath);
      return;
    case "pickFile": {
      // 選んだファイルはワークスペースルートからの相対で欄に入れる。外を選んだら入れない。
      // 実行ファイルが読むのはルートの中だけで、lintも外を指すパスをerrorにする。
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
      let rules: RulesOverride;
      try {
        rules = stage(current, loaded, message.sections);
      } catch (error) {
        fail(current, `編集中の内容を保存できません: ${(error as Error).message}`);
        return;
      }
      const result = await runTest(root, binSetting(), rules, message.tool, message.subject);
      if (!alive(current) || stale(current, loaded, "判定の試行")) {
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
      if (!fs.existsSync(loaded.expectationsPath)) {
        current.host.post({
          type: "notice",
          message: `期待する判定のファイル（${loaded.expectationsRel}）がまだありません。作ると、ここで一括で確かめられます。書き方は、エージェントに /ccnavi-config と頼むか、.claude/skills/ccnavi-config/references/common/check.md を見てください。`,
        } satisfies ToRules);
        return;
      }
      let rules: RulesOverride;
      try {
        rules = stage(current, loaded, message.sections);
      } catch (error) {
        fail(current, `編集中の内容を保存できません: ${(error as Error).message}`);
        return;
      }
      const result = await runExpectations(root, binSetting(), rules, loaded.expectationsPath);
      if (!alive(current) || stale(current, loaded, "期待する判定の確認")) {
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
      const result = await runSuggest(root, binSetting());
      if (!alive(current) || stale(current, loaded, "候補の取得")) {
        return;
      }
      if (!result.ok) {
        fail(current, result.error);
        return;
      }
      current.host.post({ type: "suggested", result: result.value } satisfies ToRules);
      return;
    }
    case "save":
      await save(current, loaded, () => loaded.doc.apply(message.sections));
      return;
  }
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
