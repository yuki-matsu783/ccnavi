/**
 * 案内の定義（`src/webview/tours/*.yml`）の形の検査。画面は読み込むときに同じ検査を通し、壊れていれば落ちるので、
 * 配る前にここで見つける。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { INTRO_ID, parseTour } from "../../src/core/tour-def.js";
import * as fs from "node:fs";
import * as path from "node:path";

import { loadTour, rawTour, TOURS_DIR, tourScreens } from "../helpers/tour.js";

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
    ["id が重なる", { screen: "x", courses: [course(INTRO_ID), course("a"), course("a")] }, /重なって/],
    ["id の形", { screen: "x", courses: [course(INTRO_ID), course("A_b")] }, /\.id は/],
    ["steps が空", { screen: "x", courses: [course(INTRO_ID, { steps: [] })] }, /steps が空/],
    ["body がありません", { screen: "x", courses: [course(INTRO_ID, { steps: [{ target: "a", title: "t" }] })] }, /courses\[0\]\.steps\[0\] の title と body/],
    ["手順の鍵の綴り違い", { screen: "x", courses: [course(INTRO_ID, { steps: [{ ...STEP, targt: "a" }] })] }, /知らない鍵 targt/],
    ["案内の鍵の綴り違い", { screen: "x", courses: [course(INTRO_ID, { sumary: "a" })] }, /知らない鍵 sumary/],
    ["指す先に引用符", { screen: "x", courses: [course(INTRO_ID, { steps: [{ ...STEP, target: 'a"b' }] })] }, /\.target は/],
    ["scene の値", { screen: "x", courses: [course(INTRO_ID, { steps: [{ ...STEP, scene: { open: ["a"] } }] })] }, /scene\.open/],
  ];
  for (const [name, raw, pattern] of bad) {
    const parsed = parseTour(raw);
    assert.ok(!parsed.ok, `${name}: 読めてしまった`);
    assert.match(parsed.ok ? "" : parsed.error, pattern, name);
  }
});

test("CB-T1171 手順の image は名前（小文字・数字・-）で、alt と両方書くか両方書かない。書かなければ空", () => {
  const ok = parseTour({ screen: "x", courses: [course(INTRO_ID, { steps: [{ ...STEP, target: "", image: "shot-1", alt: "画面" }, STEP] })] });
  assert.ok(ok.ok);
  assert.deepEqual(ok.ok && ok.value.courses[0].steps.map((step) => [step.image, step.alt]), [["shot-1", "画面"], ["", ""]]);

  const bad: Array<[string, Record<string, unknown>]> = [
    ["image の名前に拡張子", { image: "shot.png", alt: "a" }],
    ["image の名前に大文字", { image: "Shot", alt: "a" }],
    ["alt が無い", { image: "shot" }],
    ["image が無いのに alt", { alt: "a" }],
  ];
  for (const [name, extra] of bad) {
    const parsed = parseTour({ screen: "x", courses: [course(INTRO_ID, { steps: [{ ...STEP, ...extra }] })] });
    assert.ok(!parsed.ok, `${name}: 読めてしまった`);
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

test("CB-T1170 案内の手順が引く画像は、images/ に PNG があり、images.ts の表にある。表と置き場に使われない画像が残らない", () => {
  const dir = path.join(TOURS_DIR, "images");
  const registry = fs.readFileSync(path.join(TOURS_DIR, "images.ts"), "utf8");
  const used = new Set<string>();
  for (const screen of tourScreens()) {
    for (const course of loadTour(screen).courses) {
      for (const step of course.steps) {
        if (step.image !== "") {
          used.add(step.image);
        }
      }
    }
  }
  assert.ok(used.size >= 1, "画像を使う手順が 1 つもありません");
  const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  for (const name of used) {
    const file = path.join(dir, `${name}.png`);
    assert.ok(fs.existsSync(file), `${name}.png が images/ にありません`);
    assert.ok(fs.readFileSync(file).subarray(0, 8).equals(PNG), `${name}.png が PNG ではありません`);
    assert.match(registry, new RegExp(`"${name}": `), `${name} が images.ts の表にありません`);
  }
  const files = fs.readdirSync(dir).filter((file) => file.endsWith(".png")).map((file) => file.slice(0, -4));
  assert.deepEqual([...files].sort(), [...used].sort(), "どの手順も使っていない画像、または画像のない手順の名前があります");
});
