# 写す sh（approve-carry）

`.ccnavi/scripts/` はエージェントが書けない場所なので、完成品をここに置き、人が写す。
設計は `wip/design/approve-carry.md` §1。

| ここのファイル | 写す先 | 中身 |
|---|---|---|
| `ccnavi-push-approved.sh` | `.ccnavi/scripts/ccnavi-push-approved.sh` | 新規。承認済みチケットの置き場だけをコミットして push する |
| `ccnavi-approve.sh` | `.ccnavi/scripts/ccnavi-approve.sh` | 差し替え。後半（コミットと push）を消し、`ccnavi-push-approved.sh` を呼ぶ |

## 写す

**2 本を同じコミットで写す。** 片方だけだと次のように壊れる。

- `ccnavi-approve.sh` だけ: 呼ぶ先が無く、承認のあと何も運ばれない（`|| :` で黙って 0 になる）。
  `tests.test_push_approved_sh` の `ApproveCarriesTest` が落ちる
- `ccnavi-push-approved.sh` だけ: `ccnavi-approve.sh` が古い後半で運び続け、置き場の下の
  シンボリックリンクや `git add` の失敗の直しが端末の承認に効かない

ワークスペースルートで打つ。

```sh
cp wip/design/scripts/ccnavi-push-approved.sh wip/design/scripts/ccnavi-approve.sh .ccnavi/scripts/
```

### 実行ビット

どちらも `sh .ccnavi/scripts/<名前>` で呼ぶので、実行ビットが無くても動く。今の Git の上では
`ccnavi-approve.sh` と `ccnavi-fetch.sh` が 100755、他は 100644。`wip/` の下のファイルは
実行ビットを持たずに作られるので、揃えるなら写したあとに付ける。

```sh
chmod +x .ccnavi/scripts/ccnavi-push-approved.sh .ccnavi/scripts/ccnavi-approve.sh
git update-index --chmod=+x .ccnavi/scripts/ccnavi-push-approved.sh   # add したあと。Windows でも Git 上のモードが付く
```

`cp` は写す先が既にあればそのモードを残すので、`ccnavi-approve.sh` は 100755 のまま。
`git diff --cached --summary` に `mode change` が出ていなければよい。

## 写したあとに回すテスト

```sh
uv run python -m unittest tests.test_push_approved_sh tests.test_sh_portability tests.test_setup
```

- `tests.test_push_approved_sh`: 運ぶ sh と、`ccnavi-approve.sh` が承認のあと運ぶこと
- `tests.test_sh_portability`: macOS の bash 3.2 と BSD の道具で読める書き方
- `tests.test_setup`: 導入スクリプトが `ccnavi-push-approved.sh` を配ること（配布元に無いと落ちる）

承認の照合（`tests.test_approve_json`）は sh に依らないので、写す前から通る。

## 元に戻す

コミットする前:

```sh
git restore --staged --worktree .ccnavi/scripts/ccnavi-approve.sh
git rm --cached -q .ccnavi/scripts/ccnavi-push-approved.sh 2>/dev/null; rm -f .ccnavi/scripts/ccnavi-push-approved.sh
```

コミットしたあとは、写したコミットを `git revert` する。戻すときも 2 本を一緒に戻す。
`ccnavi-push-approved.sh` だけを消すと、写した `ccnavi-approve.sh` が呼ぶ先を失い、承認のあと何も運ばれない。
