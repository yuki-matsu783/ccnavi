/**
 * 層（設計 §11.2）のルールファイルの置き場を、ボードの JSON の `layers[]` から引く。
 *
 * 置き場は実行ファイルが解いたものを使い、拡張は `CCNAVI_PROJECT_HOME` を自分で読んでパスを組まない。
 * 組み方を 2 か所で持つと、拡張で保存したルールが判定に効かない食い違いが起きる（issue #13 はそれで起きた）。
 */
import type { BoardJson, LayerJson } from "./model.js";

/** ワークスペース自身の層の名札 */
export const LAYER_SELF = "self";

/** 層の名札に予約してある綴り。この名前のプロジェクトは層として数えない */
const RESERVED = ["common", LAYER_SELF];

/**
 * 旧のプロジェクトのルールの置き場。git プロジェクトルートからの相対。実行ファイルはもう読まない（設計 §11.12）。
 * 残っていれば「読まれていない」と示すためだけに持つ。
 */
export const OLD_PROJECT_RULES = "config/rules.yml";

/** ワークスペース自身の層。古い実行ファイルなら undefined */
export function selfLayer(board: BoardJson): LayerJson | undefined {
  return board.layers.find((l) => l.name === LAYER_SELF);
}

/**
 * プロジェクトの層。予約名（大文字小文字を問わない）のプロジェクトは層として数えないので undefined。
 * 名前だけで引くと、`projects/self` が自身の層を引いてしまう。
 */
export function projectLayer(board: BoardJson, name: string): LayerJson | undefined {
  if (RESERVED.includes(name.toLowerCase())) {
    return undefined;
  }
  return board.layers.find((l) => l.name === name);
}
