/**
 * リスク管理画面に出す言葉。要約の文、欄の名前、絞り込みが当てる文字列。
 *
 * 点は数えない。ここが作るのは「この項目はどう当たるか」を読める語順に並べた文だけで、
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

/** 値の欄の名前。加点条件で意味が変わる */
export function valueLabel(kind: FactorKind): string {
  if (kind === "glob") {
    return "glob";
  }
  if (kind === "script") {
    return "スクリプト";
  }
  if (kind === "judge") {
    return "yes/no の質問";
  }
  return "基準";
}

/** glob の欄（1 行に 1 つ）を、空行を除いた一覧にする */
function globList(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

/** 数える加点条件の、対象パスと除外パスの言い添え。どちらも空なら何も足さない */
function scopeParts(factor: FactorForm): readonly Part[] {
  const include = globList(factor.include);
  const exclude = globList(factor.exclude);
  const parts: Part[] = [];
  if (include.length > 0) {
    parts.push(dim("（対象 "), code(include.join(", ")), dim("）"));
  }
  if (exclude.length > 0) {
    parts.push(dim("（除外 "), code(exclude.join(", ")), dim("）"));
  }
  return parts;
}

/** 要約の行に出す、加点条件と値をつないだ文。読んで意味が通る語順にする */
export function describe(factor: FactorForm): readonly Part[] {
  const value = factor.value;
  if (value === "") {
    return [dim(`（${valueLabel(factor.kind)} 未設定）`)];
  }
  switch (factor.kind) {
    case "lines_over":
      return [dim("変更した行数（追加＋削除）が "), code(value), dim(" 行を超えると加点"), ...scopeParts(factor)];
    case "files_over":
      return [dim("変更したファイルが "), code(value), dim(" 件を超えると加点"), ...scopeParts(factor)];
    case "deleted_over":
      return [dim("削除したファイルが "), code(value), dim(" 件を超えると加点"), ...scopeParts(factor)];
    case "glob":
      return [code(value), dim(` に当てはまるファイルを 1 つ変更するごとに加点${factor.max === "" ? "" : `（上限 ${factor.max} 点）`}`)];
    case "script":
      return [dim("スクリプト "), code(value), dim(` が返した点を加点（点を読み取れなかったときは ${factor.points === "" ? "points" : `${factor.points} 点`}）`)];
    case "judge":
      return [dim("質問「"), code(value), dim("」の答えが yes なら加点")];
  }
}

/** 要約の左の id。未設定なら薄く言う */
export function summaryId(factor: FactorForm): string {
  return factor.id === "" ? "（id 未設定）" : factor.id;
}

/** 要約の点。未設定なら「（未設定）」と言う */
export function summaryPoints(factor: FactorForm): string {
  return factor.points === "" ? "（未設定）" : `${factor.points} 点`;
}

/** 加点条件と値をツールチップに出す文 */
export function kindTitle(factor: FactorForm): string {
  return KIND_LABELS[factor.kind].label + (factor.value === "" ? "" : `: ${factor.value}`);
}

/**
 * 絞り込みが当てる文字列。画面に出ている語（加点条件のラベルと要約の文）でも、キーの表記
 * （`lines_over` など）でも当たる。 要約に出る文をそのまま含めるので、加点条件を変えれば
 * 当たる語も変わる。ただし空の欄の代わりに出す断り（「（id 未設定）」「（未設定）」「（基準 未設定）」など）は
 * 書いてある値ではないので含めない。「未設定」で探しても、空の欄がある項目には当たらない。
 */
export function findText(factor: FactorForm): string {
  const points = factor.points === "" ? "" : summaryPoints(factor);
  const described = factor.value === "" ? "" : describe(factor).map((part) => part.text).join("");
  const summary = factor.id + points + described + factor.message;
  return `${factor.id} ${factor.kind} ${KIND_LABELS[factor.kind].label} ${summary}`.toLowerCase();
}

/** 項目の数。絞り込んでいるときは「一致 / 全体（開いたまま N）」 */
export function countText(total: number, query: string, shown: number, kept: number): string {
  if (query === "") {
    return String(total);
  }
  return `${shown} / ${total}${kept > 0 ? `（開いたまま ${kept}）` : ""}`;
}
