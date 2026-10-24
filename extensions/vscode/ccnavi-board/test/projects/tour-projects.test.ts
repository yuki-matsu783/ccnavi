/**
 * プロジェクト管理画面の案内の見本（`tours/projects.yml` の `sample:`）の検査。
 * 見本は図（`diagrams:`）の値と同じ名前・例（example.com）を使い、実在のリポジトリ・ユーザを入れない。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { parseProjectsSample } from "../../src/core/tour-projects.js";
import { rawTour } from "../helpers/tour.js";

const raw = (rawTour("projects") as { sample: unknown }).sample;

test("CB-T1270 プロジェクト管理の見本は、問題なし・warn・error（予約名）の 3 件と、別枠と、共通設定のずれ 1 件を持つ。URL は example.com だけ", () => {
  const sample = parseProjectsSample(raw);
  assert.deepEqual(sample.page.rows.map((row) => row.name), ["web-app", "api", "self"]);
  assert.deepEqual(sample.page.rows.map((row) => row.problems.map((p) => p.severity)), [[], ["warn"], ["error"]]);
  // 予約名はプロジェクトの設定として数えられず、ルールファイルの欄は空になる
  assert.equal(sample.page.rows[2].rulesRel, "");
  assert.ok(sample.page.rows.every((row) => row.origin.startsWith("https://example.com/")));
  assert.equal(sample.page.strays.length, 1);
  const list = sample.mirror?.status.kind === "ok" ? sample.mirror.status.list : undefined;
  assert.ok(list !== undefined);
  assert.deepEqual(list.projects.map((p) => [p.name, p.state]), [["web-app", "synced"], ["api", "differs"]]);
  assert.equal(list.projects[1].changes.length, 1);
});

test("CB-T1271 見本の読み取りは、形が崩れたものを名指しして断る（名前の無いプロジェクト・知らない重大度・見本に無いプロジェクトの共通設定）", () => {
  assert.throws(() => parseProjectsSample({ projects: [{ origin: "x" }] }), /name が必要/);
  assert.throws(() => parseProjectsSample({ projects: [{ name: "a", problems: [{ severity: "info" }] }] }), /severity/);
  assert.throws(() => parseProjectsSample({ projects: [{ name: "a" }], mirror: { projects: [{ name: "b" }] } }), /sample.projects にない/);
  assert.equal(parseProjectsSample({ projects: [{ name: "a" }] }).mirror, undefined);
});
