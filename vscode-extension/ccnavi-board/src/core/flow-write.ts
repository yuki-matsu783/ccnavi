/**
 * フローのファイルを書く（設計 9.3.1、ADR-0085）。フロー編集画面の保存の、ファイルに触る部分だけ。
 *
 * 置き場は承認済みの領域（既定 `.ccnavi/approved/flows/<子>.yml`）で、ユーザが持つ（エージェントの Write / Edit は判定が止める）。書くのはユーザが
 * ボードで保存したときだけ。ここはその 1 回を、リンクを辿らずに、途中で落ちても半端なファイルを残さずに書く。
 *
 * - **リンクは辿らない。** ツリーのルートからファイルまでの途中（ファイルそのものを含む）に 1 つでも
 *   シンボリックリンクがあれば書かない。辿ると、承認済みの領域の外（エージェントが書ける場所）に書いたり、
 *   外のファイルをユーザの手順書として置いたりすることになる。足りないディレクトリも 1 段ずつ作り、作ったものが
 *   リンクでないことを確かめる
 * - **書き込みは入れ替え。** 同じディレクトリに一時ファイルを `wx`（在れば失敗する。リンクも辿らない）で書き、
 *   `rename` で置き換える。`rename` は行き先がリンクでもリンクそのものを置き換え、指す先には書かない
 * - **読み込んでから外で変わっていない。** 在ったファイルは更新時刻が同じ、無かったファイルはまだ無い
 * - **ふつうのファイルで、名前が 1 つのものだけ。** 名前付きパイプは開くと待ち続け、ハードリンクは承認済みの
 *   領域の外の名前から書き換えられる。読むときは `O_NOFOLLOW | O_NONBLOCK` で開き、開いたものを確かめ直す
 * - **大きさは 256KB まで**（実行ファイルと同じ上限）。読むのも書くのも
 * - **一時ファイルを残さない。** 落ちても消す。消せずに残った `.*.tmp` は `ccnavi-push-approved.sh` が運ばない
 *
 * 残る隙間（TOCTOU）: 確かめてから `rename` までの間に、誰かが途中のディレクトリをリンクに差し替えれば
 * その先に書きうる。差し替えられるのは承認済みの領域を書ける者（ユーザ）だけで、エージェントの書き込みは判定が
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

// Windows には無い。無ければ 0（確かめ直しだけが有効）
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
 * tree から file までの途中（file 自身を含む）で最初に見つかったシンボリックリンクのパス。無ければ undefined。
 * 無い段から先は見ない（まだ無いものはリンクではない）。file が tree の下に無ければ file 自身を返す（書かない扱い）。
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
    return `ツリーの外には書き込みません（${dir}）`;
  }
  let current = tree;
  for (const part of parts) {
    current = path.join(current, part);
    try {
      fs.mkdirSync(current);
    } catch (error) {
      if (code(error) !== "EEXIST") {
        return `ディレクトリを作れません（${current}）: ${(error as Error).message}`;
      }
    }
    const stat = fs.lstatSync(current);
    if (stat.isSymbolicLink() || !stat.isDirectory()) {
      return `${current} がシンボリックリンクか、ディレクトリではないため、書き込みません`;
    }
  }
  return undefined;
}

function linkedError(where: string): string {
  return `${where} がシンボリックリンクのため、書き込みません（リンク先は承認済みの領域の外かもしれません）。リンクを外してから保存してください`;
}

/**
 * フローのファイルを書く。リンクを辿らず、読み込んだときから外で変わっていなければ、一時ファイルの入れ替えで書く。
 */
export function writeFlowFile(tree: string, file: string, text: string, expect: FlowExpect): FlowWriteResult {
  if (segments(tree, file) === undefined) {
    return { ok: false, error: `ツリーの外には書き込みません（${file}）` };
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
    return { ok: false, error: `フローが大きすぎるため、書き込みません（${size} バイト、上限 ${FLOW_FILE_LIMIT}）。実行ファイルはこれより大きいフローを読みません` };
  }
  const temp = path.join(dir, `.${path.basename(file)}.${process.pid}.${crypto.randomBytes(6).toString("hex")}.tmp`);
  let renamed = false;
  try {
    try {
      fs.writeFileSync(temp, text, { encoding: "utf8", flag: "wx" });
    } catch (error) {
      return { ok: false, error: `フローのファイルに書けません: ${(error as Error).message}` };
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
      return { ok: false, error: `フローのファイルに書けません: ${(error as Error).message}` };
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
      return `フローのファイルを確かめられません: ${(error as Error).message}`;
    }
  }
  if (!expect.exists) {
    return stat === undefined ? undefined : "フローのファイルは、読み込んだあとに画面の外で作られています。再読込してから編集し直してください（上書きしません）";
  }
  if (stat === undefined) {
    return "フローのファイルは、読み込んだあとに画面の外で消されています。再読込してから編集し直してください";
  }
  if (stat.isSymbolicLink() || !stat.isFile()) {
    return `${file} がシンボリックリンクか、ファイルではないため、書き込みません`;
  }
  if (stat.nlink > 1) {
    return hardLinkedError(file, "書き込みません");
  }
  if (stat.mtimeMs !== expect.mtimeMs) {
    return "フローのファイルは、読み込んだあとに画面の外で変更されています。再読込してから編集し直してください（この変更は上書きしません）";
  }
  return undefined;
}

/**
 * 読んだバイトを文字にする。UTF-8 として不正なら読まない（置き換え文字で埋めると、不正な部分を落として
 * 書き直すことになる）。先頭の BOM は 1 つ外す（実行ファイルの `utf-8-sig` と同じ）。文面は実行ファイル
 * （`flow.NOT_UTF8`）に揃える
 */
export function decodeFlowBytes(bytes: Uint8Array): { readonly ok: true; readonly text: string } | { readonly ok: false; readonly error: string } {
  try {
    return { ok: true, text: new TextDecoder("utf-8", { fatal: true }).decode(bytes) };
  } catch {
    return { ok: false, error: "UTF-8 として読めません" };
  }
}

/**
 * 読む。リンクを辿らない。無ければ undefined（フローは任意）、読めなければ例外。中身はバイトのまま返す
 * （開くときは、このバイトのまま実行ファイルに確かめさせ、文字にするのは `decodeFlowBytes`）
 */
export function readFlowFile(tree: string, file: string): { readonly bytes: Uint8Array; readonly mtimeMs: number } | undefined {
  if (segments(tree, file) === undefined) {
    throw new Error(`ツリーの外のファイルは読みません（${file}）`);
  }
  const linked = linkedSegment(tree, file);
  if (linked !== undefined) {
    throw new Error(`${linked} がシンボリックリンクのため、読み書きしません`);
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
    throw new Error(`${file} はふつうのファイルではない（名前付きパイプやデバイスなど）ため、読みません`);
  }
  if (stat.nlink > 1) {
    throw new Error(hardLinkedError(file, "読み書きしません"));
  }
  if (stat.size > FLOW_FILE_LIMIT) {
    throw new Error(`${file} が大きすぎるため、読みません（${stat.size} バイト、上限 ${FLOW_FILE_LIMIT}）`);
  }
  // 確かめてから開くまでに差し替えられても、開いたものを確かめ直す。名前付きパイプでも待たない
  const fd = fs.openSync(file, fs.constants.O_RDONLY | O_NOFOLLOW | O_NONBLOCK);
  try {
    const opened = fs.fstatSync(fd);
    if (!opened.isFile() || opened.ino !== stat.ino || opened.dev !== stat.dev) {
      throw new Error(`${file} を開いている間に別のファイルに差し替わったため、読みません`);
    }
    if (opened.nlink > 1) {
      throw new Error(hardLinkedError(file, "読み書きしません"));
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
      throw new Error(`${file} が大きすぎるため、読みません（上限 ${FLOW_FILE_LIMIT} バイト）`);
    }
    return { bytes: Uint8Array.from(buffer.subarray(0, length)), mtimeMs: opened.mtimeMs };
  } finally {
    fs.closeSync(fd);
  }
}

function hardLinkedError(file: string, what: string): string {
  return `${file} はハードリンク（ほかのパスからも同じ中身を開ける）のため、${what}。承認済みの領域の外のパスから書き換えられる可能性があります。リンクを外してから開き直してください`;
}
