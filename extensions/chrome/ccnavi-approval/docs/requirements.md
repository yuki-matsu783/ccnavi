---
title: ccnavi 承認ボードの要件
type: requirements
description: Chrome 拡張 ccnavi 承認ボードが何をするかの概要と、機能ごとの詳細への案内
tags: [design-doc, extension, approval]
keywords: [要件, Chrome 拡張, 承認ボード, 承認, 取り下げ, レビュー済み, 始める, PAT, GitHub, GitLab, プロジェクト]
---

# ccnavi 承認ボードの要件

Chrome 拡張「ccnavi 承認ボード」が何をするかを書く。承認者がブラウザと PAT だけで、リモートの承認待ちを見て、承認・取り下げ・レビュー済みを親のブランチへ書き、issue から親のブランチを始める。組み立てと試験は [README](../README.md)、構成は [design.md](design.md) にある。拡張と ccnavi 本体の間の取り決め（同梱の ccnavi の呼び方、読む JSON の形、互換の版、ダイジェストの照合）は本体の [要件 2.13 拡張との取り決め](../../../../docs/requirements/extension-if.md)（REQ-EXT）と [設計](../../../../docs/design.md) に書く。拡張自身の要件のうち、本体の要件から切り出したものには REQ-CHR の ID を振ってある。

## 何をするか

| 機能 | 開くファイル |
|---|---|
| 読み取りボード。統合先と置き場の読み取り、承認待ちの並べ方、直近 N 日、自動の読み直し | [requirements/board.md](requirements/board.md) |
| 互換の版。統合先の CCNAVI_COMPAT と同梱の互換の版が違うときのふるまい | [requirements/compat.md](requirements/compat.md) |
| 承認。承認を押したときの読み直し・照合・親のブランチへの 1 コミット | [requirements/approve.md](requirements/approve.md) |
| 取り下げ。着手前の新規の承認を取り下げるときのふるまい | [requirements/withdraw.md](requirements/withdraw.md) |
| レビュー済み。依頼済みのフェーズをレビュー済みにするときのふるまい | [requirements/reviewed.md](requirements/reviewed.md) |
| PAT の期限。PAT の期限の読み方と知らせ方 | [requirements/pat.md](requirements/pat.md) |
| GitLab。GitLab での読み書きと、書き込みがぶつかったときのふるまい | [requirements/gitlab.md](requirements/gitlab.md) |
| プロジェクトのリポジトリ。プロジェクトのリポジトリの登録と、レイヤー・置き場の読み方 | [requirements/projects.md](requirements/projects.md) |
| issue から始める。「始める」で issue から親のブランチを作るときのふるまい | [requirements/start.md](requirements/start.md) |
