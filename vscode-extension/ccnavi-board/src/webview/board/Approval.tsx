/**
 * 承認のオーバーレイ。一覧の識別子の表、承認画面の本文（`<pre>`）、対象外の提案と読めない提案、
 * 「この N 件を承認する」「やめる」。本文は実行ファイルが組んだものをそのまま出し、項目には分けない。
 *
 * 状態は拡張ホストが持っていて、ここは渡されたものを見せるだけ。ボタンを押したら拡張ホストへ返す。
 */
import { useEffect, useRef, type JSX } from "react";

import type { ApprovePreview } from "../../core/approvemodel.js";
import type { ApprovalOverlay } from "../../core/board-view.js";
import { post } from "../vscode.js";

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
  // Esc でやめる。承認している最中は閉じない
  useEffect(() => {
    if (overlay.kind === "approving") {
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
          <p className="approval-note">承認待ちの一覧を読み込んでいる…</p>
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
          <h2 id="approval-title">{overlay.count} 件を承認した</h2>
          {overlay.carried === true ? <p className="approval-note">承認済みチケットのコミットと push を端末に送った。</p> : null}
          <p className="approval-note">
            Claude Code に伝える文を用意した。コピーして進行中のセッションに貼るか、新しいセッションで開く。送るときは自分で Enter を押す。
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
      <h2 id="approval-title">Ticket 承認リクエスト: {count} 件</h2>
      {notice ? <p className="approval-note warn">{notice}</p> : null}
      {count === 0 ? (
        <p className="approval-note">承認待ちのチケット無し</p>
      ) : (
        <table className="approval-batch">
          <thead>
            <tr>
              <th>識別子</th>
              <th>題</th>
              <th>場所</th>
            </tr>
          </thead>
          <tbody>
            {preview.batch.map((b) => (
              <tr key={b.ticket}>
                <td className="approval-id">{b.ticket}</td>
                <td>{b.title}</td>
                <td>{b.revision ? "親の改版" : b.parent === null ? "親" : `親 ${b.parent} / フェーズ ${b.phase ?? "?"}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <pre className="approval-text">{preview.text}</pre>
      {preview.rejected.length > 0 ? (
        <>
          <h3>承認の対象にしない</h3>
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
          <h3>読めない提案・承認済みチケット</h3>
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
