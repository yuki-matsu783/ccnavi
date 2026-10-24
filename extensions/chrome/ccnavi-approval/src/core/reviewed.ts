/**
 * レビュー済みの読み取り側。
 *
 * 判定（依頼の記録・依頼の後に動いたか・同じ MR か・変更要求・未解決のスレッド）は Python の
 * `confirm`（手元の `ccnavi review confirm` と同じコア）が出す。ここはホストから材料を取ってきて渡すだけで、材料は次のとおり。
 *
 * - MR のスレッドとレビューを取得した結果（`reviewCopy`。`ccnavi-review.sh fetch` と同じ形）
 * - 依頼の後に親のブランチが動いていれば、Python が求める 2 つ（`need_compare`）の変更の一覧（`compareFiles`）
 *
 * ボードはこれで候補のフェーズごとに「通るか」と理由とスレッドを出し、書く流れ（`write.ts` の
 * `confirmPhase`）は押したときに同じ手順を読み直して 1 コミットにする。GitLab の MR も読む
 * （取得した結果の形は同じ。GitLab のスレッドは最初のノートの書き手 `author` を持ち、Python が依頼の投稿者
 * `poster` と比べて ccnavi の依頼のスレッドを除く。目印は誰でも書けるので書き手で見分ける）。
 */
import type { Compare, ConfirmResult, DecideResult, PyCall, Reviewable, Snapshot, Actor } from "./py.js";
import { py } from "./py.js";
import type { ReviewCopy } from "./github.js";
import { localStamp } from "./stamp.js";

/** service worker へ頼む関数（`owner`・`repo` は呼び手が前に付ける） */
export type Ask = (op: string, args: readonly unknown[]) => Promise<unknown>;

export interface ConfirmInput {
  /** 判定の入力（統合先・P・閉包の P_X） */
  readonly snapshot: Snapshot;
  readonly family: string;
  readonly phase: number;
  readonly copy: ReviewCopy;
  readonly stamp: string;
  readonly actor?: Actor;
}

/**
 * Python の入口を呼ぶ。依頼の後に先頭が動いていれば（`need_compare`）求められた 2 つの変更の一覧を読み、
 * 手で `done/` に動かされた子があれば（`need_manual`）その移動の時刻を読んで、呼び直す。
 * 手で動かした子の時刻は、全部の子の最初のコミットの時刻のうち最も新しいもの。1 つでも読めなければ 0
 * （Python が確かめられないものとして確定しない）。`readTimes` が偽なら時刻は読まず 0 で呼び直す
 */
async function withNeeds<R extends { need_compare?: { base: string; head: string }; need_manual?: { paths: readonly string[] } }>(
  ask: Ask,
  family: string,
  run: (extra: Record<string, unknown>) => Promise<R>,
  readTimes = true,
): Promise<R> {
  let extra: Record<string, unknown> = {};
  let res = await run(extra);
  if (res.need_compare) {
    const compare = (await ask("compareFiles", [res.need_compare.base, res.need_compare.head])) as Compare;
    extra = { compare };
    res = await run(extra);
  }
  if (res.need_manual) {
    let newest = 0;
    for (const path of readTimes ? res.need_manual.paths : []) {
      const got = (await ask("pathCommitTime", [family, path])) as { time: number | null };
      if (typeof got.time !== "number" || got.time <= 0) {
        newest = 0;
        break;
      }
      newest = Math.max(newest, got.time);
    }
    res = await run({ ...extra, manual_moved_at: newest });
  }
  return res;
}

/** Python の `confirm` を呼ぶ。依頼の後に先頭が動いていれば、求められた 2 つの変更の一覧を読んで呼び直す */
export async function askConfirm(call: PyCall, ask: Ask, input: ConfirmInput): Promise<ConfirmResult> {
  const body = {
    snapshot: input.snapshot,
    family: input.family,
    phase: input.phase,
    result: input.copy,
    stamp: input.stamp,
    ...(input.actor ? { actor: input.actor } : {}),
  };
  return await withNeeds(ask, input.family, (extra) => py.confirm(call, { ...body, ...extra }));
}

export interface DecideInput extends ConfirmInput {
  readonly mode: "preview" | "apply";
  /** apply のとき。指摘の鍵 → 行き先 */
  readonly choices?: Readonly<Record<string, DecideChoice>>;
  /** apply のとき。preview が返した digest */
  readonly digest?: string;
  /** ホストで受け入れ済みと確かめた鍵（`fix` は入れない） */
  readonly carry?: readonly string[];
}

/**
 * Python の `decide` を呼ぶ（書かない）。`need_compare`・`need_manual` の往復は `confirm` と同じ。
 * 手で動かした子は decide の判断に使わないので、時刻が読めなくても進める
 */
export async function askDecide(call: PyCall, ask: Ask, input: DecideInput): Promise<DecideResult> {
  const body = {
    snapshot: input.snapshot,
    family: input.family,
    phase: input.phase,
    result: input.copy,
    stamp: input.stamp,
    mode: input.mode,
    ...(input.mode === "apply" ? { choices: input.choices ?? {}, shown_digest: input.digest ?? "" } : {}),
    ...(input.carry && input.carry.length > 0 ? { carry: input.carry } : {}),
    ...(input.actor ? { actor: input.actor } : {}),
  };
  return await withNeeds(ask, input.family, (extra) => py.decide(call, { ...body, ...extra }), false);
}

/** ボードに出すフェーズ 1 つ。`problems` が空で `error` も無ければ「レビュー済みにする」を出せる */
export interface ReviewPanel {
  readonly phase: number;
  readonly mr: number;
  /** 依頼を記録したホスト（`github`・`gitlab`） */
  readonly host?: string;
  readonly children: readonly string[];
  /** 読んだ MR のスレッドとレビュー（読めなければ null） */
  readonly copy: ReviewCopy | null;
  /** Python の confirm が通さない理由（手元の confirm の標準エラーと同じ文面） */
  readonly problems: readonly string[];
  /** 読めなかった・扱えない（GitLab など）理由 */
  readonly error: string;
  /** 残った指摘の行き先を決める画面の材料（未解決の指摘があるときに、読めていれば入る） */
  readonly decide?: DecidePreview;
}

/** 残った指摘の行き先。対応しない・このフェーズで直す（続きの子）・issue に回す */
export type DecideChoice = "keep" | "fix" | "issue";

export const DECIDE_CHOICES: readonly DecideChoice[] = ["keep", "fix", "issue"];

export const DECIDE_LABELS: Readonly<Record<DecideChoice, string>> = {
  keep: "対応しない",
  fix: "このフェーズで直す",
  issue: "issue に回す",
};

export interface DecideThread {
  /** 選択を結ぶ鍵（URL か、無ければスレッドの id） */
  readonly key: string;
  readonly url: string;
  readonly path: string;
  readonly line: number;
  readonly body: string;
  /** 指摘の版。画面は見せるだけで解釈しない */
  readonly version: string;
  /** 最後のコメント（再提起を見分ける）。取れなければ空 */
  readonly last_comment: { readonly id: string; readonly at: string; readonly author: string };
  /** Chrome では扱えない理由（解決できない種類のスレッドなど）。あれば行き先を選ばせない */
  readonly unsupported?: string;
}

export interface DecidePreview {
  readonly version: number;
  readonly parent: string;
  readonly phase: number;
  readonly mr: { readonly number: number; readonly url: string };
  readonly threads: readonly DecideThread[];
  /** 見せた指摘のダイジェスト（不透明な文字列）。確定のときに返す */
  readonly digest: string;
}

/**
 * 候補のフェーズごとに Python に通るかを聞く（書かない）。MR・スレッド・レビューを取得した結果は親子のチケット（親のブランチ）に
 * 1 つなので、GitHub の候補があれば 1 度だけ読んで全部のフェーズに使う。
 */
export async function reviewPanels(
  reviewable: readonly Reviewable[],
  call: PyCall,
  ask: Ask,
  base: { snapshot: Snapshot; family: string },
  now: Date,
): Promise<ReviewPanel[]> {
  const out: ReviewPanel[] = [];
  let copy: ReviewCopy | null = null;
  let copyError = "";
  if (reviewable.some((r) => r.host === "github" || r.host === "gitlab")) {
    try {
      copy = (await ask("reviewCopy", [base.family])) as ReviewCopy;
    } catch (err) {
      copyError = (err as Error).message ?? String(err);
    }
  }
  for (const r of reviewable) {
    const panel = { phase: r.phase, mr: r.mr, host: r.host, children: r.children };
    if (r.host !== "github" && r.host !== "gitlab") {
      out.push({ ...panel, copy: null, problems: [], error: `レビューを依頼したホストは ${r.host || "（記録なし）"} です。Chrome 拡張からレビュー済みにできるのは GitHub と GitLab のみです。` });
      continue;
    }
    if (copy === null) {
      out.push({ ...panel, copy: null, problems: [], error: copyError });
      continue;
    }
    if (copy.host !== r.host) {
      // 依頼を記録したホストと、このリポジトリのホストが違う（別のホストの MR で依頼した）
      out.push({ ...panel, copy: null, problems: [], error: `レビューを依頼したホスト（${r.host}）とこのリポジトリのホスト（${copy.host}）が異なります。レビューを依頼し直してください。` });
      continue;
    }
    try {
      const res = await askConfirm(call, ask, { ...base, phase: r.phase, copy, stamp: localStamp(now) });
      out.push({ ...panel, copy, problems: res.problems, error: "" });
    } catch (err) {
      out.push({ ...panel, copy: null, problems: [], error: (err as Error).message ?? String(err) });
    }
  }
  return out;
}
