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

export function lockFromBoard(board: BoardJson): Lock {
  const doing = board.tickets
    .filter((t) => t.proposal !== null && t.proposal.state === "doing")
    .map((t) => t.ticket);
  if (doing.length === 0) {
    return { locked: false, reason: "", doing };
  }
  return {
    locked: true,
    reason: `作業中のチケットがある（${doing.join(", ")}）。終わるか取り消すまで保存できない`,
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
