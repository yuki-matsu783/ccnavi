/**
 * 絞り込みの比べ方。検索語も本文も NFKC で正規化してから小文字にする。
 * 全角英数字（`ＡＢＣ`）と半角（`abc`）、半角カナと全角カナを同じものとして探せる。
 * ルール・フェーズ・リスクの画面が同じ作りで使う。
 */
/**
 * 絞り込み中の上下ボタンの title。隠れた行を越えて動くと画面上は動かないように見えるので、
 * 絞り込み中（検索語が空でない）は並べ替えを止める。
 */
export function moveTitle(direction: "上へ" | "下へ", finding: boolean): string {
  return finding ? "絞り込み中は並べ替えできません。検索欄を空にしてください" : direction;
}

export function foldFind(text: string): string {
  return text.normalize("NFKC").toLowerCase();
}
