/**
 * 最初の中身を HTML から読む。入れ物を組む側が `<script type="application/json">` に埋めてある
 * （埋める形は各画面の契約の `embedData`）。2 枚目からは拡張ホストが postMessage で渡す。
 *
 * 読めないのは拡張の不具合だけなので、画面はそれを文面にして出す。落とすと白いままになる。
 */
export function readInitial<D>(id: string, onError: (detail: string) => D): D {
  const element = document.getElementById(id);
  const text = element?.textContent ?? "";
  if (text === "") {
    return onError("画面に渡す中身が無い");
  }
  try {
    return JSON.parse(text) as D;
  } catch (error) {
    return onError(`画面に渡す中身を読めなかった: ${String(error)}`);
  }
}
