/**
 * 見た目の切り替え。拡張ホストが `{ type: "appearance", value }` を送ると body のクラスだけを付け替える。
 * 中身は作り直さないので、開いているオーバーレイも絞り込みも打ちかけの入力も消えない。
 *
 * 文字列で組む画面は同じことを `core/appearance.ts` の `APPEARANCE_SCRIPT` でやっている。
 * React の画面はこちらを読む（どの画面の部品からも読むので、画面ごとの契約には依存しない）。
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
