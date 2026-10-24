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

/** 上端のバナー（案内のための見本です）の下に置くための、吹き出しの上端 */
const WIDE_TOP = 52;
/** 画面の縁から離す量（幅のある吹き出し） */
const WIDE_SIDE = 16;

/**
 * 図や画像のある、幅のある吹き出しの置き場所。指す先の位置で上下に飛ばず、指す先と重ならない側（指す先が左半分なら右端、
 * 右半分なら左端）の、バナーの下に置く。手順が進んでも、同じ側に留まりやすい。
 */
export function placeWide(target: Rect | undefined, bubble: { readonly width: number }, view: { readonly width: number; readonly height: number }): { top: number; left: number } {
  const centerX = target === undefined ? view.width / 2 : target.left + target.width / 2;
  const right = Math.max(WIDE_SIDE, view.width - bubble.width - WIDE_SIDE);
  const left = centerX < view.width / 2 ? right : WIDE_SIDE;
  return { top: Math.min(WIDE_TOP, Math.max(0, view.height - 120)), left };
}

/** ヘッダをつかんで動かした吹き出しの左上を、画面の中に収める（ヘッダを必ずつかみ直せるよう、一部は画面に残す） */
export function clampDragged(at: { readonly top: number; readonly left: number }, bubbleWidth: number, view: { readonly width: number; readonly height: number }): { top: number; left: number } {
  const KEEP = 80;
  const top = Math.min(Math.max(at.top, 0), Math.max(0, view.height - 48));
  const left = Math.min(Math.max(at.left, KEEP - bubbleWidth), Math.max(0, view.width - KEEP));
  return { top, left };
}
