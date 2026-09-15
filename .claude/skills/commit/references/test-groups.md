# テストのグループと、回すグループの決め方

コミット前の検査（SKILL.md の手順 2）で、全件ではなくグループ単位でテストを回すための表。

## グループ

`tests/<グループ>/` に置く。時間は macOS で 1 本ずつ回したときの目安。

| グループ | 主題 | 時間 |
|---|---|---|
| `core` | 部品の単体と、速い受入テスト（shellread・glob・lint・`build.py` の形・sh の書き方など） | 約 5 秒 |
| `guard` | 判定とルール（受入テスト、自己保護、運用のルール、フォールバック、実行後の監視、プロジェクト） | 約 60 秒 |
| `config` | 設定の層の合成（rules / phases / risk） | 約 33 秒 |
| `ticket` | チケット・フェーズ・承認・ボード・リスク | 約 140 秒 |
| `sh` | 配布する sh と導入スクリプト（setup・git のラッパー・運ぶ sh・ランチャー・clean） | 約 80 秒 |
| `e2e` | 本物のワークスペースを組み立てて sh を外から叩く。組み立て済みの実行ファイル（`dist/ccnavi`）が要り、無ければ skip | 約 8 秒 |

```sh
uv run python -m unittest discover -s tests/core -t .    # 1 グループ
uv run python -m unittest discover -s tests -t .         # 全件
```

複数のグループは 1 つずつ続けて回す（`discover -s` は 1 か所しか取らない）。

## 回すグループ

**`core` はいつも回す。** そのうえで、変えたファイルを下の表に当てて、当たった行のグループを足す。

| 変えたもの | 足すグループ |
|---|---|
| `tests/<グループ>/` の中 | そのグループ |
| `ccnavi/*.py`・`main.py` | `guard` `config` `ticket` |
| `ccnavi/platformtag.py` | 上に加えて `sh` |
| `build.py` | `guard` `sh` `e2e` |
| `.ccnavi/scripts/ccnavi-ticket.sh`・`ccnavi-approve.sh`・`ccnavi-review.sh` | `ticket` `config` `e2e` |
| `.ccnavi/scripts/ccnavi-git.sh` | `sh` `guard` `config` `e2e` |
| `.ccnavi/scripts/ccnavi-launcher.sh`・`scripts/ccnavi-setup.sh` | `sh` `guard` `config` `e2e` |
| `.ccnavi/scripts/ccnavi-push-approved.sh`・`ccnavi-clean.sh`・`ccnavi-clean.js`・`ccnavi-fetch.sh` | `sh` `config` `e2e` |
| `.claude/hooks/*.sh` | `guard` `e2e` |
| `tests/fixtures/` | `guard` `ticket` |
| `vscode-extension/` | `ticket` |
| ドキュメントだけ（`*.md`・`docs/`） | 回さない |

**全件を回すとき。**

- 表のどの行にも当たらないファイルを変えた
- `.ccnavi/scripts/ccnavi-common.sh`、`.ccnavi/common/`、`.ccnavi/config/`、`tests/__init__.py`、`tests/inproc.py`、
  `pyproject.toml`・`uv.lock` を変えた（ほぼ全グループが読む）
- 統合先へ戻す前、MR に出す前
- どの行に当たるか迷った

`ccnavi/*.py` を変えたとき `e2e` は足さない。e2e が試すのはソースではなく組み立て済みの実行ファイルで、
組み立て直さないと変更が届かないため。組み立て直したなら足す。

## 表を直すとき

グループを足す・分け直すときは、ファイルを `tests/<グループ>/` へ移し、テストどうしの import
（`from tests.<グループ>.test_x import ...`）がグループをまたがないようにする。またぐなら、共有する
部品を `tests/` 直下の補助モジュールへ出す。置き場の決まりは `tests/core/test_layout.py` が見る。
