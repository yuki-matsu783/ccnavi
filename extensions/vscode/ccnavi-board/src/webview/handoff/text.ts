/**
 * 取り込み待ち画面に出す言葉。判定は実行ファイルの答えが決めていて、ここは答えの値を読みやすい言葉に直すだけ。
 * 知らない値はそのまま出す（実行ファイルが種類や状態を足しても、画面が黙って読み違えない）。
 */

/** 種類の言葉。`kind` の値も併記して、実行ファイルの答えと突き合わせられるようにする */
export function kindLabel(kind: string): string {
  switch (kind) {
    case "template":
      return "テンプレート";
    case "layer-config":
      return "設定";
    case "docs":
      return "docs";
    case "defense":
      return "防御（ターミナルのみ）";
    default:
      return kind === "" ? "（種類なし）" : kind;
  }
}

/** 状態の言葉 */
export function stateLabel(state: string): string {
  switch (state) {
    case "new":
      return "新規";
    case "changed":
      return "変更あり";
    case "unchanged":
      return "変更なし";
    case "conflict":
      return "衝突";
    case "rejected":
      return "受け付けない";
    default:
      return state === "" ? "（状態なし）" : state;
  }
}

/** 状態の札の色。取り込める状態は ok、取り込めない状態は bad、変更なしは色なし */
export function stateTone(state: string): "ok" | "bad" | "" {
  if (state === "new" || state === "changed") {
    return "ok";
  }
  return state === "unchanged" ? "" : "bad";
}
