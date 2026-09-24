/**
 * 1 枚のカード。バッジ（人が動く必要がある状態）・属性の行・親のフェーズ一覧・不備・操作。
 * 何を出すかは組み立て（core/board.ts）が決めた値のとおりで、ここで判定し直さない。
 */
import type { JSX } from "react";

import type { Action, Card, PhaseChip } from "../../core/board.js";
import type { Moved } from "../../core/board-moved.js";
import { post } from "./post.js";
import {
  COPY_LABELS,
  MARK_LABELS,
  holdLabel,
  isHighRisk,
  isHttpUrl,
  movedLabel,
  mrText,
  phaseStatusBrief,
  phaseStatusFull,
  riskText,
  worktreeName,
} from "./text.js";

/**
 * `moved` は、前の読み直しからこのカードが動いたこと（`core/board-moved.ts`）。動いていなければ
 * 渡らない。何が動いたかを決めるのは画面（`App.tsx`）で、ここは受け取った分に印を出すだけ。
 */
export function CardItem({ card, hidden, moved }: { readonly card: Card; readonly hidden: boolean; readonly moved?: Moved }): JSX.Element {
  const classes = ["card", card.isParent ? "parent" : "child"];
  if (card.issues.length > 0) {
    classes.push("has-issue");
  }
  if (card.gateClosed) {
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
  // ボタンとリンク（マージリクエスト）の上では提案を開かない
  const open = (target: EventTarget | null): void => {
    if (target instanceof Element && target.closest("button, a") !== null) {
      return;
    }
    if (card.openPath !== "") {
      post({ type: "open", filePath: card.openPath });
    }
  };
  const where = card.isParent ? "親" : `子 · 親 ${card.parent} / フェーズ ${card.phase ?? "?"}`;
  return (
    <li
      className={classes.join(" ")}
      data-id={card.id}
      data-path={card.openPath}
      data-project={card.project}
      data-family={card.family}
      data-attention={card.attention ? "1" : "0"}
      data-moved={moved === undefined ? undefined : `${moved.from ?? "none"}-${moved.to}`}
      tabIndex={0}
      onClick={(event) => open(event.target)}
      onKeyDown={(event) => {
        if (event.key === "Enter" && !(event.target instanceof Element && event.target.closest("button, a") !== null)) {
          event.preventDefault();
          open(event.target);
        }
      }}
    >
      <div className="card-head">
        <span className="num">{card.id}</span>
        <span className="title">{card.title}</span>
        <span className="where">{where}</span>
      </div>
      {moved !== undefined ? (
        <div className="moved-mark" title="前の読み直しから列が変わった。次に何かが動くまで残る">
          {movedLabel(moved)}
        </div>
      ) : null}
      {card.stage !== "" ? <div className="stage">{card.stage}</div> : null}
      <Badges card={card} />
      <Facts card={card} />
      {card.phases.length > 0 ? <Phases phases={card.phases} /> : null}
      {card.issues.length > 0 ? (
        <ul className="issues">
          {card.issues.map((issue, i) => (
            <li key={i}>{issue}</li>
          ))}
        </ul>
      ) : null}
      {card.actions.length > 0 ? (
        <div className="card-actions">
          {card.actions.map((action, i) => (
            <ActionButton key={i} action={action} id={card.id} />
          ))}
        </div>
      ) : null}
    </li>
  );
}

/**
 * 枠付きのバッジは、人が動く必要がある状態だけ。未承認、レビュー準備中／レビュー待ち、
 * 書き込み停止中、ワークツリーなし（閉じたチケットは除く）、実績のリスクが HIGH 以上、
 * 本物が決まらない写り。出すバッジが無ければ行ごと出さない。
 */
function Badges({ card }: { readonly card: Card }): JSX.Element | null {
  const badges: JSX.Element[] = [];
  if (card.copyStatus === "none") {
    badges.push(<Badge key="copy" kind="copy copy-none" text={COPY_LABELS.none} />);
  }
  // 止めている間の 1 枚。依頼の前後で名前が変わるだけで、バッジは増えない。どちらの段かは
  // 判定が JSON の `review_waiting` で言う。ここで marks や reviewed を見て組み直さない。
  if (card.gateClosed) {
    badges.push(<Badge key="hold" kind="hold" text={holdLabel(card)} />);
  }
  // 承認済みチケット自体が信じられない（ADR-0058）。理由の全文は不備の行に出る（`board.cardOf`）ので、
  // ここは一目で分かる短い言葉に留める。
  if (card.blocked !== "") {
    badges.push(<Badge key="blocked" kind="blocked" text="書き込み停止中" title={card.blocked} />);
  }
  if (!card.worktreeExists && card.copyStatus !== "closed") {
    badges.push(<Badge key="worktree" kind="worktree none" text="ワークツリーなし" />);
  }
  if (isHighRisk(card.riskLevel)) {
    badges.push(<Badge key="risk" kind={`risk risk-${card.riskLevel.toLowerCase()}`} text={riskText(card)} />);
  }
  // 写りがあること自体は普通なので数では出さない。どれが本物か決まらないときだけ言う。
  if (card.scattered.length > 0) {
    const where = card.scattered.map((s) => `${s.tree || "main"}:${s.state}`).join(", ");
    badges.push(<Badge key="seen" kind="seen" text={`複数の場所にある（${card.scattered.length} か所）`} title={where} />);
  }
  return badges.length === 0 ? null : <div className="badges">{badges}</div>;
}

/**
 * 枠の無い薄い文字で 1 行に並べる属性。承認済／レビュー待ち／クローズ、人間レビューの要否、ワークツリー、
 * マーカー（終了と依頼済は出さない）、Draft 解除済、締めた、リスク（MEDIUM 以下）、base、プロジェクト。
 *
 * 列やバッジと同じことは重ねて書かない。完了・取り消しの列にいる閉じたカードには、クローズと人間レビューの要否を
 * 出さない（閉じたことは列で分かり、レビューが済むかは省略／レビュー済で分かる）。提案が残っていて未着手・作業中の
 * 列にいる閉じたカードには、列と食い違うことの手がかりとしてクローズを出す。
 * 終了の印（pending）は、止まっている間はバッジの「レビュー準備中」が言い、済んだ後は経過でしかない。
 * 依頼済はレビュー待ちのバッジが言う。どちらもフェーズ行の全文には残る。
 */
function Facts({ card }: { readonly card: Card }): JSX.Element {
  const facts: JSX.Element[] = [];
  const closedInColumn = card.copyStatus === "closed" && (card.column === "done" || card.column === "cancelled");
  if (card.copyStatus !== "none" && !closedInColumn) {
    facts.push(<Fact key="copy" kind={`copy-${card.copyStatus}`} text={COPY_LABELS[card.copyStatus]} />);
  }
  if (!closedInColumn) {
    facts.push(<Fact key="review" kind="review" text={`人間レビュー${card.reviewRequired ? "要" : "不要"}`} title={card.reviewReason} />);
  }
  if (card.worktreeExists) {
    facts.push(<Fact key="worktree" kind="worktree" text={`ワークツリー ${worktreeName(card.worktreePath)}`} title={card.worktreePath} />);
  }
  for (const mark of card.marks) {
    if (mark !== "requested" && mark !== "pending") {
      facts.push(<Fact key={`mark-${mark}`} kind={`mark mark-${mark}`} text={MARK_LABELS[mark] ?? mark} />);
    }
  }
  if (card.mrUrl !== "") {
    facts.push(<MrLink key="mr" url={card.mrUrl} number={card.mrNumber} title="マージリクエストを開く" />);
  }
  if (card.ready) {
    facts.push(<Fact key="ready" kind="ready" text="Draft 解除済" />);
  }
  if (card.wrapped) {
    facts.push(<Fact key="wrapped" kind="wrapped" text="締めた" />);
  }
  if (card.riskLevel !== "" && !isHighRisk(card.riskLevel)) {
    facts.push(<Fact key="risk" kind={`risk risk-${card.riskLevel.toLowerCase()}`} text={riskText(card)} />);
  }
  if (card.baseSha !== "") {
    facts.push(<Fact key="sha" kind="sha" text={`base ${card.baseSha.slice(0, 7)}`} title={card.baseSha} />);
  }
  if (card.project !== "") {
    facts.push(<Fact key="project" kind="project" text={`project ${card.project}`} />);
  }
  return <div className="facts">{facts}</div>;
}

/**
 * 親カードのフェーズ一覧。1 フェーズ 1 行で、左の丸がフェーズ。右の状態は要約（狭い列）と全文（広げたとき）を
 * 両方持ち、どちらを見せるかは CSS が幅で決める。要約は見た目だけのもの（aria-hidden）で、
 * 全文は狭いときも読み上げには渡す。狭いままマウスで読むときのために、全文は行の tooltip にも置く。
 */
function Phases({ phases }: { readonly phases: readonly PhaseChip[] }): JSX.Element {
  return (
    <ul className="phases">
      {phases.map((p) => {
        const full = phaseStatusFull(p);
        return (
          <li key={p.number} className={`phase phase-${p.state}${p.gateClosed ? " review-hold" : ""}`} title={full}>
            <span className="phase-dot" aria-hidden="true" />
            <span className="phase-name">
              <span className="phase-label">{p.label}</span>
              {p.tickets.length > 0 ? <span className="phase-tickets">{p.tickets.join(", ")}</span> : null}
            </span>
            <span className="phase-status">
              <span className="phase-brief" aria-hidden="true">
                {phaseStatusBrief(p)}
              </span>
              <span className="phase-full">{full}</span>
              {/* 依頼の投稿へのリンク。依頼のマーカーがあるときだけ（レビューが済んだ後も経緯として残す） */}
              {p.mrUrl !== "" ? <MrLink url={p.mrUrl} number={p.mrNumber} title={`フェーズ ${p.label} のレビューの依頼を開く`} /> : null}
              {p.actions.map((action, i) => (
                <ActionButton key={i} action={action} id={`${p.parent}:${p.number}`} />
              ))}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function Badge({ kind, text, title = "" }: { readonly kind: string; readonly text: string; readonly title?: string }): JSX.Element {
  return (
    <span className={`badge ${kind}`} title={title === "" ? undefined : title}>
      {text}
    </span>
  );
}

function Fact({ kind, text, title = "" }: { readonly kind: string; readonly text: string; readonly title?: string }): JSX.Element {
  return (
    <span className={`fact ${kind}`} title={title === "" ? undefined : title}>
      {text}
    </span>
  );
}

/**
 * マージリクエストへのリンク。中身は依頼のマーカーが持つ URL で、http(s) 以外は開かせない
 * （`javascript:` などが混じっても文字として出すだけ）。Webview の外部リンクは VS Code が既定のブラウザで開く。
 */
function MrLink({ url, number, title }: { readonly url: string; readonly number: number | null; readonly title: string }): JSX.Element {
  const text = mrText(number);
  if (!isHttpUrl(url)) {
    return <Fact kind="mr" text={text} title={url} />;
  }
  return (
    <a className="fact mr mr-link" href={url} title={title}>
      {text}
    </a>
  );
}

/** 人が押せる操作。押したら拡張ホストへ返すだけで、画面は何も置かない */
function ActionButton({ action, id }: { readonly action: Action; readonly id: string }): JSX.Element {
  switch (action.kind) {
    case "approve":
      // このカードだけを承認の対象にする（`--approve --preview --json <識別子>`）。同じ親の承認待ちが他にあっても巻き込まない。
      return (
        <button
          type="button"
          className="action"
          data-action="approve-one"
          data-ticket={id}
          title={`このチケットだけを承認する（ccnavi --approve ${id}）。まとめて承認するなら上部のボタン`}
          onClick={() => post({ type: "approve", tickets: [id], filtered: true })}
        >
          この 1 件を承認
        </button>
      );
    case "decide":
      return (
        <button
          type="button"
          className="action"
          data-action="decide"
          data-parent={action.parent}
          data-phase={action.phase}
          title={`未解決（Unresolved）の指摘の対応方針を 1 件ずつ決める（対応しない・このフェーズで直す・issue に回す）`}
          onClick={() => post({ type: "decide", parent: action.parent, phase: action.phase })}
        >
          決める
        </button>
      );
    case "reviewed":
      // マーカーは置かない。レビューを終えたことを Claude Code に伝える文を組み、コピー / 新しいセッションで開く で渡す。
      // confirm を打ってマーカーを置くのは、その文を受けたエージェント
      return (
        <button
          type="button"
          className="action"
          data-action="reviewed"
          data-parent={action.parent}
          data-phase={action.phase}
          title={`レビューを終えたことを Claude Code に伝える文を作る（エージェントが ccnavi-review.sh confirm --phase ${action.phase} を打つ）`}
          onClick={() => post({ type: "reviewed", parent: action.parent, phase: action.phase })}
        >
          レビュー済み連絡
        </button>
      );
  }
}
