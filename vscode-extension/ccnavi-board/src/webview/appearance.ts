/**
 * 見た目の切り替え。拡張ホストが `{ type: "appearance", value }` を送ると body のクラスだけを付け替える。
 * 中身は作り直さないので、開いているオーバーレイも絞り込みも打ちかけの入力も消えない。
 *
 * 5 画面とも React なので、受け口はここ 1 本だけ。どの画面の部品からも読むため、画面ごとの契約には依存しない。
 */
export function applyAppearance(value: unknown): void {
  for (const name of Array.from(document.body.classList)) {
    if (name.startsWith("ccnavi-claude-")) {
      document.body.classList.remove(name);
    }
  }
  if (value === "claude-light" || value === "claude-dark") {
    document.body.classList.add(`ccnavi-${value}`);
  }
}
