---
type: guide
title: 開発用の hook
description: ccnavi 自身の開発で使う hook（編集時の整形と検査、ターンの終わりのテスト、ADR の番号の検査、拡張のテスト）の役割、登録の有無、settings.local.json への足し方
tags: [development, testing]
keywords: [開発用 hook, lint-py, test-py, mark-ext, test-ext, check-adr, PostToolUse, Stop, settings.local.json, 差し戻し, test-groups.js]
---

# 開発用の hook

ccnavi のリポジトリ自身の開発に使う hook は `.claude/hooks/` にある。配布しない。ccnavi が配布先へ登録する hook とは別物。
どのテストをどの順で回すかの決まりは[ccnaviのPythonのテストを回す](testing.md)にある。

| hook | 契機 | 役割 | `.claude/settings.json` |
|---|---|---|---|
| `lint-py.sh` | `PostToolUse`（`Write\|Edit\|NotebookEdit`） | 編集した Python のファイルの整形と検査 | 登録済み |
| `check-adr.sh` | `PostToolUse`（`Write\|Edit\|NotebookEdit`） | ADR の番号の重なりと置き場の間違いを、書いた直後に止める | 登録済み |
| `test-py.sh` | `Stop` | ターンの終わりに、触ったツリーのテストを 1 回回す | 未登録 |
| `mark-ext.sh` | `PostToolUse`（`Write\|Edit`） | 拡張のファイルを触ったことの書き残し | 未登録 |
| `test-ext.sh` | `Stop` | ターンの終わりに、触った拡張のファイルに関わるテストのグループだけ回す | 未登録 |

未登録のものを回すなら、ユーザごとの `.claude/settings.local.json`（追跡しない）に足す。

## Python の整形・検査とテスト

`lint-py.sh` は、編集したファイルの整形と検査を走らせる。テストは走らせない。
編集していないファイルの指摘は出ない。他のファイルを壊す変更（定義の削除で別ファイルの import が壊れる等）はその場では出ず、ターンの終わりの `test-py.sh` のテストが失敗すれば、そこで見つかる。

`test-py.sh` は、ターンの終わりに 1 回だけテストを回す。失敗していたら差し戻すが、差し戻しは 3 回まで。上限に達したら止まらせて、判断をユーザへ返す。

`lint-py.sh` は、編集したファイルからいちばん近い `pyproject.toml` を上に辿ってツリーを決め、触ったツリーを `logs/session/<セッション>.trees` に書き残す。`test-py.sh` は触ったツリーだけをテストする。

## 拡張のテスト

拡張（VS Code の `extensions/vscode/ccnavi-board`、Chrome の `extensions/chrome/ccnavi-approval`、共通の `extensions/shared`）のテストも同じ形で回す。`mark-ext.sh` が、拡張のファイルを触ったら `logs/session/<セッション>.ext-files` に書き残し、`test-ext.sh` がターンの終わりに 1 回回す。
回すのは触ったファイルが関わるグループだけで、決めるのは `extensions/vscode/ccnavi-board/scripts/test-groups.js`。テストの `import` を辿り、辿れないものだけをパスの規則で補う。拡張を触っていないターンは何もしない。
差し戻しは 3 回までで、回数は `test-py.sh` と別に数える。

`settings.local.json` には次のように足す。

```json
"PostToolUse": [
  { "matcher": "Write|Edit",
    "hooks": [{ "type": "command", "command": "sh \"${CLAUDE_PROJECT_DIR}/.claude/hooks/mark-ext.sh\"", "timeout": 10 }] }
],
"Stop": [
  { "matcher": "",
    "hooks": [{ "type": "command", "command": "sh \"${CLAUDE_PROJECT_DIR}/.claude/hooks/test-ext.sh\"", "timeout": 180 }] }
]
```

`test-py.sh` を回すときは、同じ形で `Stop` に足す。

## 気づけないこと

- 気づけるのは「同じ機械で 1 回回して失敗すること」だけ。混み具合で結果が変わる失敗（React の描き直しを待つテストなど）は見つけられない。CI も繰り返しの仕掛けも無い
- Bash で変えたもの（`sed -i`、`merge` で入った変更）は書き残されないので回らない
- 実行ファイルはどの hook でも作り直さない。確かめるときに手で組み立てる（[組み立てと配布物](build-and-dist.md)）
