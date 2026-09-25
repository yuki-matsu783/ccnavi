/**
 * フローのファイルを書く（設計 9.3.1、ADR-0085）。フロー編集画面の保存の、ファイルに触る部分だけ。
 *
 * 置き場は承認済みの領域（既定 `.ccnavi/approved/flows/<子>.json`）で、エージェントは書けない。書くのは人が
 * ボードで保存したときだけ。ここはその 1 回を、リンクを辿らずに、途中で落ちても半端なファイルを残さずに書く。
 *
 * - **リンクは辿らない。** ツリーのルートからファイルまでの途中（ファイルそのものを含む）に 1 つでも
 *   シンボリックリンクがあれば書かない。辿ると、承認済みの領域の外（エージェントが書ける場所）に書いたり、
 *   外のファイルを人の手順書として置いたりすることになる。足りないディレクトリも 1 段ずつ作り、作ったものが
 *   リンクでないことを確かめる
 * - **書き込みは入れ替え。** 同じディレクトリに一時ファイルを `wx`（在れば落ちる。リンクも辿らない）で書き、
 *   `rename` で置き換える。`rename` は行き先がリンクでもリンクそのものを置き換え、指す先には書かない
 * - **読み込んでから外で変わっていない。** 在ったファイルは更新時刻が同じ、無かったファイルはまだ無い
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
  const temp = path.join(dir, `.${path.basename(file)}.${process.pid}.${crypto.randomBytes(6).toString("hex")}.tmp`);
  try {
    fs.writeFileSync(temp, text, { encoding: "utf8", flag: "wx" });
  } catch (error) {
    return { ok: false, error: `フローのファイルに書けない: ${(error as Error).message}` };
  }
  // 入れ替えの直前にもう一度確かめる（確かめてから入れ替えるまでの隙間を狭める）
  const relinked = linkedSegment(tree, file);
  const again = relinked !== undefined ? linkedError(relinked) : changedSince(file, expect);
  if (again !== undefined) {
    fs.rmSync(temp, { force: true });
    return { ok: false, error: again };
  }
  try {
    fs.renameSync(temp, file);
  } catch (error) {
    fs.rmSync(temp, { force: true });
    return { ok: false, error: `フローのファイルに書けない: ${(error as Error).message}` };
  }
  return { ok: true };
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
  if (stat.mtimeMs !== expect.mtimeMs) {
    return "フローのファイルが読み込んだあとに外で変更されている。再読込してから編集し直す（この変更は上書きしない）";
  }
  return undefined;
}

/** 読む。リンクを辿らない。無ければ undefined（フローは任意）、読めなければ例外 */
export function readFlowFile(tree: string, file: string): { readonly text: string; readonly mtimeMs: number } | undefined {
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
    throw new Error(`${file} がファイルでないので読まない`);
  }
  return { text: fs.readFileSync(file, "utf8"), mtimeMs: stat.mtimeMs };
}
