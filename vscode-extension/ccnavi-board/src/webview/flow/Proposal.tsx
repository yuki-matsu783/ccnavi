/**
 * エージェントの下書き（ADR-0100）を取り込む前に見せる差分（`core/flow-diff.ts` の `textDiff`）と、エージェントへの
 * 依頼の文。どちらもボードの承認のオーバーレイと同じ幕の上の 1 枚（見た目は `SaveReview.css` と `Proposal.css`）。
 *
 * **差分は文の前後まで見せる。** フローの文は担当のサブエージェントへの案内文になり、プロンプトインジェクションの
 * 経路になる。歯止めはユーザがここで読むことなので、変わった欄の名前だけでなく、値の前と後を並べる。
 *
 * 「取り込む」は編集中の内容に入れるだけで、書かない（保存はいつもの経路）。着手中（錠）は押せない。未保存の変更が
 * あれば、それを捨てて取り込むことを文とボタンの言葉で確かめる。
 */
import { useEffect, useRef, type JSX } from "react";

import { textDiff, type TextChange, type ValueKind } from "../../core/flow-diff.js";
import type { FlowDoc } from "../../core/flow-doc.js";
import type { FlowLock, FlowProposal } from "../../core/flow-view.js";
import { post } from "./post.js";

const KIND_TITLES: Readonly<Record<TextChange["kind"], string>> = {
  meta: "フロー",
  "added-node": "足すノード",
  "removed-node": "消すノード",
  "changed-node": "変えるノード",
  "added-connection": "足す線",
  "removed-connection": "消す線",
  "changed-connection": "変える線",
};

/** 開いた下書きの様子。読み込み中・取り込めない・取り込める */
export type ProposalView =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly error: string }
  | { readonly kind: "ready"; readonly proposal: FlowProposal };

/** 値の種類の印（`1` と `"1"` を見分ける） */
function Kind({ kind }: { readonly kind: ValueKind | undefined }): JSX.Element | null {
  return kind === undefined ? null : <span className="proposal-value-kind">{kind}</span>;
}

function Change({ change }: { readonly change: TextChange }): JSX.Element {
  return (
    <li className="proposal-change" data-kind={change.kind}>
      <div className="proposal-head">
        <span className="proposal-kind">{KIND_TITLES[change.kind]}</span> {change.label}
      </div>
      <dl className="proposal-texts">
        {change.texts.map((text, index) => (
          <div key={index} className="proposal-field">
            <dt>{text.field}</dt>
            {text.before !== undefined && (
              <dd className="proposal-before" title="いまのフロー">
                <Kind kind={text.beforeKind} />
                <pre>{text.before}</pre>
              </dd>
            )}
            {text.after !== undefined && (
              <dd className="proposal-after" title="下書き">
                <Kind kind={text.afterKind} />
                <pre>{text.after}</pre>
              </dd>
            )}
          </div>
        ))}
      </dl>
    </li>
  );
}

export interface ProposalReviewProps {
  readonly view: ProposalView;
  /** いまのフロー（読み込んだ中身） */
  readonly base: FlowDoc;
  readonly lock: FlowLock;
  /** 編集中に未保存の変更がある */
  readonly dirty: boolean;
  readonly onImport: (proposal: FlowProposal) => void;
  readonly onCancel: () => void;
}

export function ProposalReview({ view, base, lock, dirty, onImport, onCancel }: ProposalReviewProps): JSX.Element {
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancel.current?.focus();
  }, []);
  const diff = view.kind === "ready" ? textDiff(base, view.proposal.doc) : undefined;
  const changes = diff?.changes ?? [];
  // 欄ごとに分けて見せられなかった下書きは取り込ませない（読めていないものを入れない）
  const blocked = lock.locked || diff?.problem !== undefined;
  return (
    <div className="review-backdrop">
      <section className="review proposal" id="proposal-review" role="dialog" aria-modal="true" aria-labelledby="proposal-review-title">
        <h2 id="proposal-review-title">エージェントの提案を確かめる</h2>
        {view.kind === "loading" && <p className="dim">下書きを ccnavi で確かめています…</p>}
        {view.kind === "error" && (
          <p className="proposal-error" id="proposal-error">
            {view.error}
          </p>
        )}
        {view.kind === "ready" && (
          <>
            <p className="dim small">
              下書き {view.proposal.draftPath} と、いまのフローとの違いです。フローの文は担当のサブエージェントへの案内になります。
              外部の道具を使わせる指示などが紛れていないか、文を読んでから取り込んでください。
            </p>
            {diff?.problem !== undefined && (
              <p className="proposal-error" id="proposal-problem">
                取り込めません: {diff.problem}。下の全体の JSON を確かめ、エディタで直すか、エージェントに書き直しを頼んでください。
              </p>
            )}
            {changes.length === 0 && <p id="proposal-order-only">並び順だけが違います（ノードと線の中身は同じです）。</p>}
            <ul className="proposal-list">
              {changes.map((change, index) => (
                <Change key={index} change={change} />
              ))}
            </ul>
            {lock.locked && <p className="proposal-locked">着手中のため取り込めません。{lock.reason}</p>}
            {!lock.locked && dirty && (
              <p className="proposal-dirty" id="proposal-dirty">
                未保存の変更があります。取り込むと、編集中の内容は下書きの中身に置き換わります（元に戻すで戻せます）。
              </p>
            )}
          </>
        )}
        <div className="review-actions">
          <button type="button" className="action" data-action="cancel-proposal" ref={cancel} onClick={onCancel}>
            閉じる
          </button>
          {view.kind === "ready" && (
            <button type="button" className="action primary" data-action="import-proposal" disabled={blocked} onClick={() => onImport(view.proposal)}>
              {dirty ? "未保存の変更を捨てて取り込む" : "取り込む"}
            </button>
          )}
        </div>
      </section>
    </div>
  );
}

/** エージェントへの依頼の文。承認の文と同じ 2 ボタンで渡す。文は拡張ホストが持っている分を使う */
export function RequestText({ prompt, onClose }: { readonly prompt: string; readonly onClose: () => void }): JSX.Element {
  const copy = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    copy.current?.focus();
  }, []);
  return (
    <div className="review-backdrop">
      <section className="review" id="request-text" role="dialog" aria-modal="true" aria-labelledby="request-text-title">
        <h2 id="request-text-title">エージェントに頼む文</h2>
        <p className="dim small">
          コピーして進行中のセッションに貼るか、新しいセッションで開いてください。送るときは自分で Enter を押してください。エージェントは下書きを書くだけで、
          取り込むかはこの画面で差分を読んで決めます。
        </p>
        <pre className="request-prompt">{prompt}</pre>
        <div className="review-actions">
          <button type="button" className="action primary" data-action="request-copy" ref={copy} onClick={() => post({ type: "requestCopy" })}>
            コピー
          </button>
          <button type="button" className="action" data-action="request-open" onClick={() => post({ type: "requestOpen" })}>
            新しいセッションで開く
          </button>
          <button type="button" className="action" data-action="request-close" onClick={onClose}>
            閉じる
          </button>
        </div>
      </section>
    </div>
  );
}
