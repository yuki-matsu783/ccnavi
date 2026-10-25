/**
 * 設定を編集する 3 画面（ルール管理・リスク管理・フェーズ管理）のパネルが共通で持つ段取り。
 * VS Code の API に触れるので単体テストの対象外。
 *
 * 3 画面は同じ形をしている。対象（共通の設定・ワークスペース・プロジェクト）を 1 枚のタブで切り替える。
 * 編集の途中を持つので `retainContextWhenHidden` は真。ここにまとめたのは、パネルの
 * 受け付け・再表示・破棄、監視の反応の遅らせ方、読み直しと描き直し、対象の切り替え。
 *
 * 画面ごとに違う仕事（何を読む・何を見せる・どのファイルを見張る・保存できるかをどう聞く）は、
 * `settingsPanel` に渡す `SettingsScreen` のコールバックで注入する。**残す仕事は画面ごとに必要で、残し忘れると
 * 不具合になる**ので、ここには画面ごとの差を持たせず、差は必ず呼ぶ側に置く。
 */
import * as fs from "node:fs";
import * as vscode from "vscode";

import { postAppearance } from "./appearance.js";
import { lockFromError, type Lock } from "./core/lock.js";
import type { ScreenHost } from "./core/screen-host.js";
import { OWN_WRITE_GRACE_MS } from "./core/watch.js";
import { debounce, watchTickets } from "./panel-common.js";

/** 3 画面のパネルが持つ状態。画面ごとに違うのは、画面に渡す形（`D`）・対象（`T`）・読んだ結果（`L`）だけ */
export interface SettingsState<D, T, L> {
  /** いま見せている対象。別の対象を開くと入れ替わる（タブは 1 枚） */
  target: T;
  readonly panel: vscode.WebviewPanel;
  readonly folder: vscode.WorkspaceFolder;
  readonly tmpDir: string;
  readonly host: ScreenHost<D>;
  /** 編集対象と設定ファイルの監視。対象のパスが変わるので、再読み込みのたびに張り直す */
  fileWatchers: vscode.FileSystemWatcher[];
  /** チケットの置き場の監視。開いている間ずっと同じ */
  watchers: vscode.FileSystemWatcher[];
  timer?: NodeJS.Timeout;
  lockTimer?: NodeJS.Timeout;
  loaded?: L;
  /** 読み直せなかった理由。`loaded` と排他で、どちらかは必ず入っている */
  error?: string;
  lock: Lock;
  /** 「ファイルの変更を検知しました」を出したまま、まだ読み直していない。再表示されたときに送り直す */
  changedPending: boolean;
  wroteAt: number;
  /** 画面に未保存の変更があるか（画面が `dirty` で知らせる）。別の対象へ切り替えるときに聞くかを決める */
  dirty: boolean;
  /**
   * 読み直しの番号。読むたびに 1 つ進め、読み終えたときに変わっていたら破棄する。
   * 設定ファイルの場所を実行ファイルに聞く間に別の対象へ切り替わると、前の対象の答えが後から届くため
   */
  seq: number;
  /** 「破棄して切り替える？」を出している間は真。重ねて開かれても 2 枚目の問いを出さない */
  asking: boolean;
}

/** ここから画面へ送る知らせ。画面の契約（`To*`）に入っていなければならない（`accepts` で型が確かめる） */
export type SettingsNotice = { readonly type: "lock"; readonly lock: Lock } | { readonly type: "changed" };

/** 画面ごとの仕事。`D` 画面に渡す形、`T` 対象、`L` 読んだ結果、`M` 画面の契約（`To*`） */
export interface SettingsScreen<D, T, L, M> {
  /** `true` と書く。ここから送るので、`lock` と `changed` が `M` に入っていないと型が合わなくなる */
  readonly accepts: SettingsNotice extends M ? true : never;
  /** このパネルがまだ開いている現役のものか */
  alive(current: SettingsState<D, T, L>): boolean;
  /** 閉じた。開いているパネルの札（モジュールの `state`）が `current` なら外す */
  release(current: SettingsState<D, T, L>): void;
  /** 画面からの操作の受け付け（受け取った値の型の絞り込みも含む） */
  handleMessage(current: SettingsState<D, T, L>, message: unknown): void;
  /** 対象の中身を読む。読めなければ投げる */
  read(root: string, target: T): Promise<L>;
  /** 読めた中身を渡す（`current.loaded` が入っている） */
  show(current: SettingsState<D, T, L>): void;
  /** 読めなかったことを見せる */
  showError(current: SettingsState<D, T, L>, error: string): void;
  /** 編集対象と設定ファイルの監視を張り直す */
  watchFiles(current: SettingsState<D, T, L>): void;
  /** 保存できるかを取り直して画面へ知らせる */
  refreshLock(current: SettingsState<D, T, L>): Promise<unknown>;
  /** いまの対象を読んでいる最中に出す「読み込み中」 */
  loading(current: SettingsState<D, T, L>): D;
  sameTarget(a: T, b: T): boolean;
  /** 確認の文で使う対象の名前 */
  targetLabel(target: T): string;
}

/** `settingsPanel` が返す、画面のパネルから呼ぶ関数 */
export interface SettingsPanel<D, T, L> {
  /** パネルを作った直後に 1 度呼ぶ。受け付け・再表示・破棄と、監視を繋ぐ */
  registerPanelHandlers(current: SettingsState<D, T, L>): void;
  /** 設定ファイルが変わったと知らせる（自分の保存は除く） */
  scheduleChanged(current: SettingsState<D, T, L>): void;
  /** 保存できるかの取り直しを、反応を束ねて頼む */
  scheduleLock(current: SettingsState<D, T, L>): void;
  reload(current: SettingsState<D, T, L>): Promise<void>;
  /**
   * いまの状態で渡し直す。1 枚目を読み込んでいる間に見送られたもの（`deferred`）は、画面が
   * 組み上がった（`ready`）ところでここから渡る。
   */
  redraw(current: SettingsState<D, T, L>): void;
  /**
   * 開いているタブの対象を入れ替える。未保存の変更があれば、破棄するかを先に聞く。
   * やめたらタブは前面に出たまま、前の対象と編集が残る。
   *
   * 読み終えるまでは前の対象の中身を消して「読み込み中」を見せ、操作も受けない（`loaded` を空にする）。
   * 前の対象の保存が往復の途中なら、画面の `stale` が破棄する。
   */
  switchTarget(current: SettingsState<D, T, L>, target: T): Promise<void>;
}

export function settingsPanel<D, T, L, M>(screen: SettingsScreen<D, T, L, M>): SettingsPanel<D, T, L> {
  type S = SettingsState<D, T, L>;

  function registerPanelHandlers(current: S): void {
    const { panel, folder } = current;

    panel.webview.onDidReceiveMessage((message: unknown) => {
      screen.handleMessage(current, message);
    });

    // 保持する画面は非表示でも生きていて、`postMessage` は届く。VS Code の文書は、同じ型定義の中で
    // 記述が食い違っている（`retainContextWhenHidden` の側は「非表示の画面には送れない」と言う）。
    // どちらが正しくても困らないよう、再表示されたところで、いま出すべき知らせを送り直す。
    // 中身（`data`）は送らない。送ると、非表示の間に打っていた編集がここで消える。
    // 見た目（`appearance`）も同じ扱い。保持しない画面は入れ物から作り直されるので `ready` で渡るが、
    // 保持する画面は作り直されないので、非表示の間の切り替えが届いていなかったらここでしか気づけない。
    panel.onDidChangeViewState(() => {
      if (!panel.visible || !screen.alive(current)) {
        return;
      }
      current.host.post({ type: "lock", lock: current.lock } satisfies SettingsNotice);
      postAppearance(current.host);
      if (current.changedPending) {
        current.host.post({ type: "changed" } satisfies SettingsNotice);
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

  function scheduleChanged(current: S): void {
    if (Date.now() - current.wroteAt < OWN_WRITE_GRACE_MS) {
      return;
    }
    debounce(current, "timer", () => {
      if (screen.alive(current)) {
        current.changedPending = true;
        current.host.post({ type: "changed" } satisfies SettingsNotice);
      }
    });
  }

  function scheduleLock(current: S): void {
    debounce(current, "lockTimer", () => {
      if (screen.alive(current)) {
        void screen.refreshLock(current);
      }
    });
  }

  async function reload(current: S): Promise<void> {
    current.seq += 1;
    const seq = current.seq;
    let loaded: L;
    try {
      loaded = await screen.read(current.folder.uri.fsPath, current.target);
    } catch (error) {
      if (screen.alive(current) && current.seq === seq) {
        screen.showError(current, (error as Error).message);
      }
      return;
    }
    // 読んでいる間に別の対象へ切り替わった（または読み直しが重なった）なら、後から来たほうに任せる
    if (!screen.alive(current) || current.seq !== seq) {
      return;
    }
    current.loaded = loaded;
    screen.show(current);
    screen.watchFiles(current);
    void screen.refreshLock(current);
  }

  function redraw(current: S): void {
    if (current.loaded !== undefined) {
      screen.show(current);
      return;
    }
    if (current.error !== undefined) {
      screen.showError(current, current.error);
      return;
    }
    // 切り替え先を読んでいる最中
    current.host.send(screen.loading(current));
  }

  async function switchTarget(current: S, target: T): Promise<void> {
    if (current.dirty) {
      // 問いを出している間に別の対象を開かれたら、前面に出すだけにする（問いは先に出したものに答えてもらう）
      if (current.asking) {
        return;
      }
      current.asking = true;
      const choice = await vscode.window.showWarningMessage(
        `未保存の変更があります。破棄して「${screen.targetLabel(target)}」に切り替えますか？`,
        { modal: true },
        "切り替える",
      );
      current.asking = false;
      // 問いを出している間にパネルを閉じられる。切り替え先が同じになっていることもある（2 度押した）
      if (choice !== "切り替える" || !screen.alive(current) || screen.sameTarget(current.target, target)) {
        return;
      }
    }
    current.target = target;
    current.loaded = undefined;
    current.error = undefined;
    current.dirty = false;
    current.changedPending = false;
    current.lock = lockFromError("確認中…");
    // 前の対象のファイルの監視は外す。切り替え先が読めたら `reload` が張り直す。読めずにエラーのままなら、
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

  return { registerPanelHandlers, scheduleChanged, scheduleLock, reload, redraw, switchTarget };
}
