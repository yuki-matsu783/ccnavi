/**
 * 試験用の小さな git リポジトリを作る（一時のディレクトリに、ブランチごとのファイルと先頭のコミットの日付を持つ形）。
 * `repoToFixture` と `scripts/render-scene.js` の試験が使う。ここは書き込み系の git を打ってよい（作るのは試験用の一時のもの）。
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

export interface RepoBranch {
  readonly files: Record<string, string | Buffer>;
  /** 先頭のコミットの日付（ISO） */
  readonly date: string;
}

function run(dir: string, args: string[], date?: string): void {
  execFileSync("git", ["-C", dir, ...args], {
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, ...(date ? { GIT_AUTHOR_DATE: date, GIT_COMMITTER_DATE: date } : {}), GIT_CONFIG_GLOBAL: "/dev/null", GIT_CONFIG_SYSTEM: "/dev/null" },
  });
}

/** `branches` の最初のものを統合先（main）として作り、残りを main から切って、それぞれ 1 コミットにする */
export function makeRepo(dir: string, branches: Record<string, RepoBranch>): void {
  fs.mkdirSync(dir, { recursive: true });
  run(dir, ["init", "-q", "-b", "main"]);
  run(dir, ["config", "user.name", "試験"]);
  run(dir, ["config", "user.email", "test@example.invalid"]);
  run(dir, ["config", "commit.gpgsign", "false"]);
  let first = true;
  for (const [name, b] of Object.entries(branches)) {
    if (first) {
      first = false;
      if (name !== "main") throw new Error("最初のブランチは main");
    } else {
      run(dir, ["checkout", "-q", "-b", name, "main"]);
      run(dir, ["rm", "-rq", "--ignore-unmatch", "."]);
    }
    for (const [file, content] of Object.entries(b.files)) {
      const target = path.join(dir, file);
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, content);
    }
    run(dir, ["add", "-A", "-f", "."]);
    run(dir, ["commit", "-q", "-m", `init ${name}`, "--allow-empty"], b.date);
  }
  run(dir, ["checkout", "-q", "main"]);
}
