/**
 * ルールを保存してよいか。作業中のチケット（提案が `doing`）が 1 件でもあれば保存しない。
 *
 * hook はツール呼び出しのたびにルールを読み直すので、セッションが動いている最中に
 * 保存すると次の呼び出しから効く。途中で判定が変わるのを避けるため、着手済みの
 * チケットがある間は編集はできても保存はできない。
 *
 * 答えはボードの JSON（実行ファイルの出力）から読む。拡張は提案の状態を自分で解釈しない。
 */
import type { BoardJson } from "./model.js";

export interface Lock {
  readonly locked: boolean;
  /** 保存できない理由。空なら保存できる */
  readonly reason: string;
  readonly doing: readonly string[];
}

/**
 * ワークスペースのルールは、どのツリーの doing でも保存を止める（Bash の和に効くため）。
 * プロジェクトのルール（`project` を渡す）は、そのプロジェクトの doing だけを見る。
 */
export function lockFromBoard(board: BoardJson, project?: string): Lock {
  const doing = board.tickets
    .filter((t) => t.proposal !== null && t.proposal.state === "doing")
    .filter((t) => project === undefined || t.project === project)
    .map((t) => t.ticket);
  if (doing.length === 0) {
    return { locked: false, reason: "", doing };
  }
  const where = project === undefined ? "" : `プロジェクト ${project} に`;
  return {
    locked: true,
    reason: `${where}作業中のチケットがある（${doing.join(", ")}）。終わるか取り消すまで保存できない`,
    doing,
  };
}

/** ボードが読めなかったとき。確かめられないなら閉じる側に倒す */
export function lockFromError(error: string): Lock {
  return {
    locked: true,
    reason: `作業中のチケットが無いことを確かめられないので保存できない: ${error}`,
    doing: [],
  };
}
