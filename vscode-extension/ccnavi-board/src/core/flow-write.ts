/**
 * フローのファイルを書く（設計 9.3.1、ADR-0085）。フロー編集画面の保存の、ファイルに触る部分だけ。
 *
 * 置き場は承認済みの領域（既定 `.ccnavi/approved/flows/<子>.yml`）で、人が持つ（エージェントの Write / Edit は判定が止める）。書くのは人が
 * ボードで保存したときだけ。ここはその 1 回を、リンクを辿らずに、途中で落ちても半端なファイルを残さずに書く。
 *
 * - **リンクは辿らない。** ツリーのルートからファイルまでの途中（ファイルそのものを含む）に 1 つでも
 *   シンボリックリンクがあれば書かない。辿ると、承認済みの領域の外（エージェントが書ける場所）に書いたり、
 *   外のファイルを人の手順書として置いたりすることになる。足りないディレクトリも 1 段ずつ作り、作ったものが
 *   リンクでないことを確かめる
 * - **書き込みは入れ替え。** 同じディレクトリに一時ファイルを `wx`（在れば落ちる。リンクも辿らない）で書き、
 *   `rename` で置き換える。`rename` は行き先がリンクでもリンクそのものを置き換え、指す先には書かない
 * - **読み込んでから外で変わっていない。** 在ったファイルは更新時刻が同じ、無かったファイルはまだ無い
 * - **ふつうのファイルで、名前が 1 つのものだけ。** 名前付きパイプは開くと待ち続け、ハードリンクは承認済みの
 *   領域の外の名前から書き換えられる。読むときは `O_NOFOLLOW | O_NONBLOCK` で開き、開いたものを確かめ直す
 * - **大きさは 256KB まで**（実行ファイルと同じ上限）。読むのも書くのも
 * - **一時ファイルを残さない。** 落ちても消す。消せずに残った `.*.tmp` は `ccnavi-push-approved.sh` が運ばない
 *
 * 残る隙間（TOCTOU）: 確かめてから `rename` までの間に、誰かが途中のディレクトリをリンクに差し替えれば
 * その先に書きうる。差し替えられるのは承認済みの領域を書ける者（人）だけで、エージェントの書き込みは判定が
 * 止める。`rename` の直前にもう一度確かめて、隙間を狭めてある。
 *
 * VS Code の API は使わない（単体テストで確かめる）。
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";

export type FlowWriteResult = { readonly ok: true } | { readonly ok: false; readonly error: string };

/** 読み書きするファイルの大きさの上限（バイト）。実行ファイル（`flow.FILE_LIMIT`）と同じ 256KB */
export const FLOW_FILE_LIMIT = 256 * 1024;

// Windows には無い。無ければ 0（確かめ直しだけが効く）
const O_NOFOLLOW = fs.constants.O_NOFOLLOW ?? 0;
const O_NONBLOCK = fs.constants.O_NONBLOCK ?? 0;

/** 読み込んだときのファイルの様子。無かったなら `exists: false` */
export interface FlowExpect {
  readonly exists: boolean;
  readonly mtimeMs: number;
}

function code(error: unknown): string {
  return (error as NodeJS.ErrnoException).code ?? "";
}

/** tree の下の相対の各段。tree の下でなければ undefined */
function segments(tree: string, file: string): readonly string[] | undefined {
  if (tree === "" || !path.isAbsolute(tree) || !path.isAbsolute(file)) {
    return undefined;
  }
  const rel = path.relative(tree, file);
  if (rel === "" || rel === ".." || rel.startsWith(`..${path.sep}`) || path.isAbsolute(rel)) {
    return undefined;
  }
  return rel.split(path.sep).filter((s) => s !== "");
}

/**
 * tree から file までの途中（file 自身を含む）で最初に見つかったシンボリックリンクの綴り。無ければ undefined。
 * 無い段から先は見ない（まだ無いものはリンクではない）。file が tree の下に無ければ file 自身を返す（書かない側）。
 */
export function linkedSegment(tree: string, file: string): string | undefined {
  const parts = segments(tree, file);
  if (parts === undefined) {
    return file;
  }
  let current = tree;
  for (const part of parts) {
    current = path.join(current, part);
    let stat: fs.Stats;
    try {
      stat = fs.lstatSync(current);
    } catch (error) {
      if (code(error) === "ENOENT") {
        return undefined;
      }
      return current;
    }
    if (stat.isSymbolicLink()) {
      return current;
    }
  }
  return undefined;
}

/** 足りないディレクトリを 1 段ずつ作る。作った・在ったものがリンクかディレクトリでなければ止める */
function makeDirs(tree: string, dir: string): string | undefined {
  const parts = segments(tree, dir);
  if (parts === undefined) {
    return `ツリーの外には書かない（${dir}）`;
  }
  let current = tree;
  for (const part of parts) {
    current = path.join(current, part);
    try {
      fs.mkdirSync(current);
    } catch (error) {
      if (code(error) !== "EEXIST") {
        return `ディレクトリを作れない（${current}）: ${(error as Error).message}`;
      }
    }
    const stat = fs.lstatSync(current);
    if (stat.isSymbolicLink() || !stat.isDirectory()) {
      return `${current} がシンボリックリンクかディレクトリでないので書かない`;
    }
  }
  return undefined;
}

function linkedError(where: string): string {
  return `${where} がシンボリックリンクなので書かない（リンクの先は承認済みの領域の外かもしれない）。リンクを外してから保存する`;
}

/**
 * フローのファイルを書く。リンクを辿らず、読み込んだときから外で変わっていなければ、一時ファイルの入れ替えで書く。
 */
export function writeFlowFile(tree: string, file: string, text: string, expect: FlowExpect): FlowWriteResult {
  if (segments(tree, file) === undefined) {
    return { ok: false, error: `ツリーの外には書かない（${file}）` };
  }
  const linked = linkedSegment(tree, file);
  if (linked !== undefined) {
    return { ok: false, error: linkedError(linked) };
  }
  const dir = path.dirname(file);
  const made = makeDirs(tree, dir);
  if (made !== undefined) {
    return { ok: false, error: made };
  }
  const changed = changedSince(file, expect);
  if (changed !== undefined) {
    return { ok: false, error: changed };
  }
  const size = Buffer.byteLength(text, "utf8");
  if (size > FLOW_FILE_LIMIT) {
    return { ok: false, error: `フローが大きすぎるので書かない（${size} バイト、上限 ${FLOW_FILE_LIMIT}）。実行ファイルはこれより大きいフローを読まない` };
  }
  const temp = path.join(dir, `.${path.basename(file)}.${process.pid}.${crypto.randomBytes(6).toString("hex")}.tmp`);
  let renamed = false;
  try {
    try {
      fs.writeFileSync(temp, text, { encoding: "utf8", flag: "wx" });
    } catch (error) {
      return { ok: false, error: `フローのファイルに書けない: ${(error as Error).message}` };
    }
    // 入れ替えの直前にもう一度確かめる（確かめてから入れ替えるまでの隙間を狭める）
    const relinked = linkedSegment(tree, file);
    const again = relinked !== undefined ? linkedError(relinked) : changedSince(file, expect);
    if (again !== undefined) {
      return { ok: false, error: again };
    }
    try {
      fs.renameSync(temp, file);
      renamed = true;
    } catch (error) {
      return { ok: false, error: `フローのファイルに書けない: ${(error as Error).message}` };
    }
    return { ok: true };
  } finally {
    // 途中で落ちても（例外も含めて）一時ファイルを残さない
    if (!renamed) {
      try {
        fs.rmSync(temp, { force: true });
      } catch {
        // 消せなければ残る。push の sh は `.*.tmp` を運ばない
      }
    }
  }
}

/** 読み込んでから外で変わったなら理由。変わっていなければ undefined */
function changedSince(file: string, expect: FlowExpect): string | undefined {
  let stat: fs.Stats | undefined;
  try {
    stat = fs.lstatSync(file);
  } catch (error) {
    if (code(error) !== "ENOENT") {
      return `フローのファイルを確かめられない: ${(error as Error).message}`;
    }
  }
  if (!expect.exists) {
    return stat === undefined ? undefined : "フローのファイルが読み込んだあとに外で作られている。再読込してから編集し直す（上書きしない）";
  }
  if (stat === undefined) {
    return "フローのファイルが読み込んだあとに外で消されている。再読込してから編集し直す";
  }
  if (stat.isSymbolicLink() || !stat.isFile()) {
    return `${file} がシンボリックリンクかファイルでないので書かない`;
  }
  if (stat.nlink > 1) {
    return hardLinkedError(file, "書かない");
  }
  if (stat.mtimeMs !== expect.mtimeMs) {
    return "フローのファイルが読み込んだあとに外で変更されている。再読込してから編集し直す（この変更は上書きしない）";
  }
  return undefined;
}

/**
 * 読んだバイトを文字にする。UTF-8 として壊れていれば読まない（置き換え文字で埋めると、壊れた部分を落として
 * 書き直すことになる）。先頭の BOM は 1 つ外す（実行ファイルの `utf-8-sig` と同じ）。文面は実行ファイル
 * （`flow.NOT_UTF8`）に揃える
 */
export function decodeFlowBytes(bytes: Uint8Array): { readonly ok: true; readonly text: string } | { readonly ok: false; readonly error: string } {
  try {
    return { ok: true, text: new TextDecoder("utf-8", { fatal: true }).decode(bytes) };
  } catch {
    return { ok: false, error: "UTF-8 として読めない" };
  }
}

/**
 * 読む。リンクを辿らない。無ければ undefined（フローは任意）、読めなければ例外。中身はバイトのまま返す
 * （開くときは、このバイトのまま実行ファイルに確かめさせ、文字にするのは `decodeFlowBytes`）
 */
export function readFlowFile(tree: string, file: string): { readonly bytes: Uint8Array; readonly mtimeMs: number } | undefined {
  if (segments(tree, file) === undefined) {
    throw new Error(`ツリーの外は読まない（${file}）`);
  }
  const linked = linkedSegment(tree, file);
  if (linked !== undefined) {
    throw new Error(`${linked} がシンボリックリンクなので読まない（書きもしない）`);
  }
  let stat: fs.Stats;
  try {
    stat = fs.lstatSync(file);
  } catch (error) {
    if (code(error) === "ENOENT") {
      return undefined;
    }
    throw error;
  }
  if (!stat.isFile()) {
    throw new Error(`${file} がふつうのファイルでない（名前付きパイプ・デバイスなど）ので読まない`);
  }
  if (stat.nlink > 1) {
    throw new Error(hardLinkedError(file, "読まない（書きもしない）"));
  }
  if (stat.size > FLOW_FILE_LIMIT) {
    throw new Error(`${file} が大きすぎるので読まない（${stat.size} バイト、上限 ${FLOW_FILE_LIMIT}）`);
  }
  // 確かめてから開くまでに差し替えられても、開いたものを確かめ直す。名前付きパイプでも待たない
  const fd = fs.openSync(file, fs.constants.O_RDONLY | O_NOFOLLOW | O_NONBLOCK);
  try {
    const opened = fs.fstatSync(fd);
    if (!opened.isFile() || opened.ino !== stat.ino || opened.dev !== stat.dev) {
      throw new Error(`${file} が開くあいだに別のファイルに差し替わったので読まない`);
    }
    if (opened.nlink > 1) {
      throw new Error(hardLinkedError(file, "読まない（書きもしない）"));
    }
    const buffer = Buffer.alloc(FLOW_FILE_LIMIT + 1);
    let length = 0;
    while (length < buffer.length) {
      const got = fs.readSync(fd, buffer, length, buffer.length - length, null);
      if (got === 0) {
        break;
      }
      length += got;
    }
    if (length > FLOW_FILE_LIMIT) {
      throw new Error(`${file} が大きすぎるので読まない（上限 ${FLOW_FILE_LIMIT} バイト）`);
    }
    return { bytes: Uint8Array.from(buffer.subarray(0, length)), mtimeMs: opened.mtimeMs };
  } finally {
    fs.closeSync(fd);
  }
}

function hardLinkedError(file: string, what: string): string {
  return `${file} はハードリンク（ほかの名前からも同じ中身に届く）なので${what}。承認済みの領域の外の名前から書き換えられうる。リンクを外してから開き直す`;
}
