/**
 * フェーズ管理画面に出す言葉。要約に出す範囲の文、絞り込みが適用する文字列、id の重なりの文面。
 *
 * ここが作るのは並べて読めるようにした文だけ。
 */
import type { PhaseForm } from "../../core/phases-view.js";

/** 補足（agent / when）に何か入っているか */
export function hasMore(phase: PhaseForm): boolean {
  return phase.agent !== "" || phase.when !== "";
}

/** 「補足」の見出しにつける一言 */
export function moreNote(phase: PhaseForm): string {
  return hasMore(phase) ? "（設定あり）" : "（未設定）案内するエージェントと、使う場面を設定します";
}

/** 要約に出す範囲。inherit ならその表記、glob が無ければ未設定と言う */
export function scopeText(phase: PhaseForm): string {
  if (phase.inherit) {
    return "inherit";
  }
  return phase.scope.length === 0 ? "（scope 未設定）" : phase.scope.join(", ");
}

/** 編集可能な範囲のツールチップ */
export function scopeTitle(phase: PhaseForm): string {
  return phase.inherit ? "親チケットの編集可能範囲を引き継ぎます。" : phase.scope.join(", ");
}

/** リストの欄は 1 つの欄に "," 区切りで出し、打つたびにリストへ戻す */
export function splitList(text: string): readonly string[] {
  return text
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part !== "");
}

/** 絞り込みが適用する文字列。id・タイトル・範囲・成果物・使う場面にヒットする */
export function findText(phase: PhaseForm): string {
  return `${phase.id} ${phase.title} ${phase.scope.join(" ")} ${phase.deliverables.join(" ")} ${phase.when}`.toLowerCase();
}

/** 定義の数。絞り込んでいるときは「一致 / 全体（開いたまま N）」 */
export function countText(total: number, query: string, shown: number, kept: number): string {
  if (query === "") {
    return String(total);
  }
  return `${shown} / ${total}${kept > 0 ? `（絞り込み外で開いている項目: ${kept} 件）` : ""}`;
}

/** id が重なっているときに下部へ出す文 */
export function duplicateNote(ids: ReadonlySet<string>): string {
  const names = Array.from(ids).map((id) => (id === "" ? "空の id" : id));
  return `id が重なっています（${names.join(", ")}）。重なりを直すまで保存できません。`;
}

/** 定義が 1 つも無いときに一覧へ出す文。ファイルの有無で変わる */
export function emptyNote(exists: boolean): string {
  if (exists) {
    return "定義がありません。定義が空のファイルは ccnavi で読み込めません。保存する前に、定義を 1 つ以上追加してください。";
  }
  return "フェーズ定義ファイルはまだ作成されていません。未設定のため、エラーではありません。定義を追加して保存すると、ファイルが作られます";
}

/** 共通の設定の定義と表示名が重なっているときに下部へ出す文 */
export function commonTitleNote(ids: ReadonlySet<string>): string {
  const names = Array.from(ids).map((id) => (id === "" ? "空" : id));
  return `共通の設定のフェーズ定義とタイトル（title）が重なっています（${names.join(", ")}）。重なっている定義は使われません。タイトルを変えてから保存してください。`;
}

/** 共通の設定にも同じ id がある定義に添える文 */
export function commonIdNote(ids: ReadonlySet<string>): string {
  return `共通の設定にも同じ id があります（${Array.from(ids).join(", ")}）。中身がすべて同じなら共通の設定の定義が使われ、中身が違えば保存時の検証（--lint）でエラーになります。変えたいときは別の id にしてください`;
}

/** 共通の設定の定義の欄の見出しにつける説明 */
export const COMMON_NOTE = "選択中の設定と組み合わせて適用される共通のフェーズ定義です。この画面では編集できません（.ccnavi/config/common/phases.yml）";
