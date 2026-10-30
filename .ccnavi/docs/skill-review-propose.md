---
type: guide
title: スキルの提案チケットと候補の書き方
description: スキルの振り返りで出す提案チケットの切り方・書き方（識別子、ccnavi-start.sh、frontmatterの見本）と、候補を書くときの注意
tags: [skills, feedback]
keywords: [振り返り, スキル, 候補, 提案, 提案チケット, ccnavi-start.sh, skill-improve, 識別子, mr, allow, yomiyasu]
---

# スキルの提案チケットと候補の書き方

拾うもの・足す先・候補の渡し先は `.ccnavi/docs/skill-review.md`。ここは、候補を書くときの注意と、提案チケットの書き方。

## 候補を書くときの注意

- 直す前に、対象の SKILL.md と、手を入れる references/ を読み直す。会話中の引用で済ませない
- 1つの事実は1か所に書く。同じ規則がすでにあるなら、新しく足さずにその文を強めるか言い直す
- 手順の流れに沿って書き、落とし穴は該当する手順に「規則 + なぜ（1 句）」で添える。日付・番号・ユーザの言葉の引用を中身にしない
- yomiyasuスキルを `.ccnavi/docs/`・`CLAUDE.md`・`.claude/skills/`・プロジェクトの `skills/`・`rules.yml` の `message` に適用するのは、ユーザが明示的に頼んだときだけにする。その場合も、表・箇条書き・「規則 + なぜ」の形は崩さない。yomiyasuはこれらの形を平文に崩すため
- 誤りを見つけたら、間違った文そのものを直す

## 提案チケット

提案は、その提案の識別子と同名のワークツリーに書く。提案ごとに専用のツリーなので、作業中のMRに提案のファイルは混ざらない。`skill-proposals` のような別名のツリーにまとめて書かない。承認済みチケットは提案を書いたツリーに置かれるので、別名のツリーで承認すると親が取り込み済みにならず、`ready` で止まる。承認の画面と `start`・`finish` が先に止めるが、止まってからでは承認のやり直しになる（`.ccnavi/docs/worktree.md`「直接作業からチケット作業に移るとき」）。

識別子は `chore-<issueの番号>-skill-<スキル名>-<要点>` の形にする。末尾を `-<2桁の数>` にしない（子の識別子`<親>-<2桁>-<2桁>`と紛れる）。

親の提案には、その仕事を載せるMRの番号（`mr:`）が要る。ワークツリーは`ccnavi-worktree.sh`（直接作業用）では切らず、issueを立ててから`ccnavi-start.sh`で切る。ブランチ・ワークツリー・Draft MRを作り、`mr:`に書く番号を出す
（`.ccnavi/docs/worktree-start.md`）。

| 対象 | 切り方 |
|---|---|
| ワークスペースのスキル | ワークスペースルートで `sh <ワークスペースルート>/.ccnavi/scripts/ccnavi-start.sh --issue <番号> --prefix chore --slug skill-<スキル名>-<要点>` |
| プロジェクトのスキル | `cd projects/<プロジェクト>` して、同じ `sh <ワークスペースルート>/.ccnavi/scripts/ccnavi-start.sh --issue <番号> --prefix chore --slug skill-<スキル名>-<要点>`（そのプロジェクトのissue） |

そのツリーの `wip/proposals/todo/<識別子>.md` に、出力の「必須」の行の `mr: <番号>` を入れて書き、`sh <ワークスペースルートの絶対パス>/.ccnavi/scripts/ccnavi-ticket.sh propose <識別子>` で親のブランチへ送り、パスをユーザに伝えて承認を頼む。複数の提案は、1本ずつ同名のワークツリーに書く。

提案の frontmatter と本文の書式は [proposal-format.md](proposal-format.md) のとおり。スキルの提案で決めるのは次の点。

- `ticket` は上の識別子、`mr` は `ccnavi-start.sh` の出力の「必須」の行の番号
- `human_review` は `required: true`、理由は「エージェントの手順を変えるため」
- `plan` は `quick-work` の 1 ステップ（`scope: inherit`）にする。`phases:` は手で書かず、`ccnavi --plan-order <親> --fill-phases` でコピーしてよい
- `allow` に、直すスキルのパス（例: `.claude/skills/commit/*`）と、動機のパターンと採否の記録を入れる wiki の置き場（`skill-wiki.md`）を書く。`allow` を空のまま出すと、作業の範囲が決まらず子を起こせない
- `title` は変更の要点、`rationale` は拾った信号と、次のセッションで何が変わるか
- 本文は、対象の SKILL.md（と references/）の差分。新しく作るなら全文の下書き。動機になったパターンと、採否の記録（`承認待ち`）も同じチケットに入れる

プロジェクトのスキルについての提案は、そのプロジェクトの提案として書き、`allow` に `skills/<スキル>/*` を書く。
パスはプロジェクトのルートからの相対で書く。
