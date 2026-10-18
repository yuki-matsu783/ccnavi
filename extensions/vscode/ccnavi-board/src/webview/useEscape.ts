import { useEffect, useRef } from "react";

/**
 * 重なって出るものの順。CSS の `z-index` と同じ順に並べる（大きいほど手前）。
 * Esc は、いま出ているもののうち最も手前の 1 つだけが受ける。
 */
export const ESCAPE_LAYER = {
  /** 選択肢の一覧（`rules/Rule.css`） */
  popup: 5,
  /** 承認・保存前の確認・答えの確認など、画面全体を覆う面（`board/Approval.css` ほか） */
  overlay: 10,
  /** 「?」の案内の一覧（`Tour.css`） */
  tourMenu: 50,
  /** 図の全画面（`shared/Maximize.css`） */
  maximize: 100,
  /** 案内の吹き出し（`Tour.css`） */
  tour: 1000,
} as const;

interface Entry {
  readonly layer: number;
  readonly order: number;
  readonly handler: () => void;
}

const entries = new Set<Entry>();
let counter = 0;

function onKey(event: KeyboardEvent): void {
  if (event.key !== "Escape") {
    return;
  }
  let top: Entry | undefined;
  for (const entry of entries) {
    // 同じ順なら、あとから出たほうが手前
    if (top === undefined || entry.layer > top.layer || (entry.layer === top.layer && entry.order > top.order)) {
      top = entry;
    }
  }
  if (top === undefined) {
    return;
  }
  // 背後の Esc（ほかの面・画面の keydown）に渡さない
  event.stopPropagation();
  event.preventDefault();
  top.handler();
}

/**
 * Esc で閉じる面の共通の入口。`active` の間だけ受け、重なっているときは最も手前の面だけが受ける。
 * handler は毎回の描画で差し替わってよい。
 */
export function useEscape(active: boolean, layer: number, handler: () => void): void {
  const latest = useRef(handler);
  latest.current = handler;
  useEffect(() => {
    if (!active) {
      return undefined;
    }
    const entry: Entry = { layer, order: counter++, handler: () => latest.current() };
    if (entries.size === 0) {
      window.addEventListener("keydown", onKey, true);
    }
    entries.add(entry);
    return () => {
      entries.delete(entry);
      if (entries.size === 0) {
        window.removeEventListener("keydown", onKey, true);
      }
    };
  }, [active, layer]);
}
