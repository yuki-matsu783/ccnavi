/**
 * 前の読み直しから、どのカードが列を変えたか。VS Code の API にも DOM にも触れない。
 *
 * ボードは承認や `ticket done` でひとりでに読み直る（監視。`core/watch.ts`）。読み直しは列の
 * 中身を丸ごと差し替えるだけなので、**カードは黙って別の列に現れる**。承認のときはさらに、
 * 渡す文のオーバーレイがボードを覆っていて、動く瞬間そのものを人が見られない。
 *
 * だから「動いた」を画面が覚えて印を出す。ここが決めるのは**どれが動いたか**だけで、
 * どう見せるかは `webview/board/Card.tsx` と `Card.css`。
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
