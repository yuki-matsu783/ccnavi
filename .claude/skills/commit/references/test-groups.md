# テストのグループと、回すグループの決め方

## グループ

`tests/<グループ>/` に置く。時間は macOS で 1 本ずつ回したときの目安。

| グループ | 主題 | 時間 |
|---|---|---|
| `core` | 部品の単体と、速い受入テスト（shellread・glob・lint・`build.py` の形・sh の書き方など） | 約 5 秒 |
| `guard` | 判定とルール（受入テスト、自己防衛、運用のルール、縮退、実行後の監視、プロジェクト） | 約 60 秒 |
| `config` | 設定の層の合成（rules / phases / risk） | 約 33 秒 |
| `ticket` | チケット・フェーズ・承認・ボード・リスク | 約 140 秒 |
| `sh` | 配布する sh と導入スクリプト（setup・git のラッパー・運ぶ sh・取ってくる sh・ランチャー・clean） | 約 80 秒 |
| `e2e` | 本物のワークスペースを組み立てて sh を外から叩く。組み立て済みの実行ファイル（`dist/ccnavi`）が要り、無ければ skip | 約 8 秒 |

```sh
uv run python -m unittest discover -s tests/core -t .    # 1 グループ
uv run python -m unittest discover -s tests -t .         # 全件
```

複数のグループは 1 つずつ続けて回す（`discover -s` は 1 か所しか取らない）。

全件は `tools/run_tests.py` のほうが速い（モジュールごとに別プロセスで同時に回す。中身は discover と同じ）。

```sh
uv run python tools/run_tests.py                 # 全件
uv run python tools/run_tests.py tests/ticket    # グループを名指し（複数書ける）
uv run python tools/run_tests.py --plan          # 何をどの順で回すか出すだけ
```

落ちたら新しいプロセスを起こすのをやめ、落ちた 1 本の出力だけを出す。ターンの終わりの hook はこれを使わない。

## 回すグループ

`core` はいつも回す。そのうえで、変えたファイルを下の表に当てて、当たった行のグループを足す。

| 変えたもの | 足すグループ |
|---|---|
| `tests/<グループ>/` の中 | そのグループ |
| `ccnavi/*.py`・`main.py` | `guard` `config` `ticket` |
| `ccnavi/platformtag.py` | 上に加えて `sh` |
| `build.py` | `guard` `sh` `e2e` |
| `.ccnavi/scripts/ccnavi-ticket.sh`・`ccnavi-approve.sh`・`ccnavi-review.sh` | `ticket` `config` `e2e` |
| `.ccnavi/scripts/ccnavi-git.sh` | `sh` `guard` `config` `e2e` |
| `.ccnavi/scripts/ccnavi-launcher.sh`・`scripts/ccnavi-setup.sh` | `sh` `guard` `config` `e2e` |
| `.ccnavi/scripts/ccnavi-push-approved.sh`・`ccnavi-clean.sh`・`ccnavi-clean.js` | `sh` `config` `e2e` |
| `.ccnavi/scripts/ccnavi-fetch.sh` | `sh` |
| `.claude/hooks/test-py.sh` | `e2e` |
| `.claude/hooks/mark-ext.sh`・`test-ext.sh`・`vscode-extension/ccnavi-board/scripts/test-groups.js` | `core`（`test_ext_tests`） |
| `tests/fixtures/` | `guard` `ticket` |
| `vscode-extension/` | `ticket`（`core` の `test_test_json` も例を読む） |
| `docs/adr/`（枚を足す・番号を動かす） | `core`（`test_adr_numbers`） |
| そのほかのドキュメントだけ（`*.md`・`docs/`） | 回さない |

自動テストで中身を確かめられないもの（変えたら手で動かして確かめる）:

- `.claude/hooks/lint-py.sh`
- `.claude/hooks/test-py.sh` の差し戻しの回数と、落ちたテストを差し戻すところ（`e2e` は sh の外形だけ見る）

全件を回すとき:

- 表のどの行にも当たらないファイルを変えた
- `.ccnavi/scripts/ccnavi-common.sh`、`.ccnavi/common/`、`.ccnavi/config/`、`tests/__init__.py`、`tests/inproc.py`、
  `pyproject.toml`・`uv.lock` を変えた（ほぼ全グループが読む）
- 統合先へ戻す前、MR に出す前
- どの行に当たるか迷った

`ccnavi/*.py` を変えたとき `e2e` は足さない（e2e は組み立て済みの実行ファイルを試す）。組み立て直したなら足す。

## 拡張（`vscode-extension/ccnavi-board`）のグループ

拡張側にも同じ分け方がある（`test/<グループ>/`。board / rules / risk / phases / projects / flow / shared）。
こちらは表を引かない。変えたファイルを渡せば、関わるグループだけが回る（ADR-0061）。

```sh
cd vscode-extension/ccnavi-board
pnpm test:plan src/core/rules-doc.ts   # 何を回すかだけ見る
pnpm test:for src/core/rules-doc.ts    # 回す
pnpm test                              # 全部
```

コンパイルは 1 回なので、変えたファイルは 1 回でまとめて渡す（`pnpm test:for a.ts b.ts`）。

## 表を直すとき

グループを足す・分け直すときは、ファイルを `tests/<グループ>/` へ移し、テストどうしの import
（`from tests.<グループ>.test_x import ...`）がグループをまたがないようにする。またぐなら、共有する
部品を `tests/` 直下の補助モジュールへ出す。置き場の決まりは `tests/core/test_layout.py` が見る。
