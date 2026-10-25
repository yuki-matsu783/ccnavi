---
type: skill-reference
title: テストのグループと回すグループの決め方
description: ccnavi のテストを複数のグループに分けて管理する方法と、変更時にどのグループを実行すべきか決める基準
tags: [process-test, skills]
keywords: [テストグループ, tests, core, guard, config, ticket, sh, e2e, discover]
---

# テストのグループと、回すグループの決め方

## グループ

`tests/<グループ>/` に置く。時間は macOS で 1 本ずつ回したときの目安。

| グループ | 主題 | 時間 |
|---|---|---|
| `core` | 部品の単体と、速い受入テスト（shellread・glob・lint・`build.py` の形・sh の書き方、リポジトリの期待する判定 `rule-expectations.yml` といまのルールの突き合わせなど） | 約 6 秒 |
| `guard` | 判定とルール（受入テスト、自己防衛、運用のルール、縮退、実行後チェック、プロジェクト） | 約 60 秒 |
| `config` | 設定のレイヤーの合成（rules / phases / risk） | 約 33 秒 |
| `ticket` | チケット・フェーズ・承認・ボード・リスク | 約 140 秒 |
| `sh` | 配布する sh と導入スクリプト（setup・git のラッパー・承認の push の sh・取ってくる sh・ランチャー・clean・レビューの sh・write-and-push。write-and-push は重いため、`test_write_and_push_*.py` の 6 本に分けてある）。配布する sh を起動するテストはここに置く | 約 80 秒 |
| `e2e` | 本物のワークスペースを組み立てて sh を外から実行する。組み立て済みの実行ファイル（`dist/ccnavi`）が要り、無ければ skip | 約 8 秒 |

```sh
uv run python -m unittest discover -s tests/core -t .    # 1 グループ（複数は 1 つずつ続けて回す。`discover -s` は 1 か所しか取らない）
```

全件は `uv run python -m tests`（同時実行）で回す。`discover -s tests` の 1 本は使わない。理由と手順は[テストを回す](../../../../docs/42_claude/testing.md)。時間の目安は並行セッションがあると数倍になる。

実行ファイル（`.ccnavi/bin/`、無ければ PATH の `ccnavi`）が無い環境（Claude Code on the web、ビルド前）では、ランチャー経由の `--lint` と `ccnavi` コマンドが動かない。
`python main.py --lint` で代える（ランチャーが実行ファイルを探して失敗するため）。

落ちたら新しいプロセスを起こすのをやめ、落ちた 1 本の出力だけを出す。ターンの終わりの hook はこれを使わない。
既知の失敗が残っている間は `python -m tests` で全件を見られないので、グループごとに `discover -s tests/<グループ> -t .` で回し、失敗の数を既知と比べる。

`tests/<グループ>/bench_*.py` はテストではなく（`discover` は拾わない）、`uv run python -m tests.core.bench_deadline` のように名指しで回す。
実ルールを使う bench は `*x*` 形の glob の照合が入力の長さの 2 乗で伸び、小さい入力でも数分かかる。ターンの中で回すならバックグラウンドで回す。

## 回すグループ

`core` はいつも回す。そのうえで、変えたファイルを下の表に適用して、ヒットした行のグループを足す。

| 変えたもの | 足すグループ |
|---|---|
| `tests/<グループ>/` の中 | そのグループ |
| `src/ccnavi/**/*.py`・`main.py` | `guard` `config` `ticket` |
| `src/ccnavi/infra/platformtag.py` | 上に加えて `sh` |
| `build.py` | `guard` `sh` `e2e` |
| `.ccnavi/scripts/ccnavi-ticket.sh`・`ccnavi-user-agree.sh`・`ccnavi-review.sh` | `ticket` `config` `e2e` |
| `.ccnavi/scripts/ccnavi-git.sh` | `sh` `guard` `config` `e2e` |
| `.ccnavi/scripts/ccnavi-launcher.sh`・`scripts/ccnavi-setup.sh` | `sh` `guard` `config` `e2e` |
| `.ccnavi/scripts/ccnavi-user-push-approved.sh`・`ccnavi-user-handoff.sh`・`ccnavi-user-mirror.sh`・`ccnavi-user-prune.sh`・`ccnavi-clean.sh`・`ccnavi-clean.js` | `sh` `config` `e2e` |
| `.ccnavi/scripts/ccnavi-fetch.sh`・`ccnavi-sync.sh`・`ccnavi-dirs.sh`・`ccnavi-idle.sh`・`ccnavi-session-start.sh` | `sh` |
| `.claude/hooks/test-py.sh` | `core`（`test_test_py_hook`）`e2e` |
| `.claude/hooks/check-adr.sh` | `core`（`test_check_adr_hook`） |
| `.claude/hooks/mark-ext.sh`・`test-ext.sh`・`extensions/vscode/ccnavi-board/scripts/test-groups.js` | `core`（`test_ext_tests`） |
| `tests/ticketsample.py` | `ticket` `sh` |
| `tests/fixtures/` | `guard` `ticket` |
| `extensions/vscode/` | `ticket`（`core` の `test_test_json` と `guard` の `test_suggest` も `test/fixtures/` を読む） |
| `extensions/chrome/ccnavi-approval/`（`py/`・`test/fixtures/`） | `ticket` `sh`（Chrome の入口を手元のコアと突き合わせる） |
| `docs/30_adr/`・`docs/30_adr.md`（枚を足す・番号を動かす） | `core`（`test_adr_numbers`） |
| そのほかのドキュメントだけ（`*.md`・`docs/`） | 回さない |

自動テストで中身を確かめられないもの（変えたら手で動かして確かめる）:

- `.claude/hooks/lint-py.sh`
- `.claude/hooks/test-py.sh` の差し戻しの回数の上限（`core` の `test_test_py_hook` は回すツリーの選び方・記録の掃除・1 回の差し戻しを、`e2e` は sh の外形を見る）

全件を回すとき:

- 表のどの行にもヒットしないファイルを変えた
- `.ccnavi/scripts/ccnavi-common*.sh`（入口と部品）、`.ccnavi/config/common/`、`.ccnavi/config/project/`、`tests/__init__.py`、`tests/inproc.py`、
  `pyproject.toml`・`uv.lock` を変えた（ほぼ全グループが読む）
- 統合先に取り込む前、MR に出す前（親の最後のフェーズの子を finish する前に済ませる。レビュー準備中はシェルの実行が止まるため）
- どの行にヒットするか迷った

`src/` の `--json` 出力のキーや CLI のオプションを変えるときは、`core` と `guard` の両方を回す。`extensions/vscode/ccnavi-board/test/fixtures/` を
Python のテスト（`tests/core/test_test_json.py`・`tests/guard/test_suggest.py`）も読むので、fixtures の旧キーのままだと落ちる。
拡張を別のフェーズに分けて計画するなら、前のフェーズでこの 2 本が落ちると分かったうえで分ける（直すのは fixtures を持つフェーズ）。

`src/ccnavi/**/*.py` を変えたとき `e2e` は足さない（e2e は組み立て済みの実行ファイルを試す）。組み立て直したなら足す。

## 拡張（`extensions/vscode/ccnavi-board`）のグループ

拡張側にも同じ分け方がある（`test/<グループ>/`。board / rules / risk / phases / projects / flow / workflow / templates / handoff / shared / devtools）。
こちらは表を引かない。変えたファイルを渡せば、関わるグループだけが回る。

```sh
cd extensions/vscode/ccnavi-board
pnpm test:plan src/core/rules-doc.ts   # 何を回すかだけ見る
pnpm test:for src/core/rules-doc.ts    # 回す
pnpm test                              # 全部
```

コンパイルは 1 回なので、変えたファイルは 1 回でまとめて渡す（`pnpm test:for a.ts b.ts`）。

## Chrome 拡張（`extensions/chrome/ccnavi-approval`）

グループに分けていない。拡張全体が 1 つのグループで、触ったら全部を回す。

```sh
cd extensions/chrome/ccnavi-approval
pnpm test        # 組み立て → 単体・Pyodide・手元の CPython との突き合わせ（node scripts/test.js）
pnpm test:e2e    # 拡張を読み込んだ Chromium での試験。Chromium が要る
```

ターンの終わりの hook（`.claude/hooks/test-ext.sh`）は、VS Code の拡張と同じく触ったときだけ回す。
回すのは `node scripts/test.js` だけで、`--e2e` は回さない。読み物（`*.md`・`docs/`）だけを触ったターンは回さない。

## 表を直すとき

グループを足す・分け直すときは、ファイルを `tests/<グループ>/` へ移し、テストどうしの import
（`from tests.<グループ>.test_x import ...`）がグループをまたがないようにする。またぐなら、共有する
部品を `tests/` 直下の補助モジュール（`inproc.py`・`phasecopy.py`・`ticketsample.py`）へ出す。
置き場の決まり（グループをまたぐ import が無いことも）は `tests/core/test_layout.py` が見る。

配布する sh を起動するテストは `sh` に置く。例外は 1 つで、実行ファイル（判定）と sh が同じことを
別の実装で持ち、同じ見本の表で両方を突き合わせるテストは、表の主題のグループに 1 ファイルで置く
（`guard` の `test_wrapguard.py` の `ShAgreesTest`）。表を 2 つのグループに分けると、
片方だけ直したときに食い違いに気づけないため。
