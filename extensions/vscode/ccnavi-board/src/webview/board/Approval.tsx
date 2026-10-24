/**
 * 承認のオーバーレイ。一覧の識別子の表、承認画面の本文（`<pre>`）、対象外の提案と読めない提案、
 * 「この N 件を承認する」「やめる」。本文は実行ファイルが組んだものをそのまま出し、項目には分けない。
 *
 * 計画を持つ親が一覧にあれば、本文の上にその計画の図を読むだけで出す（`PlanGraph`。中身は preview の
 * `plans` で、承認する提案そのものの順序）。図は実行ファイルの答えを描くだけ。図は親ごとにまとめ、
 * その前に順序を直す計画編集タブへの入口（「計画を編集」）を置く。押すと拡張ホストがオーバーレイを
 * 閉じてからタブを開く（タブで保存すると、この一覧はもう承認できないため）。
 *
 * 親のタブに未保存の変更があれば、その親の図の上に理由を出し、承認のボタンを押せなくする（`unsaved`。拡張ホストが
 * タブの様子が変わるたびに渡し直す）。タブで保存したあとに提案が変わった親（`changedSinceSave`）には注意を出す。
 *
 * 状態は拡張ホストが持っていて、ここは渡されたものを見せるだけ。ボタンを押したら拡張ホストへ返す。
 */
import { Fragment, useEffect, useRef, type JSX } from "react";

import { unsavedParents, unsavedText, type ApprovePlan, type ApprovePreview } from "../../core/approvemodel.js";
import type { ApprovalOverlay } from "../../core/board-view.js";
import { Dialog, DialogActions } from "../shared/Dialog.js";
import { ESCAPE_LAYER, useEscape } from "../useEscape.js";
import { PlanGraph } from "../shared/PlanGraph.js";
import { DecideBody } from "./Decide.js";
import { post } from "./post.js";
import { approvalBody } from "./text.js";

export function Approval({ overlay, unsaved = [] }: { readonly overlay: ApprovalOverlay; readonly unsaved?: readonly string[] }): JSX.Element {
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
  useEscape(overlay.kind !== "approving" && overlay.kind !== "deciding", ESCAPE_LAYER.overlay, () => post({ type: "approveCancel" }));

  return (
    <Dialog titleId="approval-title" backdropClass="approval-backdrop" cardClass="approval" backdropData={{ "data-approval": overlay.kind }} cardRef={box}>
      <Inner overlay={overlay} unsaved={unsaved} />
    </Dialog>
  );
}

function Inner({ overlay, unsaved }: { readonly overlay: ApprovalOverlay; readonly unsaved: readonly string[] }): JSX.Element {
  switch (overlay.kind) {
    case "loading":
      return (
        <>
          <p className="approval-note">承認待ちの内容を読み込み中…</p>
          <DialogActions className="approval-actions">
            <Cancel label="キャンセル" />
          </DialogActions>
        </>
      );
    case "error":
      return (
        <>
          <p className="approval-note error">{overlay.error}</p>
          <DialogActions className="approval-actions">
            <Cancel label="閉じる" />
          </DialogActions>
        </>
      );
    case "preview":
      return <Body preview={overlay.preview} approving={false} notice={overlay.notice} unsaved={unsaved} changed={overlay.changedSinceSave ?? []} />;
    case "approving":
      return <Body preview={overlay.preview} approving={true} notice={undefined} unsaved={unsaved} changed={[]} />;
    case "done":
      return (
        <>
          <h2 id="approval-title">{overlay.count} 件を承認しました</h2>
          {overlay.carried === true ? <p className="approval-note">承認済みチケットをコミット・push するコマンドをターミナルに送信しました。</p> : null}
          <p className="approval-note">
            Claude Code に伝えるプロンプトを用意しました。コピーして進行中のセッションに貼るか、新しいセッションで開いてください。
          </p>
          <pre className="approval-text" data-tour="approval-prompt">{overlay.prompt}</pre>
          <HandOver />
        </>
      );
    case "prompt":
      return (
        <>
          <h2 id="approval-title">{overlay.title}</h2>
          <p className="approval-note">{overlay.note}</p>
          <pre className="approval-text" data-tour="approval-prompt">{overlay.prompt}</pre>
          <HandOver retry={overlay.retry} />
        </>
      );
    case "decideLoading":
      return (
        <>
          <p className="approval-note">フェーズ {overlay.phase} の未解決（Unresolved）指摘を読み込み中…</p>
          <DialogActions className="approval-actions">
            <Cancel label="キャンセル" />
          </DialogActions>
        </>
      );
    case "decidePreview":
      return <DecideBody preview={overlay.preview} deciding={false} notice={overlay.notice} consult={overlay.consult} />;
    case "deciding":
      return <DecideBody preview={overlay.preview} deciding={true} notice={undefined} consult={undefined} />;
  }
}

function Body({
  preview,
  approving,
  notice,
  unsaved,
  changed,
}: {
  readonly preview: ApprovePreview;
  readonly approving: boolean;
  readonly notice: string | undefined;
  readonly unsaved: readonly string[];
  readonly changed: readonly string[];
}): JSX.Element {
  const count = preview.batch.length;
  const tickets = preview.batch.map((b) => b.ticket);
  const blocked = unsavedParents(preview, unsaved);
  const blockedText = blocked.length === 0 ? "" : unsavedText(blocked);
  return (
    <>
      <h2 id="approval-title">{count === 0 ? "承認待ちのチケットなし" : `承認待ちのチケット ${count} 件`}</h2>
      {notice ? <p className="approval-note warn">{notice}</p> : null}
      {count === 0 ? null : (
        <table className="approval-batch" data-tour="approval-batch">
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
                <td>{b.revision ? "親（改版）" : b.parent === null ? "親" : `親 ${b.parent} / フェーズ ${b.phase ?? "?"}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {preview.plans.length > 0 ? (
        <div className="plan-graphs" data-tour="approval-plan">
          {families(preview.plans).map(([ticket, plans]) => (
            <div key={ticket} className="plan-family" data-ticket={ticket}>
              {unsaved.includes(ticket) ? (
                <p className="approval-note warn plan-unsaved">計画編集タブに保存していない変更があります。変更を保存または破棄するまで、表示中のチケットは承認できません。</p>
              ) : null}
              {changed.includes(ticket) ? (
                <p className="approval-note warn plan-changed">
                  計画編集タブで保存したあとに、提案が変わっています（エージェントが変更した可能性があります）。図を確認してから承認してください。
                </p>
              ) : null}
              {approving ? null : (
                <div className="plan-family-actions">
                  <button
                    type="button"
                    className="action"
                    data-action="edit-workflow"
                    data-tour="edit-workflow"
                    data-ticket={ticket}
                    title={`このオーバーレイを閉じて、${ticket} の計画の順序を直す計画編集タブを開きます。保存すると提案内容が変更されるため、承認画面を開き直してください。`}
                    onClick={() => post({ type: "editWorkflow", ticket })}
                  >
                    計画を編集
                  </button>
                </div>
              )}
              {plans.map((plan) => (
                <PlanGraph key={`${plan.ticket}-${plan.part}`} plan={plan} />
              ))}
            </div>
          ))}
        </div>
      ) : null}
      <BodyText text={preview.text} />
      {preview.rejected.length > 0 ? (
        <>
          <h3>承認対象外の提案</h3>
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
          <h3>読み込めなかった提案・承認済みチケット</h3>
          <ul className="approval-problems">
            {preview.problems.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </>
      ) : null}
      {blockedText === "" || approving ? null : (
        <p className="approval-note warn" id="approval-blocked">
          {blockedText}
        </p>
      )}
      <DialogActions className="approval-actions">
        {count === 0 ? null : (
          <button
            type="button"
            className="action primary"
            data-action="approve-confirm"
            data-tour="approve-confirm"
            data-tickets={tickets.join(",")}
            disabled={approving || blockedText !== ""}
            title={blockedText === "" ? undefined : blockedText}
            onClick={() => post({ type: "approveConfirm", tickets, digest: preview.digest })}
          >
            {approving ? "承認中…" : `この ${count} 件を承認する`}
          </button>
        )}
        <Cancel label="キャンセル" disabled={approving} />
      </DialogActions>
    </>
  );
}

/** 計画の図を親ごとにまとめる（全体計画とフィードバック計画は同じ親の 2 つの図）。並びは届いた順 */
function families(plans: readonly ApprovePlan[]): [string, ApprovePlan[]][] {
  const out = new Map<string, ApprovePlan[]>();
  for (const plan of plans) {
    out.set(plan.ticket, [...(out.get(plan.ticket) ?? []), plan]);
  }
  return [...out];
}

/**
 * 承認画面の本文。実行ファイルが組んだ文字列を行のまま出し、説明の付く見出しだけその次の行を
 * ツールチップにまとめる（`approvalBody`）。まとめた説明は目には出さないが、読み上げと選択には残す。
 * まとめられるかどうかを決めるのは見出しの表記だけで、画面は中身を解釈しない。
 */
function BodyText({ text }: { readonly text: string }): JSX.Element {
  const lines = approvalBody(text);
  // 末尾に改行を足さない。実行ファイルが組んだ文字列と同じものが選択とコピーで取れるようにする。
  const br = (i: number): string => (i === lines.length - 1 ? "" : "\n");
  return (
    <pre className="approval-text" data-tour="approval-text">
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

/**
 * 承認のプロンプトと決めた結果のプロンプトは、同じ 2 ボタンで渡す。プロンプトは拡張ホストが持っている分を使う。
 * `retry` があるときは、指摘を読み直して決め直すボタンも出す（表示した後に指摘が追加されたとき）
 */
function HandOver({ retry }: { readonly retry?: { readonly parent: string; readonly phase: number } }): JSX.Element {
  return (
    <DialogActions className="approval-actions" tour="approval-handover">
      <button type="button" className="action primary" data-action="prompt-copy" onClick={() => post({ type: "promptCopy" })}>
        コピー
      </button>
      <button type="button" className="action" data-action="prompt-open" onClick={() => post({ type: "promptOpen" })}>
        新しいセッションで開く
      </button>
      {retry !== undefined ? (
        <button type="button" className="action" data-action="decide-retry" onClick={() => post({ type: "decide", parent: retry.parent, phase: retry.phase })}>
          指摘を読み直して決め直す
        </button>
      ) : null}
      <Cancel label="閉じる" />
    </DialogActions>
  );
}

function Cancel({ label, disabled = false }: { readonly label: string; readonly disabled?: boolean }): JSX.Element {
  return (
    <button type="button" className="action" data-action="approve-cancel" disabled={disabled} onClick={() => post({ type: "approveCancel" })}>
      {label}
    </button>
  );
}
