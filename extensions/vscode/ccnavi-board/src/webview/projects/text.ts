/**
 * カードに出す言葉。検証の指摘は実行ファイル（`--lint --json`）が出したもの。
 */
import type { LintProblem } from "../../core/lintmodel.js";

/**
 * 置き場がワークスペースの git の索引に載っているときの `(projects)` の苦情か。
 *
 * 実行ファイルは、ワークスペース自身のソースに `projects/` がある（ぶつかり）ときも、
 * `.gitignore` に入れる前に `git add -A` して入れ子のリポジトリが gitlink で載った（載せ忘れ）ときも、
 * 同じ先頭の句で言う（src/ccnavi/entry/lint.py の `_TRACKED_LEAD`）。
 * どちらでも `.gitignore` に `/projects/` を足すだけでは直らない（ぶつかりなら誤り、載せ忘れなら半分）ので、
 * 画面は `.gitignore` に追加のボタンと「無視されていない」の帯を出さず、苦情の帯だけを出す。
 * 2 つを見分ける句（`（入れ子のリポジトリとして`）には頼らない。文面を変えるなら lint.py と揃える。
 */
export function isTrackedProjectsDir(problem: LintProblem, projectsRel: string): boolean {
  return problem.detail.startsWith(`\`${projectsRel}/\` はワークスペースの git が追跡している`);
}

/** 画面に出す最終更新の時刻。`2026-10-29T09:00:00+0900` を `2026-10-29 09:00` にする。読めない形はそのまま出す */
export function formatGeneratedAt(raw: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(raw);
  return m === null ? raw : `${m[1]} ${m[2]}`;
}
