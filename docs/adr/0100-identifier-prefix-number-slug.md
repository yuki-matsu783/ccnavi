---
type: adr
title: 親の識別子を <先頭の語>-<番号>-<slug> にし、日本語の字を使えるようにする
description: issue から決める識別子の形 i<番号> をやめ、親の識別子を <先頭の語>-<番号>-<slug> にそろえる。先頭の語は既定の並び（feature・hotfix など）から選び、env で変えられる。slug には日本語の字を使える。sh の検査、git の非 ASCII の扱い、既にあるブランチとのぶつかりの warn と、ブランチ名を識別子と別に持つ branch: 欄の設計（未実装）を扱う
tags: [ticket, worktree, extension, sh-scripts]
keywords: [識別子, ブランチ名, feature, hotfix, 先頭の語, CCNAVI_BRANCH_PREFIXES, slug, 日本語, NFC, NFD, MAX_PATH, issue_identifier, 通し番号, ccnavi_is_ident, core.quotePath, branch:, 既にあるブランチ]
---
# ADR-0100: 親の識別子を <先頭の語>-<番号>-<slug> にし、日本語の字を使えるようにする

状態: 採用（1〜4 章）。5 章の `branch:` 欄は設計だけで、実装の前にユーザに確かめる点が残っている

## 1. 状況

2026-10-03。親の識別子（= 親のブランチ名・ワークツリーの名前・ファイル名。ADR-0093 の 3.1）は、
issue から決めるときは `i` + 4 桁の 0 埋め（`i0131`）、issue が無ければユーザが付ける自由な 1 語だった。
字は `[A-Za-z0-9][A-Za-z0-9._-]*` に限っていた。

- `i0131` はブランチの一覧で中身が分からない
- 日本語の題を持つ作業に、日本語の名前を付けられない
- ホストの「issue からブランチを作る」既定（`123-login`）や、よく使う `feature/` `hotfix/` の慣習と離れている

ユーザの決定:

1. `i0123` の形は今後使わない。新しい親の識別子は `<先頭の語>-<番号>-<slug>` にそろえる
2. `<番号>` は、issue から始めるときは issue の番号、issue が無いときは ccnavi の通し番号（既にある識別子の番号の続き）
3. `<slug>` は英単語が基本（`feature-63-integration-branch`）だが、日本語も入れられる（`feature-64-統合先の解決`）
4. 子は今までどおり `<親>-<2 桁連番>`
5. 先頭の語は固定しない。既定の並びは `feature`・`hotfix`・`fix`・`bugfix`・`chore`・`refactor`・`docs`。
   ユーザがファイルを書き換えなくても使え、普段はチャットで「hotfix で」と言えばエージェントがその語で提案を書く。
   並びを変えたい人だけが settings.json の `env`（`CCNAVI_BRANCH_PREFIXES=feature,hotfix,fix`）で上書きする
6. issue から始めるとき（`ticket.issue_identifier`・Chrome 拡張）の既定の語は `feature`。語は引数で渡せる（ラベルからは決めない）
7. 新規の提案（親）のブランチ名が、そのリポジトリの手元か origin に既にあるブランチと同じなら、承認の前に warn する
8. `/` を含むブランチ名（`feature/123-login`）のために、識別子と別のブランチ名を持つ `branch:` 欄を足す（5 章）

## 2. 決定（識別子の形）

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| 新しい親の形は `<先頭の語>-<番号>-<slug>`。先頭の語は英小文字で始まる英小文字と数字、番号は 0 で始まらない正の整数、slug の先頭は英数字か日本語の字 | 一覧で中身が読め、番号で issue と結べる。`-` 区切りなので識別子に `/` を入れずに済む（ファイル名とワークツリーの直下 1 段の置き場がそのまま使える） | `feature/123-x` を識別子にする（ファイル名とワークツリーの置き場が 2 段になる。5 章で別の欄として扱う） |
| 形は `--lint` の warn（新規の提案だけ）。承認は止めない | 前の段（ADR-0093 の 3.1 の 2・5・6）と同じ深刻度。承認済み・閉じたチケット（`i0055` など）は読めるまま残す | 承認で止める（既存の運用で書いた提案が一斉に止まる） |
| 先頭の語の並びは `CCNAVI_BRANCH_PREFIXES`（カンマか空白区切り）。環境変数を先に見て、無ければ `.claude/settings.local.json`、`.claude/settings.json` の `env` を読む。無ければ既定の並び | Claude Code が `env` を hook と Bash に渡すので、ほかの `CCNAVI_*` と同じ置き場で足りる。端末で打つ `ccnavi --lint` にも同じ値が効くよう、ファイルの `env` も読む（`CCNAVI_INTEGRATION_BRANCH` の `integration_local` と同じ考え） | 新しい設定ファイルを置く（ユーザの決定 5） |
| 並びに `release` と `main`・`master`・`develop` は入れられない（読まずに warn で名指しする） | 統合先や保護されたブランチの名前（`release-*`）に当たる | 黙って捨てる |
| 番号は issue の番号か通し番号。通し番号は、番号を持つ親の識別子（新しい形と、前の形 `i<番号>`・`<名前>-i<番号>`）の最大 + 1。先頭の語が違っても同じ通し番号を使う。`--lint` の形の warn と番号の重なりの warn が次の通し番号を添える | 新しいコマンドを足さずに、提案を書くエージェントが番号を知れる | 通し番号を出すコマンドを足す（ユーザとの IF が増える。要るなら後で足す） |
| 同じリポジトリの親どうしで番号が重なれば warn（新規の提案が関わるときだけ） | issue の番号と通し番号は同じ数になりうる（ユーザの決定どおり warn で足りる）。issue の番号はリポジトリごとなので、比べるのは同じ `project` の中だけ | 番号の重なりで承認を止める |
| `issue:` があるのに番号が違えば warn。プロジェクトの issue なら slug の頭が `<プロジェクト名>-` でなければ warn | 3.1 の 4・7 の考えを新しい形に写した | — |
| 別のリポジトリの課題（`issue: owner/repo#N`）は番号を比べない。通し番号を使う | その番号はこのリポジトリの issue と紛れる（3.1 の 8 と同じ考え） | — |
| 長さの上限は 64 文字（error。子は親 + 3 文字）。勧める長さは 48 文字（warn。issue から作る名前もここで切る） | 識別子はパスに 2 回出る（`.claude/worktrees/<親>/.ccnavi/approved/phases/<親>/…`）。Windows の MAX_PATH（260）から、ワークスペースまで（40 文字ほど）と置き場の綴りを引いた残り。Linux の 1 つの名前の上限（255 バイト）も、日本語 1 字 3 バイトの 64 字なら収まる | 上限を設けない |

### 2.1 プロジェクトの issue の名前空間

前の形は `<プロジェクト名>-i<番号>`（`web-i0012`）だった。ADR-0093 の 3.1 の 7 で頭にプロジェクト名を付けたのは、
issue の番号がリポジトリごとで、ワークスペースの issue 12 とプロジェクト web の issue 12 が同じ `i0012` になるため。
11.9.1 の 17 のとおり、同じ名前の家族が 2 つのリポジトリにあると、lint の「複数のリポジトリにある」（error）と、
家族の控えを名前で引く処理（`syncstate.standing_any`）が止める。新しい形でも番号はぶつかるので、名前空間は要る。

新しい形では、プロジェクト名を slug の頭に置く（`feature-12-web-login-form`）。先頭の語と番号の位置を変えずに済み、
「`<先頭の語>-<番号>-<slug>` にそろえる」（ユーザの決定 1）を崩さない。頭に置く形（`web-feature-12-…`）は採らなかった。
issue の無いプロジェクトの提案は、通し番号がリポジトリをまたいで 1 本なので、名前空間を求めない。

### 2.2 slug の作り方（issue のタイトルから）

`ticket.issue_slug`: NFKC（全角英数を半角に、半角カナを全角に）→ ASCII の英字を小文字 → 英数字と日本語の字以外を `-` に
まとめる → 前後の `-` を落とす → 長さで切る。末尾が子の形（`-<2 桁>`）になるときはその部分を落とす（`release 01` →
`feature-3-release`）。何も残らなければ slug は `issue`（`feature-12-issue`）。`feature-12` だけにすると子の形
（`feature` の子 12）に当たるため。issue のタイトルは誰でも書けるので、Chrome 拡張の service worker も
できあがった名前の字と形を確かめ直す（`protocol.startName`）。

## 3. 決定（字と綴り）

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| 使える字は ASCII の英数字と `.` `_` `-` に、ひらがな（U+3041–3096）・カタカナ（U+30A1–30FA）・長音記号（U+30FC）・CJK 統合漢字（U+4E00–9FFF）・々（U+3005）を足したもの。先頭は ASCII の英数字 | 日本語の題を名前にできる。全角英数・全角記号・全角空白・ほかの Unicode は、見た目の同じ別の字で名前を紛らわせるので使わない。NFKC で形の変わらない字だけなので、拡張の大文字小文字そろえ（NFKC + toLowerCase）と Python の casefold が同じ答えになる | Unicode の文字クラス（`\w`）で広く許す |
| NFC でない綴りは error（「NFC でない」と名指しする） | macOS の HFS+ やアプリが作る NFD の綴りは、見た目が同じでもバイト列が違う別の名前になる。結合文字（U+3099）は使える字の外なので字の検査でも落ちるが、原因を名指しする | NFC に直して読む（ファイル名・ブランチ名と食い違う） |
| ワークツリーの名前は NFC にそろえてから識別子と比べる（`tree.nfc`） | HFS+ はディレクトリの名前を NFD で返す。APFS・Windows・Linux は書いた綴りのまま返す | — |
| 識別子の検査は `ticket.id_problem` の 1 つ（字・NFC・長さ）。`is_valid_id`（履歴のファイル名・cli の引数）もこれを使う。プロジェクトの名前は ASCII のまま（`is_valid_name`） | 1 か所で決める | — |

### 3.1 sh の検査（`ccnavi_is_ident`）

前は `case` の `*[!A-Za-z0-9._-]*` で止めていた。`LC_ALL=C` の文字クラスはマルチバイトの字を 1 字として扱えず、
範囲（`[a-z]`）の読み方はロケールで変わるので、日本語を通すには使えない。

`ccnavi-common.sh` の `ccnavi_is_ident` は、パスとシェルで意味を持つ綴りだけを止める: 空、先頭の `-` と `.`、`..`、`/`、`\`、
先頭が ASCII の英数字でない、ASCII の英数字と `.` `_` `-` 以外の ASCII の字（空白・制御文字・記号）。残りは
`LC_ALL=C tr -d 'A-Za-z0-9._\200-\377-'` で消し、何も残らなければ通す（`ccnavi-review.sh` の eli5 の検査と同じ方法。
末尾の改行が `$( )` で落ちないよう番兵の `/` を足して比べる）。ASCII の外のバイトは通し、字の種類（全角記号・NFD）は
実行ファイルが確かめる（2 段目の守り。ADR-0093 の判定は実行ファイルが持つ）。`ccnavi-push-approved.sh`・`ccnavi-sync.sh`・
`ccnavi-review.sh config-synced`・`ccnavi-ticket.sh` の C1 の識別子の検査をこれに置き換えた。

### 3.2 git の非 ASCII の扱い（確かめたこと）

- `git status --porcelain`・`diff --name-only`・`ls-files`・`log --name-only` は、既定（`core.quotePath=true`）で日本語のパスを
  `"\347\265\261…"` と引用する。ccnavi がパスを読み戻す箇所は、Python も sh も `-z` か `-c core.quotePath=false` で読んでいた
  （`gitstate`・`review`・`phase`・`risk`・`c1`・`configsync`、sh の `carry_paths`・`ccnavi_c1_*`・`ccnavi-sync.sh` の `ls-tree`）。
  `-z` の無い `status --porcelain` は、空かどうかだけを見る箇所（`ops._unfinished`・`ccnavi-fetch.sh`・`ccnavi-push-approved.sh`）だけ
- `git worktree list --porcelain`、`rev-parse --abbrev-ref HEAD`、`for-each-ref` の refname は引用されない（git 2.43 で確かめた）
- ブランチ名は git の ref の規則で日本語を使える。GitHub・GitLab の API に渡すブランチ名は、拡張が `encodeURIComponent` で包んでいた
- 一時ディレクトリの git で、日本語の識別子の親と子のワークツリーを作り、承認 → 着手 → 判定 → 終える を通す試験を足した
  （`tests/ticket/test_japanese_identifier.py`）

## 4. 決定（既にあるブランチとのぶつかり）

新規の提案（親）の識別子（= 親のブランチ名）が、そのリポジトリの手元（`refs/heads/`）か origin（`refs/remotes/origin/`）に
既にあるブランチと大文字小文字を区別せずに同じなら、`--lint` と `--agree --preview --verify` が warn を出す
（verify の答えは変えない。JSON では `branch_warnings`）。ブランチの一覧は `gitstate.branch_names`（`gitcmd` の 1 回の
`for-each-ref`）で読む。提案が自分の識別子のワークツリーの中にあり、そのワークツリーが同じ名前のブランチの上にあるときは、
それが親のブランチなので言わない（提案は親のブランチの上で書く。ADR-0093 の 3.2）。

## 5. `branch:` 欄（設計。未実装）

ユーザの決定 8 は「識別子・ファイル名・ワークツリー名は `/` を含まないまま、親に任意の欄 `branch:` を足して識別子と違う
ブランチ名を持てるようにする」。触る箇所を洗い出したところ、次の 3 点がこの ADR の範囲（と並行の作業の分担）では決めきれないので、
実装の前にユーザに確かめる。

### 5.1 触る箇所

| 層 | 箇所 | 今の前提 |
|---|---|---|
| 権威（ADR-0093 の 3.3） | 手元: `syncstate.home_tree`（名前 = 識別子 かつ HEAD = ブランチ `<P>`）、`approval._authoritative`（`t.tree == t.parent or t.ticket`）、`lint._parent_trees_off_branch`、`core` の取り下げ（`copy.tree != family`）、`ops._own_commits`（子の基準に親のブランチ `t.parent` を引く） | ブランチ名 = 識別子 = ワークツリー名 |
| 家族の控え | `sync/<リポジトリ>/families/<P>`（`syncstate.family`、sh の `ccnavi_family_record`） | 鍵がブランチ名。`/` を含むと置き場が 2 段になる |
| sh | `ccnavi-sync.sh <親のブランチ名>`（`refs/remotes/origin/$P`、`.claude/worktrees/$want`）、`ccnavi-push-approved.sh`（`"$root/.claude/worktrees/$branch"` で親のワークツリーを見分ける）、C1（`refs/heads/$ccnavi_c1_family_id` へ push、`ls-remote`）、`ccnavi-fetch.sh`（チェックアウト中のブランチを進める） | 引数も ref も識別子 |
| `ccnavi-git.sh` | push の節（`ccnavi_family_record ... "$push_branch"` で控えを引く、子のワークツリーの見分け）、worktree の節（行き先の名前 = `-b` の名前、既にあるブランチは同じ名前の行き先へ）、checkout の節（親のワークツリーで別のブランチへ移らない） | ブランチ名 = ワークツリー名 |
| Chrome 拡張 | `_op_families`（家族 = ブランチ名と同じ識別子の親があるブランチ）、閉包・承認・取り下げ・レビュー済みの書き先（家族の名前 = ブランチ名）、仮のワークスペースへの展開（`.claude/worktrees/<ブランチ名>`。`is_valid_id` で `/` を断る） | ブランチ名 = 識別子 |
| 承認 | 承認画面の本文・指紋（`_carried` は写しの全文を含むので、欄を足せば指紋には入る）、改版で書き換えを断る欄（`issue:` と同じ扱い） | — |

### 5.2 確かめる点

1. **権威の置き場が欄で決まる。** ADR-0093 の 3.1 は、案 N3（`branch:` 欄）を「欄を書き換えれば権威の置き場が動く」として退け、
   「チケット → 親のブランチ名」を恒等写像にした（3.1 の 11。Python・sh・TS で食い違いようがない）。`branch:` を入れると、
   家族 X の権威のブランチは「X の親チケットの `branch:` の値」になり、その親チケットはそのブランチの上にある（自分で名乗る形）。
   別のブランチ B2 に `ticket: X`・`branch: B2` の写しを置けば、同じ家族を名乗るブランチが 2 本になる。案は「家族 X を名乗るブランチ
   （そのブランチの上の X の親チケットの `branch:` がそのブランチの名前と同じもの）が 1 本でなければ決まらないで止める」。
   判定は緩まないが、誰でも家族を止められる（今もブランチ X を消せば止まるので、同じ程度）。これで進めてよいか
2. **`ccnavi-git.sh` の push の節を変える必要がある。** push の節は家族の控えをチェックアウト中のブランチ名で引く。`branch:` を使う家族では
   識別子で引き直す（ワークツリーの名前から識別子を、実行ファイルからブランチ名を得る）必要がある。この節は並行の作業
   （integration-branch）が直していて、こちらでは触らないことになっている。どちらが、どの順で直すか
3. **sh が識別子とブランチ名の対応を知る手段。** sh は YAML を読まない（ADR-0093 の D33）。`ccnavi c1 family <識別子>` の答えに
   `branch <名前>` を足し、`ccnavi-sync.sh` の引数は識別子にする（ブランチ名は実行ファイルから得る）案でよいか。家族の控えの鍵は
   識別子のまま（`/` を置き場に入れない）にし、中に `branch` を持たせる。互換の版（`CCNAVI_COMPAT`）を上げることになる

これらが決まるまで、決定 7 の「`branch:` で既にあるブランチを指したときは warn せず、承認画面に『既存のブランチ <名前> を使う』と出す」
も入れていない（4 章の warn は識別子だけを見る）。

## 6. 入れなかったもの

- 通し番号を出すコマンド（`--lint` の文面で足りるとした）
- issue のラベルから先頭の語を決めること（ユーザの決定 6）
- 承認済み・閉じた前の形の識別子（`i0055` など）の書き換え（読めるまま残す）
- フェーズの種類の id（`phasetypes._ID`）とリスクの項目の id（`risk._ID`）。チケットの識別子ではないので ASCII のまま

## 7. 試験

Python は `tests/ticket/test_lint_branch_names.py`（形・先頭の語の並び・番号と issue・プロジェクトの名前空間・長さ・
`issue_identifier`・字と NFC・通し番号・番号の重なり・既にあるブランチ・`CCNAVI_BRANCH_PREFIXES` の読み方）、
`tests/ticket/test_chrome_project.py`（「始める」の名前と、タイトル・先頭の語の断り、悪意のあるタイトル）、
`tests/ticket/test_japanese_identifier.py`（日本語の識別子の主な経路、NFD の断り）、`tests/sh/test_ident_sh.py`
（`ccnavi_is_ident` を sh・bash と C・C.UTF-8 で）。拡張は `test/gitlab.test.ts` の CX-T150・151、`test/gitlab-review.test.ts` の
CX-T167、`test/e2e/extension.test.ts` の CX-T160、VS Code 拡張の `test/flow/flow-view.test.ts` の CB-T226。
