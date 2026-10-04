# ccnavi-common.sh を入口と部品 5 本に分ける下書き

`.ccnavi/scripts/ccnavi-common.sh`（1885 行）を、入口 1 本と部品 5 本に分けた下書きです。
`.ccnavi/scripts/` は保護されていてエージェントが書けないので、ここからユーザが写します。

## 中身

| 下書き | 写す先 | 中身 |
|---|---|---|
| `ccnavi-common.sh` | `.ccnavi/scripts/ccnavi-common.sh`（置き換え） | 冒頭の説明、`CCNAVI_COMPAT`、基礎の関数（abs・phys・workspace・bin・compat_skew・bin_try・project・mask_url・is_ident・is_branch）、部品を読む処理 |
| `ccnavi-common-state.sh` | `.ccnavi/scripts/ccnavi-common-state.sh`（新規） | 取り込み状態と統合先（`ccnavi_state` から `ccnavi_parent_tree` まで）、タイムアウト監視つきの git（`ccnavi_git_timed`・`ccnavi_git_refusal`） |
| `ccnavi-common-lock.sh` | `.ccnavi/scripts/ccnavi-common-lock.sh`（新規） | ロック（`ccnavi_lock_*`） |
| `ccnavi-common-c1.sh` | `.ccnavi/scripts/ccnavi-common-c1.sh`（新規） | C1（`ccnavi_c1_*`） |
| `ccnavi-common-host.sh` | `.ccnavi/scripts/ccnavi-common-host.sh`（新規） | ホストへの接続（`ccnavi_host_*`） |
| `ccnavi-common-log.sh` | `.ccnavi/scripts/ccnavi-common-log.sh`（新規） | 診断ログ（`log_*`・`ccnavi_log_*`） |

- 関数名・変数名・関数の中身は元のままです。元の行を切り出して並べ替えただけで、足したのは
  入口の冒頭の説明（部品の一覧）、入口の末尾の部品を読む処理、各部品の先頭 3 行の見出しです
- 呼ぶ側（`ccnavi-git.sh` など）は今までどおり `. "$(dirname "$0")/ccnavi-common.sh"` で入口だけを読みます。
  呼ぶ側の sh は直しません
- 部品の置き場は `$0`（呼んだ sh）のディレクトリから求めます。環境変数からは受け取りません。
  部品が 1 本でも欠けていれば、`scripts/ccnavi-setup.sh --force で配り直してください` と標準エラーに出して 1 で終わります
- `CCNAVI_COMPAT` は入口に残しています。`ccnavi --lint` と Chrome 拡張がこのファイルから読むためです。値は変えていません

## 写す手順

ワークスペースルートで、`main` に下書きのコミットを取り込んだ後に実行します。

1. 部品 5 本を先に写し、入口を最後に写す（入口だけが新しく、部品が無い状態を作らないため）

   ```sh
   for p in state lock c1 host log; do
   	cp wip/sh-split/ccnavi-common-$p.sh .ccnavi/scripts/ccnavi-common-$p.sh
   done
   cp wip/sh-split/ccnavi-common.sh .ccnavi/scripts/ccnavi-common.sh
   ```

2. 改行を LF のまま保つ。エディタで開いて保存し直さず、`cp` で写します。`.gitattributes` の
   `*.sh text eol=lf` で取り出しは LF になりますが、作業ツリーに CRLF で書くと差分に出ます。
   次で全部が `i/lf w/lf` になっていることを確かめます

   ```sh
   git ls-files --eol .ccnavi/scripts/ccnavi-common*.sh   # git add の後
   ```

3. 写した結果が下書きと同じかを確かめる

   ```sh
   for f in wip/sh-split/ccnavi-common*.sh; do cmp "$f" ".ccnavi/scripts/${f##*/}"; done
   ```

4. モードは今の `ccnavi-common.sh` と同じ 100644 のままにします（`.` で読むだけで、直に起動しません）
5. `.ccnavi/scripts/ccnavi-common*.sh` の 6 本と、`wip/sh-split/` の削除を 1 つのコミットにします

## 写した後に回すテスト

分けた後の動きは、写した後でしか確かめられません。共通部はほぼ全部のグループが読むので全件を回します。

```sh
uv run python tools/run_tests.py                         # 全件（discover -s tests -t . と同じ中身）
uv run --with ruff ruff check . && uv run --with ruff ruff format --check .
sh scripts/ccnavi-setup.sh --check                       # 「配布元に無くて配れないもの」に部品が出ないこと
```

特に見るもの:

- `tests/sh/`（共通部を読む sh を全部起こす。部品の読み込みが通るか）
- `tests/core/test_sh_portability.py`（bash 3.2 で読めない書き方が無いか）
- `tests/core/test_lint_compat.py`・`tests/sh/test_compat_skew.py`（`CCNAVI_COMPAT` を入口から読めるか）
- `tests/e2e/`（組み立て済みの実行ファイル `dist/ccnavi` があるときだけ回る）

手で 1 度、dash と bash で読み込めることも確かめられます（どちらも何も出さずに 0 で終われば読めています）。

```sh
for s in dash bash; do $s -c '. "$0"; command -v ccnavi_c1_end ccnavi_host_api log_info ccnavi_lock_take ccnavi_git_timed >/dev/null' .ccnavi/scripts/ccnavi-common.sh; echo "$s $?"; done
```

## 配布先

配布先は `sh scripts/ccnavi-setup.sh --force` で配り直すと、入口が入れ替わり部品 5 本が足されます。
`--force` を付けないと、部品は足されて入口は古い 1 本のまま残ります。古い入口は部品を読まないので、
そのままでも動きます。
