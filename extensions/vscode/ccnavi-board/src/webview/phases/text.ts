/**
 * フェーズ管理画面に出す言葉。要約に出す範囲の文、絞り込みが当てる文字列、id の重なりの文面。
 *
 * 定義の意味は判定しない。ここが作るのは並べて読めるようにした文だけ。
 */
import type { PhaseForm, UnreadField } from "../../core/phases-view.js";

/** 補足（agent / when）に何か入っているか */
export function hasMore(phase: PhaseForm): boolean {
  return phase.agent !== "" || phase.when !== "";
}

/** 「補足」の見出しにつける一言 */
export function moreNote(phase: PhaseForm): string {
  return hasMore(phase) ? "（設定あり）" : "（未設定）。案内するエージェント・使う場面";
}

/**
 * ファイルに残っている読まない欄の知らせ。どの欄かを名指しし、順序をどこで決めるかと、保存しても欄が
 * 残ることを言う。読まないことの答えは実行ファイル（`--lint` の warn）と同じで、画面は名指しするだけ。
 * 残っていなければ空。
 */
export function unreadNote(unread: readonly UnreadField[]): string {
  if (unread.length === 0) {
    return "";
  }
  const names: string[] = [];
  const byPhase = new Map<string, string[]>();
  for (const field of unread) {
    if (field.phase === null) {
      names.push(`${field.key}（ファイルの頭）`);
      continue;
    }
    const keys = byPhase.get(field.phase);
    if (keys === undefined) {
      byPhase.set(field.phase, [field.key]);
    } else {
      keys.push(field.key);
    }
  }
  for (const [phase, keys] of byPhase) {
    names.push(`${phase} の ${keys.join("・")}`);
  }
  return (
    `読まない欄があります: ${names.join("、")}。実行ファイルはこれらの欄を読みません。` +
    "順序は親チケットの計画の項の after で決めます。" +
    "画面はこれらの欄を出さず、保存しても欄はそのまま残ります（消すときはエディタで消してください）"
  );
}

/** 要約に出す範囲。inherit ならその表記、glob が無ければ未設定と言う */
export function scopeText(phase: PhaseForm): string {
  if (phase.inherit) {
    return "inherit";
  }
  return phase.scope.length === 0 ? "（scope 未設定）" : phase.scope.join(", ");
}

/** 範囲のツールチップ */
export function scopeTitle(phase: PhaseForm): string {
  return phase.inherit ? "親の範囲そのまま" : phase.scope.join(", ");
}

/** リストの欄は 1 つの欄に "," 区切りで出し、打つたびにリストへ戻す */
export function splitList(text: string): readonly string[] {
  return text
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part !== "");
}

/** 絞り込みが当てる文字列。id・題・範囲・成果物・使う場面に当たる */
export function findText(phase: PhaseForm): string {
  return `${phase.id} ${phase.title} ${phase.scope.join(" ")} ${phase.deliverables.join(" ")} ${phase.when}`.toLowerCase();
}

/** 定義の数。絞り込んでいるときは「一致 / 全体（開いたまま N）」 */
export function countText(total: number, query: string, shown: number, kept: number): string {
  if (query === "") {
    return String(total);
  }
  return `${shown} / ${total}${kept > 0 ? `（開いたまま ${kept}）` : ""}`;
}

/** id が重なっているときに下部へ出す文 */
export function duplicateNote(ids: ReadonlySet<string>): string {
  const names = Array.from(ids).map((id) => (id === "" ? "空" : id));
  return `id が重なっています（${names.join(", ")}）。1 つにするまで保存できません`;
}

/** 定義が 1 つも無いときに一覧へ出す文。ファイルの有無で変わる */
export function emptyNote(exists: boolean): string {
  if (exists) {
    return "定義がありません。定義が 1 つも無いファイルは実行ファイルが読めないので、保存する前に足してください";
  }
  return "ファイルがありません（この設定に定義が無い、という正常な状態です）。定義を足して保存すると、ファイルが作られます";
}
