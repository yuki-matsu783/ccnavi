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
import type { Appearance } from "./appearance.js";
import type { Board } from "./board.js";
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
   * `carried` は承認済みチケットを運ぶ sh を端末に送ったか。送ったときだけ、そう言う
   */
  | { readonly kind: "done"; readonly count: number; readonly prompt: string; readonly carried?: boolean }
  /**
   * 承認以外で Claude Code に渡す文（レビュー済みの連絡）。承認したときと同じ 2 ボタンで渡す。
   * 判定は動かしていないので、置かれたものは何も無い
   */
  | { readonly kind: "prompt"; readonly title: string; readonly note: string; readonly prompt: string };

/**
 * 画面に見せる中身。読み直せなかったときはボードの代わりに文面を渡す（`kind: "error"`）。
 * どちらにも承認のオーバーレイが載る。承認した文は取り返しがつかないので、ボードが描けないことを
 * 理由に消さない（設計 §10）。
 */
export type BoardData =
  | {
      readonly kind: "board";
      readonly board: Board;
      readonly approval?: ApprovalOverlay;
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
  | { readonly type: "appearance"; readonly value: Appearance };

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
  | { readonly type: "accept"; readonly parent: string; readonly phase: number }
  | { readonly type: "reviewed"; readonly parent: string; readonly phase: number };

/** 最初の中身を埋める `<script type="application/json">` の id。画面はこれを読んで最初の 1 枚を描く */
export const DATA_ID = "ccnavi-board-data";

/** 最初の中身を HTML に埋める形にする。埋め方は `screen-host.ts` が持つ（React の画面で同じ） */
export function embedData(data: BoardData): string {
  return embedJson(data);
}
