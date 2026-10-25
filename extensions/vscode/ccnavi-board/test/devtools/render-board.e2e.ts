/**
 * 開発用の画面確認ツール（`scripts/render-board.js`）を `--fixture test/fixtures/board.json` で実際に回し、
 * Chromium が PNG を出すことを確かめる。
 *
 * `node --test` では回さない（ファイル名が `.test.js` ではない）。`scripts/test-groups.js` がこのファイルを別に起こし、
 * ブラウザが無いときはスキップして成功に数えず、終了コードをそのまま返す
 * （0 通った / 1 失敗 / 3 環境が揃っていない。`NOT_READY` と同じ意味）。
 */
import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { buildBoard } from "../../src/core/board.js";
import { parseBoardJson } from "../../src/core/model.js";
// ツールが out/ から読むもの。`test-groups.js` は import を辿って回すグループを決めるので、ここに並べて
// 「これを直したら devtools も回す」を表す（実行時には何もしない）
import "../../src/ccnavi.js";
import "../../src/core/render.js";
import "../../src/webview-asset.js";

const ROOT = path.join(__dirname, "..", "..", "..");
const FIXTURE = path.join(ROOT, "test", "fixtures", "board.json");
const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

function main(): number {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), "render-board-e2e-"));
  try {
    const ran = spawnSync(process.execPath, [path.join(ROOT, "scripts", "render-board.js"), "--fixture", FIXTURE, "--out", out], {
      encoding: "utf8",
    });
    if (ran.status === 3) {
      console.error(`render-board e2e: 環境が揃っていません（終了コード 3）: ${ran.stderr.trim()}`);
      return 3;
    }
    if (ran.status !== 0) {
      console.error(`render-board e2e: 終了コード ${String(ran.status)}\n${ran.stdout}${ran.stderr}`);
      return 1;
    }
    const png = fs.readFileSync(path.join(out, "board.png"));
    if (png.length < 1000 || !png.subarray(0, 8).equals(PNG_SIGNATURE)) {
      console.error("render-board e2e: board.png が PNG ではありません");
      return 1;
    }
    // 画面の中身が描かれている: フィクスチャのチケットが全部、見えているテキストに出る
    const parsed = parseBoardJson(fs.readFileSync(FIXTURE, "utf8"));
    if (!parsed.ok) {
      console.error(`render-board e2e: フィクスチャを解釈できません: ${parsed.error}`);
      return 1;
    }
    const text = fs.readFileSync(path.join(out, "board.txt"), "utf8");
    const board = buildBoard(parsed.board);
    const missing = board.columns.flatMap((column) => column.cards.map((card) => card.id)).filter((id) => !text.includes(id));
    if (missing.length > 0) {
      console.error(`render-board e2e: board.txt に出ていないチケット: ${missing.join(" ")}`);
      return 1;
    }
    for (const name of ["board.html", "board.log"]) {
      if (!fs.existsSync(path.join(out, name))) {
        console.error(`render-board e2e: ${name} がありません`);
        return 1;
      }
    }
    console.log("render-board e2e: 通りました");
    return 0;
  } finally {
    fs.rmSync(out, { recursive: true, force: true });
  }
}

process.exit(main());
