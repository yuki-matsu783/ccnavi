/**
 * 最初の中身を HTML から読む。入れ物を組む側が `<script type="application/json">` に埋めてある
 * （埋める形は各画面の契約の `embedData`）。2 枚目からは拡張ホストが postMessage で渡す。
 *
 * 読めないのは拡張の不具合だけなので、画面はそれを文面にして出す。落とすと白いままになる。
 * 文面は画面の呼び名（`reopen`）だけを差し替える。開き直す先が画面ごとに違うため。
 */
export function readInitial<D>(id: string, reopen: string, onError: (text: string) => D): D {
  const element = document.getElementById(id);
  const text = element?.textContent ?? "";
  if (text === "") {
    return onError(`画面に渡す中身が無い（拡張の不具合）。${reopen}を開き直す。`);
  }
  try {
    return JSON.parse(text) as D;
  } catch (error) {
    return onError(`画面に渡す中身を読めなかった（拡張の不具合）: ${String(error)}`);
  }
}
