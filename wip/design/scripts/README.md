# 写す sh（i0064-04 置き場の固定）

`.ccnavi/scripts/` はエージェントが書けない場所なので、完成品をここに置き、人が写す。
設計は `wip/design/i0064-fixed-places.md` §3、チケットは `.ccnavi/approved/doing/i0064-04.md`。

**この手順は人が端末で打つ。** エージェントは `.ccnavi/scripts/` に書けず、git も `ccnavi-git.sh` を
通すので、ここに書いた `cp` や生の `git` をエージェントがそのまま実行しない。

**写す先は親のワークツリー**（`.claude/worktrees/i0064/.ccnavi/scripts/`）。ワークスペースルートの
`.ccnavi/scripts/`（`main`）には写さない。統合先へは親のブランチごと入る。

| ここのファイル | 写す先 | 変えたところ（他は土台と同じ） |
|---|---|---|
| `ccnavi-common.sh` | `.ccnavi/scripts/ccnavi-common.sh` | `ccnavi_project` の `ccnavi_pj_places=projects` |
| `ccnavi-fetch.sh` | `.ccnavi/scripts/ccnavi-fetch.sh` | `approved=.ccnavi/approved`・`projects=projects` |
| `ccnavi-git.sh` | `.ccnavi/scripts/ccnavi-git.sh` | 子の push の検査で置き場を直に書き、絶対パスの分岐（`case` 3 つ）を消す |
| `ccnavi-push-approved.sh` | `.ccnavi/scripts/ccnavi-push-approved.sh` | 置き場 3 つを直に書き、綴りの正規化（`%/` 3 行と `case` 3 つ）を消す。冒頭の説明から `$CCNAVI_…` を消す |
| `ccnavi-review.sh` | `.ccnavi/scripts/ccnavi-review.sh` | `state="$root/logs/state"` |
| `ccnavi-approve.sh` | `.ccnavi/scripts/ccnavi-approve.sh` | 冒頭の説明だけ（`.ccnavi/approved/doing/`） |

6 本とも既にあるファイルの差し替え。新しいファイルは無い。

## 写す前に: 土台が動いていないことを確かめる

写す版は、下の blob を土台にして作った。写す先の今のファイルがこれと違えば、その間に誰かが
`.ccnavi/scripts/` を変えている。**違えば写さず、写す版を作り直してもらう。** 写すと、その変更を黙って消す。

親のワークツリーで打つ。

```sh
git hash-object .ccnavi/scripts/ccnavi-common.sh .ccnavi/scripts/ccnavi-fetch.sh \
  .ccnavi/scripts/ccnavi-git.sh .ccnavi/scripts/ccnavi-push-approved.sh \
  .ccnavi/scripts/ccnavi-review.sh .ccnavi/scripts/ccnavi-approve.sh
```

| ファイル | 土台の `git hash-object` |
|---|---|
| `ccnavi-common.sh` | `d21cb5e8133db908987a132a8d221e80583683c1` |
| `ccnavi-fetch.sh` | `f7f3b9944382aa1b3f9ac1f3a1fe11e903158060` |
| `ccnavi-git.sh` | `9bc2a7982976c82587ca0ebbef1b75135ff1084a` |
| `ccnavi-push-approved.sh` | `097b9fc03ace078c95665cd5d8387f2ba5e67266` |
| `ccnavi-review.sh` | `91635fc85e96240bb58ab8c717975f3b16b2ccb8` |
| `ccnavi-approve.sh` | `c6eb5af8b2ba5fda4ef2088a92370bad7e7d9d28` |

土台は親のブランチの 9f33635（`main` を取り込んだ直後）の `.ccnavi/scripts/`。
写す版との差分は `diff .ccnavi/scripts/<名前> wip/design/scripts/<名前>` で見られ、上の表の箇所だけが出る。

## 写す

**6 本を 1 つのコミットで写す。** どれか 1 本だけ写すと、sh の間で置き場の決まり方が食い違う。
たとえば `ccnavi-git.sh` だけ写すと、子の push の検査は既定の置き場だけを見るのに、
`ccnavi-push-approved.sh` は env が指す置き場を運ぶ。

親のワークツリーで打つ。

```sh
cp wip/design/scripts/ccnavi-common.sh wip/design/scripts/ccnavi-fetch.sh \
   wip/design/scripts/ccnavi-git.sh wip/design/scripts/ccnavi-push-approved.sh \
   wip/design/scripts/ccnavi-review.sh wip/design/scripts/ccnavi-approve.sh .ccnavi/scripts/
git add .ccnavi/scripts/
git diff --cached --summary   # mode change が出ないこと（cp は写す先のモードを残す）
git commit -m "feat: 置き場の env を読まない sh を写す（i0064-04）"
```

### 実行ビット

6 本とも既にあるファイルなので、実行ビットの付け直しは要らない。`cp` は写す先が既にあれば
そのモードを残すので、`ccnavi-approve.sh`・`ccnavi-fetch.sh`・`ccnavi-push-approved.sh` は 100755、
残りは 100644 のまま残る。`git diff --cached --summary` に `mode change` が出ていなければよい。

## 写したあとに確かめる

親のワークツリーで打つ。

```sh
uv run python -m unittest discover -s tests -t .
grep -rnE 'CCNAVI_(PROJECTS|PROJECT_HOME|TICKETS_PROPOSAL|TICKETS_APPROVED|LOG|STATE)\b' .ccnavi/scripts/
sh .ccnavi/scripts/ccnavi-git.sh status
sh .ccnavi/scripts/ccnavi-review.sh --help
```

- 全件が通る。置き場の env を読まないことを見る A9 の 3 本（`tests.sh.test_gitwrap` の
  `PlacesAreNotReadTest`、`tests.core.test_review_origin` の `test_the_state_variable_is_not_read`、
  `tests.sh.test_push_approved_sh` の `test_does_not_read_the_place_variables`）は、写す前は落ち、ここで通る
- `grep` は何も返さない
- `ccnavi-git.sh status`（ワークツリーの中で）と `ccnavi-review.sh --help` が今どおり動く

写す前に写す版だけを確かめるなら、`CCNAVI_SH_DIR` で sh の出どころを差し替える
（写す版に無い sh も読むテスト、`tests.sh.test_clean_sh`・`tests.sh.test_bin_lookup`・
`tests.e2e.test_e2e_sh` はこれでは回さない）。

```sh
CCNAVI_SH_DIR=wip/design/scripts uv run python -m unittest \
  tests.sh.test_gitwrap tests.core.test_review_origin tests.sh.test_review_decide \
  tests.sh.test_push_approved_sh tests.sh.test_fetch_sh tests.guard.test_wrapguard
```

## 元に戻す

コミットする前:

```sh
git restore --staged --worktree .ccnavi/scripts/
```

コミットしたあとは、写したコミットを `git revert` する。戻すときも 6 本を一緒に戻す。
