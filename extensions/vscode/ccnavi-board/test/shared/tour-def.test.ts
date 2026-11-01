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
  return { id, title: "タイトル", summary: "説明", steps: [STEP], ...extra };
}

test("CB-T402 案内の定義はscreenとcoursesを持ち、先頭の案内はintro。手順のsceneは文字列・数・真偽だけ", () => {
  const ok = parseTour({ screen: "x", sample: { any: 1 }, courses: [course(INTRO_ID, { steps: [{ ...STEP, scene: { tab: "a", n: 2, on: true } }] }), course("use-case")] });
  assert.ok(ok.ok);
  assert.equal(ok.ok && ok.value.courses[0].steps[0].scene.tab, "a");
  assert.equal(ok.ok && ok.value.courses[1].steps[0].scene.tab, undefined, "sceneは引き継がず、書かなければ空");

  const bad: Array<[string, unknown, RegExp]> = [
    ["オブジェクトでない", [], /オブジェクト/],
    ["screenがありません", { courses: [course(INTRO_ID)] }, /screen/],
    ["coursesが空", { screen: "x", courses: [] }, /courses/],
    ["先頭がintroでない", { screen: "x", courses: [course("first")] }, /intro/],
    ["idが重なる", { screen: "x", courses: [course(INTRO_ID), course("a"), course("a")] }, /重複/],
    ["idの形", { screen: "x", courses: [course(INTRO_ID), course("A_b")] }, /\.idは/],
    ["stepsが空", { screen: "x", courses: [course(INTRO_ID, { steps: [] })] }, /stepsが空/],
    ["bodyがありません", { screen: "x", courses: [course(INTRO_ID, { steps: [{ target: "a", title: "t" }] })] }, /courses\[0\]\.steps\[0\]のtitleとbody/],
    ["手順のキーの綴り違い", { screen: "x", courses: [course(INTRO_ID, { steps: [{ ...STEP, targt: "a" }] })] }, /未知のキーtargt/],
    ["案内のキーの綴り違い", { screen: "x", courses: [course(INTRO_ID, { sumary: "a" })] }, /未知のキーsumary/],
    ["指す先に引用符", { screen: "x", courses: [course(INTRO_ID, { steps: [{ ...STEP, target: 'a"b' }] })] }, /\.targetは/],
    ["sceneの値", { screen: "x", courses: [course(INTRO_ID, { steps: [{ ...STEP, scene: { open: ["a"] } }] })] }, /scene\.open/],
  ];
  for (const [name, raw, pattern] of bad) {
    const parsed = parseTour(raw);
    assert.ok(!parsed.ok, `${name}: 読めてしまった`);
    assert.match(parsed.ok ? "" : parsed.error, pattern, name);
  }
});

test("CB-T1171 手順のimageは名前（小文字・数字・-）で、altと両方書くか両方書かない。書かなければ空", () => {
  const ok = parseTour({ screen: "x", courses: [course(INTRO_ID, { steps: [{ ...STEP, target: "", image: "shot-1", alt: "画面" }, STEP] })] });
  assert.ok(ok.ok);
  assert.deepEqual(ok.ok && ok.value.courses[0].steps.map((step) => [step.image, step.alt]), [["shot-1", "画面"], ["", ""]]);

  const bad: Array<[string, Record<string, unknown>]> = [
    ["imageの名前に拡張子", { image: "shot.png", alt: "a" }],
    ["imageの名前に大文字", { image: "Shot", alt: "a" }],
    ["altが無い", { image: "shot" }],
    ["imageが無いのにalt", { alt: "a" }],
    ["imageがあるのにtargetがある", { target: "save", image: "shot", alt: "a" }],
  ];
  for (const [name, extra] of bad) {
    const parsed = parseTour({ screen: "x", courses: [course(INTRO_ID, { steps: [{ ...STEP, ...extra }] })] });
    assert.ok(!parsed.ok, `${name}: 読めてしまった`);
  }
});

test("CB-T403 案内のYAMLは、ファイル名の画面を指し、どれも先頭が基本の案内で、各案内に1手順以上ある", () => {
  const screens = tourScreens();
  assert.ok(screens.length >= 1, "案内のYAMLが1本もありません");
  for (const screen of screens) {
    const def = loadTour(screen);
    assert.equal(def.screen, screen, `${screen}.ymlのscreenがファイル名と違う`);
    assert.equal(def.courses[0].id, INTRO_ID);
    assert.ok(rawTour(screen) !== undefined);
  }
});

test("CB-T1177 案内の手順が引く画像は、images/ にPNGがあり、images.tsの表にある。表と置き場に使われない画像が残らない", () => {
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
  assert.ok(used.size >= 1, "画像を使う手順が1つもありません");
  const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  for (const name of used) {
    const file = path.join(dir, `${name}.png`);
    assert.ok(fs.existsSync(file), `${name}.pngがimages/ にありません`);
    assert.ok(fs.readFileSync(file).subarray(0, 8).equals(PNG), `${name}.pngがPNGではありません`);
    assert.match(registry, new RegExp(`"${name}": `), `${name}がimages.tsの表にありません`);
  }
  const files = fs.readdirSync(dir).filter((file) => file.endsWith(".png")).map((file) => file.slice(0, -4));
  assert.deepEqual([...files].sort(), [...used].sort(), "どの手順も使っていない画像、または画像のない手順の名前があります");
});

test("CB-T1173 画像を使う案内はボード画面だけ（画像の表を渡すのもCSPにimg-src data: を足すのもボードだけ）", () => {
  for (const screen of tourScreens().filter((name) => name !== "board")) {
    for (const course of loadTour(screen).courses) {
      assert.ok(course.steps.every((step) => step.image === ""), `${screen}/${course.id}: 画像のある手順は、その画面が画像の表とCSPを持ってから足す`);
    }
  }
});

const ACTORS = ["ユーザ", "画面"];
const DIAGRAM = { actors: ACTORS, steps: [{ id: "a", from: "ユーザ", to: "画面", text: "押す", value: "x" }, { id: "b", from: "画面", text: "覚え書き" }] };

test("CB-T1175 時系列の図（diagrams）は、登場者と矢印を持ち、手順がdiagramとfocusで指す。toを書かない矢印は同じ登場者の中の動き", () => {
  const ok = parseTour({ screen: "x", diagrams: { flow: DIAGRAM }, courses: [course(INTRO_ID, { steps: [{ ...STEP, diagram: "flow", focus: "a, b" }, STEP] })] });
  assert.ok(ok.ok);
  assert.deepEqual(ok.ok && ok.value.diagrams.flow.steps.map((step) => [step.id, step.from, step.to, step.value]), [["a", "ユーザ", "画面", "x"], ["b", "画面", "画面", ""]]);
  assert.deepEqual(ok.ok && ok.value.courses[0].steps.map((step) => [step.diagram, step.focus]), [["flow", ["a", "b"]], ["", []]]);

  const bad: Array<[string, Record<string, unknown>, Record<string, unknown> | undefined]> = [
    ["登場者が1人", { flow: { ...DIAGRAM, actors: ["ユーザ"] } }, undefined],
    ["登場者が重なる", { flow: { ...DIAGRAM, actors: ["ユーザ", "ユーザ"] } }, undefined],
    ["登場者が7人", { flow: { ...DIAGRAM, actors: ["a", "b", "c", "d", "e", "f", "g"] } }, undefined],
    ["矢印のidが重なる", { flow: { ...DIAGRAM, steps: [DIAGRAM.steps[0], { ...DIAGRAM.steps[0] }] } }, undefined],
    ["fromが登場者にない", { flow: { ...DIAGRAM, steps: [{ ...DIAGRAM.steps[0], from: "だれか" }] } }, undefined],
    ["矢印のキーの綴り違い", { flow: { ...DIAGRAM, steps: [{ ...DIAGRAM.steps[0], txt: "a" }] } }, undefined],
    ["矢印が無い", { flow: { ...DIAGRAM, steps: [] } }, undefined],
    ["図の名前の形", { Flow: DIAGRAM }, undefined],
    ["引く図がない", { flow: DIAGRAM }, { diagram: "other" }],
    ["focusが図にない矢印", { flow: DIAGRAM }, { diagram: "flow", focus: "zzz" }],
    ["focusだけ", { flow: DIAGRAM }, { focus: "a" }],
    ["imageとdiagramを同時に", { flow: DIAGRAM }, { diagram: "flow", target: "", image: "shot", alt: "a" }],
  ];
  for (const [name, diagrams, step] of bad) {
    const parsed = parseTour({ screen: "x", diagrams, courses: [course(INTRO_ID, { steps: [{ ...STEP, ...(step ?? {}) }] })] });
    assert.ok(!parsed.ok, `${name}: 読めてしまった`);
  }
});

test("CB-T1176 案内のYAMLの図は、どれかの手順が引く（使われない図を残さない）", () => {
  for (const screen of tourScreens()) {
    const def = loadTour(screen);
    const used = new Set(def.courses.flatMap((course) => course.steps.map((step) => step.diagram)).filter((name) => name !== ""));
    assert.deepEqual(Object.keys(def.diagrams).sort(), [...used].sort(), `${screen}: 引かれない図、または無い図`);
  }
});
