import { useEffect, useRef } from "react";

/** Ctrl+S（macOSはCmd+S）か。Shift・Altが付いたもの（別名で保存など）は数えない */
export function isSaveKey(event: KeyboardEvent): boolean {
  return (event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey && event.key.toLowerCase() === "s" && !event.isComposing;
}

/** Ctrl+Sを押した時点の保存の様子 */
export interface SaveKeyTarget {
  /** 押せない理由（`core/save-gate.ts`の答え）。空なら押せる */
  readonly blocker: string;
  readonly save: () => void;
  /** 押せないときに理由を見せる */
  readonly blocked: (reason: string) => void;
}

/**
 * 設定を直す画面のCtrl+S。押せない理由が空のときだけ保存し、空でなければ理由を表示する（ボタンと同じ答えを使い、条件を書き直さない）。
 * `target`は押した時点で呼ぶ（描画のたびに差し替わってよい。早く返す分岐の後ろで決まる値は、refを通して渡せる）。
 * 欄に入力中でも効く。`active`が偽の間（案内・保存前の確認などの面が出ている間）は効かない。
 * 押したキーのブラウザの既定の動作は止める。
 */
export function useSaveKey(active: boolean, target: () => SaveKeyTarget): void {
  const latest = useRef(target);
  latest.current = target;
  useEffect(() => {
    if (!active) {
      return undefined;
    }
    const onKey = (event: KeyboardEvent): void => {
      if (!isSaveKey(event)) {
        return;
      }
      event.preventDefault();
      const now = latest.current();
      if (now.blocker === "") {
        now.save();
      } else {
        now.blocked(now.blocker);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active]);
}
