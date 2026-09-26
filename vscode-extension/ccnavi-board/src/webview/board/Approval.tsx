/**
 * 承認のオーバーレイ。一覧の識別子の表、承認画面の本文（`<pre>`）、対象外の提案と読めない提案、
 * 「この N 件を承認する」「やめる」。本文は実行ファイルが組んだものをそのまま出し、項目には分けない。
 *
 * 状態は拡張ホストが持っていて、ここは渡されたものを見せるだけ。ボタンを押したら拡張ホストへ返す。
 */
import { Fragment, useEffect, useRef, type JSX } from "react";

import type { ApprovePreview } from "../../core/approvemodel.js";
import type { ApprovalOverlay } from "../../core/board-view.js";
import { DecideBody } from "./Decide.js";
import { post } from "./post.js";
import { approvalBody } from "./text.js";

export function Approval({ overlay }: { readonly overlay: ApprovalOverlay }): JSX.Element {
  const box = useRef<HTMLElement>(null);
  // 開いたら「やめる」に焦点を置く（承認したあとは「コピー」）。押せないボタンには置かない
  useEffect(() => {
    const first = box.current?.querySelector<HTMLButtonElement>('button[data-action="prompt-copy"]')
      ?? box.current?.querySelector<HTMLButtonElement>('button[data-action="approve-cancel"]');
    if (first && !first.disabled) {
      first.focus();
    }
  }, [overlay.kind]);
  // Esc でやめる。承認している最中と、行き先を置いている最中は閉じない
  useEffect(() => {
    if (overlay.kind === "approving" || overlay.kind === "deciding") {
      return undefined;
    }
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        post({ type: "approveCancel" });
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [overlay.kind]);

  return (
    <div className="approval-backdrop" data-approval={overlay.kind}>
      <section className="approval" role="dialog" aria-modal="true" aria-labelledby="approval-title" ref={box}>
        <Inner overlay={overlay} />
      </section>
    </div>
  );
}

function Inner({ overlay }: { readonly overlay: ApprovalOverlay }): JSX.Element {
  switch (overlay.kind) {
    case "loading":
      return (
        <>
          <p className="approval-note">承認待ちの内容を読み込み中…</p>
          <div className="approval-actions">
            <Cancel label="やめる" />
          </div>
        </>
      );
    case "error":
      return (
        <>
          <p className="approval-note error">{overlay.error}</p>
          <div className="approval-actions">
            <Cancel label="閉じる" />
          </div>
        </>
      );
    case "preview":
      return <Body preview={overlay.preview} approving={false} notice={overlay.notice} />;
    case "approving":
      return <Body preview={overlay.preview} approving={true} notice={undefined} />;
    case "done":
      return (
        <>
          <h2 id="approval-title">{overlay.count} 件を承認しました</h2>
          {overlay.carried === true ? <p className="approval-note">承認済みチケットのコミットと push をターミナルに送りました。</p> : null}
          <p className="approval-note">
            Claude Code に伝える文を用意しました。コピーして進行中のセッションに貼るか、新しいセッションで開いてください。送るときは自分で Enter を押してください。
          </p>
          <pre className="approval-text">{overlay.prompt}</pre>
          <HandOver />
        </>
      );
    case "prompt":
      return (
        <>
          <h2 id="approval-title">{overlay.title}</h2>
          <p className="approval-note">{overlay.note}</p>
          <pre className="approval-text">{overlay.prompt}</pre>
          <HandOver />
        </>
      );
    case "decideLoading":
      return (
        <>
          <p className="approval-note">フェーズ {overlay.phase} の未解決（Unresolved）の指摘を読み込み中…</p>
          <div className="approval-actions">
            <Cancel label="やめる" />
          </div>
        </>
      );
    case "decidePreview":
      return <DecideBody preview={overlay.preview} deciding={false} notice={overlay.notice} />;
    case "deciding":
      return <DecideBody preview={overlay.preview} deciding={true} notice={undefined} />;
  }
}

function Body({
  preview,
  approving,
  notice,
}: {
  readonly preview: ApprovePreview;
  readonly approving: boolean;
  readonly notice: string | undefined;
}): JSX.Element {
  const count = preview.batch.length;
  const tickets = preview.batch.map((b) => b.ticket);
  return (
    <>
      <h2 id="approval-title">{count === 0 ? "承認待ちのチケットなし" : `承認待ちのチケット ${count} 件`}</h2>
      {notice ? <p className="approval-note warn">{notice}</p> : null}
      {count === 0 ? null : (
        <table className="approval-batch">
          <thead>
            <tr>
              <th>ID</th>
              <th>タイトル</th>
              <th>場所</th>
            </tr>
          </thead>
          <tbody>
            {preview.batch.map((b) => (
              <tr key={b.ticket}>
                <td className="approval-id">{b.ticket}</td>
                <td>{b.title}</td>
                <td>{b.revision ? "親（計画の改訂）" : b.parent === null ? "親" : `親 ${b.parent} / フェーズ ${b.phase ?? "?"}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <BodyText text={preview.text} />
      {preview.rejected.length > 0 ? (
        <>
          <h3>承認の対象にしない提案</h3>
          <ul className="approval-rejected">
            {preview.rejected.map((r) => (
              <li key={r.ticket}>
                <span className="approval-id">{r.ticket}</span>
                {r.problems.map((p, i) => (
                  <div key={i}>{p}</div>
                ))}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {preview.problems.length > 0 ? (
        <>
          <h3>読めなかった提案と承認済みチケット</h3>
          <ul className="approval-problems">
            {preview.problems.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </>
      ) : null}
      <div className="approval-actions">
        {count === 0 ? null : (
          <button
            type="button"
            className="action primary"
            data-action="approve-confirm"
            data-tickets={tickets.join(",")}
            disabled={approving}
            onClick={() => post({ type: "approveConfirm", tickets })}
          >
            {approving ? "承認中…" : `この ${count} 件を承認する`}
          </button>
        )}
        <Cancel label="やめる" disabled={approving} />
      </div>
    </>
  );
}

/**
 * 承認画面の本文。実行ファイルが組んだ文字列を行のまま出し、**説明の付く見出しだけ**その次の行を
 * ツールチップに畳む（`approvalBody`）。畳んだ説明は目には出さないが、読み上げと選択には残す。
 * 畳めるかどうかを決めるのは見出しの綴りだけで、画面は中身を解釈しない。
 */
function BodyText({ text }: { readonly text: string }): JSX.Element {
  const lines = approvalBody(text);
  // 末尾に改行を足さない。実行ファイルが組んだ文字列と同じものが選択とコピーで取れるようにする。
  const br = (i: number): string => (i === lines.length - 1 ? "" : "\n");
  return (
    <pre className="approval-text">
      {lines.map((body, i) =>
        body.note === "" ? (
          <Fragment key={i}>{body.line + br(i)}</Fragment>
        ) : (
          <Fragment key={i}>
            <span className="approval-head" title={body.note}>
              {body.line}
            </span>
            <span className="approval-hint">{` ${body.note}`}</span>
            {br(i)}
          </Fragment>
        ),
      )}
    </pre>
  );
}

/** 承認の文とレビュー済みの連絡は、同じ 2 ボタンで渡す。文は拡張ホストが持っている分を使う */
function HandOver(): JSX.Element {
  return (
    <div className="approval-actions">
      <button type="button" className="action primary" data-action="prompt-copy" onClick={() => post({ type: "promptCopy" })}>
        コピー
      </button>
      <button type="button" className="action" data-action="prompt-open" onClick={() => post({ type: "promptOpen" })}>
        新しいセッションで開く
      </button>
      <Cancel label="閉じる" />
    </div>
  );
}

function Cancel({ label, disabled = false }: { readonly label: string; readonly disabled?: boolean }): JSX.Element {
  return (
    <button type="button" className="action" data-action="approve-cancel" disabled={disabled} onClick={() => post({ type: "approveCancel" })}>
      {label}
    </button>
  );
}
