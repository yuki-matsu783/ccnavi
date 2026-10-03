/**
 * 診断ログ。ワークスペースルートの `logs/diag/<出どころ>.log` に 1 行ずつ足す（docs/claude/logging.md）。
 *
 * sh（`.ccnavi/scripts/ccnavi-common.sh` の log_*）と実行ファイル（`ccnavi/records/diaglog.py`）と
 * 同じ形の行を出す。
 *
 *     2026-09-27T10:15:03+09:00 ERROR ccnavi-board[4242] 画面の前提が崩れている screen=rules
 *
 * **画面にも console にも何も出さない。** 書けないときは何も出さずに捨て、例外を外へ出さない。
 * ユーザに見せる通知（showErrorMessage など）とは分けてあり、そちらはこのモジュールと関係なく書く。
 *
 * 伏せるのは URL と scp 形式に埋まった資格情報だけ（maskUserinfo。sh と Python と同じ規則で `***` にする）。
 * ほかの秘密の形は伏せない。秘密の値・ファイルの中身・環境変数の値を渡さないのが決まり。
 *
 * **シンボリックリンクはたどらない。** `logs`・`logs/diag`・書き込み先のどれかがシンボリックリンクなら書かずに捨てる
 * （lstat で見て、書き込み先は O_NOFOLLOW のある OS ではそれでも開く）。ファイルは 0600 で作る。
 * 出どころの名前が `[A-Za-z0-9_-]` 以外を含むときも書かない。
 *
 * node の型を取り除くだけで動く書き方にしてある（enum も引数のプロパティも使わない）。
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
  const usable = root !== "" && NAME.test(name);
  const emit = (level: Level, msg: string, fields: Fields | undefined): void => {
    if (RANK[level] < min || !usable) {
      return;
    }
    try {
      append(file, formatLine(stamp(new Date()), level, name, process.pid, maskUserinfo(msg), masked(fields ?? {})));
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

/** 出どころに使える名前。これ以外の字を含む名前では書かない */
const NAME = /^[A-Za-z0-9_-]+$/;

const MASK = "***";

/** 値を文字にしてから伏せる。null と undefined は空のまま */
function masked(fields: Fields): Fields {
  const out: Record<string, Value> = {};
  for (const [key, value] of Object.entries(fields)) {
    out[key] = maskUserinfo(text(value));
  }
  return out;
}

/**
 * URL と scp 形に埋まった資格情報を `***` にする（sh の ccnavi_log_mask、Python の mask_userinfo と同じ読み）。
 * 空白・タブ・LF・CR で切った語ごとに見る。`://` を含む語は authority（次の `/` まで）の最後の `@` より前を、
 * 含まない語は最初の `/` より前の最後の `@` より前に `:` があればそこを伏せる。
 */
export function maskUserinfo(value: string): string {
  if (!value.includes("@")) {
    return value;
  }
  return value
    .split(/([ \t\n\r]+)/)
    .map((part, i) => (i % 2 === 1 ? part : maskWord(part)))
    .join("");
}

function maskWord(word: string): string {
  if (!word.includes("@")) {
    return word;
  }
  if (!word.includes("://")) {
    const head = word.split("/", 1)[0];
    const at = head.lastIndexOf("@");
    if (at < 0) {
      return word;
    }
    const user = head.slice(0, at);
    return user.includes(":") ? `${MASK}@${word.slice(at + 1)}` : word;
  }
  let out = "";
  let rest = word;
  for (let sep = rest.indexOf("://"); sep >= 0; sep = rest.indexOf("://")) {
    out += `${rest.slice(0, sep)}://`;
    rest = rest.slice(sep + 3);
    const slash = rest.indexOf("/");
    const authority = slash < 0 ? rest : rest.slice(0, slash);
    const at = authority.lastIndexOf("@");
    if (at >= 0) {
      out += `${MASK}@${authority.slice(at + 1)}`;
      rest = rest.slice(authority.length);
    }
  }
  return out + rest;
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

/** 改行（CR LF・CR・LF）を `\n` の 2 字に置き換える */
export function fold(value: string): string {
  return value.replace(/\r\n|\r|\n/g, "\\n");
}

/** logfmt の値。空白・タブ・`"`・`=`・改行を含めば囲み、`\` と `"` の前に `\` をつける */
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

function isLink(target: string): boolean {
  try {
    return fs.lstatSync(target).isSymbolicLink();
  } catch {
    return false;
  }
}

/**
 * 1 行を O_APPEND で 1 度に書く。置き場が無ければ作る。
 * `logs`・`logs/diag`・書き込み先のどれかがリンクなら書かない。新しいファイルは 0600
 */
function append(file: string, line: string): void {
  const dir = path.dirname(file);
  if (isLink(path.dirname(dir)) || isLink(dir) || isLink(file)) {
    return;
  }
  const c = fs.constants;
  const flags = c.O_WRONLY | c.O_APPEND | c.O_CREAT | (c.O_NOFOLLOW ?? 0);
  let fd: number;
  try {
    fd = fs.openSync(file, flags, 0o600);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      throw error;
    }
    fs.mkdirSync(dir, { recursive: true });
    fd = fs.openSync(file, flags, 0o600);
  }
  try {
    fs.writeSync(fd, `${line}\n`);
  } finally {
    fs.closeSync(fd);
  }
}
