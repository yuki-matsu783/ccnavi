/**
 * ルール設定画面の、拡張ホストと Webview の間の契約。
 *
 * 画面は React で組み、拡張ホストは HTML を組み立てない（ADR-0064）。拡張ホストが渡すのは
 * 「いま何を見せるか」（`RulesData`）だけで、ルールの行も判定の結果も hook の表も画面が作る。
 * 画面が返すのは人が押した操作（`RulesMessage`）だけで、判定もせず、ファイルも書かない。
 *
 * **ルールの形（`SECTIONS`・`RuleForm`・`RulesModel`）もここに置く。** 読み書き（`rules-doc.ts`）の側に
 * 置いたままだと、画面がそこから `yaml` を辿ることになり、束ねたものに YAML の解析器が丸ごと入る。
 * 同じ理由で、ここには VS Code の API も DOM も node も入れない。
 *
 * この画面は `retainContextWhenHidden: true`（編集の途中を持つ）。渡し方は `retainedHost` で、
 * 入れ物は 1 度しか入らない（ADR-0062）。**中身（`data`）が届くのは、画面の編集を捨ててよいとき
 * だけ**（人が「再読込」を押した、保存が通って中身が入れ替わった）。ファイルが外で変わっただけの
 * ときは `changed` の帯を出し、捨てるかどうかは人が決める。
 */
import type { Appearance } from "./appearance.js";
import type { HookEntry } from "./hooks.js";
import type { Lock } from "./lock.js";
import { embedJson, type DataMessage } from "./screen-host.js";
import type { SamplesJson, TestJson } from "./testmodel.js";

// ---- ルールの形（画面と読み書きで分け合う）

export const SECTIONS = ["deny", "ask", "allow"] as const;
export type Section = (typeof SECTIONS)[number];
export type PatternKind = "glob" | "regex";

/** タイプの言い換え。画面の見出しで `deny` などの綴りに添える */
export const SECTION_LABELS: Readonly<Record<Section, string>> = {
  deny: "拒否する",
  ask: "人に確認する",
  allow: "許可する",
};

/**
 * 判定が対象を取り出せるツール。ccnavi の judge.SUBJECT_FIELDS（diagnose.KNOWN_TOOLS）と同じ並び。
 * 名前は Claude Code の権限ルール `ToolName(指定子)` から括弧の中を除いたもの。
 */
export const KNOWN_TOOLS = [
  "Bash",
  "PowerShell",
  "Read",
  "Grep",
  "Glob",
  "Edit",
  "Write",
  "NotebookEdit",
  "Skill",
  "Agent",
  "WebFetch",
] as const;

/** 画面で編集する 1 件。`origin` は読み込んだときの位置で、新しいルールは null */
export interface RuleForm {
  readonly origin: { readonly section: Section; readonly index: number } | null;
  readonly id: string;
  readonly match: string;
  readonly kind: PatternKind;
  readonly pattern: string;
  readonly message: string;
  /** 当たったときにモデルへ渡す文（`additionalContext`）。無ければ空 */
  readonly additionalContext: string;
  /** 1 つの文脈で最初に当たったときだけ渡す文（`additionalContextOnce`）。無ければ空 */
  readonly additionalContextOnce: string;
  /** 文に続けて本文を渡すファイル（`additionalContextFile`）。ルートからの相対パス。無ければ空 */
  readonly additionalContextFile: string;
  /** 最初に当たったときだけ本文を渡すファイル（`additionalContextOnceFile`）。無ければ空 */
  readonly additionalContextOnceFile: string;
  /**
   * 渡す回の刻み（`every`）。当たった回数がこの倍数になった回だけ文が渡り、
   * `additionalContextOnce` はその最初の 1 回（＝ N 回目）に渡る。書いていなければ空。
   *
   * 他の欄と同じく**書かれたままの文字**で持つ。数（`number | null`）で持つと、空欄が
   * 「刻み無し」なのか「刻みとして読めない値（`0`・`-1`・`x`）だった」のかを区別できず、
   * 刻みを外す操作も、読めない値を画面から直す道も書けない。読めない値は書いたまま
   * 書き戻し、咎めるのは保存前の `ccnavi --lint`。画面が黙って直すと、lint が名指し
   * している対象が消えて苦情の出どころが分からなくなる
   */
  readonly every: string;
}

/** ファイル選択ダイアログで埋める欄 */
export type FileField = "additionalContextFile" | "additionalContextOnceFile";

export type Sections = Readonly<Record<Section, readonly RuleForm[]>>;

export interface RulesModel {
  readonly version: number | null;
  readonly sections: Sections;
  /** 読み込み時の苦情。ルールの形が読めなかった場所。あっても他は出す */
  readonly problems: readonly string[];
}

// ---- 画面に見せる形

export interface RulesPage {
  readonly root: string;
  /** ルールファイル（ワークスペースルートからの相対で見せる） */
  readonly rulesPath: string;
  /** `.claude/settings.json` の env.CCNAVI_MODE。空なら未設定 */
  readonly mode: string;
  readonly model: RulesModel;
  readonly hooks: readonly HookEntry[];
  /** 読めた設定ファイル。無いものは一覧に「無い」と出す */
  readonly hookFiles: { readonly settings: boolean; readonly settingsLocal: boolean };
  readonly samplesPath: string;
  readonly lock: Lock;
  /** 上部に出す注意（実行ファイルがこの層を読めていない、など） */
  readonly notices?: readonly string[];
}

// ---- やり取り

/**
 * 画面に見せる中身。読み直せなかったときはルールの代わりに文面を渡す（`kind: "error"`）。
 * 他の 4 画面と同じ形で、画面はどちらでも 1 枚を描く。
 */
export type RulesData =
  | { readonly kind: "page"; readonly page: RulesPage }
  | { readonly kind: "error"; readonly error: string };

/**
 * 拡張ホスト → 画面。中身を包む形は `screen-host.ts` が決める（渡すのはそこ）。
 *
 * `judged` と `sampled` は実行ファイルに聞いた判定の結果、`failed` は操作の結果をその場で言う
 * 一言、`lock` は保存してよいかの取り直し、`changed` はファイルが外で変わったという帯、
 * `picked` はダイアログで選んだファイルの綴り。どれも画面の編集には触らない
 * （`picked` は名指しした 1 欄だけを埋める）。
 */
export type ToRules =
  | DataMessage<RulesData>
  | { readonly type: "judged"; readonly result: TestJson; readonly hooks: readonly HookEntry[] }
  | { readonly type: "sampled"; readonly result: SamplesJson }
  | { readonly type: "failed"; readonly message: string }
  | { readonly type: "lock"; readonly lock: Lock }
  | { readonly type: "changed" }
  /** 頼んだ往復が起きなかった（人が「破棄して読み直す？」をやめた）。画面は欄を戻す */
  | { readonly type: "cancelled" }
  /** 選んだファイルの綴り。`key` は画面が渡した行の鍵で、拡張ホストはそのまま返す */
  | { readonly type: "picked"; readonly key: string; readonly field: FileField; readonly path: string }
  | { readonly type: "appearance"; readonly value: Appearance };

/** 画面 → 拡張ホスト。受け側（rules-panel の `asMessage`）が形を確かめてから使う */
export type RulesMessage =
  /** 画面が組み上がった。拡張ホストはここで中身を渡し直す */
  | { readonly type: "ready" }
  | { readonly type: "reload"; readonly dirty: boolean }
  | { readonly type: "openFile"; readonly which: "rules" | "samples" }
  | { readonly type: "save"; readonly sections: Sections }
  /** 編集中の内容で 1 件だけ判定する。保存は要らない */
  | { readonly type: "judge"; readonly sections: Sections; readonly tool: string; readonly subject: string }
  /** 編集中の内容でサンプルを一括で判定する */
  | { readonly type: "samples"; readonly sections: Sections }
  | { readonly type: "pickFile"; readonly key: string; readonly field: FileField };

/** 最初の中身を埋める `<script type="application/json">` の id。画面はこれを読んで最初の 1 枚を描く */
export const DATA_ID = "ccnavi-rules-data";

/** 最初の中身を HTML に埋める形にする。埋め方は `screen-host.ts` が持つ（React の画面で同じ） */
export function embedData(data: RulesData): string {
  return embedJson(data);
}
