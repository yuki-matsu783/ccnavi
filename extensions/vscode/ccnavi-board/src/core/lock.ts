/**
 * ルールを保存してよいか。作業中のチケット（承認済みチケットが `doing/` にあり、着手済み）が 1 件でもあれば保存しない。
 *
 * hook はツール呼び出しのたびにルールを読み直すので、セッションが動いている最中に
 * 保存すると次の呼び出しから反映される。途中で判定が変わるのを避けるため、着手済みの
 * チケットがある間は編集はできても保存はできない。
 *
 * 答えはボードの JSON（実行ファイルの出力）から読む。
 */
import type { BoardJson } from "./model.js";

export interface Lock {
  readonly locked: boolean;
  /** 保存できない理由。空なら保存できる */
  readonly reason: string;
  readonly doing: readonly string[];
  /**
   * いまターンの途中にいるセッション。ターン中の保存は、保存できても、次のツール呼び出しの
   * 実行後チェックに戻されることがある。保存を止める理由ではなく、保存の前に確かめる印。
   */
  readonly turns: readonly string[];
}

/** 応答中の保存の前に見せる文。応答中のセッションが無ければ空 */
export function turnWarning(lock: Lock): string {
  if (lock.turns.length === 0) {
    return "";
  }
  return `エージェントが応答の途中です（セッション ${lock.turns.join(", ")}）。いま保存すると、次のツール呼び出しで元に戻されることがあります。応答が終わってから保存すると確実です。`;
}

/**
 * 共通の設定とワークスペースの設定のルール（`project` を渡さない）は、どのツリーの doing でも保存を止める（Bash の和に影響するため）。
 * プロジェクトのルール（`project` を渡す）は、そのプロジェクトの doing だけを見る。
 * 作業中かは実行ファイルが答える（チケットの `in_progress`）。承認しただけで着手していないもの・レビュー待ち・閉じたものは
 * 実行ファイルが作業中に数えない。
 */
export function lockFromBoard(board: BoardJson, project?: string): Lock {
  const mine = board.tickets.filter((t) => project === undefined || t.project === project);
  const doing = mine.filter((t) => t.in_progress === true).map((t) => t.ticket);
  const turns = board.turns.map((t) => t.session);
  if (doing.length === 0) {
    return { locked: false, reason: "", doing, turns };
  }
  const where = project === undefined ? "" : `プロジェクト ${project} に`;
  return {
    locked: true,
    reason: `${where}作業中のチケットがあります（${doing.join(", ")}）。いま保存すると、進行中の作業の判定が途中で変わるため、作業が終わってから保存してください。`,
    doing,
    turns,
  };
}

/**
 * 帯のクラス。作業中のチケットがあって保存を止めているとき（`doing` が空でない）だけ情報色にする。
 * 読めなかったとき・確認中（`lockFromError`）は異常なので赤のまま
 */
export function lockClass(lock: Lock): string {
  if (!lock.locked) {
    return "lock hidden";
  }
  return lock.doing.length > 0 ? "lock info" : "lock";
}

/** ボードが読めなかったとき。確かめられないなら保存できない扱いにする */
export function lockFromError(error: string): Lock {
  return {
    locked: true,
    reason: `保存できるか確認できません: ${error}`,
    doing: [],
    turns: [],
  };
}
