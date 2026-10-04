---
type: adr
title: 親の識別子を <先頭の語>-<番号>-<slug> にし、日本語の字を使えるようにする
description: issue から決める識別子の形 i<番号> をやめ、親の識別子を <先頭の語>-<番号>-<slug> にそろえる。先頭の語は既定の並び（feature・hotfix など）から選び、env で変えられる。slug には日本語の字を使える。sh の検査、git の非 ASCII の扱い、既にあるブランチとのぶつかりの warn と、ブランチ名を識別子と別に持つ branch: 欄を扱う
tags: [ticket, worktree, extension, sh-scripts]
keywords: [識別子, ブランチ名, feature, hotfix, 先頭の語, CCNAVI_BRANCH_PREFIXES, slug, 日本語, NFC, NFD, MAX_PATH, issue_identifier, 通し番号, ccnavi_is_ident, core.quotePath, branch:, 既にあるブランチ]
---
# ADR-0100: 親の識別子を <先頭の語>-<番号>-<slug> にし、日本語の字を使えるようにする

状態: 採用（1〜5 章。5 章の `branch:` 欄は 2026-10-04 に実装）

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
`branch:` を持つ親はこの warn を出さず、承認画面に「既存のブランチ <名前> を使う」と出す（5 章）。

## 5. `branch:` 欄

ユーザの決定 8 は「識別子・ファイル名・ワークツリー名は `/` を含まないまま、親に任意の欄 `branch:` を足して識別子と違う
ブランチ名を持てるようにする」。ユーザが既に作った `/` を含むブランチ（`feature/123-login`、`hotfix/45`）や、識別子と違う名前の
既存のブランチで、チケットの作業をできるようにする。2026-10-04 に実装した。

```yaml
ticket: feature-123-login
branch: feature/123-login
```

`branch:` が無ければ親のブランチ名は識別子そのもの（今どおり）。子のブランチは今どおり子の識別子で、push しない。

### 5.1 ユーザが決めたこと

1. **同じ家族を名乗るブランチが 1 本でなければ、権威が決まらないとして止める**（締める向き。判定は緩めない）
2. `ccnavi-git.sh` の push の節（家族の控えをチェックアウト中のブランチ名で引いている箇所など）も、この作業で直す。
   統合先への push の拒否（`ccnavi_integration`）は壊さない
3. sh が識別子とブランチ名の対応を知る手段は、実行ファイルの `ccnavi c1 family <識別子>` の答えに足した `branch <名前>`。
   `ccnavi-sync.sh` の引数は識別子。家族の控えの鍵は識別子のまま（`/` を控えの置き場のパスに入れない）。互換の版
   `CCNAVI_COMPAT` を 3 から 4 に上げる。Chrome 拡張も「家族 = ブランチ名」の前提を直し、`branch:` を読む
4. `branch:` は承認画面に出し、承認の指紋に入れる。承認の後に書き換えても効かない（改版は `branch:` の書き換えを断る。
   `issue:` と同じ扱い）
5. 統合先と保護されたブランチ（main・master・develop・release・release/*・release-*、`CCNAVI_INTEGRATION_BRANCH` などで決まる
   実際の統合先）は error。`..`・空白・制御文字など git で使えない綴りも error
6. `worktree add` で行き先の名前と違う既存のブランチを出すのは、そのワークツリー名の承認済みチケットの `branch:` と
   一致するときだけ。親のワークツリーでの checkout・switch の禁止は、承認済みの `branch:` の値を親のブランチとして扱う
   （当初は「承認済みチケット（か提案）」だった。敵対的レビューで、提案の `branch:` から他人の既存ブランチを取り出して
   送れる・保護されたブランチの控えができると分かり、承認済みだけにした。5.3）
7. 新規の親の `branch:` が既にあるブランチ（手元か origin）を指すときは、4 章のぶつかりの warn を出さず、承認画面
   （`--agree --preview` とその JSON、Chrome 拡張の承認画面）に「既存のブランチ <名前> を使う」と出す

### 5.2 承認の前と後（親のブランチへ移る手順）

**承認前の提案の `branch:` は、どこでも使わない。** 親のブランチ名は承認済みの親の写し（`doing/`・`done/`・`review/`）の
`branch:` からだけ引き（`syncstate.approved_branch`）、承認されるまでは `branch:` が無いのと同じく識別子のブランチで
作業する。「検査を通った、まだどこにも無い新しいブランチを承認前に切るのだけ許す」形は採らなかった。手元はホストの
ブランチを全部は知らない（取ってきていない他人のブランチと名前がぶつかる）ので、安全な側の「承認前は使わない」にした。

手順（ユーザの操作は増えない。エージェントの操作が 1 つ増える）:

1. エージェント: 識別子のブランチで親のワークツリーを切る（`worktree add .claude/worktrees/<P> -b <P> <統合先>`）。
   提案に `branch: <B>` を書き、コミットする（承認前に push して Chrome で承認してもらってもよい）
2. ユーザ: 承認する（端末か Chrome）。承認画面に「既存のブランチ <B> を使う」か「新しく切るブランチ」と出る
3. エージェント: 親のワークツリーで `ccnavi-git.sh switch <B>`（`checkout <B>`・`-b`・`--create` も同じ）。
   承認済みの写しが `branch: <B>` を名乗るときだけ、次の 1 操作になる（`ccnavi-git.sh` の `co_carry`）
   - 作業ツリーが綺麗で、途中の操作が無く、家族の控えが無いか識別子のブランチの `present` のときだけ
   - `<B>` が無ければ今の先頭から切る。在れば（手元か origin）`<B>` へ移ってから識別子のブランチを merge し、
     承認済みチケットとマーカーを `<B>` に乗せる。merge が落ちたら取りやめて識別子のブランチへ戻る
   - 別の家族の控えが `<B>` を親のブランチとしていれば移らない
4. エージェント: `ccnavi-git.sh push -u origin <B>`。家族の控えが無ければ `branch <B>` で作り、識別子のブランチの控え
   （承認前に送った家族）なら `branch` を `<B>` に書き直す。書き直すまでの間は、立ち位置・`ccnavi-sync.sh` が
   「移った後まだ送っていない」と言って止める（取り込みの後の検査で blocked にはしない）

承認済みの写しが既に別のツリー（ワークスペースルートなど）にあれば、`worktree add .claude/worktrees/<P> <B>` で直に
出すこともできる（決定 6）。そのときは写しを親のワークツリーへ運ぶのはユーザ（ADR-0093 の 3.5 と同じ）。

Chrome では、承認前の提案は識別子のブランチの家族として読む（提案の `branch:` では名乗らない。`_claimed`）。
承認で写しが `branch: <B>` を持つと、識別子のブランチはその家族を名乗らなくなり、手元が 3・4 を済ませるまで
ボードにその家族は出ない。

### 5.2.1 ADR-0093 が案 N3 を退けた理由への答え

ADR-0093 の 3.1 は、案 N3（`branch:` 欄）を「欄を書き換えれば権威の置き場が動く」として退け、「チケット → 親のブランチ名」を
恒等写像にした（3.1 の 11）。`branch:` を入れても権威の置き場が欄の書き換えで動かないよう、次の 4 つで締める。

| 締め方 | 何を防ぐか |
|---|---|
| **自分で名乗る形。** 家族 X の権威のブランチは、そのブランチの上の X の承認済みの親の写しの `branch:`（無ければ識別子。承認前の提案は識別子）がそのブランチの名前と同じもの。手元では `.claude/worktrees/<X>` の HEAD がその名前を指すことも求める（`syncstate.Families.home_tree`） | 別のブランチの写しに `branch:` を書いても、そのブランチが親のワークツリーにならない |
| **名乗るブランチが 1 本でなければ止める**（ユーザの決定 1）。手元は同じリポジトリのツリー（ワークスペースルート・プロジェクト・ワークツリー）のうち、HEAD のブランチをそのツリーの親チケットが名乗るもの（`Families.claims`）。Chrome は読んだブランチ（表示用の候補と、判定の入力）のうち、その家族を名乗るもの（`_ident_branches`）。`branch:` の家族では識別子と同じ名前のブランチも読みに行く（`rivals`） | 別のブランチ B2 に `ticket: X`・`branch: B2` の写しを置いて家族を 2 つにする。誰でも家族を止められるが、今もブランチ X を消せば止まるので同じ程度 |
| **家族の控えが親のブランチ名を持つ。** 控えの `branch` と親チケットが名乗る名前が違えば止める（`Families._standing`、`ccnavi-sync.sh`）。書き直すのは、承認済みの `branch:` のブランチへ移って push したときだけ（5.2 の 4） | 取り込んだ後に権威を別のブランチへ動かす。戻すにはユーザが `--forget` で控えを消す |
| **承認で決まる。** 提案の `branch:` は使わず、承認済みの写しの `branch:` だけを使う。承認画面に出し、指紋（写しの全文）に入る。改版で `branch:` を変えられない（`agree.revision_problems`）。承認済みチケットの置き場はエージェントが書けない（自己防衛） | 提案を書き換えるだけで既存のブランチを取り出す・移る・送る・取り込みの権威にする。見せた後・承認の後の書き換え |
| **使える名前だけを渡す。** 実行ファイルは承認済みの `branch:` も `ticket.branch_problem` と統合先の名前（`Families.integration_names`）で確かめ直し、通らなければ `c1 family` が `branch` の行を出さず `branch_refused` で理由を言い、立ち位置も止める。sh（worktree add・checkout・sync・C1・push の控え）は `branch` の行が無ければ識別子の外へ動かさずに止める | 手で書いた写しの `origin/main`・統合先の名前 |
| **2 つの家族が同じブランチを名乗らない。** 承認で、親のブランチ名が同じリポジトリの開いた別のチケットの親のブランチ名か識別子と同じなら、両方とも承認しない（`approval.branch_problems`）。sh は同じブランチを名乗る控えが 2 つ以上なら push・移るを止める（`ccnavi_family_record_of_branch` が全部を出す） | 家族 B の `branch:` を家族 A の識別子にして、A のブランチを B の権威にする |

手元の判定は git を起こさないので、ホストのブランチ（手元にワークツリーの無いもの）は見ない。手元が見ない分は、取り込み
（`ccnavi-sync.sh`）が控えの `branch` で、Chrome が読んだブランチで見る。Chrome が読むのは直近 N 日と指定のブランチ、
判定の入力（統合先・`P`・閉包）、識別子と同じ名前のブランチで、それより古いブランチが同じ家族を名乗っていても見えない
（受け入れる危険）。手元ではワークツリーに出したブランチだけが名乗れるので、古いブランチを
ワークツリーに出せば手元の判定が止める。

### 5.3 欄の読み方と字

- `ticket.branch_problem`: 字は識別子の字（`ID_CHARS`）に段の区切りの `/` を足したものだけで、先頭は ASCII の英数字。
  git が許すほかの字（`$`・`;`・引用符・全角の字）は sh と拡張が名前を扱う箇所で意味を持つか、見た目の同じ別の名前を作るので
  使わない（締める向き）。そのうえで `git check-ref-format --branch` の形の規則（`..`・`//`・`.` で始まる段・`.lock` で終わる段・
  先頭や末尾の `/`・末尾の `.`）、NFC でない綴り、200 字を超える長さを error にする
- git の綴りと紛れる名前も error（レビューの指摘 2）: 先頭の段が `refs`・`heads`・`remotes`・`tags`・`origin`・`upstream`・
  `HEAD`、どこかの段が `HEAD` か `*_HEAD`、保護されたブランチの名前（main・master・develop・release）を先頭の段に持つもの
  （`main/x`・`release/1`）と `release-*`。`branch: origin/main` で `-b origin/main` を作ると `rev-parse --abbrev-ref` が
  `heads/origin/main` を返して push の節と食い違い、`symbolic-ref --short refs/remotes/origin/HEAD` まで変わって
  `ccnavi_default_branch` が外れるため
- 統合先の名前との一致は承認の側（`approval.branch_problems`）が error にする。比べる名前は `ccnavi_integration` と同じ
  並び（環境変数、`.claude/settings.local.json` の `env`、取り込みの控えの `head`、`origin/HEAD` が指すもの、
  `origin/main`・`origin/master`。`Families.integration_names`）の全部
- 子に書いた `branch:` は warn で、読まない（`issue:` と同じ）
- sh の 2 段目の守りは `ccnavi_is_branch`（`ccnavi_is_ident` に `/` と段の形の検査、git の綴りと保護されたブランチの名前の
  検査を足したもの）。識別子と同じ名前は `ccnavi_is_ident` で見る（`ccnavi_branch_ok`）

### 5.4 sh が親のブランチ名を知る手段

sh はチケットを読まない（ADR-0093 の D33）。`ccnavi c1 family <識別子>` の答えに `branch <名前>` を足した
（`syncstate.Families.branch_any`）。名前は親のワークツリーの承認済みの親の写し、家族の控えの `branch`、ほかのツリーの
承認済みの親の写し、識別子の順に探す（提案は見ない）。使えない名前なら `branch` の行の代わりに `branch_refused <理由>` を出す。
`ccnavi_family_branch` は実行ファイルが無ければ 1、名前が使えない・答えないなら 2 を返し、`ccnavi-git.sh` は 1 なら識別子と
同じ名前だけを、2 なら何も親のブランチとして扱わない。

| sh | 使い方 |
|---|---|
| `ccnavi-common.sh` | `ccnavi_family_branch <ws> <識別子>`（実行ファイルに聞く。聞けなければ 1）、`ccnavi_family_record_of_branch <ws> <リポジトリ> <ブランチ>`（控えを `branch` の行で探す）、C1 の `ccnavi_c1_branch` |
| `ccnavi-fetch.sh` | セッションの頭で待たせないよう実行ファイルは起こさず、家族の控えの `branch` を読む |

実行ファイルが無い（ソースも無い）ワークスペースでは、`ccnavi-sync.sh` は識別子をブランチ名とする（前の動き）。
`ccnavi-git.sh` は識別子と同じ名前だけを親のブランチとして扱う（`branch:` の家族の worktree add は通らない。締める向き）。

### 5.5 親のブランチ名を識別子から組み立てていた箇所（洗い出した一覧）

| 層 | 箇所 | 直したこと |
|---|---|---|
| 権威（手元） | `syncstate.Families.home_tree` | HEAD が `Families.branch`（親チケットの名乗る名前）を指すこと |
| 権威（手元） | `syncstate.Families._standing` | 控えの `branch` との食い違い、名乗るブランチが 2 本以上で止める。止めたときの文面と案内（`guidance`）は親のブランチ名で言う |
| 権威（手元） | `approval.outside_reason`・`family_problems` の文面 | 親のブランチ名で言う（置き場の綴りは識別子のまま） |
| 承認 | `agree.screen`・`_batch_entry` | 「■ ブランチ」と「既存のブランチ <名前> を使う」/「新しく切るブランチ」。JSON の `branch`・`existing_branch` |
| 承認 | `agree.existing_branch_warnings`・`_written_on_own_branch` | `branch:` の親は warn しない。自分のブランチの上かは `branch_name` で見る。既にあるかは `tree.has_branch`（ファイルだけを読む） |
| 承認 | `agree.revision_problems` | 改版で `branch:` を変えさせない |
| 承認 | `approval.branch_problems` | 統合先の名前（`origin/HEAD` を含む）に当たる `branch:` と、2 つの家族が同じブランチを名乗る形を断る |
| lint | `lint._parent_trees_off_branch` | 親のワークツリーが親のブランチの上に居るかを `Families.branch` で見る |
| 実行ファイル | `c1.family`・`cli` の `c1 sort` の版の検査 | 答えに `branch`（使えなければ `branch_refused`）を足す。`c1 sort` の版に `refs/remotes/origin/<親のブランチ>` を受ける |
| 実行ファイル | `ops._own_commits`（ADR-0087 の終える促しの取り込み元） | 子なら親のブランチ（`Families.branch`）の先を除く |
| 実行ファイル | `ops.start` の案内、`review._followup_next` | ワークツリーを作る案内と子の起点に親のブランチ名を使う |
| 家族の控え | `ccnavi_family_record`・`syncstate.family` | 鍵は識別子のまま、中の `branch` に親のブランチ名を書く（前の控えは識別子を書いている） |
| `ccnavi-git.sh` | worktree の節 | 行き先の名前（識別子）の親チケットが名乗る名前なら、`<行き先> <ブランチ>` と `<行き先> -b <ブランチ>` を通す |
| `ccnavi-git.sh` | checkout・switch の節 | 親のワークツリーで許す移り先は親のブランチ（承認済みの `branch:` の値）だけ。識別子のブランチの上から承認済みの `branch:` のブランチへ移るのは `co_carry`（5.2 の 3）。移った後は識別子のブランチへも戻れない |
| `ccnavi-git.sh` | push の節 | 消えた家族の検査は控えを `branch` の行で探す。最初の push の控えはワークツリーの名前（識別子）を鍵に、居るブランチが親のブランチのときだけ作る。子のワークツリーの見分けと統合先への push の拒否は変えない |
| `ccnavi-sync.sh` | 引数・家族の集め方・取り込み・消えたかの確かめ・控え | 引数は識別子。親のブランチ名が決まらなければ（引数なしでも）止めたと言って失敗に数える。移った後まだ送っていない家族は取り込みの後の検査をしない。ref（`refs/remotes/origin/<B>`）・`ls-remote` の一覧・fetch・merge の文・控えの `branch`・戻し方の案内は親のブランチ名、控えの鍵とロックは識別子 |
| `ccnavi-common.sh` | C1（`ccnavi_c1_prepare`・`ccnavi_c1_write`・`ccnavi_c1_undo`・`ccnavi_c1_sent`） | 未送信の比べ（`origin/<B>`）・push・`ls-remote`・`update-ref`・控えの `branch` は `ccnavi_c1_branch` |
| `ccnavi-push-approved.sh` | 親のワークツリーの見分け、`carry_family` | ワークツリーの名前（識別子）で `c1 family` を聞く。送る ref は親のブランチ名 |
| `ccnavi-fetch.sh` | 早送りの対象と ref | 控えの `branch` を親のブランチとして、居るブランチと比べ、`origin/<B>` に早送りする |
| `ccnavi-review.sh` | MR の元ブランチ、C1、下書きの名前 | MR は居るブランチ（親のブランチ）で探して作る（前のまま）。C1 の家族と下書きの名前（実行ファイルと揃える）は親のワークツリーの名前（識別子） |
| Chrome（Python） | `_op_families`・`_closure`・`_build`・`records`・`_op_board`・`_family_tree`・`_op_confirm` | 要求の `family` はブランチ名のまま、家族の識別子は名乗る親チケットで決める（`_family_ident`。承認前の提案は識別子で名乗る `_claimed`）。仮のツリーは `.claude/worktrees/<識別子>` で HEAD は親のブランチ、控えは識別子の鍵に `branch`。名乗るブランチが 2 本以上なら、判定する家族でも閉包の先行の家族（`ambiguous`）でも止める。ホストに無い家族の識別子が識別子の形でなければ控えを書かずに止める |
| Chrome（TS） | `snapshot.ts`・`py.ts`・`render.ts` | 家族の一覧の `family`（識別子）・`conflict`、閉包の `idents`・`rivals`、先行の家族を読みに行く見当の `hints`。見出しに識別子も出す |

変えなかったもの（識別子のまま正しいもの）: ワークツリーの名前と置き場の綴り（`approval._authoritative`・`home_dir`・
`core.withdrawable` の `copy.tree`）、マーカーと跡の置き場、ロックの名前、`review.py` の未送信の検査（居るブランチを読む）、
`Changes.per_branch`・`agree.read_set`（ツリーの HEAD のブランチ名を読む）。

### 5.6 試験

`tests/sh/test_branch_field_sh.py`（一時のリポジトリで `/` を含むブランチの承認 → 移る → 着手 → push → 取り込み → 終える、
承認前に送った家族の控えの書き直し、承認後に新しいブランチを切る形、承認前・使えない `branch:` を worktree add・checkout・
sync・c1 family・push が使わないこと、名乗るブランチが 2 本、控えの `branch` の食い違い、消えた家族と 2 つの控えへの push）、
`tests/ticket/test_branch_field.py`（字と形と git の綴り、子の欄、承認画面と JSON、改版、統合先の名前（`origin/HEAD`）、
2 つの家族の同じブランチ、lint、`c1 family`、Chrome の入口と先行の家族）、
`tests/sh/test_ident_sh.py` の `ccnavi_is_branch`。拡張は `test/board.test.ts` の CX-T180（`/` を含むブランチの家族と、
同じ家族を名乗るブランチが 2 本のとき）。

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
