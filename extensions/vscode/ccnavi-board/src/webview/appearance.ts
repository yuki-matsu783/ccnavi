/**
 * 見た目の切り替え。拡張ホストが `{ type: "appearance", value }` を送ると body のクラスと拡大率（`zoom`）だけを切り替える。
 * 中身は作り直さないので、開いているオーバーレイも絞り込みも打ちかけの入力も消えない。
 *
 * 9 画面とも React で、見た目を受け取るのはここ 1 本だけ。画面では `useHostMessages` がこの関数を呼ぶ。どの画面の部品からも読むため、画面ごとの契約には依存しない。
 */
export function applyAppearance(value: unknown, zoom?: unknown): void {
  document.body.style.zoom = typeof zoom === "number" && zoom !== 1 ? String(zoom) : "";
  for (const name of Array.from(document.body.classList)) {
    if (name.startsWith("ccnavi-claude-")) {
      document.body.classList.remove(name);
    }
  }
  if (value === "claude-light" || value === "claude-dark") {
    document.body.classList.add(`ccnavi-${value}`);
  }
}
