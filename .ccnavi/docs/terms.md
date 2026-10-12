---
type: guide
title: ccnaviの呼び名
description: ワークスペースルート、gitプロジェクトルート、統合先ブランチなど、ccnaviの文書と案内で使う用語の定義
tags: [environment]
keywords: [呼び名, 用語, ワークスペースルート, gitプロジェクトルート, 統合先ブランチ, 統合先, CLAUDE_PROJECT_DIR]
---

# ccnaviの呼び名

- ワークスペースルート: Claude Codeを起動したディレクトリ（`CLAUDE_PROJECT_DIR`）。プロジェクトの作業もワークスペースルートで起動して`cd`で入る。プロジェクトの中で起動すると、ワークスペースの`.claude/settings.json`が読まれず、ccnaviのhookが動かない
- gitプロジェクトルート: `.git`があるディレクトリ。ワークスペース自身、`projects/`の下の各プロジェクト、
  各ワークツリーがそれぞれ持つ
- 統合先ブランチ: 作業を合流させるブランチ
