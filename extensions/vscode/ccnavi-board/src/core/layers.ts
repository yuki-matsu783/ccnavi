/**
 * 共通・ワークスペース・プロジェクトの設定のルールファイルの場所を、ボードの JSON の `layers[]` から引く。
 * この 3 つは実行ファイルの言うレイヤー（layer）にあたる（設計 11.2）。
 *
 * 場所は実行ファイルが解いたものを使い、拡張は `.ccnavi` から自分でパスを組まない。
 * 組み方を 2 か所で持つと、拡張で保存したルールが判定に使われない食い違いが起きる。
 */
import type { BoardJson, LayerJson } from "./model.js";

/** ワークスペースの設定に付けるレイヤーの名前 */
export const LAYER_SELF = "self";

/** レイヤー（layer）の名前に予約してある表記。この名前のプロジェクトはプロジェクトの設定として数えない */
/** 共通の設定に付けるレイヤーの名前 */
export const LAYER_COMMON = "common";

const RESERVED = [LAYER_COMMON, LAYER_SELF];

/** 共通の設定。実行ファイルは常に出す。JSON に無ければ undefined */
export function commonLayer(board: BoardJson): LayerJson | undefined {
  return board.layers.find((l) => l.name === LAYER_COMMON);
}

/** ワークスペースの設定。実行ファイルは常に出す。JSON に無ければ undefined */
export function selfLayer(board: BoardJson): LayerJson | undefined {
  return board.layers.find((l) => l.name === LAYER_SELF);
}

/**
 * プロジェクトの設定。予約名（大文字小文字を問わない）のプロジェクトはプロジェクトの設定として数えないので undefined。
 * 名前だけで引くと、`projects/self` がワークスペースの設定を引いてしまう。
 */
export function projectLayer(board: BoardJson, name: string): LayerJson | undefined {
  if (RESERVED.includes(name.toLowerCase())) {
    return undefined;
  }
  return board.layers.find((l) => l.name === name);
}
