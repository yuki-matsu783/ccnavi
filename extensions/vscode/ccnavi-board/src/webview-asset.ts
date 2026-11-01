/**
 * バンドルした画面（`out/webview/<名前>.js`）と、そのCSS（`out/webview/<名前>.css`）を読む。
 * 拡張が`<script nonce>`と`<style nonce>`に流し込む。
 *
 * ファイルとしてWebviewに読ませないのは、`localResourceRoots`を空のままにして
 * 「外部資源に依存しない1枚のHTML」を保つため。読むのは1度だけで、あとは覚えておく。
 * VS CodeのAPIには触れないが、置き場（`out/`）のパスを知っているのでここに置く。
 *
 * CSSがここに来るのは、WebviewのCSPがnonceを持つ`<style>`しか通さないため。nonceは
 * 入れ物のHTMLを組む側（拡張ホスト）が作るので、挿すのもそちら。CSSの中身は画面の側
 * （`src/webview/<名前>/*.css`）にあり、ここが知っているのはバンドルの出口のパスだけ。
 */
import * as fs from "node:fs";
import * as path from "node:path";

const cache = new Map<string, string>();

/**
 * バンドルしたものは`out/webview/`にある。走っているのがバンドルした`out/extension.js`（`__dirname`は`out/`）でも、
 * tscが出した`out/src/webview-asset.js`（`__dirname`は`out/src/`）でも読めるように、両方を見る。
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
  throw new Error(`画面のバンドルが見つかりません: ${name}（拡張のビルドが揃っていません。pnpm run compileを実行してください）`);
}

/**
 * バンドルした画面のスクリプト。渡すのは画面の名前（`"board"`。`src/webview/<名前>/`の`board`）で、
 * 拡張子はここが付ける。名前だけを受けるので、`.js`と`.css`を取り違えることが無い
 */
export function webviewScript(name: string): string {
  return asset(`${name}.js`);
}

/** バンドルした画面のCSS。渡すのは画面の名前（`"board"`） */
export function webviewStyle(name: string): string {
  return asset(`${name}.css`);
}
