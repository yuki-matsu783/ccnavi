---
type: skill-reference
title: zip の受け取りと展開
description: ccnavi のビルド済みの zip を受け取るところから、チェックサムの確かめ方、OS ごとの展開、機械に合う zip の見分け方まで
tags: [ccnavi-install, skills]
keywords: [zip, チェックサム, sha256, 展開, 共有フォルダ, certutil, shasum, sha256sum, unzip, 配布元]
---

# zip の受け取りと展開

`ccnavi-install.sh` は zip を受けない。ユーザが自分で展開し、展開したフォルダ（配布元）の場所をスキルに返す。
配布元の信頼（受け取り元とチェックサム）を確かめる前に、先へ進まない。

## 1. 受け取り元を確かめる

zip を、決められた共有フォルダから受け取ったかをユーザに確かめる。メールやチャットで回ってきたもの、出どころが分からないものは
受け取り直してもらう。zip の隣に `<zip 名>.sha256` があるはずなので、これも同じ場所から受け取る。

## 2. チェックサムを確かめる

ユーザにターミナルで打ってもらい、結果を `.sha256` の値と見比べてもらう（空白の有無と大文字小文字は無視してよい）。
結果は会話に貼らなくてよい。一致したかどうかだけを聞く。

| OS | コマンド |
|---|---|
| macOS | `shasum -a 256 <zip>` |
| Linux | `sha256sum <zip>`（無ければ `shasum -a 256 <zip>`） |
| Windows | `certutil -hashfile <zip> SHA256` |

一致しなければ展開せず、受け取り直してもらう。

## 3. 展開する

| OS | 方法 |
|---|---|
| Windows | エクスプローラーで zip を右クリックして「すべて展開」 |
| macOS | Finder でダブルクリック、または `unzip <zip>` |
| Linux | `unzip <zip>`（無ければ [tools.md](tools.md)） |

macOS の Finder と `unzip` は、zip の中のシンボリックリンクを保つ。展開に使う道具は、リンクを保つものを選ぶ。

展開後、ユーザに展開先のフォルダの場所を聞く。そのフォルダの直下に `scripts/ccnavi-install.sh` があればそれが配布元。
フォルダの中にもう 1 段、同じ名前のフォルダができていることがある（`install` は 1 段下にちょうど 1 つあれば自動で使う）。

## 4. 機械に合う zip か

zip の名前は `ccnavi-<短いコミット>-<os>-<arch>[-dirty].zip`。`<os>-<arch>` が、この機械（doctor の `machine` 行）と同じか確かめる。

- 違うときは、この機械向けの zip を頼むか、ソースからのビルド（`--build`）にするかを聞く。
- arm64 の macOS と Windows は、x86_64 向けの zip を変換して動かせる（Rosetta 2・Windows on Arm）。自分向けが無いときはそちらで足りる。
- 名前に `-dirty` が付いた zip は、未コミットの状態から作られている。配る側に、コミットして作り直したものを頼む。
