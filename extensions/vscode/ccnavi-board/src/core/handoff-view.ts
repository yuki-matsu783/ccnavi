/**
 * 取り込み待ち画面の、拡張ホストと Webview の間の契約。見せるのは `ccnavi user handoff --json`（見るだけ）の答えで、
 * `wip/handoff/<相対パス>` に置いた下書きを、同じツリーの元のパス（`.claude` の階層は `handoff-.claude` に読み替えて置かれ、取り込みで戻る）へ取り込むもの。
 *
 * 判定（取り込み先の種類・状態・検査・差分・ハッシュ）は実行ファイルが答え、画面は読むだけにする。
 * 画面が決めるのは、どれを取り込むか（選択）と、取り込めない理由の言い方だけ。読み取りは `handoffmodel.ts` の側に置く。
 * 画面は React で組み、拡張ホストは HTML を組み立てない（`templates-view.ts` と同じ作り）。ここには VS Code の API も DOM も node も入れない。
 */
import type { AppearanceMessage } from "./appearance.js";
import type { Lock } from "./lock.js";
import { embedJson, type DataMessage } from "./screen-host.js";

// ---- 答えの形（`user handoff --json`）

/** 取り込み先の種類。知らない値も通す（実行ファイルが新しい種類を足しても、画面は止めない） */
export type HandoffKind = "template" | "layer-config" | "docs" | "defense" | (string & {});

/** 対象の状態。知らない値も通すが、取り込めるのは `new` と `changed` だけ */
export type HandoffState = "new" | "changed" | "unchanged" | "rejected" | (string & {});

/** 対象 1 件 */
export interface HandoffEntry {
  /** ツリーからの相対パス。`/` 区切り */
  readonly path: string;
  readonly kind: HandoffKind;
  readonly state: HandoffState;
  /** ボードから取り込めるか。偽（`defense`）はターミナルでだけ取り込む */
  readonly board: boolean;
  /** 見るだけの呼び出しで指した対象か（パスを省いたときは、許可リストにヒットし unchanged でないもの全部） */
  readonly selected: boolean;
  readonly errors: readonly string[];
  readonly warnings: readonly string[];
  /** 差分の文。バイト列の違いは `\uXXXX` などの見える形で入っている */
  readonly diff: string;
  /** 対象ごとのハッシュ。見せたものと取り込む直前のものが同じかを見るのに使う */
  readonly entry_sha: string;
}

/** 対象外にした 1 件。`why` は `not-allowed`（種類の表に無い）か `linked-dir`（リンクのディレクトリ）。ほかの値はそのまま扱う */
export interface HandoffIgnored {
  readonly path: string;
  readonly why: string;
}

/** `user handoff [<パス>…] --json` の答えの全体 */
export interface HandoffList {
  readonly version: number;
  /** 見たツリーの絶対パス */
  readonly tree: string;
  /** 取り込める種類の表に無いパスと、リンクのディレクトリの件数 */
  readonly ignored: number;
  /** 対象外の名前と理由（置き場からの相対パス）。古い実行ファイルは返さない。`ignored` と件数が一致する */
  readonly ignored_paths?: readonly HandoffIgnored[];
  readonly entries: readonly HandoffEntry[];
  /** 選んだ対象の集合のハッシュ。拡張は組み直さず、取り込みたいパスを指した答えのものを使う */
  readonly expect: string;
  /** 選んだ対象に error・rejected が無い */
  readonly ok: boolean;
}

/** 取り込めた対象 */
export interface HandoffApplied {
  readonly path: string;
  readonly kind: HandoffKind;
}

/** 下書きを残した対象と理由 */
export interface HandoffKept {
  readonly path: string;
  readonly reason: string;
}

/** 取り込みの答え。失敗は `error` に理由 */
export type HandoffResult =
  | { readonly ok: true; readonly applied: readonly HandoffApplied[]; readonly removedDrafts: readonly string[]; readonly keptDrafts: readonly HandoffKept[] }
  | { readonly ok: false; readonly error: string };

// ---- 画面に見せる形

/** 見るツリーの選択肢。`key` は `main:` / `project:<名前>` / `worktree:<名前>` */
export interface HandoffTreeOption {
  readonly key: string;
  readonly label: string;
}

export interface HandoffPage {
  readonly root: string;
  readonly generatedAt: string;
  /** 開いているツリーの `key` */
  readonly tree: string;
  readonly trees: readonly HandoffTreeOption[];
  readonly list: HandoffList;
  /**
   * 作業中のチケットが取り込みを止めるか。止まるのは `template` と `layer-config` の種類だけ
   * （`handoffmodel.ts` の `lockReason`）。実行ファイルには錠が無く、拡張の画面だけが持つ
   */
  readonly lock: Lock;
  readonly notices: readonly string[];
}

/** 画面に見せる中身。読み直せなかったときは一覧の代わりに文面を渡す */
export type HandoffData =
  | { readonly kind: "page"; readonly page: HandoffPage }
  /** 実行ファイルが `handoff` の機能を持たない。画面は無効にして理由を言う */
  | { readonly kind: "disabled"; readonly reason: string }
  /** `trees` は読めなかった画面から別のツリーへ戻るための欄。ボードを読めていないので、ワークスペースだけを載せる */
  | { readonly kind: "error"; readonly error: string; readonly tree?: string; readonly trees?: readonly HandoffTreeOption[] }
  /** ツリーを切り替えて新しいツリーを読んでいる間の表示 */
  | { readonly kind: "loading"; readonly text: string };

/** 取り込みの結果として画面に返す形。`paths` は頼んだ対象 */
export interface HandoffOutcome {
  readonly paths: readonly string[];
  readonly result: HandoffResult;
}

/** 拡張ホスト → 画面。中身を包む形は `screen-host.ts` が決める */
export type ToHandoff =
  | DataMessage<HandoffData>
  | { readonly type: "changed" }
  | { readonly type: "lock"; readonly lock: Lock }
  /** 頼んだ往復が起きなかった。画面は欄を戻す */
  | { readonly type: "cancelled" }
  /** 取り込みの答え */
  | ({ readonly type: "imported" } & HandoffOutcome)
  /** ターミナルで打つコマンド（`defense` の対象用）。`command` が空なら `error` に理由 */
  | { readonly type: "terminal"; readonly path: string; readonly command: string; readonly error: string }
  | AppearanceMessage;

/** 画面 → 拡張ホスト。受け側が形を確かめてから使う */
export type HandoffMessage =
  /** 画面が組み上がった。拡張ホストはここで中身を渡し直す */
  | { readonly type: "ready" }
  /** 画面を読み直す */
  | { readonly type: "refresh" }
  /** 別のツリーを見る */
  | { readonly type: "switchTree"; readonly key: string }
  /** 選んだ対象を取り込む */
  | { readonly type: "import"; readonly paths: readonly string[] }
  /** ターミナルで打つコマンドを出す（`board` が偽の対象） */
  | { readonly type: "terminal"; readonly path: string }
  /** 文面をクリップボードへコピーする */
  | { readonly type: "copy"; readonly text: string };

/** 最初の中身を埋める `<script type="application/json">` の id */
export const DATA_ID = "ccnavi-handoff-data";

/** 最初の中身を HTML に埋める形にする。埋め方は `screen-host.ts` が持つ */
export function embedData(data: HandoffData): string {
  return embedJson(data);
}
