/**
 * ボード画面の、拡張ホストと Webview の間の契約。
 *
 * 画面は React で組み、拡張ホストは HTML を組み立てない。更新のたびに画面を作り直さず、画面の中身にも型検査を効かせるため。
 * 拡張ホストが渡すのは「いま何を見せるか」（`BoardData`）だけで、列やカードの DOM は画面が作る。
 * 画面が返すのはユーザが押した操作（`BoardMessage`）だけ。
 *
 * この形を保つために、ここには VS Code の API も DOM も入れない。両側から import されるので、
 * 片方だけが持てるものを置くとバンドルできなくなる。
 */
import type { ApprovePreview } from "./approvemodel.js";
import type { DecidePreview } from "./decidemodel.js";
import type { AppearanceMessage } from "./appearance.js";
import type { Board } from "./board.js";
import type { Moved } from "./board-moved.js";
import type { MergeView } from "./merge.js";
import { embedJson, type DataMessage } from "./screen-host.js";

/**
 * 承認のオーバーレイの状態。拡張ホストが持ち、見せるたびに渡す。画面の中に持たないのは、
 * 監視の更新でボードが入れ替わってもオーバーレイが消えないようにするため。
 */
export type ApprovalOverlay =
  | { readonly kind: "loading" }
  /**
   * `changedSinceSave` は、計画編集タブで保存したあとに提案が変わった親（一覧を開いた時点で拡張ホストが比べた）。
   * 図の上に注意を出すだけで、承認は止めない
   */
  | { readonly kind: "preview"; readonly preview: ApprovePreview; readonly notice?: string; readonly changedSinceSave?: readonly string[] }
  | { readonly kind: "approving"; readonly preview: ApprovePreview }
  | { readonly kind: "error"; readonly error: string }
  /**
   * 承認できた。Claude Code に渡す文と、コピー / 新しいセッションで開く / ターミナルで実行 を出す。
   * `carried` は承認済みチケットをコミットして push する sh をターミナルに送ることにしたか（その sh が置いてあるか）。
   * 送るのは拡張ホストで、送れたかまでは見ていない。真のときだけ、そう言う
   */
  | { readonly kind: "done"; readonly count: number; readonly prompt: string; readonly carried?: boolean }
  /**
   * 承認以外で Claude Code に渡すプロンプト（残った指摘を決めた結果）。承認したときと同じ 2 ボタンで渡す
   */
  | {
      readonly kind: "prompt";
      readonly title: string;
      readonly note: string;
      readonly prompt: string;
      /** 渡したときにユーザへ言う呼び名（「…をコピーした」）。無ければ「プロンプト」 */
      readonly what?: string;
      /**
       * 取り返せないプロンプトか（残った指摘を決めた結果。続きの子はもう置かれている）。真なら、
       * 承認した文（`done`）と同じく、渡し終えるか閉じるまで別のオーバーレイを被せない
       */
      readonly keep?: boolean;
      /**
       * 決め直しへ戻れる。表示した後に指摘が追加されて、レビュー済みにしていないとき（実行ファイルの答えの
       * `reviewed` が偽で `pending_extra` がある）。押すと残った指摘を読み直す。続きの子が起きたときは付けない
       * （そのプロンプトは取り返せない）
       */
      readonly retry?: { readonly parent: string; readonly phase: number };
    }
  /**
   * 残った指摘を読み込んでいる（`ccnavi-review.sh decide <N> --preview`）。「レビュー完了」を押すとここに入る。
   * `tree` は親のワークツリーで、sh をそこで走らせる。`mr` はフェーズ行の依頼が持つ MR の番号（分かるときだけ。
   * 読み取りの検証が、読んだ番号とこれを照らす）。`notice` は見せ直す理由（見せた指摘と今の指摘が違った）
   */
  | {
      readonly kind: "decideLoading";
      readonly parent: string;
      readonly phase: number;
      readonly tree: string;
      readonly mr: number | null;
      readonly notice?: string;
    }
  /**
   * 残った指摘を見せた。0 件なら「未解決の指摘はありません」と［レビュー済みにする］、1 件以上なら行き先を指摘ごとに選ぶ。
   * 押されるまで何も置かない。`consult` は「エージェントに相談するプロンプトをコピー」の結果（コピーできた・できなかった）
   */
  | {
      readonly kind: "decidePreview";
      readonly preview: DecidePreview;
      readonly tree: string;
      readonly notice?: string;
      readonly consult?: { readonly ok: boolean; readonly text: string };
    }
  /** 選んだ行き先を置いている。ここでは閉じない */
  | { readonly kind: "deciding"; readonly preview: DecidePreview; readonly tree: string };

/**
 * 画面に見せる中身。読み直せなかったときはボードの代わりに文面を渡す（`kind: "error"`）。
 * どちらにも承認のオーバーレイが重なる。承認した文は取り返しがつかないので、ボードが描けないことを
 * 理由に消さない。
 */
export type BoardData =
  | {
      readonly kind: "board";
      readonly board: Board;
      readonly approval?: ApprovalOverlay;
      /**
       * 前の読み直しから動いたカード（`board-moved.ts`）。決めるのも覚えるのも拡張ホストで、
       * オーバーレイと同じ理由（画面は非表示になると破棄される）。画面は渡された分に動いた表示を出すだけ
       */
      readonly moved?: readonly Moved[];
      /**
       * 計画編集タブに未保存の変更がある親（`core/workflow-tabs.ts`）。その親を含む承認の一覧は、
       * 承認のボタンを押せなくして理由を出す。拡張ホストはタブの様子が変わるたびに描き直す
       */
      readonly unsaved?: readonly string[];
      /**
       * マージ状況の確認の結果（`core/merge.ts`）。持つのも決めるのも拡張ホストで、オーバーレイと同じ理由
       * （画面は非表示になると破棄される）。まだ一度も確認していなければ無い。画面は渡された分を帯に出すだけ
       */
      readonly merge?: MergeView;
      /**
       * 開いた直後に選ぶプロジェクトの絞り込み（いまは呼び出し元が無い）。
       * 入るのは 1 枚目の HTML に埋めるときだけで、`ToBoard` の `data` では渡さない
       * （画面が組み上がった後は `filter` のメッセージで渡す）
       */
      readonly filter?: string;
    }
  | {
      readonly kind: "error";
      readonly error: string;
      readonly approval?: ApprovalOverlay;
      readonly unsaved?: readonly string[];
      readonly filter?: string;
    };

/** 拡張ホスト → 画面。中身を包む形は `screen-host.ts` が決める（渡すのはそこ） */
export type ToBoard =
  | DataMessage<BoardData>
  /** 開くときにプロジェクトを指定されたとき（いまは呼び出し元が無い） */
  | { readonly type: "filter"; readonly project: string }
  /** 初回の吹き出しの案内を出す。画面は指す先が出てから始める（`src/tour.ts`） */
  | { readonly type: "tour" }
  | AppearanceMessage;

/** 画面 → 拡張ホスト。受け側（board-panel の `asMessage`）が形を確かめてから使う */
export type BoardMessage =
  /** 画面が組み上がった。非表示になって作り直された画面が、いまの中身をもらい直すために送る */
  | { readonly type: "ready" }
  | { readonly type: "open"; readonly filePath: string }
  | { readonly type: "refresh" }
  | { readonly type: "approve"; readonly tickets: readonly string[]; readonly filtered: boolean }
  | { readonly type: "approveConfirm"; readonly tickets: readonly string[]; readonly digest: string }
  | { readonly type: "approveCancel" }
  | { readonly type: "promptCopy" }
  | { readonly type: "promptOpen" }
  /** 承認のプロンプトを、ボードのターミナルで `claude -p` として実行する。オーバーレイは閉じる */
  | { readonly type: "promptRun" }
  /** ［レビュー完了］を押した。残った指摘を読む流れに入る（0 件ならレビュー済みにする画面、1 件以上なら対応方針の画面） */
  | { readonly type: "decide"; readonly parent: string; readonly phase: number }
  /** 残った指摘の行き先を決めた（0 件なら「レビュー済みにする」）。キーは指摘の `key`、値は `keep` / `fix` / `issue` */
  | { readonly type: "decideConfirm"; readonly choices: Readonly<Record<string, string>> }
  /** 対応方針の画面の「エージェントに相談するプロンプトをコピー」を押した。オーバーレイは閉じない */
  | { readonly type: "decideConsult" }
  /**
   * 子のカードの「フロー」を押した。拡張ホストはいまのボードにその子が在ることを確かめてから、
   * フロー編集画面を開く（置き場と錠はその画面が実行ファイルに聞き直す）
   */
  | { readonly type: "flow"; readonly ticket: string }
  /**
   * 親の提案のカードの「計画編集」を押した。拡張ホストはいまのボードでそのカードが入口を出していることを
   * 確かめてから、その親の計画編集タブを開く（`ccnaviBoard.openWorkflow` と同じ入口）
   */
  | { readonly type: "workflow"; readonly ticket: string }
  /**
   * 完了した親のカードの「テンプレートの下書きを作る」を押した。拡張ホストはいまのボードでそのカードが入口を出していることを
   * 確かめてから、`ccnavi --templates --draft` を呼ぶ
   */
  | { readonly type: "templateDraft"; readonly ticket: string }
  /**
   * 承認のオーバーレイの「計画を編集」を押した。拡張ホストはオーバーレイを閉じてから、その親の
   * 計画編集タブを開く（一覧の図にある親だけ。決めるのは承認の遷移）
   */
  | { readonly type: "editWorkflow"; readonly ticket: string }
  /** 「マージ状況を確認」を押した。確認中なら拡張ホストが受けない（二重に走らせない） */
  | { readonly type: "mergeCheck" }
  /**
   * マージ済みの親の［片付ける］を押した。拡張ホストが確認のダイアログを出し、「実行する」なら
   * `cd … && sh …/ccnavi-sync.sh <親>` をボードのターミナルへ送る（MR がマージ済みかの確かめ直しと片付けは sync が決める）
   */
  | { readonly type: "mergeRetire"; readonly parent: string }
  /** マージせずに閉じた親の、記録を消すコマンドのコピーを押した。コピーするだけで、ボードからは送らない */
  | { readonly type: "mergeCopyAbandon"; readonly parent: string }
  /** 吹き出しの案内を閉じた（最後まで見ても、途中でやめても）。拡張ホストは次から初回の案内を頼まない */
  | { readonly type: "tourDone" };


/** 最初の中身を埋める `<script type="application/json">` の id。画面はこれを読んで最初の 1 枚を描く */
export const DATA_ID = "ccnavi-board-data";

/** 最初の中身を HTML に埋める形にする。埋め方は `screen-host.ts` が持つ（React の画面で同じ） */
export function embedData(data: BoardData): string {
  return embedJson(data);
}
