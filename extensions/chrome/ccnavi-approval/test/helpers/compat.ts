/**
 * 見本と依存の互換の版。src/ccnavi/entry/version.py の COMPAT を読み、版を上げても試験を直さずに済むようにする。
 * 版が違う形を試すときは COMPAT + 1 を使う。
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PACKAGE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const found = /^COMPAT = (\d+)$/m.exec(fs.readFileSync(path.join(PACKAGE, "..", "..", "..", "src", "ccnavi", "entry", "version.py"), "utf8"));
if (!found) throw new Error("src/ccnavi/entry/version.py の COMPAT を読めない");

export const COMPAT = Number(found[1]);
