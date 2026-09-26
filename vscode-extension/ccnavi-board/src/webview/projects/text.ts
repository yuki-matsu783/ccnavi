/**
 * カードに出す言葉。検証の苦情は実行ファイル（`--lint --json`）が言ったもので、ここは
 * 同じ事象を 2 度出さないように間引くだけ。拡張が判定をやり直すことはしない（ADR-0035）。
 */
import type { LintProblem } from "../../core/lintmodel.js";
import type { ProjectRow } from "../../core/projects-view.js";

/**
 * 置き場がワークスペースの git の索引に載っているときの `(projects)` の苦情か。
 *
 * 実行ファイルは、ワークスペース自身のソースに `projects/` がある（ぶつかり）ときも、
 * `.gitignore` に入れる前に `git add -A` して入れ子のリポジトリが gitlink で載った（載せ忘れ）ときも、
 * 同じ先頭の句で言う（ccnavi/lint.py の `_TRACKED_LEAD`。設計 wip/design/i0064-fixed-places.md §4.2）。
 * どちらでも `.gitignore` に `/projects/` を足すだけでは直らない（ぶつかりなら誤り、載せ忘れなら半分）ので、
 * 画面は `.gitignore` に追加のボタンと「無視されていない」の帯を出さず、苦情の帯だけを出す（§4.5 の分岐 1 の案 A）。
 * 2 つを見分ける句（`（入れ子のリポジトリとして`）には頼らない。文面を変えるなら lint.py と揃える。
 */
export function isTrackedProjectsDir(problem: LintProblem, projectsRel: string): boolean {
  return problem.detail.startsWith(`\`${projectsRel}/\` はワークスペースの git が追跡している`);
}

/** 層の設定の置き場（プロジェクトのルートからの相対）。層のルールの置き場から逆算し、無ければ既定 */
export function settingsDir(row: ProjectRow): string {
  const prefix = `${row.rel}/`;
  const inside = row.rulesRel.startsWith(prefix) ? row.rulesRel.slice(prefix.length) : "";
  const cut = inside.lastIndexOf("/");
  return cut < 0 ? ".ccnavi/config" : inside.slice(0, cut);
}

/**
 * カードに出す苦情。`.claude/` があることは説明付きの 1 行で言い、lint の同じ指摘
 * （ccnavi/lint.py の文面「.claude/ を持つ。…」）は重ねない。
 * 「.claude/settings.json を読めない」のような別の指摘まで消さないよう、文面の先頭で当てる。
 */
export function problemsOf(row: ProjectRow): readonly LintProblem[] {
  return [
    ...(row.hasClaudeDir
      ? [
          {
            severity: "warn" as const,
            where: "",
            detail: `.claude/ がある。Claude Code はそこにあるスキルを読み込み、cd するとそこが別のワークスペースルートに見える。プロジェクトの設定は ${settingsDir(row)}/ に置く`,
          },
        ]
      : []),
    ...row.problems.filter((p) => !(row.hasClaudeDir && p.detail.startsWith(".claude/ を持つ"))),
  ];
}
