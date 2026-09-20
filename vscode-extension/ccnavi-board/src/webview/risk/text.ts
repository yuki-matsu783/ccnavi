/**
 * リスク管理画面に出す言葉。要約の文、欄の名前、絞り込みが当てる文字列。
 *
 * 点は数えない（ADR-0035）。ここが作るのは「この項目はどう当たるか」を読める語順に並べた文だけで、
 * 何点になるかは実行ファイルが子を閉じるときに出す。
 */
import { KIND_LABELS, type FactorForm, type FactorKind } from "../../core/risk-view.js";

/** 要約の文の 1 片。`code` は等幅で出す（値そのもの）、`dim` は薄い地の文 */
export interface Part {
  readonly tone: "dim" | "code";
  readonly text: string;
}

const dim = (text: string): Part => ({ tone: "dim", text });
const code = (text: string): Part => ({ tone: "code", text });

/** 値の欄の名前。当て方で意味が変わる */
export function valueLabel(kind: FactorKind): string {
  if (kind === "glob") {
    return "glob";
  }
  if (kind === "script") {
    return "スクリプト";
  }
  if (kind === "judge") {
    return "問い";
  }
  return "しきい値";
}

/** 要約の行に出す、当て方と値をつないだ文。読んで意味が通る語順にする */
export function describe(factor: FactorForm): readonly Part[] {
  const value = factor.value;
  if (value === "") {
    return [dim(`（${valueLabel(factor.kind)} 未設定）`)];
  }
  switch (factor.kind) {
    case "lines_over":
      return [dim("差分が "), code(value), dim(" 行を超えたら加点")];
    case "files_over":
      return [dim("変えたファイルが "), code(value), dim(" 件を超えたら加点")];
    case "deleted_over":
      return [dim("消したファイルが "), code(value), dim(" 件を超えたら加点")];
    case "glob":
      return [code(value), dim(` にヒットしたファイルが 1 つあるごとに加点${factor.max === "" ? "" : `（上限 ${factor.max} 点）`}`)];
    case "script":
      return [dim("スクリプト "), code(value), dim(` が出した点を加点（測れなければ ${factor.points === "" ? "points" : `${factor.points} 点`}）`)];
    case "judge":
      return [dim("問い「"), code(value), dim("」に yes だったら加点")];
  }
}

/** 要約の左の id。未設定なら薄く言う */
export function summaryId(factor: FactorForm): string {
  return factor.id === "" ? "（id 未設定）" : factor.id;
}

/** 要約の点。未設定なら横棒 */
export function summaryPoints(factor: FactorForm): string {
  return factor.points === "" ? "—" : `${factor.points} 点`;
}

/** 当て方と値をツールチップに出す文 */
export function kindTitle(factor: FactorForm): string {
  return KIND_LABELS[factor.kind].label + (factor.value === "" ? "" : `: ${factor.value}`);
}

/**
 * 絞り込みが当てる文字列。**画面に出ている語（当て方の札と要約の文）でも、キーの綴り
 * （`lines_over` など）でも当たる。** 要約に出る文をそのまま含めるので、当て方を変えれば
 * 当たる語も変わる。
 */
export function findText(factor: FactorForm): string {
  const summary = summaryId(factor) + summaryPoints(factor) + describe(factor).map((part) => part.text).join("") + factor.message;
  return `${factor.id} ${factor.kind} ${KIND_LABELS[factor.kind].label} ${summary}`.toLowerCase();
}

/** 項目の数。絞り込んでいるときは「一致 / 全体（開いたまま N）」 */
export function countText(total: number, query: string, shown: number, kept: number): string {
  if (query === "") {
    return String(total);
  }
  return `${shown} / ${total}${kept > 0 ? `（開いたまま ${kept}）` : ""}`;
}
