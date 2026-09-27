/**
 * 診断ログ。ワークスペースルートの `logs/diag/<出どころ>.log` に 1 行ずつ足す（docs/claude/logging.md）。
 *
 * sh（`.ccnavi/scripts/ccnavi-common.sh` の log_*）と実行ファイル（`ccnavi/diaglog.py`）と
 * 同じ形の行を出す。
 *
 *     2026-09-27T10:15:03+09:00 ERROR ccnavi-board[4242] 画面の前提が崩れている screen=rules
 *
 * **画面にも console にも何も出さない。** 書けないときは黙って捨て、例外を外へ出さない。
 * 利用者に見せる通知（showErrorMessage など）とは別物で、そちらはこのモジュールと関係なく書く。
 *
 * 秘密を伏せる仕掛けは持たない。秘密の値・ファイルの中身・環境変数の値を渡さないのが決まり。
 *
 * node の型を剥がすだけで動く書き方にしてある（enum も引数のプロパティも使わない）。
 * Python のテスト（tests/core/test_diaglog.py）が `node` で直に読み、3 つの言語の行を比べる。
 */
import * as fs from "node:fs";
import * as path from "node:path";

export const LEVEL_ENV = "CCNAVI_LOG_LEVEL";

export type Level = "DEBUG" | "INFO" | "WARN" | "ERROR";

const RANK: Readonly<Record<Level, number>> = { DEBUG: 10, INFO: 20, WARN: 30, ERROR: 40 };

/** 値に使える形。null と undefined は空、真偽は true / false（Python と揃える） */
export type Value = string | number | boolean | null | undefined;
export type Fields = Readonly<Record<string, Value>>;

export interface Logger {
  enabled(level: Level): boolean;
  debug(msg: string, fields?: Fields): void;
  info(msg: string, fields?: Fields): void;
  warn(msg: string, fields?: Fields): void;
  error(msg: string, fields?: Fields): void;
}

/** CCNAVI_LOG_LEVEL から閾値を読む。空と読めない値は INFO */
export function threshold(env: NodeJS.ProcessEnv = process.env): Level {
  const word = (env[LEVEL_ENV] ?? "").toUpperCase();
  return word === "DEBUG" || word === "INFO" || word === "WARN" || word === "ERROR" ? word : "INFO";
}

/**
 * 出どころ `name` の書き手。`root` はワークスペースルート。空なら何も書かない。
 */
export function get(name: string, root: string): Logger {
  const min = RANK[threshold()];
  const file = path.join(root, "logs", "diag", `${name}.log`);
  const emit = (level: Level, msg: string, fields: Fields | undefined): void => {
    if (RANK[level] < min || root === "") {
      return;
    }
    try {
      append(file, formatLine(stamp(new Date()), level, name, process.pid, msg, fields ?? {}));
    } catch {
      // ログの失敗で本体を止めない
    }
  };
  return {
    enabled: (level) => RANK[level] >= min,
    debug: (msg, fields) => emit("DEBUG", msg, fields),
    info: (msg, fields) => emit("INFO", msg, fields),
    warn: (msg, fields) => emit("WARN", msg, fields),
    error: (msg, fields) => emit("ERROR", msg, fields),
  };
}

function two(n: number): string {
  return String(n).padStart(2, "0");
}

/** 現地時刻と時差を秒まで。`2026-09-27T10:15:03+09:00` */
export function stamp(when: Date): string {
  const offset = -when.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const abs = Math.abs(offset);
  return (
    `${when.getFullYear()}-${two(when.getMonth() + 1)}-${two(when.getDate())}` +
    `T${two(when.getHours())}:${two(when.getMinutes())}:${two(when.getSeconds())}` +
    `${sign}${two(Math.floor(abs / 60))}:${two(abs % 60)}`
  );
}

/** 値を文字にする */
export function text(value: Value): string {
  if (value === null || value === undefined) {
    return "";
  }
  return String(value);
}

/** 改行（CR LF・CR・LF）を `\n` の 2 字に畳む */
export function fold(value: string): string {
  return value.replace(/\r\n|\r|\n/g, "\\n");
}

/** logfmt の値。空白・タブ・`"`・`=`・改行を含めば囲み、`\` と `"` を逃がす */
export function quote(value: string): string {
  if (!/[ \t"=\r\n]/.test(value)) {
    return value;
  }
  return `"${fold(value.replace(/\\/g, "\\\\").replace(/"/g, '\\"'))}"`;
}

const LABEL: Readonly<Record<Level, string>> = { DEBUG: "DEBUG", INFO: "INFO ", WARN: "WARN ", ERROR: "ERROR" };

/** 1 行（改行を含まない）。値は渡された順に並べる */
export function formatLine(when: string, level: Level, name: string, pid: number, msg: string, fields: Fields): string {
  const tail = Object.entries(fields)
    .map(([key, value]) => ` ${key}=${quote(text(value))}`)
    .join("");
  return `${when} ${LABEL[level]} ${name}[${pid}] ${fold(msg)}${tail}`;
}

/** 1 行を O_APPEND で 1 度に書く。置き場が無ければ作る */
function append(file: string, line: string): void {
  const data = `${line}\n`;
  try {
    fs.appendFileSync(file, data, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      throw error;
    }
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.appendFileSync(file, data, "utf8");
  }
}
