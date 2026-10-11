/**
 * 「始める」の欄（`[data-testid=start]`）。issue は押されてから読む（ボードを開くたびには読まない）。
 * issue の題は素の文字列（textContent）。押すと親のブランチを統合先の先頭から作る（`Actions.start`）。
 */
import type { JSX } from "react";

import type { RepoBoard } from "../core/snapshot.js";
import type { Actions, IssueList } from "./controller.js";
import { ActionButton, Link, Notice } from "./parts.js";

export function Start({ board, actions, issues }: { board: RepoBoard; actions: Actions; issues?: IssueList }): JSX.Element {
  return (
    <section className="start" data-testid="start">
      <h3>issue から始める（親のブランチを統合先の先頭から作る。マージリクエストは最初の push の後に作られる）</h3>
      {!issues ? (
        <ActionButton action="issues" onClick={() => actions.loadIssues?.(board)}>
          issue を読む
        </ActionButton>
      ) : issues.error ? (
        <Notice kind="error" text={issues.error} />
      ) : (
        <>
          {(issues.list ?? []).length === 0 ? <p className="empty">開いている issue は無い</p> : null}
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
