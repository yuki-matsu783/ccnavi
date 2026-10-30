/**
 * 1 枚のカード。上から 見出し（タイトル・ID・状態のバッジ。DOM は ID・タイトル・バッジの順で、タイトルを先頭に出すのは CSS の order）・位置・MR（あれば）・ユーザ待ち・注意・フェーズ行（親だけ）・補助の操作・詳細（畳む）。
 * 何を出すかは組み立て（core/board.ts）と状態のバッジ（core/status.ts）が決めた値のとおりで、ここで判定し直さない。
 *
 * バッジは「状況」、「ユーザ待ち」は「ユーザができること」。別の規則で決めるので、同時に出てよい
 * （「要確認」のバッジと［承認］など）。ボタンは `card-button`（共通の `button.action` は使わない。寸法は `Card.css`）。
 */
import type { JSX } from "react";

import type { Action, Card, PhaseChip } from "../../core/board.js";
import type { Moved } from "../../core/board-moved.js";
import { flowButtonLabel } from "../../core/flow-view.js";
import type { FlowJson } from "../../core/model.js";
import type { Status } from "../../core/status.js";
import { post } from "./post.js";
import { detailRows, isHttpUrl, movedLabel, mrText, notesOf, predecessorsBadge, type DetailRow, type Note } from "./text.js";

type ReviewAction = Extract<Action, { readonly kind: "review" }>;

/**
 * `moved` は、前の読み直しからこのカードが動いたこと（`core/board-moved.ts`）。動いていなければ
 * 渡らない。何が動いたかを決めるのは画面（`App.tsx`）で、ここは受け取った分に動いた表示を出すだけ。
 * `detailsOpen` は詳細を開いているか。覚えるのは画面（`state.ts`）で、ここは出すだけ。
 */
export function CardItem({
  card,
  hidden,
  moved,
  detailsOpen,
  onDetails,
}: {
  readonly card: Card;
  readonly hidden: boolean;
  readonly moved?: Moved;
  readonly detailsOpen: boolean;
  readonly onDetails: (open: boolean) => void;
}): JSX.Element {
  const classes = ["card", card.isParent ? "parent" : "child"];
  if (card.issues.length > 0) {
    classes.push("has-issue");
  }
  if (card.status.label === "レビュー待ち" || card.status.label === "レビュー準備中") {
    classes.push("review-hold");
  }
  if (card.pendingApproval) {
    classes.push("pending");
  }
  if (moved !== undefined) {
    classes.push("moved");
  }
  if (hidden) {
    classes.push("hidden");
  }
  // ボタン・リンク・詳細（開閉と、開いた本文）の上では提案を開かない
  const open = (target: EventTarget | null): void => {
    if (target instanceof Element && target.closest(NOT_OPENING) !== null) {
      return;
    }
    if (card.openPath !== "") {
      post({ type: "open", filePath: card.openPath });
    }
  };
  const where = card.isParent ? card.stage : `親 ${card.parent} / ${card.phaseLabel === "" ? `フェーズ ${card.phase ?? "?"}` : card.phaseLabel}`;
  const auxiliary = card.flow !== null || card.workflow || card.templateDraft;
  return (
    <li
      className={classes.join(" ")}
      data-id={card.id}
      data-tour={`card-${card.id}`}
      data-path={card.openPath}
      data-project={card.project}
      data-family={card.family}
      data-attention={card.attention ? "1" : "0"}
      data-status={card.status.label}
      data-moved={moved === undefined ? undefined : `${moved.from ?? "none"}-${moved.to}`}
      tabIndex={0}
      onClick={(event) => {
        // ID をドラッグして文字を選んだあとの click は、共通の親（カード）まで届く。文字を選択中は開かない（Enter では見ない）
        if ((window.getSelection?.()?.toString() ?? "") === "") {
          open(event.target);
        }
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" && !(event.target instanceof Element && event.target.closest(NOT_OPENING) !== null)) {
          event.preventDefault();
          open(event.target);
        }
      }}
    >
      <div className="card-head">
        <span className="num" title={card.id}>{card.id}</span>
        <span className="title">{card.title}</span>
        <StatusMark status={card.status} />
      </div>
      {where !== "" ? <div className={card.isParent ? "stage" : "where"}>{where}</div> : null}
      <MrLink card={card} />
      {moved !== undefined ? (
        <div className="moved-mark" data-tour="moved" title="前回の更新から列が変わりました。この表示は、次にどれかのカードの列が変わるまで残ります">
          {movedLabel(moved)}
        </div>
      ) : null}
      <Turn card={card} />
      <Notes card={card} />
      {card.phases.length > 0 ? <Phases phases={card.phases} /> : null}
      {auxiliary ? (
        <div className="card-actions" data-tour="card-actions">
          {card.flow !== null ? <FlowButton flow={card.flow} id={card.id} closed={card.column === "done" || card.column === "cancelled"} /> : null}
          {card.workflow ? <WorkflowButton id={card.id} /> : null}
          {card.templateDraft ? <TemplateDraftButton id={card.id} /> : null}
        </div>
      ) : null}
      <Details card={card} open={detailsOpen} onToggle={onDetails} />
    </li>
  );
}

/** カードの上で押しても提案を開かない場所。ボタン・リンク・詳細（開閉と開いた本文） */
const NOT_OPENING = "button, a, details";

/** マージリクエスト（`MR #番号`）。詳細を開かなくても見えるよう、位置の下に出す。URL が http(s) のときだけリンクにする */
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

/** 状態のバッジ。見出しの右端に 1 つ。言葉は必ず出す（色だけに頼らない）。理由があればツールチップ */
function StatusMark({ status }: { readonly status: Status }): JSX.Element {
  return (
    <span className={`status status-${status.tone}`} data-tour="status" title={status.title === "" ? undefined : status.title}>
      {status.label}
    </span>
  );
}

/**
 * ユーザ待ち。ユーザができることがあるときだけ出す。主ボタン（primary）は 1 つ。
 * 親は承認待ちのときだけ（レビューの操作は各フェーズ行に置く）。子は承認 → レビュー完了の順で 1 つだけ。
 * 承認は `pendingApproval` だけを条件にする。バッジが「要確認」でも出す
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
          {card.isParent ? <p className="turn-text">内容を確認して承認してください</p> : null}
          <div className="turn-buttons">
            <ApproveButton id={card.id} />
          </div>
        </>
      ) : null}
      {!approve && review !== undefined ? (
        <>
          <p className="turn-text">マージリクエストでレビューしてください</p>
          <div className="turn-buttons">
            <ReviewButtons action={review} />
          </div>
        </>
      ) : null}
    </div>
  );
}

/**
 * 注意。例外のときだけ、短い言葉で並べる（理由の全文は詳細）。出すものが無ければ行ごと出さない。
 * 先行待ちの識別子は途中で折り返さない（折り返すのは識別子の間の空白か、見出しと括弧の間だけ）
 */
function Notes({ card }: { readonly card: Card }): JSX.Element | null {
  const notes = notesOf(card);
  const preds = card.predecessorsUnmet.length > 0 ? predecessorsBadge(card) : undefined;
  if (notes.length === 0 && preds === undefined) {
    return null;
  }
  return (
    <div className="badges notes" data-tour="notes">
      {notes.map((note) => (
        <Badge key={note.key} note={note} />
      ))}
      {preds !== undefined ? (
        <span className="badge preds" data-tour="badge-preds" title={preds.title}>
          {preds.lead}（
          {preds.ids.map((id, i) => (
            <span key={id}>
              <span className="badge-id">{id}</span>
              {i < preds.ids.length - 1 ? ", " : ""}
            </span>
          ))}
          ）
        </span>
      ) : null}
    </div>
  );
}

function Badge({ note }: { readonly note: Note }): JSX.Element {
  return (
    <span className={`badge ${note.kind}`} data-tour={`badge-${note.key}`}>
      {note.text}
    </span>
  );
}

/**
 * 親カードのフェーズ一覧。1 フェーズ 1 行で、左の丸がフェーズ、右が状態の一言（`core/status.ts` の `phaseStatusOf`）。
 * ユーザが対応する注意（`manual`）は 1 行で出す。レビュー待ちの行には、行の中に［MR を開く］［レビュー完了］を置く
 * （複数のフェーズが同時にレビュー待ちなら、それぞれの行に置く。主ボタンは行ごとに 1 つ）。
 * 状態の列に入れると、55% で止めた列の中でボタンが縦に積まれて行が高くなるため、ボタンは行の 2 段目に左寄せで並べる
 */
function Phases({ phases }: { readonly phases: readonly PhaseChip[] }): JSX.Element {
  return (
    <ul className="phases" data-tour="phases">
      {phases.map((p) => {
        const review = p.actions.find((a): a is ReviewAction => a.kind === "review");
        return (
          <li key={p.number} className={`phase phase-${p.state}${p.status === "レビュー待ち" || p.status === "レビュー準備中" ? " review-hold" : ""}${p.status === "要確認" ? " phase-check" : ""}`} data-phase={p.number} data-status={p.status}>
            <span className="phase-dot" aria-hidden="true" />
            <span className="phase-name">
              <span className="phase-label">{p.label}</span>
              {p.tickets.length > 0 ? <span className="phase-tickets">{p.tickets.join(", ")}</span> : null}
            </span>
            <span className="phase-status">{p.status}</span>
            {p.manual !== "" ? <span className="phase-manual">{p.manual}</span> : null}
            {review !== undefined ? (
              <span className="phase-actions" data-tour="phase-review">
                <ReviewButtons action={review} />
              </span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * 詳細。畳んでおき、開きたいときだけ見る。開閉は画面が覚える（カードごと。更新や列の移動でも閉じない）ので、
 * ここは渡された状態を出し、押されたら知らせるだけ。ブラウザの既定の開閉は止める（状態を 2 か所に持たない）。
 * ここのリンクは普通のリンクのまま（ボタンの寸法にしない）
 */
function Details({ card, open, onToggle }: { readonly card: Card; readonly open: boolean; readonly onToggle: (open: boolean) => void }): JSX.Element | null {
  const rows = detailRows(card);
  if (rows.length === 0) {
    return null;
  }
  return (
    <details className="card-details" data-tour="details" open={open}>
      <summary
        onClick={(event) => {
          event.preventDefault();
          onToggle(!open);
        }}
      >
        詳細
      </summary>
      {open ? (
        <dl className="detail-rows">
          {rows.map((row) => (
            <DetailItem key={row.key} row={row} />
          ))}
        </dl>
      ) : null}
    </details>
  );
}

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
 * ［MR を開く］（リンク）と［レビュー完了］（主ボタン）。リンクは URL が http(s) のときだけ（`javascript:` などは出さない）。
 * ［レビュー完了］は、残った指摘を読む流れに入るだけで、まだ何も置かない
 */
function ReviewButtons({ action }: { readonly action: ReviewAction }): JSX.Element {
  return (
    <>
      {isHttpUrl(action.mrUrl) ? (
        <a className="card-button" data-action="open-mr" data-tour="mr" href={action.mrUrl} title="マージリクエストを開きます。指摘はそこにコメントで書きます">
          MR を開く
        </a>
      ) : null}
      <button
        type="button"
        className="card-button primary"
        data-action="review-done"
        data-tour="review-done"
        data-parent={action.parent}
        data-phase={action.phase}
        title="レビューを終えたことを記録します。未解決の指摘を読み、無ければレビュー済みにします。あれば対応方針を決めます"
        onClick={() => post({ type: "decide", parent: action.parent, phase: action.phase })}
      >
        レビュー完了
      </button>
    </>
  );
}

/** このカードだけを承認の対象にする（`--agree --preview --json <識別子>`）。同じ親の承認待ちが他にあっても巻き込まない */
function ApproveButton({ id }: { readonly id: string }): JSX.Element {
  return (
    <button
      type="button"
      className="card-button primary"
      data-action="approve-one"
      data-tour="approve-one"
      data-ticket={id}
      title={`このチケットだけを承認します（ccnavi user agree ${id}）。まとめて承認するなら上部のボタンを使ってください`}
      onClick={() => post({ type: "approve", tickets: [id], filtered: true })}
    >
      承認
    </button>
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
      title={`子チケットの作業の手順（フロー）を図で${closed && !flow.locked ? "見ます。終了済みのため変更する必要はありません" : flow.locked ? "見ます。着手中・レビュー中は変更できません" : `${flow.exists ? "直します" : "作ります"}。着手すると、終わるまで変更できなくなります`}（${flow.rel}）`}
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
      title="計画のステップどうしの順序（どのステップにどのステップが先行するか）を図で直す計画編集タブを開きます。保存すると提案内容が変更されるため、承認画面を開き直してください。"
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
      title="この親の計画とフローから、テンプレートの下書きを作ります。下書きに効力はなく、取り込み待ち画面で取り込むまで使われません。親を ready で閉じる前に作ってください"
      onClick={() => post({ type: "templateDraft", ticket: id })}
    >
      テンプレートの下書きを作る
    </button>
  );
}
