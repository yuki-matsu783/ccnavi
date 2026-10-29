import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as zlib from "node:zlib";
import {
  buildZip,
  captureKind,
  crc32,
  ffmpegCandidates,
  gifArgs,
  logsSince,
  markLogs,
  recordArgs,
  reportStamp,
  reportText,
  screenshotCommand,
} from "../../src/core/bug-report.js";

/** zip を中央ディレクトリから読み戻して（名前 → 中身）、書いた形を外の道具に頼らず確かめる */
function readZip(zip: Buffer): Map<string, { data: Buffer; method: number }> {
  const end = zip.length - 22;
  assert.equal(zip.readUInt32LE(end), 0x06054b50, "終わりの印が無い");
  const count = zip.readUInt16LE(end + 10);
  let at = zip.readUInt32LE(end + 16);
  const out = new Map<string, { data: Buffer; method: number }>();
  for (let i = 0; i < count; i++) {
    assert.equal(zip.readUInt32LE(at), 0x02014b50);
    assert.equal(zip.readUInt16LE(at + 8) & 0x0800, 0x0800, "名前が UTF-8 の印が無い");
    const method = zip.readUInt16LE(at + 10);
    const crc = zip.readUInt32LE(at + 16);
    const size = zip.readUInt32LE(at + 20);
    const nameLength = zip.readUInt16LE(at + 28);
    const local = zip.readUInt32LE(at + 42);
    const name = zip.subarray(at + 46, at + 46 + nameLength).toString("utf8");
    assert.equal(zip.readUInt32LE(local), 0x04034b50);
    const start = local + 30 + zip.readUInt16LE(local + 26) + zip.readUInt16LE(local + 28);
    const body = zip.subarray(start, start + size);
    const data = method === 8 ? zlib.inflateRawSync(body) : Buffer.from(body);
    assert.equal(crc32(data), crc, `${name} の CRC が合わない`);
    out.set(name, { data, method });
    at += 46 + nameLength;
  }
  return out;
}

test("CB-T1229 バグ報告の画面の取り方は Windows・macOS・WSL の 3 つで、ほかの Linux では録画もスクリーンショットもしない", () => {
  assert.equal(captureKind("win32", {}, ""), "windows");
  assert.equal(captureKind("darwin", {}, ""), "mac");
  assert.equal(captureKind("linux", { WSL_DISTRO_NAME: "Ubuntu" }, ""), "wsl");
  assert.equal(captureKind("linux", {}, "Linux version 5.15.153.1-microsoft-standard-WSL2"), "wsl");
  assert.equal(captureKind("linux", {}, "Linux version 6.8.0-generic"), "none");
  assert.equal(screenshotCommand("none", "/tmp/a.png"), undefined);
  assert.deepEqual(ffmpegCandidates("none", ""), []);
});

test("CB-T1230 ffmpeg は設定があればそれだけを使い、無ければ OS ごとの名前を探す（WSL は Windows 側の ffmpeg.exe、macOS は Homebrew の置き場も）", () => {
  assert.deepEqual(ffmpegCandidates("mac", "  /opt/ffmpeg  "), ["/opt/ffmpeg"]);
  assert.deepEqual(ffmpegCandidates("windows", ""), ["ffmpeg"]);
  assert.deepEqual(ffmpegCandidates("wsl", ""), ["ffmpeg.exe"]);
  assert.deepEqual(ffmpegCandidates("mac", ""), ["ffmpeg", "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]);
});

test("CB-T1231 録画は画面全体を mkv に録り（Windows と WSL は gdigrab、macOS は avfoundation）、gif はパレットを作ってから塗る", () => {
  const windows = recordArgs("windows", "C:\\t\\screen.mkv");
  assert.deepEqual(windows.slice(windows.indexOf("-f"), windows.indexOf("-f") + 2), ["-f", "gdigrab"]);
  assert.equal(windows[windows.indexOf("-i") + 1], "desktop");
  assert.equal(windows.at(-1), "C:\\t\\screen.mkv");
  assert.deepEqual(recordArgs("wsl", "x").slice(0, 10), recordArgs("windows", "x").slice(0, 10));
  const mac = recordArgs("mac", "/t/screen.mkv");
  assert.equal(mac[mac.indexOf("-f") + 1], "avfoundation");
  assert.equal(mac[mac.indexOf("-i") + 1], "Capture screen 0:none");
  // 縦横を偶数にそろえる（yuv420p で落ちないため）
  assert.match(windows[windows.indexOf("-vf") + 1], /trunc\(min\(1920,iw\)\/2\)\*2':-2/);
  const gif = gifArgs("in.mkv", "out.gif");
  assert.equal(gif[gif.indexOf("-i") + 1], "in.mkv");
  assert.equal(gif.at(-1), "out.gif");
  assert.match(gif[gif.indexOf("-vf") + 1], /palettegen.*paletteuse/);
});

test("CB-T1232 スクリーンショットは ffmpeg を使わず、macOS は screencapture、Windows と WSL は PowerShell で撮る。パスの引用符がスクリプトの中で崩れない", () => {
  assert.deepEqual(screenshotCommand("mac", "/t/s 1.png"), { file: "screencapture", args: ["-x", "-t", "png", "/t/s 1.png"] });
  const windows = screenshotCommand("windows", "C:\\Users\\o'neil\\s.png");
  assert.ok(windows !== undefined);
  assert.equal(windows.file, "powershell");
  assert.equal(screenshotCommand("wsl", "x")?.file, "powershell.exe");
  const script = Buffer.from(windows.args[windows.args.indexOf("-EncodedCommand") + 1], "base64").toString("utf16le");
  assert.match(script, /SetProcessDPIAware/);
  assert.match(script, /\$bmp\.Save\('C:\\Users\\o''neil\\s\.png', /);
});

test("CB-T1233 ログは始めたときの大きさを控え、終わりには、そのあとに足された行だけを取る（作り直されたファイルと新しく現れたファイルは全体）", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bug-report-test-"));
  try {
    fs.writeFileSync(path.join(dir, "ccnavi-board.log"), "古い行\n");
    fs.writeFileSync(path.join(dir, "ccnavi.log"), "古い行\n");
    fs.writeFileSync(path.join(dir, "rotated.log"), "長い古い行がここにある\n");
    fs.writeFileSync(path.join(dir, "note.txt"), "ログではない\n");
    const marks = markLogs(dir);
    assert.deepEqual([...marks.keys()], ["ccnavi-board.log", "ccnavi.log", "rotated.log"]);

    fs.appendFileSync(path.join(dir, "ccnavi-board.log"), "新しい行\n");
    fs.writeFileSync(path.join(dir, "rotated.log"), "短い\n");
    fs.writeFileSync(path.join(dir, "ccnavi-sh.log"), "現れた行\n");
    const got = logsSince(dir, marks);
    assert.deepEqual(
      Object.fromEntries([...got].map(([name, data]) => [name, data.toString("utf8")])),
      { "ccnavi-board.log": "新しい行\n", "ccnavi-sh.log": "現れた行\n", "rotated.log": "短い\n" },
    );
    // 置き場が無くても落ちない
    assert.equal(markLogs(path.join(dir, "missing")).size, 0);
    assert.equal(logsSince(path.join(dir, "missing"), marks).size, 0);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("CB-T1234 zip は読み戻せて、名前は UTF-8、gif と png はそのまま、テキストは縮めて入れる", () => {
  const text = Buffer.from("2026-10-29T10:00:00+09:00 INFO  ccnavi-board[1] バグ報告を始めた\n".repeat(50), "utf8");
  const gif = Buffer.from("GIF89a fake");
  const zip = buildZip(
    [
      { name: "report.txt", data: Buffer.from("説明\n") },
      { name: "logs/ccnavi-board.log", data: text },
      { name: "screen.gif", data: gif },
      { name: "screenshots/screenshot-01.png", data: Buffer.alloc(0) },
    ],
    new Date(2026, 9, 29, 10, 0, 0),
  );
  const entries = readZip(zip);
  assert.deepEqual([...entries.keys()], ["report.txt", "logs/ccnavi-board.log", "screen.gif", "screenshots/screenshot-01.png"]);
  assert.equal(entries.get("logs/ccnavi-board.log")?.data.toString("utf8"), text.toString("utf8"));
  assert.equal(entries.get("logs/ccnavi-board.log")?.method, 8);
  assert.equal(entries.get("screen.gif")?.method, 0);
  assert.deepEqual(entries.get("screen.gif")?.data, gif);
  assert.equal(entries.get("report.txt")?.data.toString("utf8"), "説明\n");
  // 既知の値（"123456789" の CRC-32）
  assert.equal(crc32(Buffer.from("123456789")), 0xcbf43926);
});

test("CB-T1235 報告の説明には録画の結果・枚数・ログ・エラーが並び、zip の名前は始めた時刻から付ける", () => {
  const text = reportText({
    started: new Date("2026-10-29T01:00:00Z"),
    finished: new Date("2026-10-29T01:05:00Z"),
    vscodeVersion: "1.105.0",
    extensionVersion: "0.12.0",
    platform: "linux 5.15",
    capture: "wsl",
    recording: "ffmpeg が見つからない",
    screenshots: 2,
    logs: [],
    errors: ["スクリーンショット: 失敗"],
  });
  assert.match(text, /録画: ffmpeg が見つからない/);
  assert.match(text, /スクリーンショット: 2 枚/);
  assert.match(text, /ログ: この間に書かれた行は無い/);
  assert.match(text, /エラー:\n- スクリーンショット: 失敗\n$/);
  assert.equal(reportStamp(new Date(2026, 9, 29, 15, 30, 12)), "20261029-153012");
});
