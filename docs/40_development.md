---
type: guide
title: 開発の入口
description: ccnavi 自身を開発する人向けの入口。読み手ごとの最初の文書の地図、文書の案内表、開発環境のコマンド、手で 1 回動かす方法、トップレベルのディレクトリ構成
tags: [development, docs]
keywords: [開発, 入口, 開発環境, テスト, unittest, ruff, build.py, uv, 手で動かす, ディレクトリ構成, 文書の案内, 読み手]
---

# 開発の入口

ccnavi 自身を開発する人向けの入口である。使う人向けの手引きは[手引きの目次](50_guide/index.md)にある。

## 最初に開く文書

読み手は 4 者に分かれる。利用者の人、利用者のエージェント、開発者の人、開発者のエージェント。それぞれが最初に開く文書、置き場、書くことの分け方は[文書の置き場・読み手・リンクの向き](../.agents/docs/docs-structure.md)の「読み手と置き場」にある。表はそちらが正で、ここには写さない。

開発者の人はこの文書から、開発者のエージェントは [AGENTS.md](../AGENTS.md) の「詳細」の表から入る。

## 文書の案内表

| 知りたいこと | 読む場所 |
|---|---|
| 用語（ccnavi の概念） | [CONTEXT.md](CONTEXT.md) |
| 根底にある考え方（なぜ・何を・どう）。スライド版は[01_idea/index.html](01_idea/index.html) | [00_idea.md](00_idea.md) |
| 外から観測できる約束 | [10_requirements.md](10_requirements.md) |
| 実行ファイルと拡張の JSON の形 | [11_requirements/json-shapes.md](11_requirements/json-shapes.md) |
| いまの実装の作り | [20_design.md](20_design.md) |
| サブパッケージごとのモジュール構成 | [21_design/modules.md](21_design/modules.md) |
| 判断の理由と経緯、採らなかった案 | [30_adr.md](30_adr.md) |
| 実測で確かめた前提 | [設計の付録 C](21_design/appendix.md) |
| ccnavi の使い方（人向けの手引き） | [50_guide/index.md](50_guide/index.md) |
| ccnavi の使い方（エージェント向け。配布先にも配る） | [`.ccnavi/CCNAVI.md`](../.ccnavi/CCNAVI.md) の「詳細」の表が指す `.ccnavi/docs/` |
| 学びの蓄積（パターンと採否の記録。実行ファイルが読む） | `docs/wiki/` |
| ccnavi の開発で常に守る決まり（テスト、ログ、sh、出力の案内、文書の書き方など） | `.agents/docs/`（入口は [AGENTS.md](../AGENTS.md) の「詳細」の表） |
| 開発用の hook | [開発用の hook](../.agents/docs/dev-hooks.md) |
| 実行ファイルの組み立てと配布物、配布する sh の確かめ方 | [組み立てと配布物](../.agents/docs/build-and-dist.md) |
| 作業の決まり（`.ccnavi/CCNAVI.md`）に条件を足す | [作業の決まりに条件を足す](../.agents/docs/ccnavimd-conditions.md) |
| 環境の版、環境固有の落とし穴 | [実行環境とADRの扱い](../.agents/docs/environment.md) |

## 開発環境

必要な道具と版は[実行環境とADRの扱い](../.agents/docs/environment.md)にある。

```sh
uv run python -m tests                             # 全件を別プロセスで同時に回す
uv run python -m tests tests/core                  # 1 グループ
uv run --with ruff ruff check .                    # 静的検査
uv run --with ruff ruff format .                   # 整形
uv run --with ruff ruff format --check .           # 整形の確認だけ
uv run --with pyinstaller python build.py          # 実行ファイルの組み立て
```

- テストは `python -m tests` か `python -m unittest` で回す。ファイルを直接実行すると `tests` パッケージを import できずに失敗する。全件の回し方と、回すグループの決め方は[ccnaviのPythonのテストを回す](../.agents/docs/testing.md)にある
- 拡張（`extensions/`）のテストは、各拡張のディレクトリで `pnpm test` を回す。触ったファイルから回すグループを決める仕組みは[開発用の hook](../.agents/docs/dev-hooks.md)にある
- ログの置き場と書き方は[ログの書き方](../.agents/docs/logging.md)にある

## 手で 1 回動かす

hook の入力を標準入力に渡して、判定を見る。

```sh
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"git push"}}' \
  | uv run python -m ccnavi --mode enable
```

## ディレクトリ構成（トップレベル）

役割のまとまりで並べる。ファイル 1 本ごとの役割は、そのファイル先頭の説明にある。サブパッケージごとの構成は[モジュール構成](21_design/modules.md)にある。

| 場所 | 中身 |
|---|---|
| `src/ccnavi/` | 実行ファイルのソース。サブパッケージは役割ごとに 7 つ |
| `main.py`・`build.py` | 配布物の入口（PyInstaller に渡すスクリプト）と、配布物の組み立て |
| `pyproject.toml`・`uv.lock` | Python のプロジェクト設定と、依存の固定 |
| `tests/` | テスト。グループごとのディレクトリに分ける（グループは[ccnaviのPythonのテストを回す](../.agents/docs/testing.md)で決める）。入口の標準入出力・終了コード・ファイルだけを見る受入テスト（`guard`・`sh`・`e2e` が中心）と、`ccnavi` の部品を直接呼ぶ単体テスト（`core`・`ticket` に多い）が混在する。GitLab の模擬と契約テスト（`contract/`）、受入シナリオ（`scenarios/`）、手動の道具（`manual/`）、テスト用の見本（`fixtures/`）もここにある |
| `docs/` | 設計・要件・ADR・手引き。番号の付け方は[文書の置き場・読み手・リンクの向き](../.agents/docs/docs-structure.md)の「番号」にある。`CONTEXT.md` は用語集、`wiki/` は学びの蓄積 |
| `.agents/docs/` | ccnavi の開発で常に守る決まり。配布しない |
| `.ccnavi/` | ccnavi 自身の設定と、配布先へ配るものの元。作業の決まり（`CCNAVI.md`）、使い方の文書（`docs/`）、設定 3 本（`config/`）、文面ファイル（`messages/`）、配布する sh（`scripts/`。一覧と役割は[配布するスクリプト](50_guide/scripts.md)）。`bin/` は `build.py` が作る実行ファイルの置き場で追跡しない。`scripts/ccnavimd.sh` は配布先が作るファイルで、このリポジトリには無い |
| `.claude/` | このリポジトリの Claude Code の設定。`settings.json`（hook の登録）、`hooks/`（開発用の hook）、`skills/`（このリポジトリのスキル）。`settings.local.json` は利用者ごとのファイルで追跡しない |
| `scripts/` | ccnavi を入れる・配る・点検する側の道具。導入（`ccnavi-install.sh`・`ccnavi-setup.sh`・`ccnavi-doctor.sh`）、配布の zip（`ccnavi-package.sh`）、開発機への導入（`ccnavi-build-install.sh`）、Stop の AI 判定の登録（`ccnavi-stop-review.sh`）、PR の検査（`ccnavi-check-no-wip.sh`）、ルールの期待の点検（`check_rules.py`）。配布先には `ccnavi-setup.sh` が必要なものだけを配る |
| `extensions/` | VS Code 拡張（`vscode/ccnavi-board`）、Chrome 拡張（`chrome/ccnavi-approval`）、共有の部品（`shared/`）。拡張ごとの開発の手引きは各拡張の `docs/` と `VERIFY.md` にある |
| `.github/` | Issue のテンプレートと CI の定義 |
| `.vscode/` | VS Code の設定 |
| `README.md`・`AGENTS.md`・`CLAUDE.md` | 利用者向けの入口、開発者のエージェント向けの決まり、`AGENTS.md` を読み込ませる 1 行 |
| `wip/` | 作業ブランチで使う下書きの置き場。main には入れない（`.github` の CI と `scripts/ccnavi-check-no-wip.sh` が、main へ入れる PR の差分を見る） |

git 管理外のもの: `dist/`・`build/`（組み立ての出力）、`logs/`（記録。絶対パスとコマンド全文が入るのでコミットしない）、`knowledge/`（参照専用の資料。Claude Code の hook にまつわる実測）、`.venv/`、`projects/`、`scratchpad/`。
