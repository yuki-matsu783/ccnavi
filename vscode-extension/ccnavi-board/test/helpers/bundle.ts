/**
 * 束ねた画面（`out/webview/<名前>.js`）と、その CSS（`out/webview/<名前>.css`）を読む。
 * 拡張の `src/webview-asset.ts` が配るときに読むのと同じものを、テストからも読む。
 *
 * 束ねるのは `pnpm test` の中の `scripts/bundle-webview.js`。テストだけ先に走らせたときは
 * 「何を通せばよいか」を言って落ちる（束ねが無いまま HTML を組むと、白い画面を見て悩むことになる）。
 */
import * as fs from "node:fs";
import * as path from "node:path";

const cache = new Map<string, string>();

function bundled(name: string): string {
  const found = cache.get(name);
  if (found !== undefined) {
    return found;
  }
  // このファイルは `out/test/helpers/` から走る。束ねは `out/webview/`
  const full = path.join(__dirname, "..", "..", "webview", name);
  if (!fs.existsSync(full)) {
    throw new Error(`画面が束ねられていない: ${full}（node scripts/bundle-webview.js を通す）`);
  }
  const text = fs.readFileSync(full, "utf8");
  cache.set(name, text);
  return text;
}

/** 束ねた画面のスクリプト。`name` は画面の名前（`"board"`） */
export function screenScript(name: string): string {
  return bundled(`${name}.js`);
}

/** 束ねた画面の CSS。`name` は画面の名前（`"board"`） */
export function screenStyle(name: string): string {
  return bundled(`${name}.css`);
}

/**
 * 画面の CSS の置き場（`src/webview/`）。束ねる前の綴りを見るテストが使う。
 * このファイルは `out/test/helpers/` から走るので、3 つ上がリポジトリのルート。
 */
export const WEBVIEW_SRC = path.join(__dirname, "..", "..", "..", "src", "webview");

/**
 * 1 枚の HTML の `<style nonce>` の中身を、1 行に潰して返す。
 *
 * 束ねた CSS は esbuild が並べ直すので（1 宣言 1 行、選択子も 1 つ 1 行）、規則そのものを
 * 文字列で見たいテストは、この形（`.card { a: 1; b: 2; }`）で読む。並べ方が変わっても、
 * 当てるものと宣言が変わらなければ通る。
 */
export function flatStyle(html: string): string {
  const found = /<style nonce="[^"]*">\n([\s\S]*?)\n<\/style>/.exec(html);
  if (found === null) {
    throw new Error("HTML に <style nonce> が無い");
  }
  return found[1].replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\s*\n\s*/g, " ").trim();
}
