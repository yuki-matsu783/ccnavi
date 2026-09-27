/**
 * プロジェクト管理画面の「.gitignore に置き場を足す」の、ファイルに触る部分だけ。
 *
 * - **無い（ENOENT）ときだけ新しく作る。** それ以外で読めない（権限・ディレクトリ・壊れた装置など）ときは
 *   書かない。読めないまま「無い」として書くと、既にある中身を置き場の 1 行だけで上書きする
 * - 読めたら、置き場の行を足した本文を書く（既にあれば同じ本文を書き戻す）
 *
 * 利用者への文面（`message`）もここで組み、呼び手はそのまま画面へ渡す。診断ログは呼び手が
 * `step` と `code` で書く（ここは logger を持たない）。VS Code の API は使わない（単体テストで確かめる）。
 */
import * as fs from "node:fs";

import { gitignoreWithProjects } from "./projects.js";

export type GitignoreFix =
  | { readonly ok: true }
  | { readonly ok: false; readonly step: "read" | "write"; readonly code: string | undefined; readonly message: string };

/** `file`（ワークスペースの `.gitignore`）に `/<projectsRel>/` を足す */
export function addProjectsToGitignore(file: string, projectsRel: string): GitignoreFix {
  let before: string | undefined;
  try {
    before = fs.readFileSync(file, "utf8");
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code !== "ENOENT") {
      return { ok: false, step: "read", code, message: `.gitignore を読めないので書きませんでした: ${(error as Error).message}` };
    }
    before = undefined;
  }
  try {
    fs.writeFileSync(file, gitignoreWithProjects(before, projectsRel), "utf8");
  } catch (error) {
    return { ok: false, step: "write", code: (error as NodeJS.ErrnoException).code, message: `.gitignore に書けません: ${(error as Error).message}` };
  }
  return { ok: true };
}
