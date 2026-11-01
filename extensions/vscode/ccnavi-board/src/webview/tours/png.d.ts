/** 案内の画像（`images/*.png`）。esbuildがdata: のURLにして読ませる（`scripts/bundle-webview.js`） */
declare module "*.png" {
  const value: string;
  export default value;
}
