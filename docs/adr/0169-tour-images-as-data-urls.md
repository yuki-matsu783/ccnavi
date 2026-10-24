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

ボードの案内に、作業を頼んでからマージするまでの流れを回る案内を足す。流れには、ボードの外の画面（GitLab の issue と MR の作成・マージ、Claude Code の入力欄、GitLab の差分に指摘を付ける吹き出しと、コメントの種類を選ぶ「Start thread」）が含まれる。ボードは外の画面を描けないので、画面を保存した画像で見せたい。

Webview は `localResourceRoots` を空にし、CSP を `default-src 'none'` と nonce だけにした、外部資源に依存しない 1 枚の HTML として組んである。画像はそのままでは読めない。

## 決定

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| 画像は `src/webview/tours/images/<名前>.png` に置き、esbuild の `dataurl` の読み込みで data: の URL にして JS へ入れる。`tours/images.ts` の表が名前から引く | `localResourceRoots` を空のままにでき、画面が配るものは 1 枚の HTML で閉じる | `asWebviewUri` で読ませる（`localResourceRoots` に置き場を足し、拡張ホストが URL を作って画面へ渡す） |
| 画像のある案内を持つボードの入れ物だけ、CSP に `img-src data:` を足す。ほかの画面の CSP は変えない | 許すのは data: だけで、外の URL は読めないまま。画像の無い画面に許しを広げない | 全画面の CSP を揃えて広げる |
| 手順の定義（YAML）は画像を名前（`image:`）と代替文（`alt:`）で書く。画像の手順は指す先を空にし、吹き出しを中ほどに広く出す | 読み込みで吹き出しの高さが変わるので、指す先の横に置くと位置がずれる。広げないと画像の中の字が読めない | 指す先の横に出す |
| YAML の名前・置き場の PNG・`images.ts` の表の突き合わせは、テストで見る | 名前の綴り違いは、表に無いまま画像なしで出て気づけない | 実行時に落とす |

## 得たもの・失ったもの

- 得たもの: ボードの外の画面を、案内の中で見せられる。外へ出る手段（`localResourceRoots`・外の URL）は増えない。
- 失ったもの: 画像のぶん、ボードのバンドルが大きくなる（PNG の約 1.4 倍。8 枚で約 450KB）。画像は GitLab・Claude Code の見た目に依るので、画面が変われば撮り直す。
