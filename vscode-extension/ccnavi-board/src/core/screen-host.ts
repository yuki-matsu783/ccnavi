/**
 * 画面（Webview）に中身を渡すときの段取り。中身を `postMessage` で渡す画面（いまはボード）が使う。
 *
 * **`retainContextWhenHidden` が偽の画面だけを見ている。** 裏に回ると画面は捨てられ、表に戻ると
 * 入れてある HTML から作り直される、という前提で組んである。保持する画面（ルール設定・リスク管理・
 * フェーズ管理）にそのまま当てると、裏にいる間の入れ直しで編集中の内容が消える。
 *
 * 渡し方は 3 通りあり、どれになるかは画面の生死で決まる。`send` はどれになったかを返す。
 *
 * | 画面の状態 | 渡し方（`Delivery`） |
 * |---|---|
 * | 組み上がっている | `posted`。`postMessage` で中身だけ渡し、画面は要るところだけ描き直す |
 * | 作り直している最中 | `deferred`。**渡さない。** 受け口（`message` のリスナ）はまだ無く、送っても落ちる |
 * | 裏に回っている / まだ 1 枚も入れていない | `rebuilt`。入れ物ごと入れ直す。表に戻ると VS Code はこれから作り直す |
 *
 * `deferred` になったものは捨てられる。画面が組み上がると `ready` が届くので、**受けた側がそこで
 * 渡し直す**（board-panel の `ready` → `redraw`）。1 度きりの指示（「このプロジェクトで絞って開く」）は、
 * `rebuilt` なら入れ物に埋めて渡り、`posted` なら `post` が真を返したときだけ渡っている。
 * **渡る前に消さないこと。** 消してから落ちると二度と届かない。
 *
 * 画面の生死は 2 つで見る。**裏に回ったことは `hidden()` で教えてもらい**、加えて呼ばれるたびに
 * `surface.visible` も読む。片方だけでは足りない。
 *
 * - 自分で読むだけだと、裏へ回って表へ戻るまでの間に 1 度も呼ばれなければ、行って戻ったことに
 *   気づけない（実機はその形。`onDidChangeViewState` のあいだ、この段取りは呼ばれない）
 * - 教えてもらうだけだと、教え忘れがそのまま「送ってはいけないものを送る」になる
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

/** 渡し方。`send` が返す */
export type Delivery = "posted" | "deferred" | "rebuilt";

export interface ScreenHost<D> {
  /** いま `postMessage` が届く（1 枚入っていて、表に出ていて、組み上がっている） */
  readonly live: boolean;
  /**
   * いま見せるものを渡す。
   *
   * `rebuilt` は入れ物ごと入れ直す道で、そこでだけ渡せるもの（1 度きりの指示を埋めた中身）が
   * あれば `rebuilt` に渡す。省けば `data` をそのまま使う
   */
  send(data: D, rebuilt?: D): Delivery;
  /** 生きている画面にだけ届くメッセージ。届いたら真、落ちるので送らなかったら偽 */
  post(message: unknown): boolean;
  /** 画面から `ready` が届いた。呼んだ側は、続けて中身を渡し直す */
  ready(): void;
  /**
   * 裏に回った（`onDidChangeViewState`）。画面は捨てられているので、組み上がっていない側に倒す。
   * 行って戻るまでこの段取りが 1 度も呼ばれないと自分では気づけないので、ここで教えてもらう
   */
  hidden(): void;
}

/** 中身を包む形。画面はこの `type` で受ける */
export interface DataMessage<D> {
  readonly type: "data";
  readonly data: D;
}

/**
 * 最初の中身を HTML の `<script type="application/json">` に埋める形にする。
 * `</script>` や `<!--` が中身に現れても HTML を閉じないよう、`<` を `\u003c` にする
 * （JSON としては同じ文字列で、JSON.parse が元に戻す）。
 *
 * 実体参照にはしない。`<script type="application/json">` の中身は実体参照を解かないので、
 * `&amp;` と書くと画面には `&amp;` のまま届く。画面ごとの契約はこれを自分の型で包んで出す。
 */
export function embedJson(value: unknown): string {
  return JSON.stringify(value).replace(/</g, "\\u003c");
}

/**
 * `render` は入れ物の HTML を組む。呼ぶたびに nonce を変えること。同じ文字列を `webview.html` に
 * 入れても VS Code は何もしないので、作り直したいときに作り直せなくなる。
 */
export function screenHost<D>(surface: Surface, render: (data: D) => string): ScreenHost<D> {
  let htmlSet = false;
  let mounted = false;
  let seen = surface.visible;

  /**
   * 呼ばれるたびに表裏を見て、前と違えば組み上がっていない側に倒す。どちらの向きでも、
   * そのとき生きていた画面は捨てられている（裏へ = 捨てられる、表へ = 入れてある HTML から
   * 作り直される）。`hidden()` の取りこぼしを拾うためのもので、これだけには頼れない
   * （行って戻るまで呼ばれなければ、違いが見えない）。
   */
  const sync = (): void => {
    const visible = surface.visible;
    if (visible !== seen) {
      seen = visible;
      mounted = false;
    }
  };

  return {
    get live(): boolean {
      sync();
      return htmlSet && seen && mounted;
    },
    send(data: D, rebuilt?: D): Delivery {
      sync();
      if (htmlSet && seen) {
        if (mounted) {
          const message: DataMessage<D> = { type: "data", data };
          surface.post(message);
          return "posted";
        }
        // 作り直している最中。いま読み込んでいるものを捨てないよう、入れ直しもしない
        return "deferred";
      }
      surface.html(render(rebuilt ?? data));
      htmlSet = true;
      mounted = false;
      return "rebuilt";
    },
    post(message: unknown): boolean {
      sync();
      if (!(htmlSet && seen && mounted)) {
        return false;
      }
      surface.post(message);
      return true;
    },
    ready(): void {
      sync();
      // 裏にいる画面は `ready` を送らない。届いたなら、それは捨てられた画面が残していったもの。
      // これを真に受けると、作り直し中の画面へ送って落とすことになる。
      // （`live` と `post` も表に出ていることを見るので、ここは二重の守り。単体では外から観測できない）
      if (!seen) {
        return;
      }
      mounted = true;
    },
    hidden(): void {
      seen = surface.visible;
      mounted = false;
    },
  };
}
