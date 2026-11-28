/**
 * 1枚のカード。上から 見出し（タイトル・ID・状態のバッジ。DOMはID・タイトル・バッジの順で、タイトルを先頭に出すのはCSSのorder）・
 * 局面の行（親だけ）・動いた表示・注意（短い言葉）・主な操作（あれば1つ）。
 * 位置・MR・注意の理由の全文・リスク・フェーズ行・フローの入口・補助の操作・詳細は、カードを選ぶと右のパネル（`Panel.tsx`）に出す。
 * 何を出すかは組み立て（core/board.ts）と状態のバッジ（core/status.ts）が決めた値のとおりで、ここで判定し直さない。
 *
 * バッジは「状況」、主な操作は「ユーザができること」。別の規則で決めるので、同時に出てよい
 * （「要確認」のバッジと［承認］など）。ボタンは`card-button`（共通の`button.action`は使わない。寸法は`Card.css`）。
 */
import type { JSX } from "react";

import type { Action, Card } from "../../core/board.js";
import type { Moved } from "../../core/board-moved.js";
import type { Status } from "../../core/status.js";
import { post } from "./post.js";
import { movedLabel, notesOf, predecessorsBadge, type Note } from "./text.js";

export type ReviewAction = Extract<Action, { readonly kind: "review" }>;

/**
 * `moved`は、前の読み直しからこのカードが動いたこと（`core/board-moved.ts`）。動いていなければ
 * 渡らない。何が動いたかを決めるのは画面（`App.tsx`）で、ここは受け取った分に動いた表示を出すだけ。
 * `selected`はこのカードを右のパネルに出しているか。覚えるのは画面（`state.ts`）で、ここは出すだけ。
 */
export function CardItem({
  card,
  hidden,
  moved,
  selected,
  onSelect,
}: {
  readonly card: Card;
  readonly hidden: boolean;
  readonly moved?: Moved;
  readonly selected: boolean;
  readonly onSelect: () => void;
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
  if (selected) {
    classes.push("selected");
  }
  if (hidden) {
    classes.push("hidden");
  }
  // ボタン・リンクの上では選ばない（そのボタンの操作だけをする）
  const select = (target: EventTarget | null): void => {
    if (target instanceof Element && target.closest(NOT_SELECTING) !== null) {
      return;
    }
    onSelect();
  };
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
      aria-current={selected ? "true" : undefined}
      tabIndex={0}
      onClick={(event) => {
        // IDをドラッグして文字を選んだあとのclickは、共通の親（カード）まで届く。文字を選択中は選ばない（Enterでは見ない）
        if ((window.getSelection?.()?.toString() ?? "") === "") {
          select(event.target);
        }
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" && !(event.target instanceof Element && event.target.closest(NOT_SELECTING) !== null)) {
          event.preventDefault();
          select(event.target);
        }
      }}
    >
      <div className="card-head">
        <span className="num" title={card.id}>{card.id}</span>
        <span className="title">{card.title}</span>
        <StatusMark status={card.status} />
      </div>
      {card.isParent && card.stage !== "" ? <div className="stage">{card.stage}</div> : null}
      {moved !== undefined ? (
        <div className="moved-mark" data-tour="moved" title="前回の更新から列が変わりました。この表示は、次にどれかのカードの列が変わるまで残ります">
          {movedLabel(moved)}
        </div>
      ) : null}
      <Notes card={card} />
      <Primary card={card} />
    </li>
  );
}

/** カードの上で押しても選ばない場所。ボタン・リンク */
const NOT_SELECTING = "button, a";

/** 状態のバッジ。見出しの右端に1つ。言葉は必ず出す（色だけに頼らない）。理由があればツールチップ */
export function StatusMark({ status }: { readonly status: Status }): JSX.Element {
  return (
    <span className={`status status-${status.tone}`} data-tour="status" title={status.title === "" ? undefined : status.title}>
      {status.label}
    </span>
  );
}

/**
 * カードの主な操作。あれば1つだけ出す（主ボタン）。承認待ちなら［承認］、そうでなくバッジが「レビュー待ち」の子なら［レビュー完了］。
 * 承認は`pendingApproval`だけを条件にする（バッジが「要確認」でも出す）。［MRを開く］と案内の文は右のパネルの「ユーザ待ち」に出す
 */
function Primary({ card }: { readonly card: Card }): JSX.Element | null {
  const approve = card.actions.some((a) => a.kind === "approve");
  const review = card.actions.find((a): a is ReviewAction => a.kind === "review");
  if (!approve && review === undefined) {
    return null;
  }
  return <div className="card-primary">{approve ? <ApproveButton id={card.id} tour /> : review !== undefined ? <ReviewDoneButton action={review} tour /> : null}</div>;
}

/**
 * 注意。例外のときだけ、短い言葉で並べる（理由の全文は右のパネルの詳細）。出すものが無ければ行ごと出さない。
 * 先行待ちの識別子は途中で折り返さない（折り返すのは識別子の間の空白か、見出しと括弧の間だけ）
 */
export function Notes({ card }: { readonly card: Card }): JSX.Element | null {
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
 * ［レビュー完了］（主ボタン）。残った指摘を読む流れに入るだけで、まだ何も置かない。
 * `tour`はカードの上のボタンにだけ付ける（案内の指す先を1つにする）
 */
export function ReviewDoneButton({ action, tour = false }: { readonly action: ReviewAction; readonly tour?: boolean }): JSX.Element {
  return (
    <button
      type="button"
      className="card-button primary"
      data-action="review-done"
      data-tour={tour ? "review-done" : undefined}
      data-parent={action.parent}
      data-phase={action.phase}
      title="レビューを終えたことを記録します。未解決の指摘を読み、無ければレビュー済みにします。あれば対応方針を決めます"
      onClick={() => post({ type: "decide", parent: action.parent, phase: action.phase })}
    >
      レビュー完了
    </button>
  );
}

/** このカードだけを承認の対象にする（`--agree --preview --json <識別子>`）。同じ親の承認待ちが他にあっても巻き込まない */
export function ApproveButton({ id, tour = false }: { readonly id: string; readonly tour?: boolean }): JSX.Element {
  return (
    <button
      type="button"
      className="card-button primary"
      data-action="approve-one"
      data-tour={tour ? "approve-one" : undefined}
      data-ticket={id}
      title={`このチケットだけを承認します（ccnavi user agree ${id}）。まとめて承認するなら上部のボタンを使ってください`}
      onClick={() => post({ type: "approve", tickets: [id], filtered: true })}
    >
      承認
    </button>
  );
}
