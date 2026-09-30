/**
 * レビュー済み（ADR-0093 の 8.9。段階 4）の読み取り側。
 *
 * 判定（依頼の記録・依頼の後に動いたか・同じ MR か・変更要求・未解決のスレッド）は Python の
 * `confirm`（手元の `ccnavi review confirm` と同じコア）が出す。ここはホストから材料を取ってきて渡すだけ:
 *
 * - MR のスレッドとレビューの写し（`reviewCopy`。`ccnavi-review.sh fetch` と同じ形）
 * - 依頼の後に親のブランチが動いていれば、Python が求める 2 つ（`need_compare`）の変更の一覧（`compareFiles`）
 *
 * ボードはこれで候補のフェーズごとに「通るか」と理由とスレッドを出し、書く流れ（`write.ts` の
 * `confirmPhase`）は押したときに同じ手順を読み直して 1 コミットにする。
 */
import type { Compare, ConfirmResult, PyCall, Reviewable, Snapshot, Actor } from "./py.js";
import { py } from "./py.js";
import type { ReviewCopy } from "./github.js";
import { localStamp } from "./stamp.js";

/** service worker へ頼む口（`owner`・`repo` は呼び手が前に付ける） */
export type Ask = (op: string, args: readonly unknown[]) => Promise<unknown>;

export interface ConfirmInput {
  readonly settings: string | null;
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
    settings: input.settings,
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
  readonly children: readonly string[];
  /** 読んだ MR のスレッドとレビュー（読めなければ null） */
  readonly copy: ReviewCopy | null;
  /** Python の confirm が通さない理由（手元の confirm の標準エラーと同じ文面） */
  readonly problems: readonly string[];
  /** 読めなかった・扱えない（GitLab など）理由 */
  readonly error: string;
}

/**
 * 候補のフェーズごとに Python に通るかを聞く（書かない）。MR・スレッド・レビューの写しは家族（親のブランチ）に
 * 1 つなので、GitHub の候補があれば 1 度だけ読んで全部のフェーズに使う。
 */
export async function reviewPanels(
  reviewable: readonly Reviewable[],
  call: PyCall,
  ask: Ask,
  base: { settings: string | null; snapshot: Snapshot; family: string },
  now: Date,
): Promise<ReviewPanel[]> {
  const out: ReviewPanel[] = [];
  let copy: ReviewCopy | null = null;
  let copyError = "";
  if (reviewable.some((r) => r.host === "github")) {
    try {
      copy = (await ask("reviewCopy", [base.family])) as ReviewCopy;
    } catch (err) {
      copyError = (err as Error).message ?? String(err);
    }
  }
  for (const r of reviewable) {
    const panel = { phase: r.phase, mr: r.mr, children: r.children };
    if (r.host !== "github") {
      out.push({ ...panel, copy: null, problems: [], error: `依頼したホストが ${r.host || "（記録なし）"}。Chrome のレビュー済みは GitHub だけ（GitLab は段階 5）` });
      continue;
    }
    if (copy === null) {
      out.push({ ...panel, copy: null, problems: [], error: copyError });
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
