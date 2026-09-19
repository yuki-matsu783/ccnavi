/**
 * 画面（Webview）に中身を渡すときの段取り。中身を `postMessage` で渡す画面（いまはボード）が使う。
 *
 * 渡し方は 3 通りあり、どれになるかは画面の生死で決まる。
 *
 * | 画面の状態 | 渡し方 |
 * |---|---|
 * | 組み上がっている（`live`） | `postMessage` で中身だけ渡す。画面は要るところだけ描き直す |
 * | 作り直している最中（`reloading`） | **渡さない。** 受け口（`message` のリスナ）はまだ無く、送っても落ちる |
 * | 裏に回っている / まだ 1 枚も入れていない | 入れ物ごと入れ直す。表に戻ると VS Code はこれから作り直す |
 *
 * `reloading` の間に渡したものは捨てられる。画面が組み上がると `ready` が届くので、**受けた側が
 * そこで渡し直す**（board-panel の `ready` → `redraw`）。1 度きりの指示（「このプロジェクトで
 * 絞って開く」）を渡すときは、`post` が真を返すまで消さないこと。渡す前に消すと、作り直しの窓に
 * 落ちて二度と届かない。
 *
 * VS Code の API には触れない。必要な口（`Surface`）だけを受け取るので、単体で試せる。
 */

/** 拡張ホストが持つ Webview の口。パネルをこの形に合わせて渡す */
export interface Surface {
  /**
   * 表に出ているか。`retainContextWhenHidden` が偽の画面は、裏に回ると捨てられる。
   * 捨てられた画面に送っても誰にも届かない
   */
  readonly visible: boolean;
  /** 入れ物ごと入れ直す（画面は作り直される） */
  html(text: string): void;
  post(message: unknown): void;
}

export interface ScreenHost<D> {
  /** いま `postMessage` が届く（1 枚入っていて、表に出ていて、組み上がっている） */
  readonly live: boolean;
  /** 表に出ているが、まだ組み上がっていない。ここで渡したものは落ちる */
  readonly reloading: boolean;
  /** いま見せるものを渡す。上の表のどれかになる */
  send(data: D): void;
  /** 生きている画面にだけ届くメッセージ。届いたら真、落ちるので送らなかったら偽 */
  post(message: unknown): boolean;
  /** 画面から `ready` が届いた。呼んだ側は、続けて中身を渡し直す */
  ready(): void;
  /** 裏に回った。画面は捨てられているので、次は入れ物ごと入れ直す */
  hidden(): void;
}

/** 中身を包む形。画面はこの `type` で受ける */
export interface DataMessage<D> {
  readonly type: "data";
  readonly data: D;
}

/**
 * `render` は入れ物の HTML を組む。呼ぶたびに nonce を変えること。同じ文字列を `webview.html` に
 * 入れても VS Code は何もしないので、作り直したいときに作り直せなくなる。
 */
export function screenHost<D>(surface: Surface, render: (data: D) => string): ScreenHost<D> {
  let htmlSet = false;
  let mounted = false;
  const host: ScreenHost<D> = {
    get live(): boolean {
      return htmlSet && surface.visible && mounted;
    },
    get reloading(): boolean {
      return htmlSet && surface.visible && !mounted;
    },
    send(data: D): void {
      if (host.live) {
        const message: DataMessage<D> = { type: "data", data };
        surface.post(message);
        return;
      }
      if (host.reloading) {
        return;
      }
      surface.html(render(data));
      htmlSet = true;
      mounted = false;
    },
    post(message: unknown): boolean {
      if (!host.live) {
        return false;
      }
      surface.post(message);
      return true;
    },
    ready(): void {
      mounted = true;
    },
    hidden(): void {
      mounted = false;
    },
  };
  return host;
}
