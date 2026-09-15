// @ts-check
import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["out/**", "media/**"] },
  js.configs.recommended,
  tseslint.configs.recommendedTypeChecked,
  {
    // 型を見る検査は tsconfig.test.json（src と test の両方）を使う
    files: ["**/*.ts"],
    languageOptions: {
      parserOptions: { project: "./tsconfig.test.json", tsconfigRootDir: import.meta.dirname },
    },
  },
  {
    // ビルドの補助スクリプトと、この設定ファイル自身。tsconfig の外なので型は見ない
    files: ["**/*.js", "**/*.mjs"],
    extends: [tseslint.configs.disableTypeChecked],
    languageOptions: { globals: { require: "readonly", module: "readonly", process: "readonly", __dirname: "readonly", console: "readonly" } },
    rules: {
      // CommonJS で書いている。import には直せない
      "@typescript-eslint/no-require-imports": "off",
    },
  },
  {
    // YAML から読んだ値は unknown で来る。scalar を文字にするのがこの関数の仕事で、
    // map や seq が来たら [object Object] になるのは承知の上（画面には問題として別に出る）
    files: ["src/core/*-doc.ts", "test/helpers/dom.ts"],
    rules: { "@typescript-eslint/no-base-to-string": "off" },
  },
  {
    // テストだけ外す検査。どれも「テストだから意図してやっている」もの
    files: ["test/**/*.ts"],
    rules: {
      // node:test の test() は Promise を返す。1 つずつ void を付ける意味がない
      "@typescript-eslint/no-floating-promises": "off",
      // Webview のスクリプトを取り出して評価し、実際の動きを確かめる
      "@typescript-eslint/no-implied-eval": "off",
      // YAML と CSS の字下げをそのまま照合する。並んだ空白は意図したもの
      "no-regex-spaces": "off",
      // happy-dom の window から取り出した値は any になる。照合の相手なので通す
      "@typescript-eslint/no-unsafe-assignment": "off",
      "@typescript-eslint/no-unsafe-call": "off",
      "@typescript-eslint/no-unsafe-member-access": "off",
      "@typescript-eslint/no-unsafe-return": "off",
    },
  },
);
