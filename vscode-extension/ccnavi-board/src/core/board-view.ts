/**
 * ボード画面の、拡張ホストと Webview の間の契約。
 *
 * 画面は React で組み、拡張ホストは HTML を組み立てない（ADR: 拡張の画面を React にする）。
 * 拡張ホストが渡すのは「いま何を見せるか」（`BoardData`）だけで、列やカードの DOM は画面が作る。
 * 画面が返すのは人が押した操作（`BoardMessage`）だけで、判定も実行ファイルの呼び出しもしない。
 *
 * この形を保つために、ここには VS Code の API も DOM も入れない。両側から import されるので、
 * 片方だけが持てるものを置くと束ねられなくなる。
 */
import type { ApprovePreview } from "./approvemodel.js";
import type { DecidePreview } from "./decidemodel.js";
import type { AppearanceMessage } from "./appearance.js";
import type { Board } from "./board.js";
import type { Moved } from "./board-moved.js";
import { embedJson, type DataMessage } from "./screen-host.js";

/**
 * 承認のオーバーレイの状態。拡張ホストが持ち、見せるたびに渡す。画面の中に持たないのは、
 * 監視の更新でボードが入れ替わってもオーバーレイが消えないようにするため。
 */
export type ApprovalOverlay =
  | { readonly kind: "loading" }
  | { readonly kind: "preview"; readonly preview: ApprovePreview; readonly notice?: string }
  | { readonly kind: "approving"; readonly preview: ApprovePreview }
  | { readonly kind: "error"; readonly error: string }
  /**
   * 承認できた。Claude Code に渡す文と、コピー / 新しいセッションで開く を出す。
   * `carried` は承認済みチケットを運ぶ sh を端末に**送ることにしたか**（その sh が置いてあるか）。
   * 送るのは拡張ホストで、送れたかまでは見ていない。真のときだけ、そう言う
   */
  | { readonly kind: "done"; readonly count: number; readonly prompt: string; readonly carried?: boolean }
  /**
   * 承認以外で Claude Code に渡す文（レビュー済みの連絡）。承認したときと同じ 2 ボタンで渡す。
   * 判定は動かしていないので、置かれたものは何も無い
   */
  | {
      readonly kind: "prompt";
      readonly title: string;
      readonly note: string;
      readonly prompt: string;
      /** 渡したときに人へ言う呼び名（「…をコピーした」）。無ければレビュー済みの連絡の文 */
      readonly what?: string;
      /**
       * 取り返せない文か（残った指摘を決めた結果。続きの子はもう置かれている）。真なら、
       * 承認した文（`done`）と同じく、渡し終えるか閉じるまで別のオーバーレイを被せない
       */
      readonly keep?: boolean;
    }
  /**
   * 残った指摘を読み込んでいる（`ccnavi-review.sh decide <N> --preview`）。`tree` は親のワークツリーで、
   * sh をそこで走らせる。`notice` は見せ直す理由（見せた指摘と今の指摘が違った）
   */
  | {
      readonly kind: "decideLoading";
      readonly parent: string;
      readonly phase: number;
      readonly tree: string;
      readonly notice?: string;
    }
  /** 残った指摘を見せた。行き先を指摘ごとに選ぶ。押されるまで何も置かない */
  | { readonly kind: "decidePreview"; readonly preview: DecidePreview; readonly tree: string; readonly notice?: string }
  /** 選んだ行き先を置いている。**ここでは閉じない** */
  | { readonly kind: "deciding"; readonly preview: DecidePreview; readonly tree: string };

/**
 * 画面に見せる中身。読み直せなかったときはボードの代わりに文面を渡す（`kind: "error"`）。
 * どちらにも承認のオーバーレイが載る。承認した文は取り返しがつかないので、ボードが描けないことを
 * 理由に消さない（設計 10）。
 */
export type BoardData =
  | {
      readonly kind: "board";
      readonly board: Board;
      readonly approval?: ApprovalOverlay;
      /**
       * 前の読み直しから動いたカード（`board-moved.ts`）。**決めるのも覚えるのも拡張ホスト**で、
       * オーバーレイと同じ理由（画面は裏に回ると捨てられる）。画面は渡された分に印を出すだけ
       */
      readonly moved?: readonly Moved[];
      /**
       * 開いた直後に選ぶプロジェクトの絞り込み。プロジェクト管理画面からの導線でだけ入る。
       * 入るのは 1 枚目の HTML に埋めるときだけで、`ToBoard` の `data` では渡さない
       * （画面が組み上がった後は `filter` のメッセージで渡す）
       */
      readonly filter?: string;
    }
  | {
      readonly kind: "error";
      readonly error: string;
      readonly approval?: ApprovalOverlay;
      readonly filter?: string;
    };

/** 拡張ホスト → 画面。中身を包む形は `screen-host.ts` が決める（渡すのはそこ） */
export type ToBoard =
  | DataMessage<BoardData>
  /** プロジェクト管理画面から「このプロジェクトで絞って開く」で来たとき */
  | { readonly type: "filter"; readonly project: string }
  /** 初回の吹き出しの案内を出す。画面は指す先が出てから始める（`src/tour.ts`） */
  | { readonly type: "tour" }
  | AppearanceMessage;

/** 画面 → 拡張ホスト。受け側（board-panel の `asMessage`）が形を確かめてから使う */
export type BoardMessage =
  /** 画面が組み上がった。裏に回って作り直された画面が、いまの中身をもらい直すために送る */
  | { readonly type: "ready" }
  | { readonly type: "open"; readonly filePath: string }
  | { readonly type: "refresh" }
  | { readonly type: "approve"; readonly tickets: readonly string[]; readonly filtered: boolean }
  | { readonly type: "approveConfirm"; readonly tickets: readonly string[] }
  | { readonly type: "approveCancel" }
  | { readonly type: "promptCopy" }
  | { readonly type: "promptOpen" }
  | { readonly type: "decide"; readonly parent: string; readonly phase: number }
  /** 残った指摘の行き先を決めた。鍵は指摘の `key`、値は `keep` / `fix` / `issue` */
  | { readonly type: "decideConfirm"; readonly choices: Readonly<Record<string, string>> }
  | { readonly type: "reviewed"; readonly parent: string; readonly phase: number }
  /** 吹き出しの案内を閉じた（最後まで見ても、途中でやめても）。拡張ホストは次から初回の案内を頼まない */
  | { readonly type: "tourDone" };


/** 最初の中身を埋める `<script type="application/json">` の id。画面はこれを読んで最初の 1 枚を描く */
export const DATA_ID = "ccnavi-board-data";

/** 最初の中身を HTML に埋める形にする。埋め方は `screen-host.ts` が持つ（React の画面で同じ） */
export function embedData(data: BoardData): string {
  return embedJson(data);
}
