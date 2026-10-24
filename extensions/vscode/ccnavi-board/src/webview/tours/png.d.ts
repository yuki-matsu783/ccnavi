/** 案内の画像（`images/*.png`）。esbuild が data: の URL にして読ませる（`scripts/bundle-webview.js`） */
declare module "*.png" {
  const value: string;
  export default value;
}
