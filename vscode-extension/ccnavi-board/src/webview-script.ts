/**
 * 束ねた画面のスクリプト（`out/webview/board.js`）を読む。拡張が `<script nonce>` に流し込む。
 *
 * ファイルとして Webview に読ませないのは、`localResourceRoots` を空のままにして
 * 「外部資源に依存しない 1 枚の HTML」を保つため。読むのは 1 度だけで、あとは覚えておく。
 * VS Code の API には触れないが、置き場（`out/`）の綴りを知っているのでここに置く。
 */
import * as fs from "node:fs";
import * as path from "node:path";

const cache = new Map<string, string>();

/**
 * 束ねたものは `out/webview/` にある。走っているのが束ねた `out/extension.js`（`__dirname` は `out/`）でも、
 * tsc が出した `out/src/webview-script.js`（`__dirname` は `out/src/`）でも読めるように、両方を見る。
 */
export function webviewScript(name: string): string {
  const found = cache.get(name);
  if (found !== undefined) {
    return found;
  }
  const candidates = [path.join(__dirname, "webview", name), path.join(__dirname, "..", "webview", name)];
  for (const candidate of candidates) {
    try {
      const text = fs.readFileSync(candidate, "utf8");
      cache.set(name, text);
      return text;
    } catch {
      // 次の候補へ
    }
  }
  throw new Error(`画面のスクリプトが見つからない: ${name}（拡張のビルドが揃っていない。pnpm run compile を通す）`);
}
