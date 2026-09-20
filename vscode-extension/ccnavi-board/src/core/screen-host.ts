/**
 * 画面（Webview）に中身を渡すときの段取り。中身を `postMessage` で渡す画面が使う。
 *
 * **段取りは 2 系統あり、パネルの `retainContextWhenHidden` で決まる。** どちらを使うかは
 * パネルが 1 行で選ぶ（`screenHost` か `retainedHost`）。返る形（`ScreenHost`）は同じなので、
 * 呼ぶ側の書き方は変わらない。
 *
 * | パネル | 使うもの | 裏に回ったとき |
 * |---|---|---|
 * | `retainContextWhenHidden: false`（ボード・プロジェクト管理） | `screenHost` | 画面は捨てられる。入れ物ごと入れ直す |
 * | `retainContextWhenHidden: true`（ルール設定・リスク管理・フェーズ管理） | `retainedHost` | 画面は生きている。何もしない |
 *
 * 取り違えると**どちらの向きでも壊れる**。保持する画面に `screenHost` を当てると、裏にいる間の
 * 入れ直しで人が打ちかけていた内容が消える。保持しない画面に `retainedHost` を当てると、
 * 捨てられた画面へ送り続けて中身が古いまま止まる。
 *
 * 以下は `screenHost`（保持しない画面）の話。`retainedHost` はこのファイルの下のほうにある。
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
   * 捨てられた画面に送っても誰にも届かない。
   *
   * **`retainedHost` は読まない。** 保持する画面は裏でも生きていて、`postMessage` も届くため
   */
  readonly visible: boolean;
  /** 入れ物ごと入れ直す（画面は作り直される） */
  html(text: string): void;
  post(message: unknown): void;
}

/**
 * 保持する画面（`retainedHost`）が使う口。**表裏（`visible`）は要らない。** 裏でも生きていて、
 * 読まないものを実装させると、写して作った次の画面に死んだゲッターが付いて回る
 */
export type RetainedSurface = Omit<Surface, "visible">;

/** 渡し方。`send` が返す */
export type Delivery = "posted" | "deferred" | "rebuilt";

export interface ScreenHost<D> {
  /**
   * いま `postMessage` が届く。`screenHost` は「1 枚入っていて、表に出ていて、組み上がっている」、
   * `retainedHost` は「1 枚入っていて、組み上がっている」（表裏は見ない）
   */
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
   * 裏に回った（`onDidChangeViewState`）。`screenHost` は画面が捨てられているので、組み上がって
   * いない側に倒す（行って戻るまでこの段取りが 1 度も呼ばれないと自分では気づけないので、ここで
   * 教えてもらう）。`retainedHost` は画面が生きているので何もしない
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

/**
 * 保持する画面（`retainContextWhenHidden: true`）に中身を渡す段取り。ルール設定・リスク管理・
 * フェーズ管理が使う。返る形は `screenHost` と同じなので、パネルは作るところの 1 行だけが違う。
 *
 * 保持する画面は裏に回っても捨てられない。VS Code は DOM も Webview の中の状態もそのまま持ち、
 * 表に戻しても作り直さない。だから、この段取りが `screenHost` と違うのは次の 3 つ。
 *
 * - **入れ物（HTML）は 1 度しか入れない。** 入れ直すと画面は作り直され、人が打ちかけていた
 *   内容が消える。2 枚目からは必ず `postMessage`（`posted`）で渡す
 * - **表裏を見ない。** 裏でも `postMessage` は届く（`postMessage` の文書が「live な画面には届く。
 *   保持する画面は裏でも live」と言う）。見て倒すと、裏にいる間の `lock` や `changed` の知らせが落ちる。
 *   **ただし VS Code の文書は同じ型定義の中で食い違っている**（`retainContextWhenHidden` の側は
 *   「裏に回った画面にはメッセージを送れない」と言う）。どちらが正しくても壊れないよう、呼ぶ側は
 *   表に戻ったときに、いま出すべき知らせ（`lock`・`changed`）を送り直す（risk-panel / phases-panel）
 * - **`hidden()` は何もしない。** 教えてもらっても、捨てられていないので倒すものが無い
 *
 * 残る `deferred` は 1 枚目だけ。入れ物を入れてから画面が組み上がる（`ready`）までの間は、
 * 受け口がまだ無いので送らない。そこは `screenHost` と同じで、受けた側が `ready` で渡し直す。
 *
 * **中身を渡すのは、画面の編集を捨ててよいときだけ。** 保持する画面は編集の途中を持つので、
 * 監視がファイルの変化に気づいても勝手に渡さない（`{type:"changed"}` の帯を出して人に決めさせる）。
 * 渡すのは、人が「再読込」を押したときと、保存・作成が通って中身が入れ替わったとき。
 */
export function retainedHost<D>(surface: RetainedSurface, render: (data: D) => string): ScreenHost<D> {
  let htmlSet = false;
  let mounted = false;

  return {
    get live(): boolean {
      return htmlSet && mounted;
    },
    send(data: D, rebuilt?: D): Delivery {
      if (!htmlSet) {
        surface.html(render(rebuilt ?? data));
        htmlSet = true;
        return "rebuilt";
      }
      if (!mounted) {
        // 1 枚目を読み込んでいる最中。受け口がまだ無いので送らない（入れ直しもしない）
        return "deferred";
      }
      const message: DataMessage<D> = { type: "data", data };
      surface.post(message);
      return "posted";
    },
    post(message: unknown): boolean {
      if (!(htmlSet && mounted)) {
        return false;
      }
      surface.post(message);
      return true;
    },
    ready(): void {
      mounted = true;
    },
    /** 保持する画面は捨てられない。教えてもらっても倒すものが無い */
    hidden(): void {
      // 何もしない
    },
  };
}
