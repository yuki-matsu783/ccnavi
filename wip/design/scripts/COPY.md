# 写す手順

このディレクトリの 7 本は、エージェントが書けない場所（保護済みスクリプト・hook・
ルールファイル）の完成品です。チケット `sh-ws-root` のフェーズ 3 の成果物。

エージェントがここに置き、**人が写します**。ガードは緩めていません。
`judge.py:246-247` のとおり、ルールが `deny` と言う場所はチケットの承認でも書けません。

差分ではなく全文を置いてあります。手で当てる工程を無くすためです。

## 写す前に見る

何が変わるかを、写す先と突き合わせて確かめてください。

```sh
cd <ワークスペースルート>
for f in ccnavi-common.sh ccnavi-git.sh ccnavi-ticket.sh ccnavi-review.sh; do
	diff -u ".claude/scripts/$f" ".claude/worktrees/sh-ws-root-03/wip/design/scripts/$f"
done
diff -u .claude/hooks/test-py.sh .claude/worktrees/sh-ws-root-03/wip/design/scripts/test-py.sh
diff -u .claude/ccnavi/rules.yml .claude/worktrees/sh-ws-root-03/wip/design/scripts/rules.yml
diff -u scripts/ccnavi-setup.sh .claude/worktrees/sh-ws-root-03/wip/design/scripts/ccnavi-setup.sh
```

`ccnavi-common.sh` は新設なので、`diff` は「写す先が無い」と言います。それで正しいです。

## 写す

```sh
cd <ワークスペースルート>
src=.claude/worktrees/sh-ws-root-03/wip/design/scripts
cp "$src/ccnavi-common.sh" .claude/scripts/ccnavi-common.sh
cp "$src/ccnavi-git.sh"    .claude/scripts/ccnavi-git.sh
cp "$src/ccnavi-ticket.sh" .claude/scripts/ccnavi-ticket.sh
cp "$src/ccnavi-review.sh" .claude/scripts/ccnavi-review.sh
cp "$src/test-py.sh"       .claude/hooks/test-py.sh
cp "$src/rules.yml"        .claude/ccnavi/rules.yml
cp "$src/ccnavi-setup.sh"  scripts/ccnavi-setup.sh
```

`ccnavi-common.sh` を先に写してください。3 本が起動時にこれを `.` で読むので、
先に本体だけ写すと、その間 sh が全部動かなくなります。

## 写したあとに確かめる

```sh
CCNAVI_E2E=1 uv run python -m unittest tests.test_e2e_sh -v
```

20 件すべて緑になること。写す前は `CCNAVI_SH_DIR=wip/design/scripts` を付けて
同じものを回しており、そちらでは既に 20 件緑、付けない（＝今の配布版）と 16 件赤です。

既存の in-process テストも回してください。

```sh
uv run python -m unittest discover -s tests -t . -q
```

## 戻し方

写す前の版は git が持っています。

```sh
git restore .claude/scripts .claude/hooks/test-py.sh .claude/ccnavi/rules.yml scripts/ccnavi-setup.sh
rm .claude/scripts/ccnavi-common.sh   # 新設なので restore では消えない
```

## 何が変わるか

| ファイル | 変更 |
|---|---|
| `ccnavi-common.sh` | 新設。ワークスペースルートの探し方、プロジェクト名の導出、URL の伏せ字、絶対パス化 |
| `ccnavi-git.sh` | 記録を `<ws>/logs/<プロジェクト>/` へ。push ガードの基準をワークスペースへ。`worktree add` の行き先を検査 |
| `ccnavi-ticket.sh` | 根の導出を差し替え |
| `ccnavi-review.sh` | 根の導出を差し替え。`origin` の伏せ字を集約し、生の URL を文面に残さない。ブランチ名のスラッシュ |
| `test-py.sh` | `tests/` を持たないツリーを飛ばす |
| `rules.yml` | 拒否の文面 3 か所を `{root}/.claude/scripts/...` へ |
| `ccnavi-setup.sh` | 配布と点検の一覧に `ccnavi-common.sh` を足す |

## 打ち方が変わるもの

プロジェクトの中から作業ツリーを切るとき、相対の綴りが変わります。

```sh
# これまで（プロジェクトの中で打つと、プロジェクトの中にできていた）
cd projects/lib && sh ../../.claude/scripts/ccnavi-git.sh worktree add .claude/worktrees/x -b x main

# これから（止められる。文面が正しい綴りを出す）
cd projects/lib && sh ../../.claude/scripts/ccnavi-git.sh worktree add ../../.claude/worktrees/x -b x main
```

`worktree add` で、一覧に無いオプションは通らなくなります。行き先を取り違えると
検査が意味を失うためです。使いたい形が出たら、一覧に足す変更を出してください。
