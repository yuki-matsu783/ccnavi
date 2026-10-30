---
type: adr
title: 統合先は各リポジトリの origin/HEAD だけで決め、環境変数と --integration-branch を廃止する
description: 統合先ブランチの名前を、そのリポジトリの手元の origin/HEAD（無ければ origin/main・origin/master）だけで決める。CCNAVI_INTEGRATION_BRANCH・settings.local.json の読み・--integration-branch を廃止し、SessionStart で origin/HEAD をホストの既定ブランチに合わせる
tags: [worktree, git, push, multi-repo]
keywords: [統合先, origin/HEAD, CCNAVI_INTEGRATION_BRANCH, --integration-branch, 既定ブランチ, set-head, ccnavi-fetch.sh, B1, 識別子の予約]
---

# ADR-0148: 統合先は各リポジトリの origin/HEAD だけで決め、環境変数と --integration-branch を廃止する

状態: 採用

## 状況

統合先ブランチの名前は、次の順で決めていた。

1. 環境変数 `CCNAVI_INTEGRATION_BRANCH`
2. ワークスペースの `.claude/settings.local.json` の env にある同じキー
3. 対象ツリーの `origin/HEAD` → `origin/main` → `origin/master`

決める場所は 2 つ。sh は `ccnavi-common-state.sh` の `ccnavi_integration`、Python は `ops_stop_integrate.integration_name`（Python にはさらに `--integration-branch` が挟まる）。

1 と 2 はワークスペースで 1 つの値なので、`projects/` の下のどのプロジェクトにも同じ名前が該当する（`docs/21_design/multi-repo/standalone-clone-gaps.md` の B1）。

## 決定

- 統合先は、そのリポジトリの手元の `refs/remotes/origin/HEAD` だけで決める。無ければ `origin/main` → `origin/master`。sh も Python も同じ決め方にする
- `CCNAVI_INTEGRATION_BRANCH` と `--integration-branch` を廃止する。`settings.local.json` から統合先を読む処理も無くす。上書きの手段は用意しない
- origin の無いリポジトリ（ローカルだけ・未 push）では統合先は決まらない。ローカルの `main`・`master` で代わりにはしない。`integrate` は `integrate-no-integration` で断り、Stop の促しは出さない。案内は「統合先 (origin/HEAD) が決まらない。ユーザに `git fetch origin` のあと `git remote set-head origin -a` を頼んでください」（origin が無ければ「origin が無いので統合先が決まらない」）
- 手元の `origin/HEAD` は SessionStart でホストに合わせる。`ccnavi-fetch.sh` が、承認済みチケットの取得（第 1 周）の後、ワークツリーの起点を進める（第 2 周）前に、リポジトリごとに `ls-remote --symref origin HEAD` でホストの既定ブランチを聞く。手元と違えばそのブランチを fetch し、成功してから `git symbolic-ref refs/remotes/origin/HEAD` で向け直して 1 行出す。失敗・時間切れ・オフラインのときは、何も出さず何も変えない。`git remote set-head origin -a` は手元に `refs/remotes/origin/<名前>` が無いと失敗するので使わない
- `ccnavi-sync.sh` は、ホストに聞いた名前、聞けなければ手元の `origin/HEAD`、`main`・`master` の順で統合先を決め、違えば fetch が成功した後に手元を向け直す
- Python の識別子の予約（「識別子が統合先の名前と同じ」）は、チケットの `project` ごとに、そのリポジトリの統合先で検査する
- `ccnavi-git.sh push` の固定のリスト（`main | master | develop | release | release/*`）は残す

## 理由

- ホストの既定ブランチ以外へマージする使い方はほぼ無いので、上書きの手段を持つ意味は小さい
- ワークスペースで 1 つの値が B1 を生んでいた。プロジェクトの統合先への push を止められず、Python の `--lint` の識別子の予約もプロジェクトの統合先で効かなかった
- `settings.local.json` はエージェントが書き換えられるファイルで、sh はその内容をその場で読むため、エージェントが統合先を変えられた
- Chrome 拡張はもともとホスト API の `default_branch` を見ており、sh・Python とずれていた。廃止すると sh・Python と揃う。ただし拡張のリポジトリごとの「統合先の名前」設定欄はこの変更の対象外で残っており、sh・Python との統一は欄の扱いの判断待ちである

## 得たもの・失ったもの

- 既定が `main` のまま `develop`・`release/*` へマージリクエストを出す運用（hotfix など）では、宛先がずれる。宛先はホストの画面で直す
- origin の無いリポジトリでは統合先が決まらず、`integrate` が断る。origin があることを前提にした

## 関係する ADR

- [ADR-0060](0060-default-branch-at-session-start.md): ワークツリーの起点になる既定ブランチをセッションの頭で進める。`origin/HEAD` が無いときの推測と `set-head` での直し方は、この ADR が引き継ぐ
- [ADR-0093](../extension/0093-chrome-approval-parent-branch-authority.md): `--integration-branch` を導入した経緯。この ADR で廃止する
- [ADR-0118](../rules/0118-wrapper-sh-judging-is-an-exception.md): sh の判定が統合先の名前を使う件。名前の決め方が `origin/HEAD` だけになる
