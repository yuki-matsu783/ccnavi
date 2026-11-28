/**
 * 選んだカードの右のパネル。カードを押す（またはEnter）と開き、カードに出さないもの全部をここに出す。
 * 上から 見出し（タイトル・ID・状態のバッジ・閉じる）・位置（子は親とフェーズ、親は局面）・MR・ユーザ待ち（案内の文と操作）・
 * フェーズ行（親だけ）・操作（チケットを開く・フロー・計画編集・テンプレートの下書き）・詳細（注意の理由の全文・リスクなど）。
 *
 * 何を出すかは組み立て（core/board.ts）が決めた値のとおりで、ここで判定し直さない。どのカードを選んでいるかは画面（`state.ts`）が覚え、
 * ここは渡されたカードを出し、閉じるを押されたら知らせるだけ。幅が狭い（940px以下）ときは、画面が選んだカードの下に置く。
 */
import type { JSX } from "react";

import type { Card, PhaseChip } from "../../core/board.js";
import { flowButtonLabel } from "../../core/flow-view.js";
import type { FlowJson } from "../../core/model.js";
import { ApproveButton, ReviewDoneButton, StatusMark, type ReviewAction } from "./Card.js";
import { post } from "./post.js";
import { detailRows, isHttpUrl, mrText, type DetailRow } from "./text.js";

export function CardPanel({ card, onClose, below }: { readonly card: Card; readonly onClose: () => void; readonly below: boolean }): JSX.Element {
  const where = card.isParent ? card.stage : `親${card.parent} / ${card.phaseLabel === "" ? `フェーズ${card.phase ?? "?"}` : card.phaseLabel}`;
  const rows = detailRows(card);
  return (
    <aside className={below ? "card-panel below" : "card-panel"} data-tour="panel" data-panel={card.id} aria-label={`選んだカード ${card.id}`}>
      <div className="panel-head">
        <div className="card-head">
          <span className="num" title={card.id}>{card.id}</span>
          <span className="title">{card.title}</span>
          <StatusMark status={card.status} />
        </div>
        <button type="button" className="card-button panel-close" data-action="panel-close" title="パネルを閉じます（Esc）" onClick={onClose}>
          閉じる
        </button>
      </div>
      {where !== "" ? <div className={card.isParent ? "stage" : "where"}>{where}</div> : null}
      <MrLink card={card} />
      <Turn card={card} />
      {card.phases.length > 0 ? <Phases phases={card.phases} /> : null}
      <div className="card-actions" data-tour="card-actions">
        {card.openPath !== "" ? (
          <button
            type="button"
            className="card-button"
            data-action="open-ticket"
            data-tour="open-ticket"
            title={card.proposalState !== null ? "提案のファイルを開きます" : "チケットのファイルを開きます"}
            onClick={() => post({ type: "open", filePath: card.openPath })}
          >
            チケットを開く
          </button>
        ) : null}
        {card.flow !== null ? <FlowButton flow={card.flow} id={card.id} closed={card.column === "done" || card.column === "cancelled"} /> : null}
        {card.workflow ? <WorkflowButton id={card.id} /> : null}
        {card.templateDraft ? <TemplateDraftButton id={card.id} /> : null}
      </div>
      {rows.length > 0 ? (
        <dl className="detail-rows" data-tour="details">
          {rows.map((row) => (
            <DetailItem key={row.key} row={row} />
          ))}
        </dl>
      ) : null}
    </aside>
  );
}

/** MR（`MR #番号`）。URLがhttp(s)のときだけ普通のリンクにする（ボタンの寸法にしない） */
function MrLink({ card }: { readonly card: Card }): JSX.Element | null {
  if (card.mrUrl === "" && card.mrNumber === null) {
    return null;
  }
  const text = mrText(card.mrNumber);
  return (
    <div className="card-mr" data-row="mr" title={card.mrUrl === "" ? undefined : card.mrUrl}>
      {isHttpUrl(card.mrUrl) ? (
        <a className="detail-link" href={card.mrUrl}>
          {text}
        </a>
      ) : (
        text
      )}
    </div>
  );
}

/**
 * ユーザ待ち。ユーザができることがあるときだけ出す。主ボタン（primary）は1つ。
 * 親は承認待ちのときだけ（レビューの操作は子だけに置く）。子は承認 → レビュー完了の順で1つだけ。
 * 承認は`pendingApproval`だけを条件にする。バッジが「要確認」でも出す
 */
function Turn({ card }: { readonly card: Card }): JSX.Element | null {
  const approve = card.actions.some((a) => a.kind === "approve");
  const review = card.actions.find((a): a is ReviewAction => a.kind === "review");
  if (!approve && review === undefined) {
    return null;
  }
  return (
    <div className="turn" data-tour="turn">
      <div className="turn-head">ユーザ待ち</div>
      {approve ? (
        <>
          <p className="turn-text">内容を確認して承認してください</p>
          <div className="turn-buttons">
            <ApproveButton id={card.id} />
          </div>
        </>
      ) : null}
      {!approve && review !== undefined ? (
        <>
          <p className="turn-text">MRでレビューしてください</p>
          <div className="turn-buttons">
            {isHttpUrl(review.mrUrl) ? (
              <a className="card-button" data-action="open-mr" data-tour="mr" href={review.mrUrl} title="MRを開きます。指摘は、MRにコメントで書いてください。">
                MRを開く
              </a>
            ) : null}
            <ReviewDoneButton action={review} />
          </div>
        </>
      ) : null}
    </div>
  );
}

/**
 * 親のフェーズ一覧。1フェーズ1行で、左の丸がフェーズ、右が状態の一言（`core/status.ts`の`phaseStatusOf`）。
 * ユーザが対応する注意（`manual`）は1行で出す。行にボタンは置かない（［MRを開く］［レビュー完了］は子の操作）
 */
function Phases({ phases }: { readonly phases: readonly PhaseChip[] }): JSX.Element {
  return (
    <ul className="phases" data-tour="phases">
      {phases.map((p) => (
        <li key={p.number} className={`phase phase-${p.state}${p.status === "レビュー待ち" || p.status === "レビュー準備中" ? " review-hold" : ""}${p.status === "要確認" ? " phase-check" : ""}`} data-phase={p.number} data-status={p.status}>
          <span className="phase-dot" aria-hidden="true" />
          <span className="phase-name">
            <span className="phase-label">{p.label}</span>
            {p.tickets.length > 0 ? <span className="phase-tickets">{p.tickets.join(", ")}</span> : null}
          </span>
          <span className="phase-status">{p.status}</span>
          {p.manual !== "" ? <span className="phase-manual">{p.manual}</span> : null}
        </li>
      ))}
    </ul>
  );
}

/** 詳細の1行。ここのリンクは普通のリンクのまま（ボタンの寸法にしない） */
function DetailItem({ row }: { readonly row: DetailRow }): JSX.Element {
  return (
    <div className="detail-row" data-row={row.key}>
      <dt>{row.label}</dt>
      <dd title={row.title === "" ? undefined : row.title}>
        {row.values.map((value, i) =>
          row.href !== undefined ? (
            <a key={i} className="detail-link" href={row.href}>
              {value}
            </a>
          ) : (
            <div key={i}>{value}</div>
          ),
        )}
      </dd>
    </div>
  );
}

/**
 * 子のフローを開くボタン。言葉は在るか・着手中か（実行ファイルの答えをそのまま反映したもの）で変わる。
 * 着手中でも押せる（読むだけの画面が開く）。押したら拡張ホストへ返すだけ
 */
function FlowButton({ flow, id, closed }: { readonly flow: FlowJson; readonly id: string; readonly closed: boolean }): JSX.Element {
  const state = flow.locked || closed ? "locked" : flow.exists ? "edit" : "create";
  return (
    <button
      type="button"
      className="card-button flow"
      data-action="flow"
      data-tour={`flow-${state}`}
      data-ticket={id}
      data-flow={state}
      title={`子チケットの作業の手順（フロー、${flow.rel}）を図で${closed && !flow.locked ? "見ます。終了済みのため、変更しても使われません。" : flow.locked ? "見ます。着手中・レビュー中は変更できません。" : `${flow.exists ? "直します" : "作ります"}。着手すると、終わるまで変更できなくなります。`}`}
      onClick={() => post({ type: "flow", ticket: id })}
    >
      {flowButtonLabel(flow, closed)}
    </button>
  );
}

/**
 * 親の提案の計画の順序を直す計画編集タブを開くボタン。出すかは組み立て（`card.workflow`）が決め、
 * 押したら拡張ホストへ返すだけ
 */
function WorkflowButton({ id }: { readonly id: string }): JSX.Element {
  return (
    <button
      type="button"
      className="card-button"
      data-action="workflow"
      data-tour="workflow-button"
      data-ticket={id}
      title="計画編集タブを開きます。ステップの順序（どのステップが先行するか）を図で直せます。保存すると提案の内容が変わるので、承認画面を開き直してください。"
      onClick={() => post({ type: "workflow", ticket: id })}
    >
      計画編集
    </button>
  );
}

/** 完了した親から、テンプレートの下書きを作るボタン。出すかは組み立て（`card.templateDraft`）が決め、押したら拡張ホストへ返すだけ */
function TemplateDraftButton({ id }: { readonly id: string }): JSX.Element {
  return (
    <button
      type="button"
      className="card-button"
      data-action="template-draft"
      data-ticket={id}
      title="この親の計画とフローから、テンプレートの下書きを作ります。下書きのままでは使われません。取り込み待ち画面で取り込むと使えるようになります。親チケットをクローズする（ccnavi-review.sh ready）前に作ってください。"
      onClick={() => post({ type: "templateDraft", ticket: id })}
    >
      テンプレートの下書きを作る
    </button>
  );
}
