/**
 * カードに出す言葉。検証の指摘は実行ファイル（`--lint --json`）が出したもの。
 */
import { KIND_PROJECTS_TRACKED, KIND_PROJECTS_UNIGNORED, type LintProblem } from "../../core/lintmodel.js";

/**
 * 置き場がワークスペースのgitの索引に載っているときの`(projects)`の苦情か。
 *
 * 実行ファイルは、ワークスペース自身のソースに`projects/`がある（ぶつかり）ときも、
 * `.gitignore`に入れる前に`git add -A`して入れ子のリポジトリがgitlinkで載った（載せ忘れ）ときも、
 * 同じ種類（`kind`）で言う。
 * どちらでも`.gitignore`に`/projects/`を足すだけでは直らない（ぶつかりなら誤り、載せ忘れなら半分）ので、
 * 画面は`.gitignore`に追加のボタンと「無視されていない」の帯を出さず、苦情の帯だけを出す。
 * 文面は照合しない（文面を直しても見分けが外れないように）。
 */
export function isTrackedProjectsDir(problem: LintProblem): boolean {
  return problem.kind === KIND_PROJECTS_TRACKED;
}

/** 置き場がワークスペースのgitで無視されていないときの`(projects)`の苦情か */
export function isUnignoredProjectsDir(problem: LintProblem): boolean {
  return problem.kind === KIND_PROJECTS_UNIGNORED;
}
