import * as fs from "node:fs";
import * as path from "node:path";
import { parseBoardJson, type BoardJson } from "../src/core/model.js";

/** Python 側のテスト（tests/test_board.py）が書き出した、実行ファイルの出力そのもの */
export const FIXTURE_PATH = path.join(__dirname, "..", "..", "test", "fixtures", "board.json");

export function fixtureText(): string {
  return fs.readFileSync(FIXTURE_PATH, "utf8");
}

export function fixture(): BoardJson {
  const parsed = parseBoardJson(fixtureText());
  if (!parsed.ok) {
    throw new Error(parsed.error);
  }
  return parsed.board;
}
