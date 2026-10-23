/**
 * 「始める」。`ccnavi-start.sh` と同じ手順で、issue から作業を始める。
 *
 *   1. issue を閉じる開いた MR を探す（題・本文の `Closes #<番号>` など、閉じる語に続く issue の指定。
 *      参照しているだけの MR は採用しない）
 *   2. 1 本あれば、その元ブランチを親として採用する。ブランチも MR も作らない
 *      （採用の確認: MR が open・フォークでない・マージ先が統合先・ブランチ名が保護されていない・識別子が使える・
 *       同じ識別子になる別のブランチが無い・同じブランチ名のマージ済み MR が無い。引っかかったら止める）
 *   3. 2 本以上あれば、何も作らずに一覧を出して止める（ユーザが選ぶ）
 *   4. 無ければ、親のブランチを統合先の今の先頭に作り、初期コミットを積み、Draft の MR を作る
 *      （本文は `Closes #<番号>`）
 *
 * 識別子（新規は = ブランチ名）は Python（`ticket_ids.issue_identifier`）が issue の番号とタイトルから
 * `feature-<番号>-<slug>` の形に決め、始められない理由（統合先の
 * `done/` にある・同じ名前のブランチがある・開いた親子のチケットに同じ識別子がある・予約の名前・互換の版の違い）も
 * Python が出す。採用の識別子（ブランチ名の `/` を `-` に置き換えたもの）と使えない理由も Python（`adopt`）が出す。
 * ここは issue と MR を読み、Python に聞き、ブランチ・MR を作る頼みを service worker に送るだけ。
 * service worker も名前の形・保護された名前・統合先の先頭を自分で確かめる（二重の確認）。
 *
 * ブランチは作ったのに MR を作れなかったときは、作ったことと続きの手を言って止まる（打ち直しても
 * 同じ名前のブランチが既にあると断られる。続きは `ccnavi-start.sh --issue <番号>` が作りかけとして続ける）。
 */
import { py, PyError, type Snapshot } from "./py.js";
import type { RepoConfig } from "./settings.js";
import { readIntegration, Reader, type Deps } from "./snapshot.js";
import type { Issue } from "./github.js";

export interface MrRef {
  readonly number: number;
  readonly url: string;
}

export type StartOutcome =
  /** 新しく作った: ブランチ・初期コミット・Draft の MR */
  | { readonly kind: "started"; readonly name: string; readonly head: string; readonly mr: MrRef }
  /** 既存の MR の元ブランチを採用した（何も作っていない） */
  | { readonly kind: "adopted"; readonly name: string; readonly identifier: string; readonly mr: MrRef }
  | { readonly kind: "refused"; readonly message: string }
  | { readonly kind: "failed"; readonly message: string };

/** ホストが返す開いた MR 1 本（`github.MrInfo` / `gitlab.MrInfo`） */
interface Mr {
  readonly number: number;
  readonly branch: string;
  readonly state: string;
  readonly url: string;
  readonly title: string;
  readonly body: string;
  readonly base: string;
  readonly fork: boolean;
}

/** 開いた issue の一覧（新しい順に 50 件。GitHub は PR を除く） */
export async function listIssues(repo: RepoConfig, deps: Deps): Promise<Issue[]> {
  return (await deps.call("issues", [repo.owner, repo.repo])) as Issue[];
}

/**
 * その issue を**閉じる** MR か。題・本文に、閉じる語（close・closes・closed・closing、fix・fixes・fixed・fixing、
 * resolve・resolves・resolved・resolving）の直後に `#<番号>`（同じリポジトリ）か `<owner/repo>#<番号>`、または
 * `issues/<番号>` の URL が続くもの。参照しているだけ（`#<番号>` の言及・元ブランチの名前の番号）の MR は
 * 含めない。`ccnavi-start.sh` が MR の閉じる issue（ホストのリンク）で採用を決めるのと同じ考え方で、
 * ここは題・本文の語から見る（拡張はホストの閉じるリンクを読まない）
 */
export function closesIssue(mr: Pick<Mr, "title" | "body">, issue: number): boolean {
  const n = String(issue);
  const text = `${mr.title}\n${mr.body}`;
  const keyword = "(?:close[sd]?|closing|fix(?:e[sd])?|fixing|resolve[sd]?|resolving)";
  const target = `(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#${n}(?![0-9])|\\S*/issues/${n}(?![0-9])`;
  return new RegExp(`(?:^|[^0-9A-Za-z_])${keyword}:?\\s+(?:${target})`, "i").test(text);
}

/**
 * issue から始める。`issue` は一覧で選んだ issue（番号とタイトル。タイトルから識別子の slug を作る）、
 * `seen` はボードが読んだブランチ（開いた親子のチケットの見分けに使う）、`taken` はボードが見たブランチの名前。
 * 統合先は押した時点で読み直す。先頭の語は既定の `feature`（ラベルからは決めない）。
 */
export async function startIssue(
  repo: RepoConfig,
  issue: Pick<Issue, "number" | "title">,
  seen: Snapshot | null,
  taken: readonly string[],
  deps: Deps,
): Promise<StartOutcome> {
  let made: string | null = null;
  try {
    const reader = new Reader(repo, deps);
    const base = await readIntegration(repo, reader, deps);
    const fresh = reader.snapshot(base.integration);
    const snapshot: Snapshot = {
      ...fresh,
      branches: { ...(seen?.branches ?? {}), ...fresh.branches },
      absent: [],
    };
    // 全部のブランチの名前（直近 N 日の上限を掛けない）で、大文字小文字をそろえた重なりを見る
    const all = (await deps.call("branchNames", [repo.owner, repo.repo])) as string[];
    // 1. issue を閉じる開いた MR を探す。見落として重複を作らないため、読めなければ（例外は）止める
    const open = ((await deps.call("listOpenMrs", [repo.owner, repo.repo])) as Mr[]).filter((m) => closesIssue(m, issue.number));
    if (open.length > 1) {
      return {
        kind: "refused",
        message: `Issue #${issue.number} を閉じる開いたマージリクエストが ${open.length} 本あります（${open.map((m) => `!${m.number} ${m.branch}`).join("、")}）。どれで続けるかを決めてから、手元で ccnavi-start.sh --mr <番号> を実行してください。`,
      };
    }
    if (open.length === 1) {
      return await adopt(repo, open[0], base.integration.name, snapshot, all, deps);
    }
    // 4. 新しく作る
    const res = await py.start(deps.py, {
      snapshot,
      issue: issue.number,
      title: issue.title,
      taken: [...new Set([...taken, ...all])],
    });
    if (res.problems.length > 0) return { kind: "refused", message: res.problems.join("\n") };
    const branch = (await deps.call("createBranch", [repo.owner, repo.repo, res.identifier, base.integration.head])) as { name: string; head: string };
    made = branch.name;
    const head = (await deps.call("startCommit", [repo.owner, repo.repo, branch.name, branch.head])) as string;
    const mr = (await deps.call("createDraftMr", [repo.owner, repo.repo, branch.name, base.integration.name, issue.title, `Closes #${issue.number}`])) as MrRef;
    return { kind: "started", name: branch.name, head, mr };
  } catch (err) {
    const status = (err as { status?: number }).status;
    const message = (err as Error).message ?? String(err);
    if (made !== null) {
      return {
        kind: "failed",
        message: `ブランチ ${made} は作成しましたが、続きの処理に失敗しました（${message}）。手元で ccnavi-start.sh --issue ${issue.number} を実行すると、作りかけのブランチから続きます。`,
      };
    }
    return { kind: err instanceof PyError || status === 400 ? "refused" : "failed", message };
  }
}

/**
 * 既存の MR の元ブランチを親として採用してよいか確かめる。引っかかったら止める。
 * 識別子・保護された名前・識別子の重なり・クローズ済み・互換の版は Python、MR の状態・フォーク・マージ先・
 * マージ済みの MR の有無はここ（ホストの答え）で見る。
 */
async function adopt(repo: RepoConfig, mr: Mr, integration: string, snapshot: Snapshot, names: readonly string[], deps: Deps): Promise<StartOutcome> {
  const why = (text: string): StartOutcome => ({ kind: "refused", message: `MR !${mr.number}（${mr.branch}）は採用できません。${text}` });
  if (mr.state !== "open") return why(`状態が ${mr.state || "不明"} です。open の MR だけ採用できます。`);
  if (mr.fork) return why("フォークから出た MR で、元ブランチがこのリポジトリにありません。");
  if (mr.base !== integration) return why(`マージ先が ${mr.base || "不明"} で、統合先 ${integration} ではありません。`);
  const res = await py.adopt(deps.py, { snapshot, branch: mr.branch, names });
  if (res.problems.length > 0) return why(res.problems.join("\n"));
  const merged = (await deps.call("listMergedMrs", [repo.owner, repo.repo, mr.branch])) as MrRef[];
  if (merged.length > 0) return why(`同じブランチ名のマージ済み MR（!${merged[0].number}）があります。マージ済みのブランチは再利用できません。`);
  return { kind: "adopted", name: mr.branch, identifier: res.identifier, mr: { number: mr.number, url: mr.url } };
}
