/**
 * バグ報告（サイドパネルの「バグ報告」）の、VS Code に触れない部分。
 *
 * - どの OS の取り方を使うか（Windows・macOS・WSL。ほかは録画とスクリーンショットをしない）
 * - ffmpeg に渡す引数（録画と gif への変換）と、スクリーンショットを撮るコマンド
 * - 診断ログ（`logs/diag/*.log`）のうち、報告を始めてから足された行の切り出し
 * - zip の組み立て（依存を増やさないため、ここで書く）
 *
 * ffmpeg は利用者に入れてもらう。見つからなければ録画だけをやめ、スクリーンショットとログの zip は作る。
 * 子プロセスを起動するのは src/bug-report.ts で、ここはコマンドと引数を組むだけ。
 */
import * as fs from "node:fs";
import * as path from "node:path";
import * as zlib from "node:zlib";

/** 画面の取り方の種類。`none` は録画もスクリーンショットもしない（ログだけ） */
export type Capture = "windows" | "mac" | "wsl" | "none";

/**
 * 拡張ホストが動いている OS から取り方を決める。
 * WSL では拡張ホストが Linux 側で動くので、Windows の ffmpeg.exe と powershell.exe を相互運用で呼ぶ。
 * `procVersion` は `/proc/version` の中身（読めなければ空）。
 */
export function captureKind(platform: string, env: NodeJS.ProcessEnv, procVersion: string): Capture {
  if (platform === "win32") {
    return "windows";
  }
  if (platform === "darwin") {
    return "mac";
  }
  if (platform === "linux" && ((env.WSL_DISTRO_NAME ?? "") !== "" || /microsoft/i.test(procVersion))) {
    return "wsl";
  }
  return "none";
}

/** 録画の fps。gif は大きくなりやすいので低めに置く */
export const RECORD_FPS = 10;
/** 録画の長さの上限（秒）。終了を忘れたときや拡張ホストが落ちたときに、録り続けないため */
export const RECORD_MAX_SECONDS = 600;
/** 録画の幅の上限（px）。これより広い画面は縮めて録る */
export const RECORD_MAX_WIDTH = 1920;
/** gif の fps と幅の上限 */
export const GIF_FPS = 8;
export const GIF_MAX_WIDTH = 1280;

/**
 * ffmpeg を探す候補。設定 `ccnaviBoard.ffmpegPath` があればそれだけを使う。
 * macOS では、Dock から起動した VS Code の PATH に Homebrew が入っていないことがあるので、置き場を候補に足す。
 */
export function ffmpegCandidates(kind: Capture, configured: string): string[] {
  if (configured.trim() !== "") {
    return [configured.trim()];
  }
  switch (kind) {
    case "windows":
      return ["ffmpeg"];
    case "wsl":
      return ["ffmpeg.exe"];
    case "mac":
      return ["ffmpeg", "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"];
    case "none":
      return [];
  }
}

/** 画面全体を録る入力の引数。`none` では録らない */
function inputArgs(kind: Capture): string[] {
  switch (kind) {
    case "windows":
    case "wsl":
      return ["-f", "gdigrab", "-framerate", String(RECORD_FPS), "-draw_mouse", "1", "-i", "desktop"];
    case "mac":
      return ["-f", "avfoundation", "-framerate", String(RECORD_FPS), "-capture_cursor", "1", "-capture_mouse_clicks", "1", "-i", "Capture screen 0:none"];
    case "none":
      return [];
  }
}

/**
 * 録画の引数。止めるときは標準入力に `q` を送る。
 * 途中で切れても読めるよう mkv に書き、縦横は偶数にそろえる（yuv420p の条件）。`output` は ffmpeg から見たパス。
 */
export function recordArgs(kind: Capture, output: string): string[] {
  return [
    "-hide_banner",
    "-loglevel",
    "error",
    "-y",
    ...inputArgs(kind),
    "-vf",
    `scale='trunc(min(${RECORD_MAX_WIDTH},iw)/2)*2':-2,format=yuv420p`,
    "-c:v",
    "libx264",
    "-preset",
    "ultrafast",
    "-crf",
    "28",
    "-t",
    String(RECORD_MAX_SECONDS),
    output,
  ];
}

/** 録画を gif にする引数。色は先に作ったパレットで塗る（既定の 256 色より文字が読める） */
export function gifArgs(input: string, output: string): string[] {
  return [
    "-hide_banner",
    "-loglevel",
    "error",
    "-y",
    "-i",
    input,
    "-vf",
    `fps=${GIF_FPS},scale='min(${GIF_MAX_WIDTH},iw)':-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle`,
    output,
  ];
}

/** 起動するコマンドと引数 */
export interface Command {
  readonly file: string;
  readonly args: readonly string[];
}

/**
 * Windows の画面全体（複数の画面をまたぐ仮想画面）を png に撮る PowerShell。
 * 拡大率が 100% 以外の機械で一部だけが撮れないよう、先に DPI を認識させる。
 */
function powershellScript(output: string): string {
  const quoted = `'${output.replace(/'/g, "''")}'`;
  return [
    "$ErrorActionPreference = 'Stop'",
    "Add-Type -TypeDefinition 'using System.Runtime.InteropServices; public static class CcnaviDpi { [DllImport(\"user32.dll\")] public static extern bool SetProcessDPIAware(); }'",
    "[void][CcnaviDpi]::SetProcessDPIAware()",
    "Add-Type -AssemblyName System.Windows.Forms, System.Drawing",
    "$b = [System.Windows.Forms.SystemInformation]::VirtualScreen",
    "$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height",
    "$g = [System.Drawing.Graphics]::FromImage($bmp)",
    "$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)",
    `$bmp.Save(${quoted}, [System.Drawing.Imaging.ImageFormat]::Png)`,
    "$g.Dispose(); $bmp.Dispose()",
  ].join("\n");
}

/**
 * スクリーンショットを撮るコマンド。ffmpeg は使わず、OS に元からある道具で撮る。
 * `output` はそのコマンドから見たパス（WSL なら Windows の形に直してから渡す）。
 * スクリプトは -EncodedCommand（UTF-16LE の base64）で渡し、パスの引用符の扱いに左右されないようにする。
 */
export function screenshotCommand(kind: Capture, output: string): Command | undefined {
  switch (kind) {
    case "mac":
      return { file: "screencapture", args: ["-x", "-t", "png", output] };
    case "windows":
    case "wsl": {
      const encoded = Buffer.from(powershellScript(output), "utf16le").toString("base64");
      return {
        file: kind === "wsl" ? "powershell.exe" : "powershell",
        args: ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
      };
    }
    case "none":
      return undefined;
  }
}

/** 診断ログの置き場（ワークスペースルートから） */
export function diagDir(root: string): string {
  return path.join(root, "logs", "diag");
}

/** 報告を始めたときの、ログのファイルごとの大きさ（名前 → バイト数） */
export type LogMarks = ReadonlyMap<string, number>;

function logFiles(dir: string): string[] {
  try {
    return fs
      .readdirSync(dir, { withFileTypes: true })
      .filter((entry) => entry.isFile() && entry.name.endsWith(".log"))
      .map((entry) => entry.name)
      .sort();
  } catch {
    return [];
  }
}

/** いまの大きさを控える。置き場が無ければ空 */
export function markLogs(dir: string): LogMarks {
  const marks = new Map<string, number>();
  for (const name of logFiles(dir)) {
    try {
      marks.set(name, fs.statSync(path.join(dir, name)).size);
    } catch {
      // 控えられなかったファイルは、終わりに全体を取る
    }
  }
  return marks;
}

/**
 * 控えてから足されたぶんを、ファイルごとに返す。足されていないファイルは返さない。
 * 控えたときより小さくなった（作り直された）ファイルと、新しく現れたファイルは全体を取る。
 */
export function logsSince(dir: string, marks: LogMarks): Map<string, Buffer> {
  const out = new Map<string, Buffer>();
  for (const name of logFiles(dir)) {
    let data: Buffer;
    try {
      data = fs.readFileSync(path.join(dir, name));
    } catch {
      continue;
    }
    const from = marks.get(name) ?? 0;
    const added = data.length >= from ? data.subarray(from) : data;
    if (added.length > 0) {
      out.set(name, added);
    }
  }
  return out;
}

/** 報告の名前に使う時刻。`20261029-153012` */
export function reportStamp(when: Date): string {
  const two = (n: number): string => String(n).padStart(2, "0");
  return `${when.getFullYear()}${two(when.getMonth() + 1)}${two(when.getDate())}-${two(when.getHours())}${two(when.getMinutes())}${two(when.getSeconds())}`;
}

/** zip に入れる 1 件。`name` は `/` 区切り */
export interface ZipEntry {
  readonly name: string;
  readonly data: Buffer;
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c >>> 0;
  }
  return table;
})();

export function crc32(data: Buffer): number {
  let c = 0xffffffff;
  for (const byte of data) {
    c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
  }
  return (c ^ 0xffffffff) >>> 0;
}

/** zip の日付と時刻（DOS の形。現地時刻） */
function dosTime(when: Date): { time: number; date: number } {
  return {
    time: (when.getHours() << 11) | (when.getMinutes() << 5) | Math.floor(when.getSeconds() / 2),
    date: ((Math.max(when.getFullYear(), 1980) - 1980) << 9) | ((when.getMonth() + 1) << 5) | when.getDate(),
  };
}

/** 縮めても効果が無い形式（gif・png は既に圧縮されている）。そのまま入れる */
const STORED = /\.(gif|png|jpe?g|zip|mkv|mp4)$/i;

/**
 * zip を組む。名前は UTF-8（汎用フラグの bit 11）。gif・png はそのまま、ほかは deflate で縮める。
 * 1 件 4 GiB を超えるものは扱わない（ZIP64 は書かない）。
 */
export function buildZip(entries: readonly ZipEntry[], when: Date): Buffer {
  const { time, date } = dosTime(when);
  const locals: Buffer[] = [];
  const centrals: Buffer[] = [];
  let offset = 0;
  for (const entry of entries) {
    const name = Buffer.from(entry.name, "utf8");
    const store = STORED.test(entry.name);
    const body = store ? entry.data : zlib.deflateRawSync(entry.data);
    const method = store ? 0 : 8;
    const crc = crc32(entry.data);

    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0800, 6);
    local.writeUInt16LE(method, 8);
    local.writeUInt16LE(time, 10);
    local.writeUInt16LE(date, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(body.length, 18);
    local.writeUInt32LE(entry.data.length, 22);
    local.writeUInt16LE(name.length, 26);
    local.writeUInt16LE(0, 28);
    locals.push(local, name, body);

    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0x0800, 8);
    central.writeUInt16LE(method, 10);
    central.writeUInt16LE(time, 12);
    central.writeUInt16LE(date, 14);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(body.length, 20);
    central.writeUInt32LE(entry.data.length, 24);
    central.writeUInt16LE(name.length, 28);
    central.writeUInt32LE(offset, 42);
    centrals.push(central, name);

    offset += local.length + name.length + body.length;
  }
  const directory = Buffer.concat(centrals);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, directory, end]);
}

/** 報告に添える説明（report.txt）の中身 */
export interface ReportInfo {
  readonly started: Date;
  readonly finished: Date;
  readonly vscodeVersion: string;
  readonly extensionVersion: string;
  readonly platform: string;
  readonly capture: Capture;
  /** 録画の結果。gif を入れたか、入れなかった理由 */
  readonly recording: string;
  readonly screenshots: number;
  readonly logs: readonly string[];
  /** ffmpeg やスクリーンショットのコマンドが出したエラー（そのまま） */
  readonly errors: readonly string[];
}

export function reportText(info: ReportInfo): string {
  const lines = [
    "ccnavi ボードのバグ報告",
    "",
    `開始: ${info.started.toISOString()}`,
    `終了: ${info.finished.toISOString()}`,
    `VS Code: ${info.vscodeVersion}`,
    `拡張: ${info.extensionVersion}`,
    `OS: ${info.platform}（画面の取り方: ${info.capture}）`,
    `録画: ${info.recording}`,
    `スクリーンショット: ${info.screenshots} 枚`,
    `ログ: ${info.logs.length === 0 ? "この間に書かれた行は無い" : info.logs.join(", ")}`,
  ];
  if (info.errors.length > 0) {
    lines.push("", "エラー:", ...info.errors.map((error) => `- ${error}`));
  }
  return `${lines.join("\n")}\n`;
}
