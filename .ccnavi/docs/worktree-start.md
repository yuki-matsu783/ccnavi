---
type: guide
title: issue・MRを指定して着手する
description: issueやMRを指定された作業の着手（ccnavi-start.sh）、ホストに届かないときのMCPでの代行、既存の/を含むブランチでの親チケットの作業
tags: [git, worktree]
keywords: [issue, MR, ccnavi-start.sh, ccnavi-branches.sh, 既存のブランチ, MCP, ワークツリー, 着手, ブランチ名, 親チケット]
---

# issue・MRを指定して着手する

ワークツリーの一般的な切り方は[worktree.md](worktree.md)。

## issue・MRの指定を受けたとき

- issueやMRを指定して作業を頼まれたら（`#152`・`!5`・URLなど）、ワークツリーを切る前に
  `sh <ワークスペースルート>/.ccnavi/scripts/ccnavi-start.sh --issue <番号>`（MRなら`--mr <番号>`）を打つ。
  紐づくブランチを`ccnavi-branches.sh`で探し、結果に応じて進める。プロジェクトのissue・MRは`projects/<名前>`に`cd`してから打つ。
  `--slug <語>`・`--prefix <語>`・`--title <題>`で名前と題を指定でき、`--dry-run`なら何も作らず予定だけ出す（リモートは読むだけ）

  | 結果 | 終了コード | エージェントの動き |
  |---|---|---|
  | 候補が1件 | 0 | そのブランチのワークツリーのパスが出る（無ければ`.claude/worktrees/<識別子>`に作る。`/`を含むブランチは下の手順で識別子に直し、`switch`まで済ませる。その識別子の親チケットがあると`switch`が拒否されるので、下の手順に従う）。続きの作業はそこで行う |
  | 候補が0件（`--issue`） | 0 | 統合先から`<先頭の語>-<番号>-<slug>`を切り、ワークツリー・空の初期コミット・push・Draft MR（本文に`Closes #<番号>`）まで作り、パス・ブランチ名・MRのURLを1項目1行で出す |
  | 候補が複数、または統合先より進んでおらずMRも無い古いブランチ（`--issue`） | 3 | 何も作られていない。出た一覧をユーザに見せ、既存のブランチで続けるか（下の`branch:`の手順。承認前の提案の`branch:`は使わない）・新しく切るか・やめるかを聞いて、返事を待つ |
  | ホストを見られない | 4 | 既存のMRを見落として重複を作らないため、新規作成に進まず止まる。下の「ホストに届かないとき」に従う。手元の候補がちょうど1件なら、「ホストは見ていない」と断って続ける |
  | フォークのMR、前提の未充足 | 1 | 理由が出る。ユーザに伝えて判断を仰ぐ |
  | 引数の誤り（`--slug`が要る、など）、issueが無い・権限が無い・PRの番号だった | 2 | 理由が出る。引数の誤りは文面に従って打ち直し、issueの問題は番号を確かめてユーザに伝える |

  途中で失敗して打ち直しても、出来たところ（ブランチ・ワークツリー・push・MR）は飛ばして続きから進む。
  依頼文にissue・MRの指定があると、ccnaviがUserPromptSubmitで同じ指示を足す。止めはしない
- ホストに届かないとき（Claude remoteのコンテナでは、GitHubのAPIに届かないことがある）、`ccnavi-start.sh`は終了コード4で止まり、
  どの段で届かなかったか・その段までに出来たこと・MCPで行う手順を標準出力に出す。エージェントはその手順どおりに`mcp__github__`のツールで代行し、
  同じコマンドを打ち直す。段は3つある
  - 既存のMRを確かめる段: `mcp__github__search_pull_requests`で探す。見つかれば候補としてユーザに聞く。無いと確かめたら`--mcp-checked`を付けて打ち直す
  - issueの題を読む段（slugが決められないとき）: `mcp__github__issue_read`で題を読み、`--slug <語> --title '<題>'`を付けて打ち直す
  - MRを作る段（ブランチ・ワークツリー・pushは済んでいる）: `mcp__github__create_pull_request`を、案内の値（`head`はブランチ名、`base`は統合先、
    `title`、`body`は`Closes #<番号>`、`draft: true`）で呼ぶ。作れたら、案内のコマンド（`--mcp-checked`は外してある。付けたままだと、MCPで作ったMRを見ずに止まり続ける）を打ち直すと、続きからワークツリーのパスが出る
  GitLabにはMCPの案内が無い。届かない理由と手で打つ手順が出るので、glabの認証か`GITLAB_TOKEN`をユーザに頼んで打ち直す。
  gitのpushが通らないときは、従来どおり`ccnavi-git.sh`の拒否の出力に従う

## 既にある`/`を含むブランチで親チケットの作業をするとき

- 既にある`feature/123-login`のような`/`を含むブランチで親チケットの作業をするときは、識別子は`/`を含まない
  `feature-123-login`にし、提案に`branch: feature/123-login`を書く。承認されるまでは`branch:`は使われないので、
  親のワークツリーは識別子のブランチで切る（`worktree add .claude/worktrees/feature-123-login -b feature-123-login <統合先>`）。
  承認の後、親のワークツリーで`ccnavi-git.sh switch feature/123-login`を打つと、そのブランチへ移って承認済みチケットと
  マーカーを取り込む。既にあれば識別子のブランチをmergeし、無ければ切る。作業ツリーは綺麗にしておく。続けて
  `ccnavi-git.sh push -u origin feature/123-login`を打つ。子のブランチは子の識別子で、起点は親のブランチ
  （`-b <子> feature/123-login`）。`ccnavi-sync.sh`の引数は識別子
  - 子のワークツリーは、VS Code拡張で承認したときに親のブランチから自動で切る（親のワークツリーが在り、子のものがまだ無いとき）。
    拡張は承認のJSONの`worktrees`の`create`だけを`ccnavi-git.sh worktree add <行き先> -b <子> <base>`で作る。作れなかった子は、
    警告の通知に、ワークツリー名と手で打つコマンド（同名のブランチが既にあるときの形も）を出す。承認の文面（Claude Codeに渡す文）には
    作成の失敗は入らない。親のワークツリーが無い子とプロジェクトの子は自動で切らない。
    ターミナルの承認（`--agree`）では自動で切らない
