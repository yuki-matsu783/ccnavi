/**
 * 見本と依存の互換の版。同梱の ccnavi の互換の版（go/internal/entry/version の Compat）を読み、版を上げても試験を
 * 直さずに済むようにする。版が違う形を試すときは COMPAT + 1 を使う。
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PACKAGE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const SOURCE = path.join(PACKAGE, "..", "..", "..", "go", "internal", "entry", "version", "version.go");
const found = /^const Compat = (\d+)$/m.exec(fs.readFileSync(SOURCE, "utf8"));
if (!found) throw new Error("go/internal/entry/version/version.go の Compat を読めない");

export const COMPAT = Number(found[1]);
