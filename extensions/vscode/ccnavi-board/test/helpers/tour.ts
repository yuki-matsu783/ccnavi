/**
 * 案内の定義（`src/webview/tours/<画面>.yml`）をテストから読む。画面はバンドルに入った JSON を読むが、テストは YAML を直に読み、
 * 同じ形の検査（`parseTour`）を通す。このファイルは `out/test/helpers/` から走るので、`src/` は 3 つ上。
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { parse } from "yaml";

import { parseTour, tourOf, type TourDef } from "../../src/core/tour-def.js";

export const TOURS_DIR = path.join(__dirname, "..", "..", "..", "src", "webview", "tours");

/** 案内の定義の置き場にある画面の名前（`<名前>.yml`） */
export function tourScreens(): string[] {
  return fs
    .readdirSync(TOURS_DIR)
    .filter((name) => name.endsWith(".yml"))
    .map((name) => name.slice(0, -4))
    .sort();
}

export function rawTour(screen: string): unknown {
  return parse(fs.readFileSync(path.join(TOURS_DIR, `${screen}.yml`), "utf8"));
}

export function loadTour(screen: string): TourDef {
  return tourOf(rawTour(screen));
}

export { parseTour };
