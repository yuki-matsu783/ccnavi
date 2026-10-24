/**
 * 1 つのリポジトリの欄（`section.repo[data-repo]`）。見出し（プロジェクト・統合先・直近の日数）、親子のチケット、
 * 要確認、「始める」、読み取りの回数を並べる。
 *
 * GitLab の MR（`!番号`）とプロジェクトのリポジトリ、「始める」、元に戻すコミットでも競合が収まらなかった親子のチケットの
 * 「要確認」も出す。押したときの動き（`Actions`）は呼び手が渡す。渡さなければボタンを出さない（読み取りだけ）。
 */
import { memo, useRef, type JSX } from "react";

import { MAX_RECENT_DAYS, repoKey } from "../core/settings.js";
import type { RepoBoard } from "../core/snapshot.js";
import { Attention } from "./Attention.js";
import type { Actions, IssueList } from "./controller.js";
import { Family } from "./Family.js";
import { ActionButton, Notice } from "./parts.js";
import { Start } from "./Start.js";

export interface RepoProps {
  readonly board: RepoBoard;
  readonly actions?: Actions;
  /** 親のブランチ名 → 元に戻すコミットでも競合が収まらなかったときの文面 */
  readonly attention?: Readonly<Record<string, string>>;
  /** 読んだ issue の一覧（「始める」） */
  readonly issues?: IssueList;
}

const NONE: Readonly<Record<string, string>> = {};

export const Repo = memo(function Repo({ board, actions, attention = NONE, issues }: RepoProps): JSX.Element {
  return (
    <section className="repo" data-repo={repoKey(board.repo)}>
      <header className="repo-head">
        <h2>{`${board.repo.owner}/${board.repo.repo}`}</h2>
        {board.repo.project ? (
          <p className="project" data-testid="project">{`プロジェクト ${board.repo.project}（ワークスペース ${board.repo.workspace}。手元では projects/${board.repo.project}）`}</p>
        ) : null}
        {board.integration ? (
          <p className="integration" data-testid="integration">
            {`統合先: ${board.integration.name}（${board.integration.source === "setting" ? "設定" : "ホストのデフォルトブランチ"}） ${board.integration.head.slice(0, 7)}`}
          </p>
        ) : null}
        {actions?.setRecentDays ? <RecentDays board={board} set={actions.setRecentDays} /> : null}
      </header>
      {board.error ? <Notice kind="error" text={board.error} /> : <RepoBody board={board} actions={actions} attention={attention} issues={issues} />}
    </section>
  );
});

function RepoBody({ board, actions, attention, issues }: RepoProps & { attention: Readonly<Record<string, string>> }): JSX.Element {
  const s = board.stats;
  const dismiss = (family: string) => (actions?.dismiss ? () => actions.dismiss?.(board, family) : undefined);
  return (
    <>
      {board.compat && !board.compat.same ? <Notice kind="warn" text={`${board.compat.message}（表示のみとし、承認のボタンは表示しません）`} /> : null}
      {board.missingExtras.length > 0 ? <Notice kind="warn" text={`指定したブランチがリモートに見つかりません：${board.missingExtras.join(", ")}`} /> : null}
      <p className="scope">{`確認したブランチ（表示用）：${board.candidates.length === 0 ? "なし" : board.candidates.join(", ")}`}</p>
      {board.families.length === 0 ? <p className="empty">確認したブランチの中に、親チケットに対応するブランチはありません。</p> : null}
      {board.families.map((f) => {
        const why = attention[f.family.name];
        // 「要確認」の親子のチケットには、このブラウザでは書くボタンを出さない（ユーザが確かめて外すまで）
        return (
          <Family
            key={f.family.name}
            family={f}
            actions={
              why || !actions
                ? undefined
                : {
                    approve: () => actions.approve(board, f),
                    review: actions.review ? (n: number) => actions.review?.(board, f, n) : undefined,
                  }
            }
            attention={why ? <Attention text={why} onDismiss={dismiss(f.family.name)} /> : null}
          />
        );
      })}
      {/* ブランチが見えなくなった親子のチケットの「要確認」も出す */}
      {Object.entries(attention)
        .filter(([name]) => !board.families.some((f) => f.family.name === name))
        .map(([name, why]) => (
          <article key={name} className="family" data-family={name}>
            <h3>{name}</h3>
            <Attention text={why} onDismiss={dismiss(name)} />
          </article>
        ))}
      {actions?.loadIssues && actions.start ? <Start board={board} actions={actions} issues={issues} /> : null}
      <p className="stats">{`取得回数：REST ${s.rest} 回・GraphQL ${s.graphql} 回・blob ${s.blobsFetched} 件（キャッシュから ${s.blobsCached} 件）`}</p>
    </>
  );
}

/**
 * 直近の日数の入力欄（リポジトリごと。設定画面と同じ値）。値は入力欄が持つ（uncontrolled）ので、自動の読み直しで
 * 欄を描き直しても入力中の値は消えない。保存されている値が変わったとき（設定画面で変えたあとの読み直し）だけ、
 * `key` で入力欄を作り直して新しい値を出す。送ると入力欄の文字列のまま渡す（検査と保存は呼び手）。
 * 送る（submit）は捕捉の段で受ける（泡立たない submit でも受けるため。入れ子の form は無い）。
 */
function RecentDays({ board, set }: { board: RepoBoard; set: (repo: RepoBoard, value: string) => void }): JSX.Element {
  const input = useRef<HTMLInputElement>(null);
  return (
    <form
      className="recent-days"
      data-testid="recent-days"
      onSubmitCapture={(e) => {
        e.preventDefault();
        set(board, input.current?.value ?? "");
      }}
    >
      <label>
        {"表示対象のブランチを検索する期間（過去 N 日） "}
        <input key={board.repo.recentDays} ref={input} type="number" name="recentDays" min="0" max={String(MAX_RECENT_DAYS)} step="1" defaultValue={String(board.repo.recentDays)} />
      </label>
      <ActionButton type="submit" action="recent-days">
        変更
      </ActionButton>
    </form>
  );
}
