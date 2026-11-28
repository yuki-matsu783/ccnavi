/**
 * 設定を編集する3画面（ルール管理・リスク管理・フェーズ管理）のパネルが共通で持つ段取り。
 * VS CodeのAPIに触れるので単体テストの対象外（`vscode`を差し替えて試せる所だけ試す）。
 *
 * 3画面は同じ形をしている。対象（共通の設定・ワークスペース・プロジェクト）を1枚のタブで切り替える。
 * 編集の途中を持つので`retainContextWhenHidden`は真。ここにまとめたのは、パネルの
 * 受け付け・再表示・破棄、監視の反応の遅らせ方、読み直しと描き直し、対象の切り替え、保存できるかの取り直し、
 * 画面からの操作のうち3画面で同じもの（`SettingsMessage`）の受け付け、保存の流れ（`save`）。
 *
 * 保存の流れは、一時ファイルに書く → 検証（`--lint`）→ 作業中のチケットが無いこと → 書く → 読み直す →
 * warnの帯 → 通知。書くのは`core/flow-write.ts`の`writeTreeFile`で、フロー・テンプレートの保存と同じく、
 * ツリーの外とリンクを断り、一時ファイルに書いて入れ替え、入れ替える直前にもう一度外の変化を確かめる。
 *
 * 画面ごとに違う仕事（何を読む・何を見せる・どのファイルを見張る・検証に何を渡す・画面にだけある操作）は、
 * `settingsPanel`に渡す`SettingsScreen`のコールバックで注入する。**残す仕事は画面ごとに必要で、残し忘れると
 * 不具合になる**ので、ここには画面ごとの差を持たせず、差は必ず呼ぶ側に置く。
 */
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

import { postAppearance } from "./appearance.js";
import { loadBoard, runLint, type LintOverride } from "./ccnavi.js";
import { writeTreeFile } from "./core/flow-write.js";
import type { HandoffEntry } from "./core/handoff-entry.js";
import { lintSummary } from "./core/lint-summary.js";
import { errorLinesOf, warnLinesOf } from "./core/lintmodel.js";
import { LoadFailure, type ErrorFile } from "./core/load-failure.js";
import { lockFromBoard, lockFromError, type Lock } from "./core/lock.js";
import { dirtyTitle } from "./core/save-gate.js";
import type { ScreenHost } from "./core/screen-host.js";
import type { RulesTarget } from "./core/screens.js";
import { targetLabel } from "./core/targets.js";
import { targetOptions, type TargetOption } from "./core/targets.js";
import { OWN_WRITE_GRACE_MS } from "./core/watch.js";
import { openHandoffFrom } from "./handoff-entry.js";
import { debounce, openInEditor, watchTickets } from "./panel-common.js";
import { markTourSeen, tourSeen } from "./tour.js";
import { confirmDuringTurn } from "./turn-confirm.js";

/** 3画面の対象。共通の設定（`workspace`）、ワークスペースの設定（`self`）、プロジェクト1つの設定（同じ3種） */
export type SettingsTarget = RulesTarget;

/** 共通の段取りが読む、読んだ結果の部分。画面ごとの`L`はこれに画面の分を足す */
export interface SettingsLoaded {
  /** 読んだ対象。往復の間に切り替わったかを`stale`が見る */
  readonly target: SettingsTarget;
  /** 編集するファイルが在るか。無ければ保存で作る */
  readonly exists: boolean;
  /** 無いときは0 */
  readonly mtimeMs: number;
  /** 編集するファイル。`rel`はワークスペースルートからの相対で見せるパス */
  readonly file: { readonly path: string; readonly rel: string };
  /** 取り込み待ち画面を開けるか */
  readonly handoff: HandoffEntry;
  /** 切り替えられる対象（共通の設定・ワークスペース・設定のあるプロジェクト） */
  readonly targets: readonly TargetOption[];
}

/** 3画面のパネルが持つ状態。画面ごとに違うのは、画面に渡す形（`D`）と読んだ結果（`L`）だけ */
export interface SettingsState<D, L extends SettingsLoaded> {
  /** いま見せている対象。別の対象を開くと入れ替わる（タブは1枚） */
  target: SettingsTarget;
  readonly panel: vscode.WebviewPanel;
  readonly folder: vscode.WorkspaceFolder;
  readonly tmpDir: string;
  readonly host: ScreenHost<D>;
  /** 編集対象と設定ファイルの監視。対象のパスが変わるので、更新のたびに張り直す */
  fileWatchers: vscode.FileSystemWatcher[];
  /** チケットの置き場の監視。開いている間ずっと同じ */
  watchers: vscode.FileSystemWatcher[];
  timer?: NodeJS.Timeout;
  lockTimer?: NodeJS.Timeout;
  loaded?: L;
  /** 読み直せなかった理由。`loaded`と排他で、どちらかは必ず入っている */
  error?: string;
  /** 読めなかったファイル（分かるとき）。エラー画面の［エディタで開く］が使う。`error`と一緒に入る */
  errorFile?: ErrorFile;
  lock: Lock;
  /** 「ファイルの変更を検知しました」を出したまま、まだ読み直していない。再表示されたときに送り直す */
  changedPending: boolean;
  wroteAt: number;
  /** 画面に未保存の変更があるか（画面が`dirty`で知らせる）。別の対象へ切り替えるときに聞くかを決める */
  dirty: boolean;
  /**
   * 読み直しの番号。読むたびに1つ進め、読み終えたときに変わっていたら破棄する。
   * 設定ファイルの場所を実行ファイルに聞く間に別の対象へ切り替わると、前の対象の答えが後から届くため
   */
  seq: number;
  /** 「破棄して切り替える？」を出している間は真。重ねて開かれても2枚目の問いを出さない */
  asking: boolean;
}

/** ここから画面へ送る知らせ。画面の契約（`To*`）に入っていなければならない（`accepts`で型が確かめる） */
export type SettingsNotice =
  | { readonly type: "lock"; readonly lock: Lock }
  | { readonly type: "changed" }
  | { readonly type: "failed"; readonly message: string }
  | { readonly type: "cancelled" }
  | { readonly type: "tour" }
  | { readonly type: "saved"; readonly warns: readonly string[] };

/** 画面からの操作のうち、ここが受けるもの。画面の契約（`*Message`）に入っていなければならない（`accepts`で型が確かめる） */
export type SettingsMessage =
  | { readonly type: "ready" }
  | { readonly type: "dirty"; readonly dirty: boolean }
  | { readonly type: "tourDone" }
  | { readonly type: "reload"; readonly dirty: boolean }
  | { readonly type: "openHandoff" }
  | { readonly type: "switchTarget"; readonly kind: string; readonly name: string };

const SETTINGS_MESSAGES: ReadonlySet<string> = new Set<SettingsMessage["type"]>(["ready", "dirty", "tourDone", "reload", "openHandoff", "switchTarget"]);

function isSettingsMessage(message: { readonly type: string }): message is SettingsMessage {
  return SETTINGS_MESSAGES.has(message.type);
}

/** 画面ごとの仕事。`D`画面に渡す形、`L`読んだ結果、`To`画面への契約（`To*`）、`From`画面からの契約（`*Message`） */
export interface SettingsScreen<D, L extends SettingsLoaded, To, From extends { readonly type: string }> {
  /** `true`と書く。ここから送る知らせと、ここが受ける操作が、画面の契約に入っていないと型が合わなくなる */
  readonly accepts: SettingsNotice extends To ? (SettingsMessage extends From ? true : never) : never;
  /** 画面の名前（吹き出しの案内を見たかの記録に使う） */
  readonly screen: string;
  /** タブのタイトル。未保存の間は頭に「●」を付ける（`setDirty`） */
  readonly title: string;
  /** 文で使う呼び名。`file`は編集するファイル（「フェーズ定義」→「フェーズ定義のファイル」）、`content`は中身（「更新後の定義」） */
  readonly words: { readonly file: string; readonly content: string };
  /** このパネルがまだ開いている現役のものか */
  alive(current: SettingsState<D, L>): boolean;
  /** 閉じた。開いているパネルのバッジ（モジュールの`state`）が`current`なら外す */
  release(current: SettingsState<D, L>): void;
  /** 受け取った値を画面からの操作に絞り込む。形が違えばundefined */
  parse(message: unknown): From | undefined;
  /**
   * 画面にだけある操作（`SettingsMessage`以外）。読めている（`loaded`が入っている）ときだけ呼ばれる。
   * `SettingsMessage`の分はここへ来ない
   */
  handle(current: SettingsState<D, L>, loaded: L, message: From): Promise<void>;
  /** 対象の中身を読む。読めなければ投げる */
  read(root: string, target: SettingsTarget): Promise<L>;
  /** 読めた中身を渡す（`current.loaded`が入っている） */
  show(current: SettingsState<D, L>): void;
  /** 読めなかったことを見せる */
  showError(current: SettingsState<D, L>, error: string, file?: ErrorFile): void;
  /** 編集対象と設定ファイルの監視を張り直す */
  watchFiles(current: SettingsState<D, L>): void;
  /** いまの対象を読んでいる最中に出す「読み込み中」 */
  loading(current: SettingsState<D, L>): D;
  /** 保存の前の検証（`--lint`）に渡す差し替え。`tmp`は編集中の内容を書いた一時ファイル */
  override(target: SettingsTarget, tmp: string): LintOverride;
}

/** `settingsPanel`が返す、画面のパネルから呼ぶ関数 */
export interface SettingsPanel<D, L extends SettingsLoaded> {
  /** パネルを作った直後に1度呼ぶ。受け付け・再表示・破棄と、監視を繋ぐ */
  registerPanelHandlers(current: SettingsState<D, L>): void;
  /** 画面からの操作を受ける（`registerPanelHandlers`が繋ぐ） */
  handleMessage(current: SettingsState<D, L>, message: unknown): Promise<void>;
  /** 設定ファイルが変わったと知らせる（自分の保存は除く） */
  scheduleChanged(current: SettingsState<D, L>): void;
  /** 保存できるかの取り直しを、反応を束ねて頼む */
  scheduleLock(current: SettingsState<D, L>): void;
  reload(current: SettingsState<D, L>): Promise<void>;
  /**
   * いまの状態で渡し直す。1枚目を読み込んでいる間に見送られたもの（`deferred`）は、画面が
   * 組み上がった（`ready`）ところでここから渡る。
   */
  redraw(current: SettingsState<D, L>): void;
  /**
   * 開いているタブの対象を入れ替える。未保存の変更があれば、破棄するかを先に聞く。
   * やめたらタブは前面に出たまま、前の対象と編集が残る。
   *
   * 読み終えるまでは前の対象の中身を消して「読み込み中」を見せ、操作も受けない（`loaded`を空にする）。
   * 前の対象の保存が往復の途中なら、`stale`が破棄する。
   */
  switchTarget(current: SettingsState<D, L>, target: SettingsTarget): Promise<void>;
  /**
   * 未保存かを覚え、タブのタイトルの頭の「●」を付け外しする。Webviewは閉じるのを止められないので、
   * 閉じる前に未保存だと気づけるようにする（フロー編集・計画編集・テンプレート管理と同じ印）
   */
  setDirty(current: SettingsState<D, L>, dirty: boolean): void;
  /** 保存できるかを実行ファイルに聞き、画面へ知らせる。確かめられなければ閉じる側 */
  refreshLock(current: SettingsState<D, L>): Promise<Lock>;
  /**
   * 操作を受けたときに読んでいたもの（`loaded`）が、往復の間に入れ替わっていないか。`what`は破棄するもの（「この保存」など）。
   *
   * 保存は実行ファイルへ2度出る（`--lint`と錠の取り直し）。その間にユーザが「更新」を押せば、
   * 画面の編集は破棄され、新しい中身が出ている。そこへ古い編集を書くと、破棄したはずのものが
   * ファイルに入る。ファイルに触らない操作（ルール管理の判定など）も、破棄した編集で出した答えを
   * いまの中身の答えとして見せることになるので、同じく無かったことにする。
   */
  stale(current: SettingsState<D, L>, loaded: L, what: string): boolean;
  /** 操作の結果の一言。1枚目を読み込んでいる間だけ届かずに破棄される（非表示になっていても届く） */
  fail(current: SettingsState<D, L>, message: string): void;
  /**
   * 保存する。`compose`は書く本文（投げたら保存しない）。検証（`--lint`）を通り、作業中のチケットが無く、
   * 読み込んでからファイルが外で変わっていないときだけ書く。画面だけが持つ断り（リスク管理の組み込みの配点など）は、呼ぶ前に済ませる
   */
  save(current: SettingsState<D, L>, loaded: L, compose: () => string): Promise<void>;
}

/** 実行ファイルの場所の設定 */
export function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

export function sameTarget(a: SettingsTarget, b: SettingsTarget): boolean {
  return a.kind === b.kind && (a.kind !== "project" || (b.kind === "project" && a.name === b.name));
}

/** 開いている対象の欄の値（`targets.ts`の`kind`と`name`） */
export function targetKey(target: SettingsTarget): { kind: string; name: string } {
  return { kind: target.kind, name: target.kind === "project" ? target.name : "" };
}

/**
 * 設定ファイルを読む。無い（ENOENT）のは「設定が無い」正常な状態で、空（`exists`が偽）として返す。
 * ほかの読めない理由は投げる。`what`は失敗の文で使うファイルの呼び名、`rel`はその場所
 */
export function readSettingFile(filePath: string, rel: string, what: string): { text: string; mtimeMs: number; exists: boolean } {
  try {
    const text = fs.readFileSync(filePath, "utf8");
    return { text, mtimeMs: fs.statSync(filePath).mtimeMs, exists: true };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      throw new Error(`${what}を読み込めません（${rel}）: ${(error as Error).message}`);
    }
    return { text: "", mtimeMs: 0, exists: false };
  }
}

/** 確認の文で使う対象の名前。「設定」欄の表示（core/targets.tsのlabel）をそのまま使う */
function targetLabelOf(target: SettingsTarget): string {
  return targetLabel(target.kind, target.kind === "project" ? target.name : "");
}

/** 保存を止めるチケットを絞るプロジェクト。共通の設定とワークスペースの設定は絞らない（どのツリーのdoingでも止める） */
function projectOf(target: SettingsTarget): string | undefined {
  return target.kind === "project" ? target.name : undefined;
}

export function settingsPanel<D, L extends SettingsLoaded, To, From extends { readonly type: string }>(
  screen: SettingsScreen<D, L, To, From>,
): SettingsPanel<D, L> {
  type S = SettingsState<D, L>;

  function post(current: S, message: SettingsNotice): void {
    current.host.post(message);
  }

  function registerPanelHandlers(current: S): void {
    const { panel, folder } = current;

    panel.webview.onDidReceiveMessage((message: unknown) => {
      void handleMessage(current, message);
    });

    // 保持する画面は非表示でも生きていて、`postMessage`は届く。VS Codeの文書は、同じ型定義の中で
    // 記述が食い違っている（`retainContextWhenHidden`の側は「非表示の画面には送れない」と言う）。
    // どちらが正しくても困らないよう、再表示されたところで、いま出すべき知らせを送り直す。
    // 中身（`data`）は送らない。送ると、非表示の間に打っていた編集がここで消える。
    // 見た目（`appearance`）も同じ扱い。保持しない画面は入れ物から作り直されるので`ready`で渡るが、
    // 保持する画面は作り直されないので、非表示の間の切り替えが届いていなかったらここでしか気づけない。
    panel.onDidChangeViewState(() => {
      if (!panel.visible || !screen.alive(current)) {
        return;
      }
      post(current, { type: "lock", lock: current.lock });
      postAppearance(current.host);
      if (current.changedPending) {
        post(current, { type: "changed" });
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
      screen.release(current);
    });

    screen.watchFiles(current);
    // チケットが動いたら、保存できるかを取り直す。
    current.watchers.push(...watchTickets(folder, () => scheduleLock(current)));
  }

  async function handleMessage(current: S, raw: unknown): Promise<void> {
    const message = screen.parse(raw);
    if (message === undefined || !screen.alive(current)) {
      return;
    }
    if (isSettingsMessage(message)) {
      await handleSettingsMessage(current, message);
      return;
    }
    // 読み直せていない画面では、中身に触る操作はどれも行き先が無い。開けるのは読めなかったファイルだけ
    // （「更新」と対象の切り替えは上で受ける）
    const loaded = current.loaded;
    if (loaded === undefined) {
      if (message.type === "openFile" && current.errorFile !== undefined) {
        openInEditor(current.errorFile.path);
      }
      return;
    }
    await screen.handle(current, loaded, message);
  }

  async function handleSettingsMessage(current: S, message: SettingsMessage): Promise<void> {
    switch (message.type) {
      case "dirty":
        setDirty(current, message.dirty);
        return;
      case "ready":
        // 組み上がったばかりの画面は必ず「変更なし」で始まる（作り直された画面は前の編集を持たない）
        setDirty(current, false);
        // 画面が組み上がった。1枚目を読み込んでいる間に見送った中身は、ここで渡る。
        // 見送るものが無くても渡し直す（同じ中身がもう1度届く）。VS Codeが画面を作り直す経路
        // （`Developer: Reload Webviews`）では、入れてあるHTMLの中身が古いことがあるため
        current.host.ready();
        redraw(current);
        postAppearance(current.host);
        // 初回だけ吹き出しの案内を頼む。画面は指す先が出てから始め、閉じたら`tourDone`を返す。
        // 閉じずにタブを閉じたら見た記録は残らないので、次に開いたときにもう1度出る
        if (!tourSeen(screen.screen)) {
          post(current, { type: "tour" });
        }
        return;
      case "tourDone":
        markTourSeen(screen.screen);
        return;
      case "reload": {
        if (message.dirty) {
          const choice = await vscode.window.showWarningMessage("未保存の変更があります。破棄して更新しますか？", { modal: true }, "更新");
          if (choice !== "更新") {
            // 画面は「更新」を押した時点で欄を止めている。やめたことを伝えないと止まったままになる。
            // 問いを出している間にパネルを閉じられるので、送る前に生きているかを見る
            if (screen.alive(current)) {
              post(current, { type: "cancelled" });
            }
            return;
          }
        }
        await reload(current);
        return;
      }
      case "openHandoff":
        // 設定のプロジェクトに合わせて、そのプロジェクトのツリーの下書きを開く
        await openHandoffFrom(current.loaded?.handoff, current.loaded?.target ?? current.target);
        return;
      case "switchTarget": {
        // 一覧にある対象だけを受ける。画面が古いまま、消えたプロジェクトを指していても開かない。
        // 読めていないとき（エラー画面）は、開いている対象と共通・ワークスペースだけが一覧に出ている
        const option = (current.loaded?.targets ?? targetOptions(undefined, "workspace", targetKey(current.target))).find(
          (t) => t.kind === message.kind && t.name === message.name,
        );
        if (option === undefined) {
          return;
        }
        const target: SettingsTarget =
          option.kind === "project" ? { kind: "project", name: option.name } : option.kind === "self" ? { kind: "self" } : { kind: "workspace" };
        if (!sameTarget(current.target, target)) {
          await switchTarget(current, target);
        }
        return;
      }
    }
  }

  function scheduleChanged(current: S): void {
    if (Date.now() - current.wroteAt < OWN_WRITE_GRACE_MS) {
      return;
    }
    debounce(current, "timer", () => {
      if (screen.alive(current)) {
        current.changedPending = true;
        post(current, { type: "changed" });
      }
    });
  }

  function scheduleLock(current: S): void {
    debounce(current, "lockTimer", () => {
      if (screen.alive(current)) {
        void refreshLock(current);
      }
    });
  }

  async function refreshLock(current: S): Promise<Lock> {
    const result = await loadBoard(current.folder.uri.fsPath, binSetting());
    const lock = result.ok ? lockFromBoard(result.board, projectOf(current.target)) : lockFromError(result.error);
    if (screen.alive(current)) {
      current.lock = lock;
      post(current, { type: "lock", lock });
    }
    return lock;
  }

  async function reload(current: S): Promise<void> {
    current.seq += 1;
    const seq = current.seq;
    let loaded: L;
    try {
      loaded = await screen.read(current.folder.uri.fsPath, current.target);
    } catch (error) {
      if (screen.alive(current) && current.seq === seq) {
        const file = error instanceof LoadFailure ? error.file : undefined;
        current.errorFile = file;
        screen.showError(current, (error as Error).message, file);
      }
      return;
    }
    // 読んでいる間に別の対象へ切り替わった（または読み直しが重なった）なら、後から来たほうに任せる
    if (!screen.alive(current) || current.seq !== seq) {
      return;
    }
    current.loaded = loaded;
    current.errorFile = undefined;
    screen.show(current);
    screen.watchFiles(current);
    void refreshLock(current);
  }

  function redraw(current: S): void {
    if (current.loaded !== undefined) {
      screen.show(current);
      return;
    }
    if (current.error !== undefined) {
      screen.showError(current, current.error, current.errorFile);
      return;
    }
    // 切り替え先を読んでいる最中
    current.host.send(screen.loading(current));
  }

  async function switchTarget(current: S, target: SettingsTarget): Promise<void> {
    if (current.dirty) {
      // 問いを出している間に別の対象を開かれたら、前面に出すだけにする（問いは先に出したものに答えてもらう）
      if (current.asking) {
        return;
      }
      current.asking = true;
      const choice = await vscode.window.showWarningMessage(
        `未保存の変更があります。破棄して「${targetLabelOf(target)}」に切り替えますか？`,
        { modal: true },
        "切り替える",
      );
      current.asking = false;
      // 問いを出している間にパネルを閉じられる。切り替え先が同じになっていることもある（2度押した）
      if (choice !== "切り替える" || !screen.alive(current) || sameTarget(current.target, target)) {
        return;
      }
    }
    current.target = target;
    current.loaded = undefined;
    current.error = undefined;
    setDirty(current, false);
    current.changedPending = false;
    current.lock = lockFromError("確認中…");
    // 前の対象のファイルの監視は外す。切り替え先が読めたら`reload`が張り直す。読めずにエラーのままなら、
    // 前の対象の変化を「ファイルの変更を検知しました」と拾い続けない
    if (current.timer !== undefined) {
      clearTimeout(current.timer);
      current.timer = undefined;
    }
    for (const watcher of current.fileWatchers) {
      watcher.dispose();
    }
    current.fileWatchers = [];
    current.host.send(screen.loading(current));
    await reload(current);
  }

  function setDirty(current: S, dirty: boolean): void {
    current.dirty = dirty;
    if (screen.alive(current)) {
      current.panel.title = dirtyTitle(screen.title, dirty);
    }
  }

  function stale(current: S, loaded: L, what: string): boolean {
    if (current.loaded === loaded) {
      return false;
    }
    // 往復の間に別の対象へ切り替わった。どちらも破棄する。切り替え先の画面には前の対象の話を出さない
    if (!sameTarget(loaded.target, current.target)) {
      return true;
    }
    fail(current, `画面を更新したので、${what}は取りやめました。更新後の${screen.words.content}でやり直してください`);
    return true;
  }

  function fail(current: S, message: string): void {
    post(current, { type: "failed", message });
  }

  async function save(current: S, loaded: L, compose: () => string): Promise<void> {
    const root = current.folder.uri.fsPath;
    const { file } = loaded;
    let tmp: string;
    let text: string;
    try {
      text = compose();
      tmp = path.join(current.tmpDir, path.basename(file.path));
      fs.writeFileSync(tmp, text, "utf8");
    } catch (error) {
      fail(current, `編集中の内容を保存できません: ${(error as Error).message}`);
      return;
    }

    // 1. 検証。errorが1件でもあれば保存しない。実行ファイルは共通の設定と足した形で確かめる。
    const lint = await runLint(root, binSetting(), screen.override(loaded.target, tmp));
    if (!screen.alive(current) || stale(current, loaded, "この保存")) {
      return;
    }
    if (!lint.ok) {
      fail(current, lint.error);
      return;
    }
    if (!lint.value.ok) {
      // 指摘は渡した一時ファイルのパスで出るので、画面では対象のファイルのパスに直す。
      fail(current, `設定の検証（--lint）でエラーが見つかりました。内容を修正してから保存してください。\n${errorLinesOf(lint.value.report, tmp, file.rel).join("\n")}`);
      return;
    }

    // 2. 作業中のチケットが無いこと。押した時点で取り直す。
    const lock = await refreshLock(current);
    if (!screen.alive(current) || stale(current, loaded, "この保存")) {
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
    // 確かめを待つ間に、対象の切り替えや「更新」で読み込みが入れ替わっていたら、古い編集を書かない
    if (!screen.alive(current) || stale(current, loaded, "この保存")) {
      return;
    }

    // 3. 書く。読み込んでから外で変わっていないこと（無かったファイルはまだ無いこと）、ツリーの外とリンクを断ること、
    //    一時ファイルの入れ替えは`writeTreeFile`が受け持つ。実行ファイルは設定のファイルに大きさの上限を持たないので渡さない
    current.wroteAt = Date.now();
    const written = writeTreeFile(root, file.path, text, { exists: loaded.exists, mtimeMs: loaded.mtimeMs }, screen.words.file, undefined);
    if (!written.ok) {
      // 書けなかったのに猶予を残したままだと、その間の実際の外部変更が知らされない。
      current.wroteAt = 0;
      fail(current, written.error);
      return;
    }
    await reload(current);
    // warnの本文は画面上部の帯に出す（通知は件数だけ）。中身（data）を渡したあとに送る
    const warns = warnLinesOf(lint.value.report, tmp, file.rel);
    if (warns.length > 0 && screen.alive(current)) {
      post(current, { type: "saved", warns });
    }
    vscode.window.showInformationMessage(`${file.rel}に保存しました（${lintSummary(lint.value.report)}）`);
  }

  return { registerPanelHandlers, handleMessage, scheduleChanged, scheduleLock, reload, redraw, switchTarget, setDirty, refreshLock, stale, fail, save };
}
