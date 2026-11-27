/**
 * 行を削除した直後に出す帯。［元に戻す］で直前に削除した1行を元の位置へ戻す。ルール管理・リスク管理・フェーズ管理が同じものを置く。
 * 削除は確認なしですぐ効くので、取り消しをここで受ける。覚えるのは直前の1行だけ（次に削除すると入れ替わる）。
 * 中身が入れ替わったとき（更新・保存・対象の切り替え）は画面の側が消す。戻し先の下書きが無くなるため。
 */
import type { JSX } from "react";

/** 直前に削除した行。`restore`はそのときの下書きを受けて、行を戻した下書きを返す */
export interface Removed<D> {
  readonly label: string;
  readonly restore: (draft: D) => D;
}

/** 消した位置へ戻す。その後の削除で行が減っていれば末尾へ */
export function insertAt<T>(rows: readonly T[], index: number, row: T): T[] {
  const at = Math.min(Math.max(index, 0), rows.length);
  return [...rows.slice(0, at), row, ...rows.slice(at)];
}

/** 削除した位置と行を覚えて、戻す関数を作る。行が無ければundefined */
export function removal<T extends { readonly key: string }>(rows: readonly T[], key: string): { readonly index: number; readonly row: T } | undefined {
  const index = rows.findIndex((row) => row.key === key);
  const row = rows[index];
  return row === undefined ? undefined : { index, row };
}

export function RemovedBand<D>(props: { readonly removed: Removed<D> | undefined; readonly onUndo: () => void; readonly onDismiss: () => void }): JSX.Element | null {
  const { removed, onUndo, onDismiss } = props;
  if (removed === undefined) {
    return null;
  }
  return (
    <div className="banner" data-banner="removed" role="status">
      <span>{removed.label}を削除しました（まだ保存していません）。</span>
      <button type="button" className="action" data-action="undo-remove" onClick={onUndo}>
        元に戻す
      </button>
      <button type="button" className="action" data-action="dismiss-removed" title="この案内を閉じます" onClick={onDismiss}>
        閉じる
      </button>
    </div>
  );
}
