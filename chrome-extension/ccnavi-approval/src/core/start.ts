/**
 * 「始める」（ADR-0093 の 8.6・D19。段階 5）。issue から親のブランチを統合先の今の先頭に作る。PR/MR は作らない
 * （差分 0 のブランチからは作れないので、最初の push の後に `ccnavi-review.sh request` が作る）。
 *
 * 識別子（= ブランチ名）は Python（`ticket.issue_identifier`。3.1 の 11）が決め、始められない理由（統合先の
 * `done/` にある・同じ名前のブランチがある・開いた親のブランチに同じ識別子がある・予約の名前・互換の版の違い）も
 * Python が出す。ここは issue を読み、Python に聞き、ブランチを作る頼みを service worker に送るだけ。
 * service worker も名前の形・保護された名前・統合先の先頭を自分で確かめる（二重の確認）。
 */
import { py, PyError, type Snapshot } from "./py.js";
import type { RepoConfig } from "./settings.js";
import { readIntegration, Reader, type Deps } from "./snapshot.js";
import type { Issue } from "./github.js";

export type StartOutcome =
  | { readonly kind: "started"; readonly name: string; readonly head: string }
  | { readonly kind: "refused"; readonly message: string }
  | { readonly kind: "failed"; readonly message: string };

/** 開いた issue の一覧（新しい順に 50 件。GitHub は PR を除く） */
export async function listIssues(repo: RepoConfig, deps: Deps): Promise<Issue[]> {
  return (await deps.call("issues", [repo.owner, repo.repo])) as Issue[];
}

/**
 * issue から始める。`seen` はボードが読んだブランチ（開いた親のブランチの見分けに使う）、`taken` はボードが見た
 * ブランチの名前。統合先は押した時点で読み直す。
 */
export async function startIssue(repo: RepoConfig, issue: number, seen: Snapshot | null, taken: readonly string[], deps: Deps): Promise<StartOutcome> {
  try {
    const reader = new Reader(repo, deps);
    const base = await readIntegration(repo, reader, deps);
    const fresh = reader.snapshot(base.integration);
    const snapshot: Snapshot = {
      ...fresh,
      branches: { ...(seen?.branches ?? {}), ...fresh.branches },
      absent: [],
    };
    // 全部のブランチの名前（直近 N 日の上限を掛けない）で、大文字小文字をそろえた重なりを見る（11.9.1 の 7）
    const all = (await deps.call("branchNames", [repo.owner, repo.repo])) as string[];
    const res = await py.start(deps.py, { settings: base.settings, snapshot, issue, taken: [...new Set([...taken, ...all])] });
    if (res.problems.length > 0) return { kind: "refused", message: res.problems.join("\n") };
    const made = (await deps.call("createBranch", [repo.owner, repo.repo, res.identifier, base.integration.head])) as { name: string; head: string };
    return { kind: "started", name: made.name, head: made.head };
  } catch (err) {
    const status = (err as { status?: number }).status;
    return { kind: err instanceof PyError || status === 400 ? "refused" : "failed", message: (err as Error).message ?? String(err) };
  }
}
