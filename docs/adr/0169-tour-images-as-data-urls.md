---
type: adr
title: 案内の画像は data: の URL でバンドルに入れ、ボードの CSP だけ img-src data: を許す
description: 画面の外（Claude Code・GitLab）の見た目を案内で見せる画像を、ファイルとして読ませず data: の URL にして JS へ入れる。ボードの入れ物の CSP に img-src data: を足す
tags: [extension, board, design-doc]
keywords: [案内, ツアー, 画像, PNG, data URL, CSP, img-src, localResourceRoots, esbuild, dataurl]
---
# ADR-0169: 案内の画像は data: の URL でバンドルに入れ、ボードの CSP だけ img-src data: を許す

状態: 採用

## 状況

ボードの案内に、作業を頼んでからマージするまでの流れをたどる案内を足します。流れには、ボードの外の画面が含まれます。具体的には、GitLabのissueとMRの作成・マージ、Claude Codeの入力欄、GitLabの差分に指摘を付ける吹き出しと、コメントの種類を選ぶ「Start thread」です。ボードは外の画面を描けないので、画面を保存した画像で見せたいと考えています。

Webviewは `localResourceRoots` を空にし、CSPを `default-src 'none'` と nonce だけにした、外部資源に依存しない1枚のHTMLとして組んであります。そのため、画像はそのままでは読めません。

## 決定

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| 画像は `src/webview/tours/images/<名前>.png` に置き、esbuild の `dataurl` の読み込みで data: の URL にして JS へ入れます。`tours/images.ts` の表が、名前から画像を引きます | `localResourceRoots` を空のままにでき、画面が配るものは1枚のHTMLに閉じます | `asWebviewUri` で読ませる（`localResourceRoots` に置き場を足し、拡張ホストが URL を作って画面へ渡す） |
| 画像のある案内を持つボードの入れ物だけ、CSPに `img-src data:` を足します。ほかの画面のCSPは変えません | 許すのは data: だけで、外の URL は読めないままです。画像のない画面に許しを広げません | 全画面のCSPを揃えて広げる |
| 手順の定義（YAML）は、画像を名前（`image:`）と代替文（`alt:`）で書きます。画像の手順は指す先を空にし、吹き出しを中ほどに広く出します | 読み込みで吹き出しの高さが変わるので、指す先の横に置くと位置がずれます。広げないと、画像の中の字が読めません | 指す先の横に出す |
| YAML の名前・置き場の PNG・`images.ts` の表の突き合わせは、テストで確かめます | 名前の綴りが違うと、表に載らないまま画像なしで出てしまい、気づけません | 実行時に落とす |

## 得たもの・失ったもの

- 得たもの: ボードの外の画面を、案内の中で見せられます。外へ出る手段（`localResourceRoots`・外の URL）は増えません。
- 失ったもの: 画像の分だけ、ボードのバンドルが大きくなります。PNG 9 枚の合計は約 480KB で、base64 で入るため、バンドルは約 640KB 増えます。画像は GitLab・Claude Code の見た目に依るので、画面が変わったら撮り直します。
