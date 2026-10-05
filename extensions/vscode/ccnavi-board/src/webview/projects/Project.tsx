/**
 * プロジェクト 1 件のカード。
 *
 * 一覧を表ではなくカードにしているのは、表だと列が 7 本になり、幅が足りないと検証やチケットの列が
 * 1 文字ずつ縦に並んでしまうため。カードの中の項目は幅に合わせて 1〜3 段に組み替わる（CSS の grid）。
 */
import type { JSX } from "react";

import type { LintProblem } from "../../core/lintmodel.js";
import type { ProjectRow } from "../../core/projects-view.js";
import { problemsOf } from "./text.js";

export interface ProjectProps {
  readonly row: ProjectRow;
  readonly ticketsEnabled: boolean;
}

export function Project({ row, ticketsEnabled }: ProjectProps): JSX.Element {
  const problems = problemsOf(row);
  const flags = [
    row.doing > 0 ? { key: "doing", className: "badge doing", text: `作業中 ${row.doing}` } : undefined,
    problems.some((p) => p.severity === "error") ? { key: "error", className: "badge error", text: "error" } : undefined,
    problems.some((p) => p.severity === "warn") ? { key: "warn", className: "badge warn", text: "warn" } : undefined,
  ].filter((f) => f !== undefined);
  return (
    <li className={`project${problems.length > 0 ? " has-problem" : ""}`} data-name={row.name}>
      <div className="project-head">
        <span className="name mono">{row.name}</span>
        <span className="rel small dim">{row.rel}</span>
        {flags.length > 0 && (
          <span className="flags">
            {flags.map((f) => (
              <span key={f.key} className={f.className}>
                {f.text}
              </span>
            ))}
          </span>
        )}
      </div>
      <dl className="fields">
        <div className="field">
          <dt>origin</dt>
          <dd>
            {row.origin === "" ? (
              <span className="dim">不明</span>
            ) : (
              <span className="mono small" title={row.origin}>
                {row.origin}
              </span>
            )}
          </dd>
        </div>
        <div className="field">
          <dt>ルール</dt>
          <dd>
            <Rules row={row} />
          </dd>
        </div>
        <div className="field">
          <dt>ワークツリー</dt>
          <dd>
            {row.worktrees.length === 0 ? (
              <span className="dim">なし</span>
            ) : (
              <>
                {row.worktrees.length} 件 <span className="small dim">{row.worktrees.join(", ")}</span>
              </>
            )}
          </dd>
        </div>
        {ticketsEnabled && (
          <div className="field">
            <dt>チケット</dt>
            <dd>
              {row.tickets} 件{row.doing > 0 && <span className="dim">、作業中 {row.doing} 件</span>}
            </dd>
          </div>
        )}
        <div className="field wide">
          <dt>検証</dt>
          <dd>{problems.length === 0 ? <span className="ok">問題なし</span> : <Problems problems={problems} />}</dd>
        </div>
      </dl>
    </li>
  );
}

/** プロジェクトの設定のルールファイル。プロジェクトの設定として数えられていない（予約名）なら、置く先も出さない */
function Rules({ row }: { readonly row: ProjectRow }): JSX.Element {
  if (row.rulesRel === "") {
    return <span className="dim">設定の対象になっていません（検証の error を確かめてください）</span>;
  }
  if (row.rulesExists) {
    return (
      <>
        <span className="ok">あり</span> <span className="mono small">{row.rulesRel}</span>
      </>
    );
  }
  return (
    <>
      <span className="warn-text">なし</span> <span className="mono small dim">{row.rulesRel}</span>
    </>
  );
}

function Problems({ problems }: { readonly problems: readonly LintProblem[] }): JSX.Element {
  return (
    <ul className="lint">
      {problems.map((p, index) => (
        <li key={`${p.severity}:${index}`} className={p.severity}>
          {p.severity}: {p.detail}
        </li>
      ))}
    </ul>
  );
}
