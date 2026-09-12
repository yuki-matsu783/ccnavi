# 設計 §25 改版案: rules / phases / risk をワークスペースとプロジェクトの和にする

親チケット `config-union` の決定一覧を、設計書 §25 の改版として書いたもの。docs フェーズで
`ccnavi.md` §25 へ写す。節番号は今の §25 に合わせ、差し替える節はその番号で、足す節は
既存の節の小節か末尾の新節として書く。触らない節（§25.3 ツリー、§25.5 チケット、§25.10 実測）は
ここには書かない。

## 0. 何を解くか

§25 は、道具はワークスペース、設定はプロジェクト、git はツリーごと、の 3 つを骨にして
複数のリポジトリを 1 つのワークスペースの下で扱えるようにした。ただし設定 3 本のうち
プロジェクトごとに持てるのは rules だけで、しかも Write / Edit はプロジェクトの `config/rules.yml`
1 本で判定していた。これで 2 つが成り立たない。

- ワークスペースのルールがプロジェクトのファイルに効かない。`guard-hooks` / `credentials` /
  `main-tree` のような、どのツリーでも効いてほしい deny を、プロジェクトごとに写して
  育てなければならない。写し忘れたプロジェクトが穴になる
- phases と risk がワークスペースに 1 本しか無い。phases の `scope` は作業ツリーのレイアウトに
  縛られる（`ccnavi/*`、`tests/*`）ので、レイアウトの違う 2 つ目のプロジェクトで破綻する

改版の骨は 1 つ。**3 本とも「共通層 + そのツリーの層」の和で判定する。足すだけ、上書き無し、
厳しいほうが勝つ。** Write / Edit / NotebookEdit と実行後の監視は共通層 + 行き先の 1 つの層、
Bash は共通層 + 全部の層（今どおり）。

今の §25 との差分は次のとおり。

1. 置き場が `config/rules.yml` から `.ccnavi/config/{rules,phases,risk}.yml` に変わり、
   ワークスペース自身も同じ形の層を持つ（`<ワークスペースルート>/.ccnavi/config/`）
2. Write / Edit の判定が「プロジェクトの 1 本」から「共通層 + 行き先の層」の和になる
3. phases と risk がプロジェクトごとに持てるようになり、id ごとに合成する
4. `CCNAVI_PROJECT_RULES` を廃止し、`CCNAVI_PROJECT_HOME`（既定 `.ccnavi`）1 本にする
5. selfguard の中核に、各層の `.ccnavi/config/` の 3 本と、共通層の phases.yml / risk.yml を足す
6. `.ccnavi/` 全体に組み込みの deny を掛け、`.ccnavi/scripts/` はそれと `CCNAVI_RESTORE_IF_DENY` で守る
7. `--explain` が層ごとに全件を出し、記録に `source` が入る
8. 今の `.claude/ccnavi/phases.yml` の 7 種を自身の層へ移し、共通層の phases は空から始める

`projects/` が無いか空のワークスペースでは、8 の移行を除いて判定・置き場・記録が変わらない。
これは要件にする（§25.12）。

## §25.2 置き場（差し替え）

| 何 | 場所 | git |
|---|---|---|
| hook の登録、実行ファイル、保護済みスクリプト、スキル、CLAUDE.md | ワークスペースルート | ワークスペース |
| **共通層**の rules / phases / risk | `.claude/ccnavi/{rules,phases,risk}.yml`（`CCNAVI_RULES` / `CCNAVI_PHASES` / `CCNAVI_RISK`、今と同じ） | ワークスペース |
| **ワークスペース自身の層**の rules / phases / risk | `<ワークスペースルート>/.ccnavi/config/{rules,phases,risk}.yml` | ワークスペース |
| **プロジェクトの層**の rules / phases / risk | `projects/<名前>/.ccnavi/config/{rules,phases,risk}.yml` | プロジェクト |
| 固有スクリプト（risk の `script:` が指す先） | 共通層は `.claude/ccnavi/` と `.claude/scripts/`。自身の層とプロジェクトの層は、それぞれの `.ccnavi/scripts/` | 層と同じ |
| プロジェクト | `projects/<名前>/`（`CCNAVI_PROJECTS`、既定 `projects`、ワークスペースルートからの相対） | ワークスペースでは無視。プロジェクト自身の git |
| 作業ツリー | `.claude/worktrees/<識別子>/`。ワークスペースかプロジェクトから切る | 管理外 |
| 提案 | ワークスペースの `wip/<プロジェクト>/tickets/<状態>/<識別子>.md`。ワークスペース自身の作業は `wip/tickets/<状態>/` | ワークスペース |
| 承認済みの写し、閉じた写し、フェーズの印 | ワークスペースの `.claude/ccnavi/tickets/`。写しに `project:` が入る | 管理外 |
| 記録、控え、セッションの状態 | ワークスペースの `.claude/ccnavi/` | 管理外 |
| git ラッパの記録 | ワークスペースの `logs/<プロジェクト>/`。ワークスペース自身は `logs/` | 管理外 |

**層は 3 種で、形は同じ。** 共通層は「どのツリーにも効くもの」を置く場所で、今の
`.claude/ccnavi/` のまま。自身の層とプロジェクトの層は「そのツリーにだけ効くもの」を置く場所で、
どちらも git プロジェクトルートの下の `.ccnavi/` に同じ形で置く。ワークスペース自身に層を
分けるのは、ワークスペースのフェーズの種類（`scope: ["ccnavi/*", ...]`）がワークスペースの
レイアウトにしか合わないから。共通層に置くと全プロジェクトに効いて、2 つ目のプロジェクトで
破綻する。

**環境変数は `CCNAVI_PROJECT_HOME` の 1 本。** 既定 `.ccnavi`、git プロジェクトルートからの相対。
自身の層とプロジェクトの層の両方に同じ値が効く。`config/` と `scripts/` と 3 本のファイル名は固定で、
動かせるのは傘の名前だけ。`CCNAVI_PROJECT_RULES` は廃止し、`RETIRED_ENVS` に入れる。設定されて
いれば `--lint` が warn で名指しする。黙って無視すると、書いた人は効いていると思い続ける。

**`config/` ではなく `.ccnavi/` の下に置く理由。** `config/` はプロジェクトが既に持っていることが
多い名前で、そこに ccnavi のファイルを混ぜると、組み込みで守る綴りをプロジェクトの持ち物と分けられない。
`.ccnavi/` なら 3 本とスクリプトを 1 つの傘の下に置け、組み込みの deny が `*/.ccnavi/*` の 1 行で
済む。採らなかった形は 2 つ。プロジェクトの `.claude/`（Claude Code がそこのスキルを読み、`--lint` が
迷い子として拾う。今の §25.1 のまま）と、3 本をそれぞれ別の環境変数で動かす形（動かす理由が無く、
つまみが増えるだけ）。

**読むのは常に git プロジェクトルートに checkout されている版。** ブランチは問わない。作業ツリーの
中の `.ccnavi/` は読まない（今の rules の扱いと同じ、REQ-MLT-04）。ワークスペース自身の層も同じで、
読むのはワークスペースルートの `.ccnavi/config/` で、ワークスペースから切った作業ツリーの中の写しは
読まない。

**層のファイルが無い = その層は空。** 3 本は独立に無くてよい。壊れている = その層は空 + 記録の
`fallback` に層の名前 + `--lint` の error。組み込みの既定へは落ちない。共通層が有るのに組み込みに
落とすと、共通層の deny が消える側に倒れるから。

共通層（`CCNAVI_RULES`）自身が読めないときは今のまま（組み込みの既定に落ちる、REQ-PRE-06）。
そのとき自身の層とプロジェクトの層は足さない。共通層が壊れている = 設定が壊れている、の扱いを
変えない。壊れた共通層の上に層を足しても、何が効いているかを人が読めない。

## §25.4 ルール（差し替え）

**書き込み系（Write / Edit / NotebookEdit）は共通層 + 行き先の層の和。** 行き先のツリーの
`project` がプロジェクトなら、その git プロジェクトルートの `.ccnavi/config/rules.yml`。
ワークスペースのツリー（ワークスペースルートと、そこから切った作業ツリー）なら
`<ワークスペースルート>/.ccnavi/config/rules.yml`。どちらも共通層の上に足す。ルールは今までどおり
解いた先の絶対パスに当てるので、書き方は変わらない。プロジェクトのルールに `*/schema/*` と
書けば、git プロジェクトルートでも、そこから切った作業ツリーでも当たる。

**Bash は全部の和。** 共通層、自身の層、全プロジェクトの層をタイプごとに連結して当てる。
どのプロジェクトに `cwd` があっても、`cd` を挟んでも、同じ判定になる（REQ-MLT-05）。
どのプロジェクトに効くかは payload から決められないので決めない。

**順は 共通層 → 自身の層 → プロジェクトの層（名前順）。** `deny` `ask` `allow` の順は変わらず、
同じタイプの中で層の順に並ぶ。先に当たったルールが reason に載るだけで、判定は同じ。

**id には層の名前を添える。** 共通層は裸の `id`、自身の層は `self:id`、プロジェクトは `<名前>:id`
（今の `lib:source` の形、REQ-MLT-07）。記録の `rules` と文面の `source` はこの形で出す。
読んだ人が id だけで直しに行くファイルが分かる。

`self` という名前のプロジェクト（`projects/self/`）は `self:id` と区別できないので数えない。
`--lint` が error で言う。接頭辞を別の綴りにするより、名前を 1 つ予約するほうが安い。

**重複は後ろを捨てる。** 裸の `id` が同じで、`{root}` を置き換えたあとの全欄（`match` / `glob` /
`regex` / `message` / `additionalContext*` / `additionalContext*File`）が一致する定義は、同じ
ルールの写しとみなし、後ろの層のものを捨てる。`--lint` が info で言い、`--explain` は残った 1 本
だけ出す。Bash の和でも同じ。見本を写して始めたプロジェクトが共通層と同じ行を持つのは普通の形で、
それを衝突と呼ぶと本当の衝突が埋もれる。

**同 `id` で中身が違うとき、rules は両方効く。** `--lint` が warn で言う。rules は足すだけの
面なので、両方効いても `deny` と `ask` は増える側に倒れる。`allow` は広がる側だが、行き先の 1 層
にしか足さないので、広がる範囲はそのツリーの中に閉じる（Bash は次の代償の項）。phases と risk は
両方効かせられないので扱いが違う（§25.4.1、§25.4.2）。

**代償。** 3 つ。プロジェクトは共通層の `ask` を `allow` に緩められない（和は足すだけで、
`ask` は `allow` より先に当たる）。共通層の `allow` は全プロジェクトに効く。Write / Edit に
他プロジェクトのルールは足さないので「A のルールを B にも」は書けない。要るなら共通層へ上げる。
Bash の allow の共有は今の §25.4 のまま。

採らなかった形は「プロジェクトの層で共通層を上書きできる」。プロジェクトを編集している
エージェントは（守られていなければ）プロジェクトの層を書けるので、上書きできる形は共通層の
deny を消す道になる。和なら、書けたとしても足すことしかできない。

### §25.4.1 phases の合成（新節）

**どの層を足すかは親の写しの `project:` で決まる。** 人が承認した値で、子は親から継ぐ。
空ならワークスペース自身の層。作業ツリーの切り元は `project:` と合うことを着手時に確かめてある
（REQ-MLT-13）ので、フェーズの種類を選ぶのに申告の値を使っても、判定が申告に依存する形にはならない。

**id ごとに合成する。** 共通層の種類と、その層の種類を 1 つの集合に並べる。同 `id` で中身が
違えば `--lint` が error。`title` の重なりも error（今の 1 本の中の規則を層をまたいで適用する。
人は表示名で見るので、承認画面で見分けられない）。同 `id` で全欄が一致するものは rules と
同じく写しとみなして後ろを捨て、info で言う。`overlap` / `requires` が指す先は合成後の集合の
中に居ればよい。プロジェクトの層から共通層の種類を指せる。

**衝突した層は空として扱う。** error があるとき、判定はその層を空にし（共通層の種類だけで
進む）、記録の `fallback` に層の名前を残す。共通層だけで進むと、親の `plan:` がその層の種類を
指している場合は「種類が無い」で承認が止まる。止まる側に倒れる。衝突した片方を黙って採ると、
どちらの `review:` が効いているかを人が読めない。

採らなかった形は、共通層の定義を採る（衝突を黙って解いてしまい、lint を見ない人には
上書きと同じに見える）と、合成全体を捨てて番号だけの挙動に落とす（共通層まで巻き添えになる）。

**`id` は裸のまま鍵にする。** `plan:` と `feedback:` と印は裸の `id` で種類を指す。層の名前は
`--explain` と記録の `source` に出す。rules と違って `self:design` のような接頭辞を id に付けない
のは、id が合成の鍵であり、人が `plan:` に書く名前だから。接頭辞を付けると同 `id` の衝突が
定義上起きなくなり、「同じ名前で違う中身」を error にできない。

親チケットの「`id` は共通層が裸、自身の層が `self:id`」は rules の記録の形。phases / risk でも
記録と `--explain` には `self:design` の形で出すが、`plan:` と `judge` の引数は裸の `id` のまま。

**`scope` は作業ツリーのルートからの相対のまま。** 種類が効くのは、その層のプロジェクトから切った
作業ツリーだけなので、相対の基準は今と同じ。`inherit` も同じ。`{root}` はここでは使わない。

### §25.4.2 risk の合成（新節）

**どの層を足すかは phases と同じ。** 子を閉じるときに、親の写しの `project:` の層を共通層に足す。

**`factors` は連結、`levels` はキーごとに小さいほう。** 共通層の項目の後ろに層の項目を並べる。
同 `id` は `--lint` error（今の 1 本の中の「重複している」を層をまたいで適用）。全欄一致なら
写しとして後ろを捨てる。`levels` は `medium` / `high` / `critical` のそれぞれで、書かれた値の
うち小さいほうを採る。書かれていない鍵は参加せず、どの層も書いていなければ既定（20 / 40 / 70）。
`parse` が既定で埋めた値を参加させると、`levels` を書いていないプロジェクトが共通層の緩めた
閾値を黙って戻すことになる。合成後に `medium <= high <= critical` でなければ error。各層が単独で
順を守っていればキーごとの min でも順は崩れないが、確かめるのは安い。

**衝突した層は空として扱う。** phases と同じ。`fallback` に層の名前を残し、閉じるときの出力が言う。
共通層だけで測るので、点は小さくなる方向に倒れうる。ここは「壊れたスクリプトがリスクを消す」と
同じ穴なので、`--lint` を error にして、直すまで目に付く形にする。

**`script:` の解決先は層ごとに違い、たがいに指せない。**

| 層 | `script:` に書ける綴り | 解決の基準 |
|---|---|---|
| 共通層 | `.claude/ccnavi/...` か `.claude/scripts/...`（今の `SCRIPT_HOMES`） | ワークスペースルート |
| 自身の層 | `.ccnavi/scripts/...`（`CCNAVI_PROJECT_HOME` に従う） | ワークスペースルート |
| プロジェクトの層 | `.ccnavi/scripts/...`（同上） | そのプロジェクトの git プロジェクトルート |

共通層から `.ccnavi/scripts/` を、層から `.claude/scripts/` を指す定義は `parse` で error。
指す先が git プロジェクトルートに無ければ `--lint` error（走らせるときは今どおり「測れなかった」で
その項目の点を加える）。作業ツリーの中のスクリプトは読まない。`cwd` は今どおり子の作業ツリーで、
環境変数（`CCNAVI_BASE_SHA` 等）も同じ。

たがいに指せないのは、プロジェクトのリポジトリに入る定義がワークスペースの道具に依存する形を
作らないため。プロジェクトの `.ccnavi/` はそのプロジェクトだけで閉じ、ワークスペースの側は
共通層だけで閉じる。

## §25.6 守るもの（差し替え）

selfguard の中核（ルールファイルの外で、組み込みで、控えと復元まで持つ対象）を次にする。

| 対象 | 場所 | 今との差 |
|---|---|---|
| hook の登録 | `.claude/settings.json`、`.claude/settings.local.json` | 同じ |
| 共通層の 3 本 | `CCNAVI_RULES` / `CCNAVI_PHASES` / `CCNAVI_RISK` が指すファイル | phases.yml と risk.yml を足す |
| 自身の層の 3 本 | `<ワークスペースルート>/.ccnavi/config/{rules,phases,risk}.yml` | 新規 |
| プロジェクトの層の 3 本 | `projects/<名前>/.ccnavi/config/{rules,phases,risk}.yml` | `config/rules.yml` の 1 本から 3 本へ |
| 実行ファイル | `CCNAVI_BIN_PATH` | 同じ |
| 作業ツリーの中の写し | 上の各ファイルのうち追跡されているものの、その切り元から切った作業ツリーの中の写し | phases / risk と各層の 3 本を足す |

ファイル単位で、無いものは対象から外れる（REQ-SLF-03）。Write / Edit の拒否、シェルからの
書き込みの拒否、控えと復元の 3 つとも、今 `rules.yml` に掛けているものをそのまま掛ける。

**共通層の phases.yml / risk.yml を足すのは既存の穴の修正。** この 2 本は「人の持ち物」として
ルールの `guard-ccnavi-config` で止めていたが、作業ツリーの中では allow `worktrees` が当たり、
Write / Edit で書けて、復元もされなかった。写しは統合で main へ入る道を持つ（selfguard.py 冒頭
「作業ツリーの中の写しも同じ扱い」）。ルールで止める形は、ルールの並びと書き方で緩みうる。
phases.yml は「レビューが要るか」を、risk.yml は「点が幾つか」を決めるので、rules.yml と同じく
判定の中身そのもの。中核へ入れて、ルールに依らず止め、書けても戻す。

**selfguard.py 冒頭「数を増やさない」との整合。** 中核に足すのは「ccnavi が動くこと自体が
懸かっているもの」に限る、と冒頭に書いてある。各層の `config/` の 3 本はそれに当たる。判定の
中身そのもので、ここが書けると、エージェントが自分のルール・レビューの要否・リスクの配点を
緩めて次の呼び出しから効く。守る場所は層の数だけ増えるが、守る対象の種類は「設定 3 本」の
1 種のまま。

`.ccnavi/scripts/` は中核に入れない。スクリプトは risk.yml から `script:` で参照されて初めて効き、
参照が無ければ何もしない。risk.yml が中核で守られていれば、新しい参照を足すことはできない。
残る道は「既に参照されているスクリプトの中身を書き換える」で、これは次の組み込み deny と
`CCNAVI_RESTORE_IF_DENY` で止めて戻す。中核の控えと復元まで持たせるのは、ファイル数が
定まらないものを毎回読んで比べることになり、期限に効く。

**`.ccnavi/` 全体に組み込みの deny を掛ける。** `selfguard.add_rules` が `deny` の先頭に足す
ルールをもう 1 本増やす。`match: Write|Edit|NotebookEdit`、`glob: */.ccnavi/*`、id は
`builtin-guard-project-home`（`CCNAVI_PROJECT_HOME` の値で綴りを組む）。シェルからの書き込みは
`_PLACES` / `_COPY_PLACES` に `\.ccnavi[\\/]` を足して同じ 1 本（`builtin-guard-setting-files`）で
止める。ルールファイルの外に置くのは今の `builtin-guard-project-rules` と同じ理由で、置き場が
設定で動き、そのプロジェクトのルール自身に任せると書けた瞬間に緩められるから。今の
`project_rules_clause` と `PROJECT_RULES_RULE_ID` はこの 1 本に置き換える。

deny なので、実行後の監視が保護領域に数え、`CCNAVI_RESTORE_IF_DENY` が git の変更一覧に出た
差分を戻す。これで `.ccnavi/scripts/` の追跡済みファイルへの書き換えは、Write / Edit とシェルで
止まり、抜けても戻る。

**作業ツリーの中の `.ccnavi/` の新規ファイルは拾わない。** 明記する。作業ツリーの `.ccnavi/scripts/`
に足した新しいファイルは、git プロジェクトルートの risk.yml から参照されていないので効かない。
効くのは、そのブランチが統合され、git プロジェクトルートに checkout された後で、そこは人の
レビューを通る。`--lint` が「作業ツリーの `.ccnavi/` に git プロジェクトルートに無いファイルが
ある」を warn で言い、統合の前に気づける形にする。

**前提。** ワークスペースの利用者は、プロジェクトの git プロジェクトルートに checkout されている
版を信頼している。ccnavi が守るのは「checkout されている版が、セッションの中で書き換えられない」
ことまでで、そこへ入る前の経路（ブランチ、MR、レビュー）は人の運用に置く。

**プロジェクトから切った作業ツリーの写しを足すのも既存の穴の修正。** 今の `_worktree_copies` は
`tree.worktrees(root)` を切り元無しで呼ぶので、プロジェクトから切った作業ツリーは列挙されず、
プロジェクトのルールの相対も「ワークスペースルートから」（`projects/lib/config/rules.yml`）で
組むので、作業ツリーの中にその綴りは無い。プロジェクトの作業ツリーの中の写しは今は守られて
いない（確認済み）。改版では `tree.worktrees(root, projects_dir)` で切り元付きに列挙し、写しの
相対を切り元の git プロジェクトルートから組む。要件（REQ-MLT-08 の変更）に「切り元から切った
作業ツリーの中の写し」と明記する。

## §25.7 実行後の監視（1 行の差し替え）

「git プロジェクトルートで見た変更に当てるのはそのプロジェクトのルール」を「共通層 + そのツリーの
層のルールの和」に直す。作業ツリーなら、そのツリーに結び付くチケットの範囲と、共通層 + 切り元の層。
Bash の和はここでも使わない。

## §25.8 `{root}` と `script:`（差し替え）

`{root}` はルールを読むときにワークスペースルートの絶対パスへ置き換わる（`rules.load`）。自身の層と
プロジェクトの層のルールでも同じで、置き換わる先はそのプロジェクトではなくワークスペースルート。
sh はそこにしか無く、拒否の文面が案内する綴りはそこを指すから。重複の判定（§25.4）も置き換えた
後の欄で比べる。

phases と risk の欄にはパスの綴りを書く場所が `script:` しか無く、`script:` は §25.4.2 の解決先で
決まるので `{root}` は使わない。文面の欄（`message` / `when`）に `{root}` を書いた場合も置き換え先は
ワークスペースルート。

今の phasetypes / risk は `{root}` を置き換えておらず、改版でも足さない（書いても素通し）。
案内の綴りが要るのは拒否の文面で、それは rules の側にある。

sh の置き場と git ラッパの記録の項は今のまま。

## §25.9 診断と記録（差し替え）

**`--explain` は層ごとに全件。** ルールは `id / match / glob（か regex）` を共通層・自身の層・
各プロジェクトの順に、タイプごとに並べる。重複で捨てた定義は出さない。phases と risk は
`id / 出どころの層 / 主な欄` の表を足す。

```
■ rules 共通層（.claude/ccnavi/rules.yml、deny 12 / ask 3 / allow 4）
  deny  guard-hooks          Write|Edit|NotebookEdit  */.claude/hooks/*
  ...
■ rules 自身の層（.ccnavi/config/rules.yml、deny 0 / ask 0 / allow 1）
  allow self:worktrees       Write|Edit               */.claude/worktrees/*
■ rules lib（projects/lib/.ccnavi/config/rules.yml、deny 2 / ask 0 / allow 1）
  deny  lib:schema           Write|Edit               */schema/*
■ phases（共通層 0 種、自身の層 7 種、lib 3 種）
  id            層     kind  title         review  scope
  design        self   work  設計          mr      wip/design/*, docs/*
  build         lib    work  ビルド        mr      src/*
■ risk（levels: medium 20 / high 40 / critical 70）
  id            層     当て方            points  message
  big-diff      共通   lines_over 300    25      行数が多い
  schema        lib    glob db/schema/*  30      スキーマに触った
```

読めない層はその位置に「読めない: <理由>」と、空として扱っていることを出す。

**記録に `source` を 1 欄足す。** 値は `common | self | <名前>`。`log.jsonl` の 1 行（`audit.Record`）
には、判定を下したルール（`rules` の先頭）の層。ルールが当たらなかった行は空。risk の記録
（`phases/<親>/<子>.risk.json`）は加点した項目ごとに、judge の記録（`<子>.judge.json`）は項目ごとに、
その項目の層。フェーズの印のうち種類を根拠に置くもの（`reviewed` / `skipped` の根拠に `review:` が
絡むもの）には、その種類の層。

印に足すのはこの「種類を根拠に置くもの」だけ。他の印は種類を見ずに置くので、層を書いても
根拠にならない。

**`--lint` が言うこと。** 今の項目（`projects/` が無視されているか、プロジェクトが `.claude/` を
持たないか、作業ツリーの切り元が `project:` と合うか）に次を足す。`.ccnavi/config/` が無いことは
言わない。無いのは正常で、言うと本当に言うべきものが埋もれる。

| 深刻度 | 何を言うか |
|---|---|
| warn | `CCNAVI_PROJECT_RULES` が設定されている（もう効かない） |
| info | 裸の `id` と全欄が一致する重複を後ろの層で捨てた（rules / phases / risk） |
| warn | rules の同 `id` で中身が違う（両方効いている） |
| error | phases の同 `id` で中身が違う、`title` が層をまたいで重なる |
| error | risk の同 `id`、合成後の `levels` の逆転 |
| error | risk の `script:` が指す先が git プロジェクトルートに無い、層の外を指している |
| error | 層のファイルが壊れている（空として扱っている） |
| warn | 作業ツリーの `.ccnavi/` に、切り元の git プロジェクトルートに無いファイルがある |

**導入スクリプト。** `ccnavi-setup.sh` は共通層に `rules.yml` と `risk.yml`、自身の層に
`phases.yml` のひな形を配る。3 本とも「まだ無いもの」の点検に数える（`note_missing`）。`--all` の
env に `CCNAVI_PROJECT_HOME: ".ccnavi"` を足す。

## §25.11 見ないもの、入れないもの（差し替え）

- VS Code 拡張の設定画面への phases / risk の追加。拡張は rules のまま
- `CCNAVI_TICKET_CONTROL` のプロジェクト単位化。README の語をワークスペース単位の意味に直すだけ
- 反映先ブランチの git オブジェクトから設定を読む形。読むのは checkout されている版だけ
- プロジェクトの `.claude/` を認める形
- `.ccnavi/scripts/` を selfguard の中核にする形（§25.6）
- プロジェクト A に居るときだけ効く Bash のルール、`cwd` と `cd` の追跡（今のまま）
- 2 段以上の深さに置いたプロジェクト、ワークスペースの外に置いたプロジェクト（今のまま）
- 作業ツリーの中に checkout された設定ファイル（今のまま。3 本とスクリプトに広げる）

受け入れる代償。

- プロジェクトは共通層の `ask` を `allow` に緩められない
- 共通層の `allow` が全プロジェクトに効く
- Write / Edit に他プロジェクトのルールは足さないので「A のルールを B にも」は書けない
- 作業ツリーで足した `.ccnavi/` の変更は、git プロジェクトルートに入るまで効かない
- 層の数だけ読み込みが増える。1 本 200 行の YAML で数 ms なので期限の中に収まるが、`ms` の欄で見る
  （§25.10 のまま）

## §25.12 移行（新節）

- 今の `.claude/ccnavi/phases.yml` の 7 種（research / design / acceptance / implement / docs /
  design-feedback / implement-feedback）を全部 `<ワークスペースルート>/.ccnavi/config/phases.yml`
  へ移す。共通層の phases は空から始める。`scope` はワークスペースのレイアウトのものなので、
  共通層に残すとプロジェクトに効いてしまう。rules と risk は汎用なので共通層に残す
- `plan:` と印は裸の `id` で種類を指すので、既存のチケットと写しは書き換えない
- 既存のワークスペースで `projects/` が無いか空なら、この移行を除いて判定・置き場・記録は
  変わらない。これを要件にする（REQ-MLT-15 の変更）
- `config/rules.yml` を持つ既存のプロジェクトは `.ccnavi/config/rules.yml` へ人が移す。旧の場所は
  読まない。`--lint` が「`config/rules.yml` があるが読まない」を warn で言う

移行の手（phases.yml の移動）は、実装フェーズの作業ツリーの中で、新しい実行ファイルを配る前に
行う。hook が呼ぶのはワークスペースルートの `dist/` の版で、作業ツリーで組み直しても hook の側は
変わらないから、組み込みの deny `*/.ccnavi/*` は移行の間はまだ効かない。配ったあとは
`.ccnavi/config/` は rules.yml と同じく人の持ち物で、エージェントの Write / Edit は通らない
（親チケットの allow `.ccnavi/*` は deny に負ける。それでよい）。既存のプロジェクトの
`config/rules.yml` の移動は人が行う。

## 実装の入口ごとの変更点

| ファイル | 何を変えるか |
|---|---|
| `ccnavi/settings.py` | `PROJECT_RULES_ENV` / `DEFAULT_PROJECT_RULES` / `Settings.project_rules` を `PROJECT_HOME_ENV = "CCNAVI_PROJECT_HOME"` / `DEFAULT_PROJECT_HOME = ".ccnavi"` / `Settings.project_home` に置き換え、`CCNAVI_PROJECT_RULES` を `RETIRED_ENVS` に足す。`project_rules_path` / `project_rules_real_path` を `layer_path(conf, home_root, kind)`（kind は rules / phases / risk）に一般化し、`--project-rules-file` の差し替え（`project_rules_files`）は rules にだけ効かせる。自身の層は `home_root = root` で同じ関数を通す |
| `ccnavi/ruleload.py` | `rules_for` を「共通層 + 行き先の層」の和に直す。層を読んで `prefix_ids`（`self:` か `<名前>:`）し、`merge_rules(base, extra, layer)` で連結しながら重複（裸 id + 置換後の全欄一致）を捨て、同 id 違いを `Problem` として返す。壊れた層は空 + `record.fallback` に層の名前（組み込みへ落とさない）。`project_rules_files` は `layer_files(conf)` にして 3 本 × 各層を selfguard へ渡す |
| `ccnavi/rules.py` | `Rule` に `source`（層の名前）と `bare_id` を持たせるか、比較用の `key()`（置換後の全欄のタプル）を足す。`load` は変えない |
| `ccnavi/phasetypes.py` | `load(path)` は 1 本のまま。`merge(common, extra, layer)` を足し、同 id で中身が違う・title の重なり・全欄一致の写しを `Problem` に分けて返す。`PhaseType` に `source` を足す。`parse` の overlap / requires の参照確認は合成後にも通す |
| `ccnavi/phase.py` | `load_types(conf)` を `load_types(conf, project)` にし、親の写しの `project:` で層を選んで共通層と合成する。呼び元（`phases_of`、承認、`lint._phase_types`、`diagnose`）に `project` を渡す |
| `ccnavi/risk.py` | `parse` に `home`（共通 / 層）を渡して `script:` の許す綴りを分ける（`SCRIPT_HOMES` は共通層用、層は `<project_home>/scripts/`）。`levels` は書かれた鍵だけを返す形にし、`merge(common, extra, layer)` で factors 連結・同 id error・levels の min・逆転の確認。`Factor` に `source` と `home`（解決の基準ディレクトリ）を足し、`run_script(root, ...)` を `run_script(factor.home, ...)` に。`load_definition(conf)` を `load_definition(conf, project)` に |
| `ccnavi/selfguard.py` | `targets` の `project_rules` 引数を `layers: list[tuple[str, str, str]]`（層、kind、パス）にし、key を `rules` / `phases` / `risk` / `rules:self` / `phases:<名前>` のように組む。共通層の `conf.phases` / `conf.risk` を足す。`_worktree_copies` は `tree.worktrees(root, projects_dir)` で切り元付きに列挙し、写しの相対を切り元の git プロジェクトルートから組む。`project_rules_clause` / `PROJECT_RULES_RULE_ID` を `project_home_clause(project_home)` / `PROJECT_HOME_RULE_ID = "builtin-guard-project-home"`（`*/.ccnavi/*`）に置き換え、`_PLACES` / `_COPY_PLACES` に `\.ccnavi[\\/]` を足す。冒頭 docstring の「3 つだけ」を「各層の設定 3 本」に書き直す |
| `ccnavi/judge.py` | `guard_setting_files` が `selfguard.targets` に渡す引数を `ruleload.layer_files(conf)` に。`decide_before` で `record.source` を埋める（`rules` の先頭の接頭辞から） |
| `ccnavi/events.py` | `watched_for` の層ごとの読み込みを `ruleload` の和（共通層 + そのツリーの層）に寄せ、`loaded` の鍵はそのまま `t.project` |
| `ccnavi/post.py` | 変更は小さい。`_source` は接頭辞付き id をそのまま名乗る。`Watched` が持つ `source` に層の並びを入れる |
| `ccnavi/audit.py` | `Record` に `source: str = ""` を足す |
| `ccnavi/ops.py` | `_score_child` と `judge` で `risk.load_definition(conf, found.project)` を呼び、`risk.json` の各 hit と `judge.json` の各項目に `source` を書く。閉じるときの出力に層の `fallback` を出す |
| `ccnavi/approval.py` | `risk.json` / `judge.json` の書式の注記に `source` を足す。印に `source` を足すなら `write_mark` の呼び元 |
| `ccnavi/diagnose.py` | `explain` を層ごとの全件に書き直す（rules を共通層・自身の層・各プロジェクトの順で、phases と risk の表を足す）。`try_one` / `run_samples` は `ruleload.rules_for` を通るので判定は変わらない |
| `ccnavi/lint.py` | `_projects` を層の点検に広げる（3 本の読み込み、旧 `config/rules.yml` の warn、作業ツリーの `.ccnavi/` の新規ファイルの warn）。`_rules` / `_phases` / `_risk` を合成後の `Problem`（重複 info、同 id warn / error、levels、script の欠け）を出す形に。`retired` に `CCNAVI_PROJECT_RULES` が載れば今の仕組みで warn が出る |
| `ccnavi/tree.py` | 変更なしの見込み。`worktrees(root, projects_dir)` と `Tree.project` をそのまま使う。`self` という名前のプロジェクトを数えない判断が入るならここ |
| `scripts/ccnavi-setup.sh` | `DEPLOY_RULES` に並べて `DEPLOY_RISK=".claude/ccnavi/risk.yml"` と `DEPLOY_PHASES=".ccnavi/config/phases.yml"` を足し、`verdict` / `copy_file` / `note_deploy` を同じ形で通す。`note_missing` に 2 本を足す。`--all` の env に `CCNAVI_PROJECT_HOME` を足す |
| `.claude/ccnavi/rules.yml`（見本） | `guard-ccnavi-config` の regex に `.ccnavi/config/` を足すか、組み込み deny に任せて注記を直す。`guard-ccnavi-config` は ask / 案内の役割（`/ccnavi-config` へ誘導）として残す |
| `.claude/ccnavi/phases.yml` → `.ccnavi/config/phases.yml` | 7 種を移す。共通層の `phases.yml` は置かない（無い = 空） |
| `.claude/skills/ccnavi-config/` | 置き場の説明を 3 層に直す（docs フェーズ） |
| `tests/` | `test_projects.py` に「共通層 + 行き先の層」「Bash の和に自身の層」「重複の排除」「同 id の warn」「壊れた層は空 + fallback」を足す。`test_phases.py` / `test_risk.py` に合成（同 id error、title 重なり、levels の min、script の層外参照、`source`）を足す。`test_selfguard.py` に各層の 3 本と共通層の phases / risk の控えと復元、プロジェクトから切った作業ツリーの写し、`*/.ccnavi/*` の deny とシェルの拒否を足す。`test_lint.py` に新しい項目、`test_setup.py` にひな形の配りと点検、`test_fallback.py` に「`projects/` が無いと変わらない」を足す。fixtures に `.ccnavi/config/` を持つプロジェクトを 2 つ置く |

## requirements.md に足す・直す REQ の候補

番号は付けない。REQ-MLT の節に置く。既存の項目で変わるものは「変更:」で示す。

- 変更（REQ-MLT-03）: ccnavi は、書き込み系ツールの判定に、共通層のルールと、行き先のツリーが属する層
  （プロジェクトなら `projects/<名前>/.ccnavi/config/rules.yml`、それ以外ならワークスペースルートの
  `.ccnavi/config/rules.yml`）のルールをタイプごとに合わせたものを用いること
- 変更（REQ-MLT-04）: ccnavi は、各層の設定ファイル（rules / phases / risk）とスクリプトとして git
  プロジェクトルートにあるものだけを読み、作業ツリーの中にあるものを読まないこと
- 変更（REQ-MLT-05）: ccnavi は、Bash の判定に、共通層と自身の層と全プロジェクトの層のルールをタイプ
  ごとに合わせたものを用い、呼び出し元の作業ディレクトリによらず同じ判定を返すこと
- 変更（REQ-MLT-06）: 層の設定ファイルが読めない間、ccnavi は、その層を空として扱い、組み込みの既定
  へ落とさず、空として扱ったことを記録と診断で示すこと
- 変更（REQ-MLT-07）: ccnavi は、記録と文面で、当たったルールの識別子に層の名前（自身の層は `self`、
  プロジェクトはその名前）を添え、記録に判定の出どころの層を残すこと
- 変更（REQ-MLT-08）: ccnavi は、各層の設定ファイル 3 本と共通層の phases / risk を、設定ファイルの
  自己防衛の対象（書き込みの拒否、シェル書き込みの拒否、控えと復元）に含め、切り元から切った作業
  ツリーの中の写しも同じ対象とすること
- 変更（REQ-MLT-15）: ccnavi は、プロジェクト置き場が無いか空のとき、自身の層への移行を除いて、
  この節の前と同じ判定・置き場・記録を保つこと
- 変更（REQ-MLT-16）: `--lint` は、既存の項目に加えて、廃止した環境変数、層をまたぐ重複と同名の衝突、
  `script:` の欠け、作業ツリーの `.ccnavi/` にある切り元に無いファイルを言い、`.ccnavi/config/` が
  無いことは言わないこと
- 変更（REQ-MLT-17）: `--explain` は、層ごとにルールの全件と、phases / risk の定義とその出どころの層を
  示すこと
- ccnavi は、層の和を足すだけで行い、後ろの層が前の層の宣言を上書きしたり取り消したりできないこと
- 同じ識別子で中身の一致する定義が複数の層にあるとき、ccnavi は、前の層のものだけを用い、診断で
  info として示すこと
- 同じ識別子で中身の違うルールが複数の層にあるとき、ccnavi は、両方を判定に用い、診断で warn として
  示すこと
- 同じ識別子で中身の違うフェーズの種類または配点の項目が複数の層にあるとき、ccnavi は、その層を空として
  扱い、診断で error として示すこと
- ccnavi は、フェーズの種類と配点を、親チケットの承認済みの `project:` が示す層と共通層の和から
  決め、子はそれを継ぐこと
- ccnavi は、配点の閾値を、層ごとに書かれた値のうち小さいほうで決め、書かれていない鍵は既定とすること
- ccnavi は、配点の `script:` を、共通層では `.claude/ccnavi/` と `.claude/scripts/` の下、各層では
  その層の `.ccnavi/scripts/` の下に限り、たがいの側を指す定義を受け付けないこと
- ccnavi は、各層の `.ccnavi/` の下への名指しのツールとシェルによる書き込みを、ルールにその宣言が
  無くても拒否すること
- `CCNAVI_PROJECT_RULES` が設定されているとき、ccnavi は、それを無視し、`--lint` で警告すること
- 導入スクリプトは、共通層に rules と risk、自身の層に phases のひな形を配り、無ければ点検で示すこと
