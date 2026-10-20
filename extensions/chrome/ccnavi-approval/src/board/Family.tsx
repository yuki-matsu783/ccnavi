/**
 * 親子のチケットの欄（`article.family[data-family]`）。判定は出さず、Python の答えを並べるだけ（判定を 1 か所に保つため）。
 *
 * 承認のボタンを出すかは Python の答え（`write.allowed`）で決まり、ここは答えのとおりに
 * 並べるだけ。押したときの動き（`FamilyActions`）は呼び手が渡す。渡さなければボタンを出さない（読み取りだけ、または要確認）。
 * Markdown の本文だけを消毒した断片で入れ、ほかの素の文字列は textContent。
 */
import type { JSX, ReactNode } from "react";

import type { FamilyBoard } from "../core/snapshot.js";
import { Markdown } from "./Markdown.js";
import { ActionButton, Notice } from "./parts.js";
import { Review } from "./Review.js";

export interface FamilyActions {
  approve(): void;
  review?(phase: number): void;
}

/** `attention` は見出しのすぐ後に置く（要確認の一言。`Attention`） */
export function Family({ family: f, actions, attention }: { family: FamilyBoard; actions?: FamilyActions; attention?: ReactNode }): JSX.Element {
  return (
    <article className="family" data-family={f.family.name}>
      <h3>
        <code>{f.family.name}</code>
        {` ${f.family.title}`}
        {/* `/` を含むブランチは識別子（`/` を `-` に置き換えたもの）と名前が違うので、識別子も出す */}
        {f.family.family && f.family.family !== f.family.name ? (
          <>
            {" （識別子 "}
            <code>{f.family.family}</code>
            {"）"}
          </>
        ) : null}
      </h3>
      {attention}
      <FamilyBody f={f} actions={actions} />
    </article>
  );
}

function FamilyBody({ f, actions }: { f: FamilyBoard; actions?: FamilyActions }): ReactNode {
  if (f.error) return <Notice kind="error" text={f.error} />;
  const r = f.result;
  if (r === null) return null;
  const others = r.closure.families.filter((n) => n !== f.family.name);
  const closure = (
    <>
      {others.length > 0 ? <p className="closure">{`判定に使用した関連チケット（先行チケットの依存関係をたどって参照した親子チケット）: ${others.join(", ")}`}</p> : null}
      {r.closure.absent.length > 0 ? <Notice kind="warn" text={`先行チケットの親ブランチがリモートに見つかりません。${r.closure.absent.join(", ")}`} /> : null}
    </>
  );
  if (r.undecided || r.refused) {
    return (
      <>
        {closure}
        <Notice kind="error" text={r.undecided || r.refused || ""} />
      </>
    );
  }
  const batch = r.batch ?? [];
  const write = r.write ?? { allowed: false, reason: "" };
  const problems = r.problems ?? [];
  return (
    <>
      {closure}
      {batch.length === 0 ? <p className="empty">承認待ちのチケットはありません。</p> : null}
      {r.text ? (
        // ccnavi が出した承認の画面の本文（範囲・リスク・計画など）を、最初に開いた形で出す（範囲を最初に見せる）。
        // 閉じた details には入れない。素の文字列なので textContent
        <section className="screen" data-testid="screen">
          <h4>承認内容（承認すると、以下の内容が反映されます）</h4>
          <pre>{r.text}</pre>
        </section>
      ) : null}
      {batch.map((entry) => (
        <section key={entry.ticket} className="entry" data-ticket={entry.ticket}>
          <h4>
            <code>{entry.ticket}</code>
            {` ${entry.title}`}
            {entry.revision ? <span className="badge">改版</span> : null}
            {entry.phase !== null ? <span className="badge">{`フェーズ ${entry.phase}`}</span> : null}
          </h4>
          <p className="path">{entry.path}</p>
          {entry.overflow.map((o, i) => (
            <Notice key={i} kind="warn" text={o} />
          ))}
          <Markdown text={entry.body} />
        </section>
      ))}
      {batch.length > 0 && r.digest ? (
        !write.allowed ? (
          <Notice kind="warn" text={write.reason || "この親子チケットには変更を反映できません。"} />
        ) : actions ? (
          <div className="actions">
            <ActionButton action="approve" onClick={() => actions.approve()}>{`承認する（${batch.map((e) => e.ticket).join(", ")}）`}</ActionButton>
            <span className="digest">{`ダイジェスト ${r.digest.slice(0, 12)}`}</span>
          </div>
        ) : null
      ) : null}
      {(f.reviews ?? []).map((panel) => (
        <Review key={panel.phase} panel={panel} writable={write.allowed} onReview={actions?.review} />
      ))}
      {(r.rejected ?? []).map((rej, i) => (
        <section key={i} className="rejected" data-ticket={rej.ticket}>
          <h4>{`承認対象外：${rej.ticket}`}</h4>
          <ul>
            {rej.problems.map((p, j) => (
              <li key={j}>{p}</li>
            ))}
          </ul>
        </section>
      ))}
      {problems.length > 0 ? (
        <ul className="problems">
          {problems.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      ) : null}
    </>
  );
}
