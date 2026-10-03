/**
 * カードに出す言葉。検証の指摘は実行ファイル（`--lint --json`）が言ったもので、ここは
 * 同じ事象を 2 度出さないように間引くだけ。拡張が判定をやり直すことはしない。
 */
import type { LintProblem } from "../../core/lintmodel.js";
import type { ProjectRow } from "../../core/projects-view.js";

/** プロジェクトの設定の場所（プロジェクトのルートからの相対）。その設定のルールファイルの場所から逆算し、無ければ既定 */
export function settingsDir(row: ProjectRow): string {
  const prefix = `${row.rel}/`;
  const inside = row.rulesRel.startsWith(prefix) ? row.rulesRel.slice(prefix.length) : "";
  const cut = inside.lastIndexOf("/");
  return cut < 0 ? ".ccnavi/config" : inside.slice(0, cut);
}

/**
 * カードに出す指摘。`.claude/` があることは説明付きの 1 行で言い、lint の同じ指摘
 * （ccnavi/entry/lint.py の文面「.claude/ を持つ。…」）は重ねない。
 * 「.claude/settings.json を読めない」のような別の指摘まで消さないよう、文面の先頭で当てる。
 */
export function problemsOf(row: ProjectRow): readonly LintProblem[] {
  return [
    ...(row.hasClaudeDir
      ? [
          {
            severity: "warn" as const,
            where: "",
            detail: `.claude/ があります。Claude Code はそこにあるスキルを読み込み、このフォルダに cd すると、ここを別のワークスペースルートとして扱います。プロジェクトの設定は ${settingsDir(row)}/ に置いてください`,
          },
        ]
      : []),
    ...row.problems.filter((p) => !(row.hasClaudeDir && p.detail.startsWith(".claude/ を持つ"))),
  ];
}
