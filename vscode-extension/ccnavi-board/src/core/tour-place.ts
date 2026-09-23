/**
 * 吹き出しの案内（`webview/phases/Tour.tsx`）の置き場所。DOM に触れないので単体で試せる。
 */

/** 画面の上の枠（`getBoundingClientRect` の形） */
export interface Rect {
  readonly top: number;
  readonly left: number;
  readonly width: number;
  readonly height: number;
}

/** 指す先と吹き出しの間 */
const GAP = 10;
/** 画面の縁から離す量 */
const EDGE = 8;

/**
 * 吹き出しの置き場所。**吹き出しの実寸で決め、画面の外には出さない。**
 * 指す先の下に収まれば下、上に収まれば上。どちらにも収まらなければ（指す先が画面より大きい）、
 * 画面の下端に寄せて指す先に重ねる。前は高さを決め打ちしていて、長い一覧を指すと吹き出しが
 * 画面の外に出て「次へ」が押せなくなった。
 */
export function placeBubble(spot: Rect, bubble: { readonly width: number; readonly height: number }, view: { readonly width: number; readonly height: number }): { top: number; left: number } {
  const below = spot.top + spot.height + GAP;
  const above = spot.top - GAP - bubble.height;
  let top: number;
  if (below + bubble.height <= view.height - EDGE) {
    top = below;
  } else if (above >= EDGE) {
    top = above;
  } else {
    top = view.height - EDGE - bubble.height;
  }
  top = Math.max(EDGE, top);
  const left = Math.min(Math.max(EDGE, spot.left), Math.max(EDGE, view.width - bubble.width - EDGE));
  return { top, left };
}

