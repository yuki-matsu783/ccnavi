/**
 * 「始める」の欄（`[data-testid=start]`）。issue は押されてから読む（ボードを開くたびには読まない）。
 * issue のタイトルは素の文字列（textContent）。押すと、issue を閉じる開いたマージリクエストがあればその元ブランチを採用し、無ければ親のブランチを統合先の先頭から作って Draft のマージリクエストを作る（`Actions.start`）。
 */
import type { JSX } from "react";

import type { RepoBoard } from "../core/snapshot.js";
import type { Actions, IssueList } from "./controller.js";
import { ActionButton, Link, Notice } from "./parts.js";

export function Start({ board, actions, issues }: { board: RepoBoard; actions: Actions; issues?: IssueList }): JSX.Element {
  return (
    <section className="start" data-testid="start">
      <h3>Issue から作業を開始（issue を閉じる開いたマージリクエストがあればその元ブランチを採用し、無ければ統合先ブランチの最新コミットを起点に、親ブランチと Draft のマージリクエストを作成します）</h3>
      {!issues ? (
        <ActionButton action="issues" onClick={() => actions.loadIssues?.(board)}>
          Issue 一覧を取得
        </ActionButton>
      ) : issues.error ? (
        <Notice kind="error" text={issues.error} />
      ) : (
        <>
          {(issues.list ?? []).length === 0 ? <p className="empty">オープン状態の Issue はありません。</p> : null}
          <ul className="issues">
            {(issues.list ?? []).map((issue) => (
              <li key={issue.number} className="issue" data-issue={String(issue.number)}>
                <Link href={issue.url} text={`#${issue.number}`} />
                {` ${issue.title} `}
                <ActionButton action="start" onClick={() => actions.start?.(board, issue)}>
                  始める
                </ActionButton>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
