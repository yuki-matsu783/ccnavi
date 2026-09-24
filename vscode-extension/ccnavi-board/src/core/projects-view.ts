/**
 * プロジェクト管理画面の、拡張ホストと Webview の間の契約。
 *
 * 画面は React で組み、拡張ホストは HTML を組み立てない（ADR-0064）。拡張ホストが渡すのは
 * 「いま何を見せるか」（`ProjectsData`）だけで、カードとメニューの DOM は画面が作る。
 * 画面が返すのは人が押した操作（`ProjectsMessage`）だけで、clone も書き込みもしない。
 *
 * この形を保つために、ここには VS Code の API も DOM も入れない。両側から import されるので、
 * 片方だけが持てるものを置くと束ねられなくなる。
 */
import type { AppearanceMessage } from "./appearance.js";
import type { LintProblem } from "./lintmodel.js";
import { embedJson, type DataMessage } from "./screen-host.js";

// ---- 画面に見せる形
//
// 組み立てるのは `projects.ts` の `buildProjectsPage`。形をここに置くのは、Webview 側が
// `projects.ts`（`commands.ts` 経由で `node:path` を読む）を辿らずに済むようにするため。

export interface Stray {
  /** ワークスペースルートからの相対、"/" 区切り */
  readonly path: string;
  readonly reason: string;
}

export interface ProjectRow {
  readonly name: string;
  readonly root: string;
  /** ルートからの相対、"/" 区切り */
  readonly rel: string;
  /** プロジェクトの層のルールファイル。ルートからの相対、"/" 区切り。層として数えられていない（予約名）なら空 */
  readonly rulesRel: string;
  readonly rulesExists: boolean;
  readonly hasClaudeDir: boolean;
  readonly origin: string;
  readonly originKey: string;
  readonly worktrees: readonly string[];
  readonly tickets: number;
  /** 作業中（ボードの作業中の列と同じ。`.ccnavi/approved/doing/` にあるものと、レビュー待ち `wip/proposals/review/`） */
  readonly doing: number;
  readonly problems: readonly LintProblem[];
}

export interface ProjectsPage {
  readonly root: string;
  readonly generatedAt: string;
  readonly ticketsEnabled: boolean;
  /** 置き場（絶対）。空なら置き場が無効（CCNAVI_PROJECTS が空） */
  readonly projectsDir: string;
  readonly projectsRel: string;
  readonly ignored: boolean;
  readonly lintError: string;
  readonly dirProblems: readonly LintProblem[];
  readonly rows: readonly ProjectRow[];
  readonly strays: readonly Stray[];
  readonly workspaceWorktrees: readonly string[];
  /** 自身の層のルールファイル。ルートからの相対、"/" 区切り */
  readonly selfRulesRel: string;
  readonly selfRulesExists: boolean;
  /** 名前の衝突を見る既存のツリー名（ワークスペース自身の空は除く） */
  readonly existingNames: readonly string[];
}

// ---- やり取り

/**
 * 画面に見せる中身。読み直せなかったときは一覧の代わりに文面を渡す（`kind: "error"`）。
 * ボードと同じ形で、画面はどちらでも 1 枚を描く。
 */
export type ProjectsData =
  | { readonly kind: "page"; readonly page: ProjectsPage }
  | { readonly kind: "error"; readonly error: string };

/**
 * 拡張ホスト → 画面。中身を包む形は `screen-host.ts` が決める（渡すのはそこ）。
 *
 * `failed` / `info` / `cloned` は clone の欄の下に出す一言で、画面が覚える。生きている画面にしか
 * 届かない（裏に回っていれば落ちる）が、操作の結果をその場で言うだけのものなので持ち越さない。
 * `cloned` のときだけ、画面は打ち込んだ URL と名前を消す。
 */
export type ToProjects =
  | DataMessage<ProjectsData>
  | { readonly type: "failed"; readonly message: string }
  | { readonly type: "info"; readonly message: string }
  | { readonly type: "cloned"; readonly message: string }
  /** 初回の吹き出しの案内を出す。画面は指す先が出てから始める（`src/tour.ts`） */
  | { readonly type: "tour" }
  | AppearanceMessage;

/** 画面 → 拡張ホスト。受け側（projects-panel の `asMessage`）が形を確かめてから使う */
export type ProjectsMessage =
  /** 画面が組み上がった。裏に回って作り直された画面が、いまの中身をもらい直すために送る */
  | { readonly type: "ready" }
  | { readonly type: "refresh" }
  | { readonly type: "clone"; readonly url: string; readonly name: string }
  | { readonly type: "fixIgnore" }
  | { readonly type: "createRules"; readonly name: string }
  | { readonly type: "createSelfRules" }
  | { readonly type: "openRules"; readonly name: string }
  | { readonly type: "openSelfRules" }
  | { readonly type: "openPhases"; readonly name: string }
  | { readonly type: "openSelfPhases" }
  | { readonly type: "openBoard"; readonly name: string }
  | { readonly type: "fetch"; readonly name: string }
  | { readonly type: "pull"; readonly name: string }
  /** 吹き出しの案内を閉じた（最後まで見ても、途中でやめても）。拡張ホストは次から初回の案内を頼まない */
  | { readonly type: "tourDone" };

/** clone の欄の下に出す一言。`failed` は失敗を示す */
export interface CloneStatus {
  readonly kind: "info" | "failed";
  readonly message: string;
}

/** 最初の中身を埋める `<script type="application/json">` の id。画面はこれを読んで最初の 1 枚を描く */
export const DATA_ID = "ccnavi-projects-data";

/** 最初の中身を HTML に埋める形にする。埋め方は `screen-host.ts` が持つ（React の画面で同じ） */
export function embedData(data: ProjectsData): string {
  return embedJson(data);
}
