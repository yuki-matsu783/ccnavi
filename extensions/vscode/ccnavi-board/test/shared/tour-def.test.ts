/**
 * 案内の定義（`src/webview/tours/*.yml`）の形の検査。画面は読み込むときに同じ検査を通し、壊れていれば落ちるので、
 * 配る前にここで見つける。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { INTRO_ID, parseTour } from "../../src/core/tour-def.js";
import { loadTour, rawTour, tourScreens } from "../helpers/tour.js";

const STEP = { target: "save", title: "保存", body: "保存します" };

function course(id: string, extra: Record<string, unknown> = {}): Record<string, unknown> {
  return { id, title: "題", summary: "説明", steps: [STEP], ...extra };
}

test("CB-T402 案内の定義は screen と courses を持ち、先頭の案内は intro。手順の scene は文字列・数・真偽だけ", () => {
  const ok = parseTour({ screen: "x", sample: { any: 1 }, courses: [course(INTRO_ID, { steps: [{ ...STEP, scene: { tab: "a", n: 2, on: true } }] }), course("use-case")] });
  assert.ok(ok.ok);
  assert.equal(ok.ok && ok.value.courses[0].steps[0].scene.tab, "a");
  assert.equal(ok.ok && ok.value.courses[1].steps[0].scene.tab, undefined, "scene は引き継がず、書かなければ空");

  const bad: Array<[string, unknown, RegExp]> = [
    ["オブジェクトでない", [], /オブジェクト/],
    ["screen がありません", { courses: [course(INTRO_ID)] }, /screen/],
    ["courses が空", { screen: "x", courses: [] }, /courses/],
    ["先頭が intro でない", { screen: "x", courses: [course("first")] }, /intro/],
    ["id が重なる", { screen: "x", courses: [course(INTRO_ID), course("a"), course("a")] }, /重複/],
    ["id の形", { screen: "x", courses: [course(INTRO_ID), course("A_b")] }, /\.id は/],
    ["steps が空", { screen: "x", courses: [course(INTRO_ID, { steps: [] })] }, /steps が空/],
    ["body がありません", { screen: "x", courses: [course(INTRO_ID, { steps: [{ target: "a", title: "t" }] })] }, /courses\[0\]\.steps\[0\] の title と body/],
    ["手順のキーの綴り違い", { screen: "x", courses: [course(INTRO_ID, { steps: [{ ...STEP, targt: "a" }] })] }, /未知のキー targt/],
    ["案内のキーの綴り違い", { screen: "x", courses: [course(INTRO_ID, { sumary: "a" })] }, /未知のキー sumary/],
    ["指す先に引用符", { screen: "x", courses: [course(INTRO_ID, { steps: [{ ...STEP, target: 'a"b' }] })] }, /\.target は/],
    ["scene の値", { screen: "x", courses: [course(INTRO_ID, { steps: [{ ...STEP, scene: { open: ["a"] } }] })] }, /scene\.open/],
  ];
  for (const [name, raw, pattern] of bad) {
    const parsed = parseTour(raw);
    assert.ok(!parsed.ok, `${name}: 読めてしまった`);
    assert.match(parsed.ok ? "" : parsed.error, pattern, name);
  }
});

test("CB-T403 案内の YAML は、ファイル名の画面を指し、どれも先頭が基本の案内で、各案内に 1 手順以上ある", () => {
  const screens = tourScreens();
  assert.ok(screens.length >= 1, "案内の YAML が 1 本もありません");
  for (const screen of screens) {
    const def = loadTour(screen);
    assert.equal(def.screen, screen, `${screen}.yml の screen がファイル名と違う`);
    assert.equal(def.courses[0].id, INTRO_ID);
    assert.ok(rawTour(screen) !== undefined);
  }
});
