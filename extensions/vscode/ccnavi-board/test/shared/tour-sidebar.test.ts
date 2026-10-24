/**
 * 左パネルの案内（ウォークスルー）の決まり。`package.json` の宣言と、案内が指すコマンド・本文の Markdown がそろっていることを見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

import { basicTourScreens, SIDEBAR_WALKTHROUGH } from "../../src/core/tour-sidebar.js";
import { tourScreens } from "../helpers/tour.js";

const ROOT = path.join(__dirname, "..", "..", "..");

interface Step {
  readonly id: string;
  readonly description: string;
  readonly media: { readonly markdown: string };
}
interface Manifest {
  readonly contributes: {
    readonly commands: ReadonlyArray<{ readonly command: string }>;
    readonly walkthroughs: ReadonlyArray<{ readonly id: string; readonly steps: readonly Step[] }>;
  };
}

const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8")) as Manifest;

test("CB-T1168 基本の案内はプロジェクト管理 → チケット管理の順。チケット制御が無効ならプロジェクト管理だけ。どちらも案内を持つ画面", () => {
  assert.deepEqual(basicTourScreens(true), ["projects", "board"]);
  assert.deepEqual(basicTourScreens(false), ["projects"]);
  for (const screen of basicTourScreens(true)) {
    assert.ok(tourScreens().includes(screen), `${screen} の案内の定義が無い`);
  }
});

test("CB-T1169 左パネルのウォークスルーが宣言され、各段の本文のファイルがあり、リンクのコマンドが登録されている", () => {
  const walkthrough = manifest.contributes.walkthroughs.find((item) => item.id === SIDEBAR_WALKTHROUGH);
  assert.ok(walkthrough, "ウォークスルーが package.json に無い");
  const commands = new Set(manifest.contributes.commands.map((item) => item.command));
  const builtin = new Set(["workbench.view.extension.ccnavi"]);
  assert.ok(walkthrough.steps.length > 0);
  for (const step of walkthrough.steps) {
    assert.ok(fs.existsSync(path.join(ROOT, step.media.markdown)), `${step.id}: ${step.media.markdown} が無い`);
    for (const match of step.description.matchAll(/\(command:([^)?]+)/g)) {
      assert.ok(commands.has(match[1]) || builtin.has(match[1]), `${step.id}: コマンド ${match[1]} が宣言されていない`);
    }
  }
  const basics = walkthrough.steps.map((step) => step.description).join("\n");
  for (const command of ["ccnaviBoard.tourProjects", "ccnaviBoard.tourBoard", "ccnaviBoard.tourBasics"]) {
    assert.ok(basics.includes(`command:${command}`), `${command} へのリンクが無い`);
  }
});
