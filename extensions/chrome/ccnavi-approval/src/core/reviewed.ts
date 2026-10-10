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
import type { Compare, ConfirmResult, PyCall, Reviewable, Snapshot, Actor } from "./py.js";
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
  const first = await py.confirm(call, body);
  if (!first.need_compare) return first;
  const compare = (await ask("compareFiles", [first.need_compare.base, first.need_compare.head])) as Compare;
  return await py.confirm(call, { ...body, compare });
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
      out.push({ ...panel, copy: null, problems: [], error: `レビューを依頼したホストが ${r.host || "（記録なし）"}。Chrome 拡張からレビュー済みにできるのは GitHub と GitLab だけ` });
      continue;
    }
    if (copy === null) {
      out.push({ ...panel, copy: null, problems: [], error: copyError });
      continue;
    }
    if (copy.host !== r.host) {
      // 依頼を記録したホストと、このリポジトリのホストが違う（別のホストの MR で依頼した）
      out.push({ ...panel, copy: null, problems: [], error: `レビューを依頼したホスト（${r.host}）とこのリポジトリのホスト（${copy.host}）が違う。レビューを依頼し直してください` });
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
