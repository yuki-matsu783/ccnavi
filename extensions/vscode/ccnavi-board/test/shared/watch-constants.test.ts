/** 監視の待ち時間と自己書込みの猶予が、`core/watch.ts` の 1 か所に集まっていること（拡張ホスト側。VS Code は要らない） */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { OWN_WRITE_GRACE_MS, WATCH_DEBOUNCE_MS } from "../../src/core/watch.js";

const SRC = path.join(__dirname, "..", "..", "..", "src");

function read(name: string): string {
  return fs.readFileSync(path.join(SRC, name), "utf8");
}

test("CB-T574 待ち時間は 120ms、自己書込みの猶予は 1500ms", () => {
  assert.equal(WATCH_DEBOUNCE_MS, 120);
  assert.equal(OWN_WRITE_GRACE_MS, 1500);
});

test("CB-T575 共通の値を使う画面は、待ち時間も猶予もローカルに持たない", () => {
  // settings は、3 画面（ルール・リスク・フェーズ）が共通で使う段取り。panel-common は、監視の反応を束ねる部品。
  for (const name of ["board", "rules", "risk", "phases", "templates", "handoff", "flow", "workflow", "settings"]) {
    const text = read(`${name}-panel.ts`);
    assert.doesNotMatch(text, /const OWN_WRITE_GRACE_MS\b/, `${name}: 猶予を持っている`);
    if (name !== "workflow") {
      assert.doesNotMatch(text, /const DEBOUNCE_MS\b/, `${name}: 待ち時間を持っている`);
    }
  }
  const common = read("panel-common.ts");
  assert.doesNotMatch(common, /const OWN_WRITE_GRACE_MS\b/, "panel-common: 猶予を持っている");
  assert.doesNotMatch(common, /const DEBOUNCE_MS\b/, "panel-common: 待ち時間を持っている");
});

test("CB-T576 違う待ち時間を使う画面は workflow（200）と projects（300）だけで、理由が未確認だと書いてある", () => {
  const workflow = read("workflow-panel.ts");
  const projects = read("projects-panel.ts");
  assert.match(workflow, /const DEBOUNCE_MS = 200;/);
  assert.match(projects, /const DEBOUNCE_MS = 300;/);
  assert.match(workflow, /理由は記録が無く未確認/);
  assert.match(projects, /理由は記録が無く未確認/);
});

test("CB-T577 自分の書き込みを抑える画面は、監視の通知の前に猶予を見る（handoff は実行中の印も見る）", () => {
  // ルール・リスク・フェーズの段取りは共通の settings-panel.ts が持つ。3 画面がそれを使っていることも確かめる。
  for (const name of ["rules", "risk", "phases"]) {
    assert.match(read(`${name}-panel.ts`), /settingsPanel</, `${name}: 共通の段取りを使っていない`);
  }
  assert.match(read("settings-panel.ts"), /Date\.now\(\) - current\.wroteAt < OWN_WRITE_GRACE_MS/, "settings: 猶予を見ていない");
  for (const name of ["handoff", "flow"]) {
    assert.match(read(`${name}-panel.ts`), /Date\.now\(\) - current\.wroteAt < OWN_WRITE_GRACE_MS/, `${name}: 猶予を見ていない`);
  }
  assert.match(read("handoff-panel.ts"), /current\.writing \|\| Date\.now\(\) - current\.wroteAt/);
});
