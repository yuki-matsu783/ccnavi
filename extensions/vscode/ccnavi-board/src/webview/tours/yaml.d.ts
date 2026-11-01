/** 案内の定義（`*.yml`）。esbuildのプラグインがJSONにして読ませる（`scripts/bundle-webview.js`）。中身は`tourOf`が確かめる */
declare module "*.yml" {
  const value: unknown;
  export default value;
}
