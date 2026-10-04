// 組み立ての出力先とキャッシュ（.cache）を消す。
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
for (const d of ["out", "dist", "dist-e2e", ".cache"]) fs.rmSync(path.join(HERE, d), { recursive: true, force: true });
