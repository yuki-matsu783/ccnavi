/**
 * 配布先の統合先に載る文面ファイル（`.ccnavi/messages/*.yml`）。このリポジトリのものをそのまま読む。
 * 同梱の ccnavi は拒否の文を統合先の文面ファイルから引くので、見本の統合先に無いと文が短い既定の文に落ちる。
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PACKAGE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const DIR = path.join(PACKAGE, "..", "..", "..", ".ccnavi", "messages");

export const MESSAGES: Record<string, string> = Object.fromEntries(
  fs
    .readdirSync(DIR)
    .filter((n) => n.endsWith(".yml"))
    .sort()
    .map((n) => [`.ccnavi/messages/${n}`, fs.readFileSync(path.join(DIR, n), "utf8")]),
);
