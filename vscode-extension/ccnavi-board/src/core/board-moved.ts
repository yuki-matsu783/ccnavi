/**
 * 前の読み直しから、どのカードが列を変えたか。VS Code の API にも DOM にも触れない。
 *
 * ボードは承認や `ticket finish` でひとりでに読み直る（監視。`core/watch.ts`）。読み直しは列の
 * 中身を丸ごと差し替えるだけなので、**カードは黙って別の列に現れる**。承認のときはさらに、
 * 渡す文のオーバーレイがボードを覆っていて、動く瞬間そのものを人が見られない。
 *
 * だから「動いた」を覚えて印を出す。ここが決めるのは**どれが動いたか**だけで、
 * どう見せるかは `webview/board/Card.tsx` と `Card.css`。
 *
 * **覚えるのは拡張ホスト**（`board-panel.ts`）で、画面ではない。承認のオーバーレイを画面の中に
 * 持たないのと同じ理由（`board-view.ts` の `ApprovalOverlay`）で、画面は裏に回ると捨てられ、
 * 表に戻ると入れてある HTML から作り直される（`retainContextWhenHidden` は偽）。画面に持たせると、
 * **承認の文を「新しいセッションで開く」で渡してボードに戻った瞬間に印が消える** — 一番見せたい
 * 場面で消えることになる。
 *
 * **時間では消さない。** 承認のオーバーレイを閉じたときにはもう消えている、を避ける。
 * 消えるのは、次に列の並びが変わったとき（`movedCards` が別の答えを出したとき）。
 */
import type { Board } from "./board.js";
import type { ProposalState } from "./model.js";

/** どのカードがどの列に居たか。識別子 → 列 */
export type Placement = Readonly<Record<string, ProposalState>>;

/** 動いた 1 枚。`from` が無いのは、前には無くて新しく現れたカード */
export interface Moved {
  readonly id: string;
  readonly from?: ProposalState;
  readonly to: ProposalState;
}

/** いまのボードの置き場所。列は組み立て（`board.ts`）が決めたものをそのまま読む */
export function placementOf(board: Board): Placement {
  const placement: Record<string, ProposalState> = {};
  for (const column of board.columns) {
    for (const card of column.cards) {
      placement[card.id] = column.state;
    }
  }
  return placement;
}

/**
 * 置き場所が前とそっくり同じか。
 *
 * **同じなら印を作り直さない。** ボードは列が動いていなくても渡り直す（承認のオーバーレイの
 * 出し入れ、「更新」で何も変わらなかったとき）。そこで作り直すと、**オーバーレイを閉じた瞬間に
 * 印が消える**（閉じると `redraw` が同じボードを渡し直す）。
 */
export function samePlacement(before: Placement, after: Placement): boolean {
  const ids = Object.keys(before);
  return ids.length === Object.keys(after).length && ids.every((id) => before[id] === after[id]);
}

/**
 * 列が変わったカードと、新しく現れたカード。消えたカードは出さない（印を付ける先が無い）。
 * 並びは `after` の並び順で、`placementOf` が作ったものなら列の順（未着手 → 作業中 → …）。
 */
export function movedCards(before: Placement, after: Placement): readonly Moved[] {
  const moved: Moved[] = [];
  for (const [id, to] of Object.entries(after)) {
    const from = before[id];
    if (from === undefined) {
      moved.push({ id, to });
    } else if (from !== to) {
      moved.push({ id, from, to });
    }
  }
  return moved;
}

/**
 * 拡張ホストが持ち直す分。`approval-machine.ts` と同じ「いまの状態 ＋ 入力 → 次の状態」の形で、
 * VS Code に触れないので単体で試せる。
 */
export interface MovedState {
  /** 最後に**読めた**ボードの置き場所。まだ 1 枚も読めていなければ無い */
  readonly placement?: Placement;
  /** いま印を出すもの */
  readonly moved: readonly Moved[];
}

/** まだ 1 枚も読めていない */
export const NOTHING_MOVED: MovedState = { moved: [] };

/**
 * 読めたボードを 1 枚入れて、次の状態を返す。**読めなかったとき（エラーの画面）は呼ばない。**
 * 呼ばなければ最後に読めたボードが残り、次に読めたものとそれを比べる。
 *
 * - 1 枚目は比べる相手が無いので、何にも印を付けない（開いた直後に全部が光ると意味が無い）
 * - 列が動いていなければ**前の印をそのまま持ち越す**。ボードは列が同じままでも渡り直る
 *   （承認のオーバーレイの出し入れ、「更新」で何も変わらなかったとき）。そこで作り直すと、
 *   承認の文を閉じた瞬間に印が消える。持ち越すときは同じ状態をそのまま返す
 */
export function movedStep(state: MovedState, board: Board): MovedState {
  const next = placementOf(board);
  const before = state.placement;
  if (before === undefined) {
    return { placement: next, moved: [] };
  }
  if (samePlacement(before, next)) {
    return state;
  }
  return { placement: next, moved: movedCards(before, next) };
}
