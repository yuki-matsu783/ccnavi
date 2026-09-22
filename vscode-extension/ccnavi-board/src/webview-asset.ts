/**
 * 束ねた画面（`out/webview/<名前>.js`）と、その CSS（`out/webview/<名前>.css`）を読む。
 * 拡張が `<script nonce>` と `<style nonce>` に流し込む。
 *
 * ファイルとして Webview に読ませないのは、`localResourceRoots` を空のままにして
 * 「外部資源に依存しない 1 枚の HTML」を保つため。読むのは 1 度だけで、あとは覚えておく。
 * VS Code の API には触れないが、置き場（`out/`）の綴りを知っているのでここに置く。
 *
 * CSS がここに来るのは、Webview の CSP が nonce を持つ `<style>` しか通さないため。nonce は
 * 入れ物の HTML を組む側（拡張ホスト）が作るので、挿すのもそちら。CSS の中身は画面の側
 * （`src/webview/<名前>/*.css`）にあり、ここが知っているのは束ねた出口の綴りだけ（ADR-0066）。
 */
import * as fs from "node:fs";
import * as path from "node:path";

const cache = new Map<string, string>();

/**
 * 束ねたものは `out/webview/` にある。走っているのが束ねた `out/extension.js`（`__dirname` は `out/`）でも、
 * tsc が出した `out/src/webview-asset.js`（`__dirname` は `out/src/`）でも読めるように、両方を見る。
 */
function asset(name: string): string {
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
  throw new Error(`画面の束ねが見つからない: ${name}（拡張のビルドが揃っていない。pnpm run compile を通す）`);
}

/**
 * 束ねた画面のスクリプト。渡すのは**画面の名前**（`"board"`。`src/webview/<名前>/` の `board`）で、
 * 拡張子はここが付ける。名前だけを受けるので、`.js` と `.css` を取り違える道が無い
 */
export function webviewScript(name: string): string {
  return asset(`${name}.js`);
}

/** 束ねた画面の CSS。渡すのは画面の名前（`"board"`） */
export function webviewStyle(name: string): string {
  return asset(`${name}.css`);
}
