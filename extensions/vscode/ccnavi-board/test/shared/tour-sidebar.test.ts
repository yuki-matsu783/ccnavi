/**
 * 左パネルの案内（ウォークスルー）の決まり。`package.json` の宣言と、案内が指すコマンド・本文の Markdown がそろっていることを確かめる。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

import { playableScreens, SIDEBAR_WALKTHROUGH, TOUR_SCREENS, WALKTHROUGH_TOURS } from "../../src/core/tour-sidebar.js";
import { tourScreens } from "../helpers/tour.js";

const ROOT = path.join(__dirname, "..", "..", "..");

interface Step {
  readonly id: string;
  readonly description: string;
  readonly media: { readonly markdown: string };
  readonly when?: string;
}
interface Manifest {
  readonly contributes: {
    readonly commands: ReadonlyArray<{ readonly command: string }>;
    readonly walkthroughs: ReadonlyArray<{ readonly id: string; readonly steps: readonly Step[] }>;
  };
}

const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8")) as Manifest;

test("CB-T1168 案内の順は、プロジェクト管理 → チケット管理 → ルール → リスク → フェーズ → テンプレート。チケット制御が無効なら開けない画面を外し、どの画面にも案内の定義がある", () => {
  assert.deepEqual(Object.values(WALKTHROUGH_TOURS).flat(), ["projects", "board", "rules", "risk", "phases", "templates"]);
  assert.deepEqual(playableScreens(WALKTHROUGH_TOURS["ccnaviBoard.tourBasics"], true), ["projects", "board"]);
  assert.deepEqual(playableScreens(WALKTHROUGH_TOURS["ccnaviBoard.tourBasics"], false), ["projects"]);
  assert.deepEqual(playableScreens(WALKTHROUGH_TOURS["ccnaviBoard.tourRules"], false), ["rules"]);
  assert.deepEqual(playableScreens(WALKTHROUGH_TOURS["ccnaviBoard.tourRisk"], false), []);
  for (const screen of Object.keys(TOUR_SCREENS)) {
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
  // 段の並びは、ボタンが呼ぶコマンドの並びと同じ（左パネルを開く、プロジェクトとチケットの段、ルール、リスク、フェーズ、テンプレート）
  const linked = walkthrough.steps.map((step) => /\(command:(ccnaviBoard\.tour[A-Za-z]+)\)/.exec(step.description)?.[1]);
  assert.deepEqual(linked, [undefined, ...Object.keys(WALKTHROUGH_TOURS)]);
  assert.equal(new Set(walkthrough.steps.map((step) => step.id)).size, walkthrough.steps.length, "段の id が重なっている");
  // 開けない画面の段は、チケット制御が無効なら出さない
  for (const [index, command] of Object.keys(WALKTHROUGH_TOURS).entries()) {
    const needs = WALKTHROUGH_TOURS[command].every((screen) => TOUR_SCREENS[screen].needsTickets);
    assert.equal(walkthrough.steps[index + 1].when !== undefined, needs, `${command} の段の when`);
  }
});
