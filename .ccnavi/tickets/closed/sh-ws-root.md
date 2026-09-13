---
version: 1
ticket: sh-ws-root
title: 保護済み sh がプロジェクトの中で動くようにする（ワークスペースルートの導出）
rationale: 'モード B（projects/ の下に別リポジトリを clone する形）で、保護済み sh 3 本が

  ワークスペースルートを git rev-parse --git-common-dir から導いている。プロジェクトの

  中や、プロジェクトから切った作業ツリーの中では、これがプロジェクトを指すため、

  実行ファイルの場所・wip/・logs/・state/ がすべてプロジェクト側にずれる。

  結果、ccnavi-ticket.sh と ccnavi-review.sh はプロジェクトの中で動かず、レビューは

  プロジェクトに .claude/ を作って失敗し、子チケットの push ガードは黙って効かなくなる。

  設計 §25.8 の「sh はワークスペースにしかない」が実装されていない。

  '
human_review:
  required: true
  reason: ワークスペースルートの導出と push ガードの経路を変えるため。ガードが効かなくなる向きの誤りが入りうる
plan:
- design
- acceptance
- staging
- docs
allow:
- match: Write|Edit
  glob: wip/design/*
- match: Write|Edit
  glob: tests/*
- match: Write|Edit
  glob: CLAUDE.md
- match: Write|Edit
  glob: HANDOVER.md
started_at: 2026-09-12T22:20:32+0900
completed_at: ''
base_sha: 46d4791c62df1e3006f4b57935204a9553ee08ef
ccnavi_approved:
  approved_at: 2026-09-12T22:19:17+0900
  source_tree: sh-ws-root
  source_path: C:\Users\taniyama\Desktop\git\ccnavi\.claude\worktrees\sh-ws-root\wip\tickets\todo\sh-ws-root.md
  revised_at: 2026-09-13T04:58:31+0900
  feedback_at: 2026-09-13T04:58:31+0900
feedback:
- implement-feedback
---

# 保護済み sh がプロジェクトの中で動くようにする

2026-09-12 の監査で見つけたものの前半。設計 §25 / REQ-MLT のうち sh 側。
Python 側（識別子の接頭辞、自己防衛の穴、lint）は別チケットにする。理由は「順序と分割」に書く。

## 何をなぜ変えるか

根は 1 つ。`ccnavi-git.sh` `ccnavi-ticket.sh` `ccnavi-review.sh` の 3 本とも、自分の根を
`git rev-parse --git-common-dir` から導いている。モード A（ワークスペース自身が git）では
これがワークスペースルートと一致するので今まで気付かれなかった。モード B では一致しない。

確認した不具合。

1. **記録がプロジェクトの中に出る**（`ccnavi-git.sh:465-468`、REQ-MLT-14 違反）。
   `projects/lib` の中で打つと `projects/lib/logs/` ができる。ワークスペースの `.gitignore` の
   `/logs/` はワークスペースルート起点なので効かず、public のリポジトリに運用の痕跡が入る
2. **子チケットの push ガードが効かない**（`ccnavi-git.sh:381-388`）。作業ツリーかどうかを
   `push_root/.claude/worktrees/*` で見るが、モード B では `push_root` がプロジェクトで、
   作業ツリーはワークスペースの下にある。条件が一致せず、承認済みの写しの `parent:` 検査が
   丸ごと飛ぶ。子ブランチがそのまま push できる
3. **`ccnavi-ticket.sh` がプロジェクトの中で動かない**（`:52-58,66`）。実行ファイルを
   `projects/lib/dist/ccnavi/ccnavi` に探して見つからない。仮に見つかっても `--root` が
   プロジェクトを指し、`wip/` と `.claude/ccnavi/tickets/` の置き場が §25.2 と食い違う
4. **`ccnavi-review.sh` が同じで、さらにプロジェクトに `.claude/` を作る**（`:74-82,88`、`:426`）。
   `projects/lib/.claude/ccnavi/state/` ができる。これは `--lint` が禁じている形（§25.9）
5. **`origin` の資格情報が漏れる**（`:114`、`:132`、`:142`）。読めなかったときに URL をそのまま
   stderr に出す。`ssh://oauth2:<token>@host:2222/g/p.git` の形だとトークンが出る
6. **伏せ字が最初の `@` までしか消さない**（`:434`）。解析は最後の `@` まで見るので、
   `glpat-A@B` のように `@` を含む資格情報だと後半が残る。scheme の `[a-z]+` が大文字も落とす
7. **`worktree add` の行き先が検査されない**（`:197-208`）。プロジェクトの中で
   `.claude/worktrees/x` と打つと `projects/lib/.claude/worktrees/x` ができる。
   プロジェクトに `.claude/` ができ（lint エラー）、`tree_of` の探す場所からも外れる
8. **`test-py.sh` がターンを止める**（`.claude/hooks/test-py.sh:67-68`）。触ったツリーに
   `tests/` が無いと `unittest discover` が失敗する。モード B ではプロジェクトが Python とは
   限らないので、プロジェクトを 1 つ置いた時点で起きる
9. **拒否の文面が届かないパスを案内する**（`.claude/ccnavi/rules.yml` の 7 か所）。
   `sh .claude/scripts/ccnavi-git.sh ...` と書いてあり、`projects/lib` から見ると存在しない。
   `{root}` は `regex:` で 1 か所使われているだけ（§25.8）
10. **ブランチ名にスラッシュがあると壊れる**（`ccnavi-review.sh:575-576`）。`feature/x` で
    存在しないディレクトリを指す。モード B とは無関係だが同じ関数群なので併せて直す

## どう変えるか

- ワークスペースルートを `cwd` から上へ歩いて探す。印は `.claude/scripts/`（自分自身の置き場）。
  `CCNAVI_WORKSPACE` があればそれを優先。見つからなければ止める
- この処理を `.claude/scripts/ccnavi-common.sh` に切り出し、3 本が `.` で読む
- 記録は `<ワークスペース>/logs/<プロジェクト>/`。プロジェクト名は sh が導く。`cwd` が
  `projects/<名前>/...` なら `<名前>`、`.claude/worktrees/<id>/...` なら `.git` の `gitdir:` を
  読んで切り元を取る。ワークスペース自身は `logs/`
- `worktree add` の行き先を検査し、ワークスペースの `.claude/worktrees/` の外なら止める。
  文面に正しい綴りを出す
- push ガードのルート導出をワークスペースルートに直す。検査は増やさない
- `origin` を伏せる処理を関数に集約し、`origin` を出力しうる全箇所に通す。`[^/]*@` に直し、
  scheme は大文字も拾う
- `test-py.sh` の存在チェックを `[ -d "$target/tests" ]` に直す
- `rules.yml` の `message` 7 か所を `{root}/.claude/scripts/...` にする

## 得るもの

- モード B でチケット作業とレビューが成立する。今は `projects/` にプロジェクトを置いた時点で
  `ccnavi-ticket.sh start` が落ちるので、チケット制御そのものが使えない
- 子チケットの push ガードが戻る。今はモード B で黙って無効。ガードが「効いているつもりで
  効いていない」状態が一番まずい
- public のリポジトリに記録と `.claude/` が漏れなくなる
- 資格情報の漏れが 3 か所塞がり、次に足されたときも関数を通る
- モード A の挙動は変わらない。上へ歩いても 1 段目でワークスペースルートに当たるため

## 失うもの

- 保護済みスクリプトが 1 本増える（`ccnavi-common.sh`）。selfguard の中核と `--lint` の
  点検対象に足す必要がある。配布（`ccnavi-setup.sh` の `DEPLOY_SCRIPTS`）にも足す
- プロジェクトの中から `worktree add .claude/worktrees/x` と相対で打つ形が止まるようになる。
  今まで通っていた綴りなので、打ち方を変える必要がある（文面で案内する）
- `gitdir:` の解析が sh と実行ファイルの 2 か所に載る。綴りが変わったとき直す場所が 2 つ
- ルート探索が見つからなかったときに止まるので、ワークスペースの外から sh を叩く使い方が
  できなくなる（`CCNAVI_WORKSPACE` で抜けられる）

## やらない場合どうなるか

モード B が使えないまま残る。`projects/` に 1 つでも clone すると、チケット作業・レビュー・
push ガードが壊れる。設計 §25 と REQ-MLT は「入った」ことになっているので、次に入る人は
動くつもりで使い、記録がプロジェクトに漏れてから気付く。

## 代案

- **実行ファイルにルートを聞く**（`ccnavi --explain --json`）。git 呼び出しのたびに実行ファイルを
  起こすので、ラッパの速さが起動時間に縛られる。採らない
- **`$0` から導く**。`sh ../../scripts/...` の形で相対に来るので `cwd` が変わると壊れる。採らない
- **`CCNAVI_WORKSPACE` を必須にする**。書き忘れが即事故になる。上書きとしてだけ残す
- **`worktree add` の行き先を書き換える**。打った綴りと起きたことがずれ、記録を読んだ人が
  追えなくなる。止める側にする

## 順序と分割

Python 側（識別子の接頭辞が消える、プロジェクト名の空白で自己防衛が抜ける、プロジェクトから
切った作業ツリーの控えが作られない、lint の早期 return、孤児の作業ツリー、`message` の
`{root}` の lint）は**このチケットに入れない**。進行中の `config-union` が、ルールの置き場を
`projects/<名前>/.ccnavi/config/` に移し、識別子の接頭辞の規則と fallback の扱いと selfguard の
中核を作り直すため、今直しても捨てられる。`config-union` が統合先に入ってから、残る 3 件
（名前の空白、孤児、`{root}`）に絞って別チケットで出す。

同じ理由で、`ccnavi.md` §25 と `requirements.md` の REQ-MLT 表にはこのチケットで**足さない**。
`config-union` が §25 を構造ごと改版するので、先に足すと必ず解き直しになる。代わりに
`HANDOVER.md` に「要求表への追記が未了」と明記する。これは「書いた時点で実装が入っている
状態を保つ」という原則の逆向きの穴で、承知のうえで受け入れる。

## 保護済みファイルの扱い

このチケットが直す対象のうち、`.claude/scripts/` の 4 本、`.claude/hooks/test-py.sh`、
`.claude/ccnavi/rules.yml` は `deny` の対象で、**チケットの承認では書けない**
（`judge.py:246-247`「チケットはルールが何も言わなかったときだけ見る。ルールのほうが強い」）。
ガードは緩めない。実装フェーズは完成したファイルを `wip/design/scripts/` に書き、
人が写す。写す手順もそこに書く。受入テストは写したあとに回す。

## 確かめ方

- 既存の in-process テスト（unittest、406 件）が通ること
- `CCNAVI_E2E=1` のときだけ走る検証を `tests/` に足す。本物のワークスペースを隔離した場所に
  作り（git init、`projects/p1` と `p2`、プロジェクトから切った作業ツリー、ワークスペースから
  切った作業ツリー）、組み立てた実行ファイルと sh を実際に叩く。確かめるのは、
  記録の置き場、push ガードの発火、`ccnavi-ticket.sh` と `ccnavi-review.sh` がプロジェクトの
  中から通ること、`worktree add` の行き先検査、`origin` の伏せ字、`test-py.sh` の素通り
- モード A（`projects/` 無し）で置き場と挙動が変わらないこと
