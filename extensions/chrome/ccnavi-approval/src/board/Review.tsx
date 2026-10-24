/**
 * 依頼済みのフェーズのレビューの欄（`section.review[data-phase]`）。スレッドは未解決を先に、本文は承認の画面と同じ消毒で描く
 * （Markdown は消毒した断片、隠れる書き方は通さない、HTML コメントは見える形で出す。承認者に見えないまま承認させないため）。
 * 「レビュー済みにする」は、Python が通さない理由を返さず、書ける親子のチケットで、読めたときだけ出す。
 */
import type { JSX } from "react";

import type { DecideChoice, ReviewPanel } from "../core/reviewed.js";
import type { DecideState } from "./controller.js";
import { Decide } from "./Decide.js";
import { Markdown } from "./Markdown.js";
import { ActionButton, Link, Notice } from "./parts.js";

/** 残った指摘の行き先を決める画面への入口。渡さなければ画面を出さない（`decide` は開いている画面の状態） */
export interface DecideHandlers {
  readonly state?: DecideState;
  open(phase: number): void;
  confirm(phase: number, choices: Record<string, DecideChoice>, digest: string): void;
  cancel(): void;
}

export function Review({ panel, writable, onReview, decide }: { panel: ReviewPanel; writable: boolean; onReview?: (phase: number) => void; decide?: DecideHandlers }): JSX.Element {
  // GitLab の MR は `!番号`、GitHub の PR は `#番号`
  const mark = (panel.copy?.host ?? panel.host) === "gitlab" ? "!" : "#";
  const head = (
    <h4>
      {`フェーズ ${panel.phase} のレビュー（`}
      {panel.copy ? <Link href={panel.copy.mr.url} text={`マージリクエスト ${mark}${panel.copy.mr.number}`} /> : `マージリクエスト ${mark}${panel.mr}`}
      {`）: ${panel.children.join(", ")}`}
    </h4>
  );
  if (panel.error) {
    return (
      <section className="review" data-phase={String(panel.phase)}>
        {head}
        <Notice kind="error" text={panel.error} />
      </section>
    );
  }
  // 並べるのは取得した結果のとおり。GitHub では目印で始まるスレッドもユーザのものとして数え、GitLab で依頼を投稿したアカウントの
  // ccnavi の依頼のスレッドを数えないのは Python。ここは未解決の件数を取得した結果のとおりに出す
  const threads = [...(panel.copy?.threads ?? [])].sort((a, b) => Number(a.resolved) - Number(b.resolved));
  const unresolved = threads.filter((t) => !t.resolved).length;
  // 未解決の指摘が残るフェーズは、行き先を決める画面から確定する（0 件のときは「レビュー済みにする」）
  const showDecide = writable && decide !== undefined && (unresolved > 0 || (panel.decide?.threads.length ?? 0) > 0);
  return (
    <section className="review" data-phase={String(panel.phase)}>
      {head}
      <p className="threads-count">{`スレッド ${threads.length} 件（未解決 ${unresolved} 件）・レビュー ${panel.copy?.reviews.length ?? 0} 件`}</p>
      <ul className="threads">
        {threads.map((t, i) => (
          <li key={i} className="thread" data-resolved={String(t.resolved)}>
            <p className="thread-head">
              <span className="badge">{t.resolved ? "解決済み" : "未解決"}</span>
              {" "}
              <Link href={t.url} text={t.path ? `${t.path}:${t.line}` : "行の指定なし"} />
            </p>
            <Markdown text={t.body} />
          </li>
        ))}
      </ul>
      {showDecide ? (
        <DecideEntry panel={panel} decide={decide as DecideHandlers} />
      ) : panel.problems.length > 0 ? (
        <ul className="problems">
          {panel.problems.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      ) : writable && onReview ? (
        <div className="actions">
          <ActionButton action="review" onClick={() => onReview(panel.phase)}>{`フェーズ ${panel.phase} をレビュー済みにする`}</ActionButton>
        </div>
      ) : null}
    </section>
  );
}

/** 決める画面を開くボタンと、開いている画面。problems（通さない理由）は画面の上に残す */
function DecideEntry({ panel, decide }: { panel: ReviewPanel; decide: DecideHandlers }): JSX.Element {
  const state = decide.state?.phase === panel.phase ? decide.state : undefined;
  const preview = state?.preview ?? null;
  return (
    <>
      {panel.problems.length > 0 ? (
        <ul className="problems">
          {panel.problems.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      ) : null}
      {state === undefined ? (
        <div className="actions">
          <ActionButton action="decide-open" onClick={() => decide.open(panel.phase)}>{`フェーズ ${panel.phase} の残った指摘の行き先を決める`}</ActionButton>
        </div>
      ) : preview === null ? (
        <>
          <p className="empty">{state.notice || "残った指摘を読み込んでいます…"}</p>
          {state.notice ? (
            <div className="actions">
              <ActionButton action="decide-cancel" onClick={() => decide.cancel()}>閉じる</ActionButton>
            </div>
          ) : null}
        </>
      ) : (
        <Decide
          preview={preview}
          deciding={state.deciding}
          notice={state.notice}
          inherited={state.inherited}
          onConfirm={(choices, digest) => decide.confirm(panel.phase, choices, digest)}
          onCancel={() => decide.cancel()}
        />
      )}
    </>
  );
}
