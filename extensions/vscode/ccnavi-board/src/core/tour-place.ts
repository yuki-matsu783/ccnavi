/**
 * 吹き出しの案内（`webview/Tour.tsx`）の置き場所。DOM に触れないので単体で試せる。
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
 * 吹き出しの置き場所。吹き出しの実寸で決め、画面の外には出さない。
 * 指す先の下に収まれば下、上に収まれば上。どちらにも収まらなければ（指す先が画面より大きい）、
 * 画面の下端にそろえて指す先に重ねる。前は高さを決め打ちしていて、長い一覧を指すと吹き出しが
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


/**
 * 説明文を、文ごとの行に分ける。「。」のあとで折り、「」・（）・［］の中の「。」では折らない。
 * 文の中の改行（`\n`）も行の区切りにする。空の行は作らない。
 */
export function sentenceLines(body: string): string[] {
  const lines: string[] = [];
  let depth = 0;
  let current = "";
  const flush = (): void => {
    if (current.trim() !== "") {
      lines.push(current.trim());
    }
    current = "";
  };
  for (const char of body) {
    if (char === "\n") {
      flush();
      continue;
    }
    current += char;
    if ("「（［".includes(char)) {
      depth += 1;
    } else if ("」）］".includes(char)) {
      depth = Math.max(0, depth - 1);
    } else if (char === "。" && depth === 0) {
      flush();
    }
  }
  flush();
  return lines;
}

/** ヘッダをつかんで動かした吹き出しの左上を、画面の中に収める（ヘッダを必ずつかみ直せるよう、一部は画面に残す） */
export function clampDragged(at: { readonly top: number; readonly left: number }, bubbleWidth: number, view: { readonly width: number; readonly height: number }): { top: number; left: number } {
  const KEEP = 80;
  const top = Math.min(Math.max(at.top, 0), Math.max(0, view.height - 48));
  const left = Math.min(Math.max(at.left, KEEP - bubbleWidth), Math.max(0, view.width - KEEP));
  return { top, left };
}
