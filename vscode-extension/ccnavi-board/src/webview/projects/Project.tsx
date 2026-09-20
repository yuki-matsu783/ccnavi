/**
 * プロジェクト 1 件のカード。
 *
 * 一覧を表ではなくカードにしているのは、表だと列が 7 本になり、幅が足りないと検証やチケットの列が
 * 1 文字ずつ縦に潰れるため。カードの中の項目は幅に合わせて 1〜3 段に組み替わる（CSS の grid）。
 */
import type { JSX } from "react";

import type { LintProblem } from "../../core/lintmodel.js";
import type { ProjectRow } from "../../core/projects-view.js";
import { Menu } from "./Menu.js";
import { post } from "./post.js";
import { problemsOf } from "./text.js";

export interface ProjectProps {
  readonly row: ProjectRow;
  readonly ticketsEnabled: boolean;
  /** いま開いているメニューの名前。App が持つ */
  readonly openMenu: string | undefined;
  readonly onOpenMenu: (id: string | undefined) => void;
}

export function Project({ row, ticketsEnabled, openMenu, onOpenMenu }: ProjectProps): JSX.Element {
  const problems = problemsOf(row);
  const flags = [
    row.doing > 0 ? { key: "doing", className: "badge doing", text: `作業中 ${row.doing}` } : undefined,
    problems.some((p) => p.severity === "error") ? { key: "error", className: "badge error", text: "error" } : undefined,
    problems.some((p) => p.severity === "warn") ? { key: "warn", className: "badge warn", text: "warn" } : undefined,
  ].filter((f) => f !== undefined);
  // メニューの中の項目を押したら、送ってから閉じる
  const send = (message: Parameters<typeof post>[0]): void => {
    post(message);
    onOpenMenu(undefined);
  };
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
      <div className="ops">
        <Menu id={`${row.name}:open`} label="開く ▾" open={openMenu === `${row.name}:open`} onOpen={onOpenMenu}>
          <button
            type="button"
            className="action"
            data-action="open-rules"
            data-name={row.name}
            disabled={!row.rulesExists}
            title={`このプロジェクトの ${row.rulesRel === "" ? "層のルール" : row.rulesRel} を編集し、判定を試す`}
            onClick={() => send({ type: "openRules", name: row.name })}
          >
            ルール管理
          </button>
          {/* フェーズの種類は親チケットの計画と子の範囲にしか読まれない。チケット制御が disable の間は
              何も動かさないので、開く側（phases-panel）と揃えて入口を出さない */}
          {ticketsEnabled && (
            <button
              type="button"
              className="action"
              data-action="open-phases"
              data-name={row.name}
              disabled={row.rulesRel === ""}
              title="このプロジェクトのチケットの計画に、共通層に足して使うフェーズの種類を編集する。無ければ画面から作れる"
              onClick={() => send({ type: "openPhases", name: row.name })}
            >
              フェーズ管理
            </button>
          )}
          {ticketsEnabled && (
            <button
              type="button"
              className="action"
              data-action="open-board"
              data-name={row.name}
              title="このプロジェクトに絞ってチケット管理を開く"
              onClick={() => send({ type: "openBoard", name: row.name })}
            >
              チケット管理
            </button>
          )}
        </Menu>
        <Menu id={`${row.name}:git`} label="git ▾" open={openMenu === `${row.name}:git`} onOpen={onOpenMenu}>
          <button
            type="button"
            className="action"
            data-action="fetch"
            data-name={row.name}
            title="git fetch をターミナルで実行する"
            onClick={() => send({ type: "fetch", name: row.name })}
          >
            fetch
          </button>
          <button
            type="button"
            className="action"
            data-action="pull"
            data-name={row.name}
            title="git pull をターミナルで実行する。衝突があれば git が止める"
            onClick={() => send({ type: "pull", name: row.name })}
          >
            pull
          </button>
        </Menu>
      </div>
    </li>
  );
}

/** 層のルールファイル。層として数えられていない（予約名）なら、置く先も作るボタンも出さない */
function Rules({ row }: { readonly row: ProjectRow }): JSX.Element {
  if (row.rulesRel === "") {
    return <span className="dim">層として数えられていない（検証の error を見る）</span>;
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
      <span className="warn-text">なし</span> <span className="mono small dim">{row.rulesRel}</span>{" "}
      <button
        type="button"
        className="action small"
        data-action="create-rules"
        data-name={row.name}
        title="共通層の rules.yml をこのプロジェクトの層にコピーする。文面の sh のパスは {root} 付きに置き換える"
        onClick={() => post({ type: "createRules", name: row.name })}
      >
        共通層からコピー
      </button>
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
