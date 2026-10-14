/**
 * 前の読み直しから、どのカードが列を変えたか。VS Code の API にも DOM にも触れない。
 *
 * ボードは承認や `ticket finish` でひとりでに読み直る（監視。`core/watch.ts`）。読み直しは列の
 * 中身をすべて差し替えるだけなので、カードは気づかないうちに別の列に現れる。承認のときはさらに、
 * 渡す文のオーバーレイがボードを覆っていて、動くところそのものをユーザが見られない。
 *
 * だから「動いた」を覚えて動いた表示を出す。ここが決めるのはどれが動いたかだけで、
 * どう見せるかは `webview/board/Card.tsx` と `Card.css`。
 *
 * 覚えるのは拡張ホスト（`board-panel.ts`）で、画面ではない。承認のオーバーレイを画面の中に
 * 持たないのと同じ理由（`board-view.ts` の `ApprovalOverlay`）で、画面は裏に回ると捨てられ、
 * 表に戻ると入れてある HTML から作り直される（`retainContextWhenHidden` は偽）。画面に持たせると、
 * 承認の文を「新しいセッションで開く」で渡してボードに戻った時点で動いた表示が消える。一番見せたい
 * 場面で消えることになる。
 *
 * 時間では消さない。 承認のオーバーレイを閉じたときにはもう消えている、を避ける。
 * 消えるのは、次に列の順序が変わったとき（`movedCards` が別の答えを出したとき）。
 */
import type { Board } from "./board.js";
import type { ProposalState } from "./model.js";

/** どのカードがどの列に居たか。識別子 → 列 */
export type Placement = Readonly<Record<string, ProposalState>>;

/**
 * 動いた 1 枚。`from` が無いのは、前には無くて新しく現れたカード。`by` が "sync" なのは、
 * 前の読み直しからこのカードの親の最終同期（`remote.fetched_at`）が進んでいるもの。
 * `ccnavi-sync.sh` が取り込んだ承認や完了でカードが動いたことを、画面が「同期で取り込み」と言う。
 */
export interface Moved {
  readonly id: string;
  readonly from?: ProposalState;
  readonly to: ProposalState;
  readonly by?: "sync";
}

/** 親ごとの最終同期（UNIX 秒）。親の識別子 → 時刻。0 は一度も取り込んでいない */
export type Synced = Readonly<Record<string, number>>;

/** いまのボードの親の最終同期。親のワークツリーが無い親（`remote` が無い）は入れない */
export function syncedOf(board: Board): Synced {
  const synced: Record<string, number> = {};
  for (const column of board.columns) {
    for (const card of column.cards) {
      if (card.isParent && card.remote !== null && column.state !== "archived") {
        synced[card.id] = card.remote.fetched_at;
      }
    }
  }
  return synced;
}

/** 前の読み直しから最終同期が進んだ親。前に居なかった親（新しく現れたワークツリー）は数えない */
export function syncedFamilies(before: Synced, after: Synced): ReadonlySet<string> {
  const advanced = new Set<string>();
  for (const [id, at] of Object.entries(after)) {
    const was = before[id];
    if (was !== undefined && at > was) {
      advanced.add(id);
    }
  }
  return advanced;
}

/** 動いたカードに、親の最終同期が進んだものへ `by: "sync"` を付ける。`familyOf` はカードの識別子 → 親の識別子 */
export function markSynced(moved: readonly Moved[], advanced: ReadonlySet<string>, familyOf: ReadonlyMap<string, string>): readonly Moved[] {
  if (advanced.size === 0) {
    return moved;
  }
  return moved.map((m) => (advanced.has(familyOf.get(m.id) ?? "") ? { ...m, by: "sync" as const } : m));
}

/** いまのボードの置き場所。列は組み立て（`board.ts`）が決めたものをそのまま読む */
export function placementOf(board: Board): Placement {
  const placement: Record<string, ProposalState> = {};
  for (const column of board.columns) {
    // アーカイブ（手元の退避）は置き場ではない。完了からアーカイブへ移ったカードを「動いた」と数えない
    if (column.state === "archived") {
      continue;
    }
    for (const card of column.cards) {
      placement[card.id] = column.state;
    }
  }
  return placement;
}

/**
 * 置き場所が前とそっくり同じか。
 *
 * 同じなら動いた表示を作り直さない。 ボードは列が動いていなくても画面へ渡し直される（承認のオーバーレイの
 * 出し入れ、「更新」で何も変わらなかったとき）。そこで作り直すと、オーバーレイを閉じた時点で
 * 動いた表示が消える（閉じると `redraw` が同じボードを渡し直す）。
 */
export function samePlacement(before: Placement, after: Placement): boolean {
  const ids = Object.keys(before);
  return ids.length === Object.keys(after).length && ids.every((id) => before[id] === after[id]);
}

/**
 * 列が変わったカードと、新しく現れたカード。消えたカードは出さない（動いた表示を付ける先が無い）。
 * 順序は `after` の並び順で、`placementOf` が作ったものなら列の順（未着手 → 作業中 → …）。
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
  /** 最後に読めたボードの置き場所。まだ 1 枚も読めていなければ無い */
  readonly placement?: Placement;
  /** 最後に読めたボードの親ごとの最終同期。動いた表示に「同期で取り込み」を付けるかを決める */
  readonly synced?: Synced;
  /** いま動いた表示を出すもの */
  readonly moved: readonly Moved[];
}

/** まだ 1 枚も読めていない */
export const NOTHING_MOVED: MovedState = { moved: [] };

/**
 * 読めたボードを 1 枚入れて、次の状態を返す。読めなかったとき（エラーの画面）は呼ばない。
 * 呼ばなければ最後に読めたボードが残り、次に読めたものとそれを比べる。
 *
 * - 1 枚目は比べる相手が無いので、何にも動いた表示を付けない（開いた直後に全部が光ると意味が無い）
 * - 列が動いていなければ前の動いた表示をそのまま持ち越す。ボードは列が同じままでも画面へ渡し直される
 *   （承認のオーバーレイの出し入れ、「更新」で何も変わらなかったとき）。そこで作り直すと、
 *   承認の文を閉じた時点で動いた表示が消える。持ち越すときは同じ状態をそのまま返す
 */
export function movedStep(state: MovedState, board: Board): MovedState {
  const next = placementOf(board);
  const nextSynced = syncedOf(board);
  const before = state.placement;
  if (before === undefined) {
    return { placement: next, synced: nextSynced, moved: [] };
  }
  if (samePlacement(before, next)) {
    // 最終同期だけが進んでも、動いた表示は変えない（動いていないので付ける先が無い）。次の比べのために時刻は覚える
    return sameSynced(state.synced ?? {}, nextSynced) ? state : { ...state, synced: nextSynced };
  }
  const familyOf = new Map<string, string>();
  for (const column of board.columns) {
    for (const card of column.cards) {
      familyOf.set(card.id, card.family);
    }
  }
  const advanced = syncedFamilies(state.synced ?? {}, nextSynced);
  return { placement: next, synced: nextSynced, moved: markSynced(movedCards(before, next), advanced, familyOf) };
}

function sameSynced(a: Synced, b: Synced): boolean {
  const ids = Object.keys(a);
  return ids.length === Object.keys(b).length && ids.every((id) => a[id] === b[id]);
}
