---
title: ccnavi 承認ボードのプロジェクトのリポジトリ
type: requirements
description: プロジェクトのリポジトリの登録と、設定・置き場の読み方
tags: [design-doc, extension, approval]
keywords: [要件, プロジェクト, projects, 共通設定, 統合先, 登録]
---

# プロジェクトのリポジトリ

入口: [ccnavi 承認ボードの要件](../requirements.md)

- プロジェクトのリポジトリ: 設定画面でプロジェクト名（手元の `projects/<名前>` の名前）と、先に登録したワークスペースの
  リポジトリを選ぶ。置き場のパスは既定に固定（統合先の `.claude/settings.json` の `env` は読まない）。共通設定・ccnavi の設定ファイル・互換のマーカーはワークスペースの統合先から、クローズしたもの（`done/`）とプロジェクトの設定はプロジェクトの統合先
  から読み、共通設定はワークスペースの統合先の `.ccnavi/config/common/` だけを読む（プロジェクトの `.ccnavi/config/common/` のミラーは読まない。親のブランチの上の設定も読まない）。Chrome の登録の名前は手元のディレクトリ名と揃える（ずれると、手元で取り込んだ後に
  判定し直したとき親子のチケットが止まる）
