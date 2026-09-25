# 置き場を既定に固定する — 実装の設計

親チケット i0064 / フェーズ 1（design）の成果物。なぜそうするかは
[ADR-0084](../../docs/adr/0084-places-are-fixed-to-defaults.md)。ここは「どこを、どう変えるか」と、
受入テストが確かめることを書く。後のフェーズ（acceptance・implement・staging・docs）はこれに従う。

## 1. 消す 6 つと固定する値

| 変数 | 固定する値 | `Settings` の欄 |
|---|---|---|
| `CCNAVI_PROJECTS` | `<root>/projects` | `projects` |
| `CCNAVI_PROJECT_HOME` | `.ccnavi` | `project_home` |
| `CCNAVI_TICKETS_PROPOSAL` | `wip/proposals` | `tickets` |
| `CCNAVI_TICKETS_APPROVED` | `.ccnavi/approved` | `approved` |
| `CCNAVI_LOG` | `<root>/logs/log.jsonl` | `log` |
| `CCNAVI_STATE` | `<root>/logs/state` | `state` |

`Settings` の欄は残す。値は `settings.load` が既定で埋め、env でも上書き設定ファイルでも動かない。
**診断のフラグは今までどおり欄を上書きする**（`cli.py` の `OVERRIDES` / `RELATIVE_OVERRIDES` /
`LAYER_OVERRIDES` は触らない）。

## 2. 実行ファイル

### 2.1 `ccnavi/settings.py`

- 定数 `LOG_ENV` `STATE_ENV` `TICKETS_ENV` `APPROVED_ENV` `PROJECTS_ENV` `PROJECT_HOME_ENV` を消す。
  参照している先があれば一緒に直す（`grep -rn "_ENV\b" ccnavi/` で拾う）
- `load` の `overrides` 表から 6 行を消す。残るのは `bin` の 1 行
  - 表は上書き設定ファイル（`ccnavi.settings.local.json`）と共有なので、そちらの
    `projects` `project_home` `log` `state` `tickets` `approved` キーも読まれなくなる。**これで正しい**
- `_log_or_none` は `bin` 以外に使われなくなる。使い手が無くなれば消す
- 定数のコメント（置き場の説明）は「固定。ADR-0084」に書き換える

### 2.2 `ccnavi/lint.py`

- `CCNAVI_TICKETS_APPROVED` が空文字のときの指摘（「空文字は受けない」）を元ごと消す
- env の値を検査している箇所が他にあれば同じく消す（`grep -n "APPROVED_ENV\|PROJECTS_ENV\|…" ccnavi/lint.py`）
- `_projects` に **索引の検査** を足す。通常のファイルならぶつかり、gitlink だけなら載せ忘れ（§4）
- `_ticket_places` の `where = … if conf.projects else "(無し)"` の分岐は、`conf.projects` が空に
  ならなくなるので単純にしてよい。ただし診断の `--projects ""` はまだ通るので、**空の扱いは残す**
  （フラグは別チケット）

### 2.3 その他の実行ファイル

`conf.projects` / `conf.tickets` / `conf.approved` / `conf.log` / `conf.state` / `conf.project_home` を
受け取っている先（`tree.py`・`judge.py`・`approval.py` など 20 か所ほど）は **触らない**。空を
受ける分岐も、診断のフラグで空が来うるので残す。

## 3. 保護済みの sh（staging で写す版を作る）

エージェントは `.ccnavi/scripts/` に書けないので、完成品を `wip/design/scripts/` に全文で置き、
人が写す。

| ファイル | 今 | 変える |
|---|---|---|
| `ccnavi-common.sh` | `ccnavi_pj_places="${CCNAVI_PROJECTS:-projects}"` | `ccnavi_pj_places=projects` |
| `ccnavi-git.sh`（push） | `CCNAVI_PROJECTS` / `CCNAVI_TICKETS_APPROVED` / `CCNAVI_TICKETS_PROPOSAL` を読み、絶対パスなら 1 か所、相対ならツリーごと | 既定の綴りをツリーごとに継ぎ足すだけにする。絶対パスの分岐を消す |
| `ccnavi-fetch.sh` | `projects="${CCNAVI_PROJECTS:-projects}"`、`approved="${CCNAVI_TICKETS_APPROVED:-.ccnavi/approved}"` | 既定の綴りを直に書く |
| `ccnavi-push-approved.sh` | 3 つを読み、末尾の `/` を落とし、空と `.` を既定に戻す | 既定の綴りを直に書き、正規化の `case` 3 つと `%/` を消す。冒頭の説明から `$CCNAVI_…` を消す |
| `ccnavi-review.sh` | `state="$root/${CCNAVI_STATE:-logs/state}"` | `state="$root/logs/state"` |
| `ccnavi-approve.sh` | 冒頭の説明に `$CCNAVI_TICKETS_APPROVED` | 説明だけ直す |

`wip/design/scripts/ccnavi-git.sh` は既に（以前のフェーズの写しとして）置かれている。**写す版は
`main` の `.ccnavi/scripts/` の現行から作り直す**。古い写しを土台にしない。

各 sh の他の `CCNAVI_*`（`CCNAVI_BIN_PATH`・`CCNAVI_WORKSPACE`・`CCNAVI_MODE`・`CCNAVI_GIT_*`・
`CCNAVI_FETCH_TIMEOUT` など）は触らない。

## 4. `projects/` のぶつかりと載せ忘れを知らせる

ADR-0084 の「見つける条件」は「`projects/` の下のファイルを 1 本でも追跡している」で、gitlink を
区別していない。この設計は、gitlink だけの場合（載せ忘れ）を改名の案内から外し、索引から外して
無視に入れる案内にする（利用者の決定）。ADR 側の直しは別に扱う。

### 4.1 条件

ワークスペースの git の索引に、`projects/` の下のものが載っているかを見る。載っているものの
**mode で 2 つに分ける。**

```
git -C <root> ls-files -s -z -- projects/
```

各行は `<mode> <oid> <stage>\t<path>`。mode `160000` は gitlink（入れ子のリポジトリを 1 つの
版として載せたもの）、それ以外（`100644`・`100755`・`120000`）は通常のファイル。

| 索引に載っているもの | 呼び名 | 案内 |
|---|---|---|
| 通常のファイルが 1 本以上（gitlink の有無は問わない） | **ぶつかり** | ワークスペースの `projects/` を改名する（`git mv projects apps`） |
| gitlink だけ | **載せ忘れ** | 索引から外して無視に入れる（`git rm -r --cached projects` と `.gitignore` に `/projects/`） |
| 何も無い | — | ここでは何も言わない（§4.2 の既存の検査へ） |

- **gitlink だけの場合はぶつかりではない。** `.gitignore` に入れ忘れたまま `git add -A` した人の
  索引には、`projects/<名前>` が gitlink として載る（git は入れ子のリポジトリの中身を追わず、
  版だけを載せる）。ワークスペース自身のソースに `projects/` があるわけではないので、改名は
  要らない。索引から外して無視に入れれば、置き場はそのまま使える
- **両方あるときはぶつかりとして扱う。** 通常のファイルがある以上、`.gitignore` に `/projects/` を
  足せという案内は誤り（ソースが追跡から外れる）。gitlink も載っていることは、ぶつかりの文面に
  1 文足して名指しする（§4.2 の文面）
- 載せ忘れの直し方に `git rm -r --cached projects` と書けるのは、索引の `projects/` の下が gitlink
  だけのときに限る（通常のファイルがあれば、それも索引から外れる）。両方あるときの 1 文では、
  gitlink のパスを 1 本ずつ名指しする
- `.gitmodules` に登録したサブモジュール（意図して載せた gitlink）も、mode だけを見るので載せ忘れと
  同じ扱いになる（§4.5 の分岐 2）
- 索引に gitlink が残っている間は、`.gitignore` に `/projects/` を足しても `git check-ignore -q projects`
  は「無視されていない」（終了コード 1）を返す（git 2.39 で確かめた）。`.gitignore` だけでは直らない
  ことの裏付けで、載せ忘れのときに既存の「無視されていない」を出さない理由の 1 つ（§4.2）
- 直す手は、コミット済みなら `git rm -r --cached projects`（ファイルは残る）。索引に載せただけで
  コミット前なら、`git rm --cached` は `-f` なしでは断るので `git reset -- projects` を使う。文面は両方を言う
- git が無い・失敗したときは何も言わない（`_tracked` の既存の振る舞いと同じ）

`lint.py` では、既存の `_tracked(root, rel)`（`_scratch` も使う）には手を入れず、`projects/` 用に
「通常のファイルの一覧」と「gitlink の一覧」を返す関数を別に置く。`-z` で読み、パスの引用
（`core.quotepath`）に左右されないようにする。

**プロジェクトが 1 つも無くても見る。** 今の `_projects` は `tree.projects(...)` が空なら何も
言わずに返すが、ぶつかりは「これからプロジェクトを置こうとした人」に要る知らせなので、
その早期リターンより前に見る。載せ忘れも同じ位置で見る（入れ子のリポジトリが消えて gitlink だけが
索引に残っている場合もある）。

### 4.2 `--lint` の出し方

| 索引（§4.1） | プロジェクトがある | 無視されていない | 出すもの |
|---|---|---|---|
| 通常のファイルあり | どちらでも | どちらでも | ぶつかりの warn だけ。gitlink もあれば 1 文足す |
| gitlink だけ | どちらでも | どちらでも | 載せ忘れの warn だけ |
| 何も無い | はい | はい | 既存の「無視されていない」warn |
| 何も無い | はい | いいえ | 何も言わない |
| 何も無い | いいえ | — | 何も言わない（今どおり） |

- 載せ忘れのときも「無視されていない」は出さない。載せ忘れの文面が `.gitignore` に足すところまで
  言うので、並べると同じことを 2 度言う。`.gitignore` に既に `/projects/` があっても、索引に載って
  いる限り載せ忘れの warn は出す（`.gitignore` は既に索引にあるものには効かない）
- 予約名と `.claude/` の検査（各プロジェクトごと）は、ぶつかり・載せ忘れの有無に関わらず今どおり出す

名札はどちらも `(projects)`。重さはどちらも warn（error にしない理由は ADR-0084 と同じ。載せ忘れでも
判定は正しく動く）。

**文面（ぶつかり）** — ADR-0084 の案のまま。例のファイルには通常のファイルの 1 本目を入れる。

> `projects/` はワークスペースの git が追跡している（例: `projects/foo/main.py`）。
> ccnavi はワークスペース直下の `projects/` をプロジェクトの置き場として使い、名前は変えられない。
> このままだと `projects/` の下で `.git` を持つディレクトリ（サブモジュールを含む）が
> プロジェクトとして数えられ、その中の設定が判定に使われる。
> 直すには、ワークスペースの `projects/` を別の名前に移す（例: `git mv projects apps`）。
> ccnavi でプロジェクトを置かないなら、このままでも動く。そのときは `projects/` の下に
> `.git` を持つものを置かない

gitlink も載っているときは、末尾に次の 1 文を足す（パスは全部、`、` で並べる）。

> 索引には入れ子のリポジトリ（`projects/lib`）も載っている。ccnavi のプロジェクトとして使うなら、
> 改名の前に `git rm --cached projects/lib` で索引から外し、改名のあとで `projects/` の下へ戻す

**文面（載せ忘れ）** — gitlink の 1 本目を例に入れ、2 本以上なら「ほか N 件」を添える。

> `projects/` はワークスペースの git が追跡している（入れ子のリポジトリとして: `projects/lib` ほか 1 件）。
> `.gitignore` に入れる前に `git add` したものとみられる。プロジェクトは自分の git を持つので、
> ワークスペースの git には載せない。載せたままだと、ワークスペースのコミットがプロジェクトの版を
> 記録し続け、`.gitignore` に `/projects/` を足しても追跡は外れない。
> 直すには、ワークスペースで `git rm -r --cached projects` を打ち（ファイルは消えない。
> まだコミットしていなければ `git reset -- projects`）、
> `.gitignore` に `/projects/` を足して（既にあればそのまま）、コミットする

**VS Code 拡張とテストが文面の句で見分けている**（`projects-render.ts` のコメントにある「`.claude/` を持つ」
と同じ作り）。見分けに使う句を決めておく。

| 句 | 使う側 | 意味 |
|---|---|---|
| 先頭: `` `projects/` はワークスペースの git が追跡している `` | 拡張・`tests/`・導入スクリプトとの突き合わせ | ぶつかりか載せ忘れ（`.gitignore` のボタンを出さない合図） |
| 先頭の句の直後: `（入れ子のリポジトリとして` | `tests/`（§4.5 の案 B を採るなら拡張も） | 載せ忘れ |

- **先頭の句は 2 つで共通にする。** gitlink も索引に載っている＝追跡されているので、句は事実として
  正しい。拡張がやること（バナーを出し、`.gitignore` のボタンと「無視されていない」のバナーを
  出さない）は 2 つで同じ（§4.5 の案 A）なので、拡張は 1 つの句だけを見ればよい。既に置いた
  A5・A8 の受入テストもこの句で書かれている
- `lint.py` 側に、この 2 つの句が拡張とテストから見られていることをコメントで残す

### 4.3 VS Code 拡張

- プロジェクト画面は、`--lint --json` の `(projects)` の苦情のうち、上の先頭の句で始まるものを
  バナー（warn）で出す。今の `dirProblems` の帯がそのまま出すので、文面は拡張で組み立てない
- そのとき「`.gitignore` に `/projects/` を足す」ボタンと、「無視されていない」のバナー
  （`page.ignored` が偽のときのもの、`webview/projects/App.tsx`）を出さない。**ぶつかりでも載せ忘れでも同じ**
  （§4.5 の分岐 1。案 A を採る）
  - 載せ忘れでボタンを出さない理由: 拡張の `page.ignored` は `.gitignore` の本文だけを見る
    （`gitignoreHasProjects`）。ボタンで `/projects/` を足すと「無視されていない」のバナーは消えるが、
    索引の gitlink は残るので `--lint` の warn は残る。1 つ直したように見えて、残りの半分
    （`git rm -r --cached`）が文面の中にしか無くなる。文面が 2 手を順に言うので、人はそれを見て打てばよい
- 「置き場が無効（`CCNAVI_PROJECTS` が空）」の分岐・文面を消す（`App.tsx`・`projects-panel.ts`・
  `projects-view.ts` のコメント）。ただし `board.settings.projects` は実行ファイルの答えなので、
  診断のフラグで空が来うる — 拡張はフラグを渡さないので、ここでは空が来ないものとして扱ってよい
- `projectsRel === "" ? "projects" : projectsRel` の分岐（`watchProjects`・`tour-sample.ts`）は
  `projectsRel` が空にならないので単純にしてよい

### 4.4 導入スクリプト

入れ終わったところ（報告をまとめる箇所）で §4.1 と同じ条件を見て、`--lint` と同じ文面を出す
（ぶつかりか載せ忘れのどちらか 1 つ）。止めず、終了コードも変えない。`--check` でも出す
（「揃っていない」には数えない — 導入の不足ではないため）。

- sh では `git -C "$root" ls-files -s -- projects/` を 1 行ずつ読み、`case "$line" in 160000\ *)` で
  gitlink を分ける。パスはタブの後ろ（`${line#*<タブ>}`。タブは変数に入れたものを使う）。表示に使うだけなので `-z` は使わず、
  bash 3.2・BSD の道具でも動く形にする
- **導入スクリプトは索引を変えない。** `git rm --cached` を打つのも、載せ忘れのために `.gitignore` に
  `/projects/` を足すのも人。導入スクリプトが `.gitignore` に足すのは今どおり ccnavi の配布物の行だけ

sh と `--lint` が同じ文面を持つので、`tests/sh/test_setup.py` の側で「`--lint` の文面の先頭の句が
導入スクリプトの出力にも含まれる」ことを確かめて、ずれを捕まえる。載せ忘れでも同じことを確かめる（A8b）。

### 4.5 迷った分岐

**分岐 1: 載せ忘れのとき、拡張の「`.gitignore` に追加」ボタンを出すか。**

| 案 | 中身 | メリット | デメリット |
|---|---|---|---|
| **A（採る）** | 出さない。ぶつかりと同じ扱い | 拡張は先頭の句 1 つを見るだけで済む。押して「直った」ように見えて索引が残る、という半端な状態を作らない。拡張が git の索引に触らない今の線を保てる | 2 手とも人が端末で打つ。`.gitignore` に足す 1 手は拡張でも出来たのに、させない |
| B | 出す。押すと `.gitignore` に足し、バナーは「残りは `git rm -r --cached projects`」の形で残る | 手の半分をボタンで済ませられる | 拡張が 2 つ目の句（`（入れ子のリポジトリとして`）で載せ忘れを見分ける必要があり、文面の変更に弱くなる。`page.ignored` が真になると今の作りでは「無視されていない」の帯が消え、`--lint` の warn だけが残るので、押した後の表示を別に作ることになる |

**分岐 2: `.gitmodules` に登録したサブモジュール（意図して載せた gitlink）も載せ忘れとして案内するか。**

| 案 | 中身 | メリット | デメリット |
|---|---|---|---|
| **A（採る）** | 区別しない。mode だけを見る | 条件が 1 つで、`--lint` と導入スクリプトで揃えやすい。今もこの人たちには「無視されていない」が出ているので、出る苦情の数は増えない | 意図してサブモジュールで束ねている人に「外して無視に入れる」と言う。その運用を続ける限り warn が消えない |
| B | `.gitmodules` にあるパスは除く | 意図した構成に苦情を出さない | `.gitmodules` を読む処理を Python と sh の 2 か所に足す。除いた後に「無視されていない」をどう扱うか（`.gitignore` に入れるとサブモジュールと矛盾する）を別に決める必要がある。そういう運用の要望はまだ出ていない |

## 5. 導入スクリプトの `env` の後始末

`scripts/ccnavi-setup.sh`

- `env_json` から `CCNAVI_LOG` を、`--all` の追加から 4 つを消す
- 既存の `settings.json` の `env` から、6 つのキーを消す。今の合成は
  `.env = ($env + (既存) + $overrides)` なので、既存に残った値はそのまま残る。合成のあとで 6 つを
  `del` する
- 消す前に、値が既定と違うものを拾って 1 行ずつ出す:

  > `CCNAVI_STATE` を .claude/settings.json から外しました（値: /var/ccnavi/state）。置き場は既定の logs/state に固定されています（ADR-0084）。

  既定と同じ値なら黙って消す
- `--check` は、6 つのどれかが残っていれば「揃っていない」に数える（打ち直せば消える、と案内する）
- 比べる既定は §1 の表の綴り（相対）。`CCNAVI_PROJECTS` は `projects`、`CCNAVI_LOG` は `logs/log.jsonl`

## 6. 受入テストが確かめること

| # | 確かめること | 置き場 |
|---|---|---|
| A1 | 6 つの env を既定と違う値で入れても、`--explain --json` の `settings` が既定の置き場を返す | `tests/config/` |
| A2 | ccnavi 自身の上書き設定ファイルに 6 つのキーを書いても、同じく既定を返す | `tests/config/` |
| A3 | `CCNAVI_PROJECTS=""` を入れても `projects/` の下のプロジェクトが数えられる | `tests/guard/test_projects.py` |
| A4 | `CCNAVI_LOG=""` を入れても記録が `logs/log.jsonl` に書かれる | `tests/guard/` か `tests/core/` |
| A5 | ワークスペースが `projects/foo.txt` を追跡しているとき、`--lint` が `(projects)` の warn を先頭の句つきで 1 件出し、「無視されていない」を出さない。プロジェクトが 0 件でも出る | `tests/ticket/test_lint_places.py` か新規 |
| A5b | 載せ忘れ: 索引の `projects/` の下が gitlink だけのとき、`--lint` が `(projects)` の warn を 1 件出す。先頭の句は A5 と同じで、直後が `（入れ子のリポジトリとして`。文面に `git rm -r --cached projects` と `/projects/` があり、`git mv` は無い。「無視されていない」を出さない。`.gitignore` に既に `/projects/` があっても出す。コミット前（索引にだけ載っている）でも出す。gitlink が 2 本なら「ほか 1 件」。プロジェクトごとの `.claude/` の検査は今どおり出る | `tests/ticket/test_lint_places.py`（A5 の隣に別のクラス） |
| A5c | 両方: 通常のファイルと gitlink が両方載っているとき、warn は 1 件で、ぶつかりの文面（`git mv`）になり、gitlink のパスを `git rm --cached projects/<名前>` の形で名指しする。`git rm -r --cached projects` は出さない | 同上 |
| A6 | 追跡が無く、プロジェクトがあり、無視されていないときは、今どおり「無視されていない」が出る | 同上 |
| A7 | 導入スクリプトが、既存の `env` にある 6 つを外し、既定と違う値だけを名指しする。既定と同じ値は黙って外す | `tests/sh/test_setup.py` |
| A8 | 導入スクリプトが `projects/` の追跡を見つけたとき、`--lint` と同じ先頭の句を出し、終了コードを変えない | `tests/sh/test_setup.py` |
| A8b | 導入スクリプトが載せ忘れ（gitlink だけ）を見つけたとき、`--lint` と同じ先頭の句と `git rm -r --cached projects` を出し、`git mv` は出さない。終了コードを変えない。`--check` でも出し、「揃っていない」に数えない。`--lint` の文面の 1 文目が出力に含まれる。索引を変えない（実行の前後で `git ls-files -s` が同じ） | `tests/sh/test_setup.py`（A8 の隣に別のクラス） |
| A9 | sh（写す版）が env の値を読まない: `CCNAVI_PROJECTS=/x` を入れても `ccnavi_project` が `projects/<名前>` を返す。`CCNAVI_STATE=/x` を入れても `ccnavi-review.sh` が `logs/state` を見る | `tests/sh/` |
| A10 | 拡張: ぶつかりの苦情があるとき、プロジェクト画面がバナーを出し、`.gitignore` のボタンと「無視されていない」のバナーを出さない | `vscode-extension/ccnavi-board/test/projects/` |
| A10b | 拡張: 載せ忘れの苦情（先頭の句のあとに `（入れ子のリポジトリとして`）でも A10 と同じ。`.gitignore` の本文に `/projects/` が既にある（`page.ignored` が真）ときも、苦情のバナーは出たまま | 同上 |

A5b・A5c・A8b の gitlink は、実際の載せ忘れと同じ手で作る: `projects/lib` で `git init` して 1 回
コミットし、ワークスペースで `git add -A`（`-f` を付けず、`.gitignore` に `/projects/` が無い状態で。
「adding embedded git repository」の警告が stderr に出るが、見ない）。`git update-index --cacheinfo 160000,…` で直に索引へ
入れる形は、人が踏む経路と違うので使わない。

A5b・A5c・A8b・A10b は実装前は落ちる（載せ忘れの分岐がまだ無い）。A5b の「`.claude/` の検査」だけは
今のコードでも通る。

### 直すテスト

env で置き場を指していたテストは、`--root` の下の既定の置き場に置く形か、診断のフラグに寄せる。

| テスト | 今 | 直し方 |
|---|---|---|
| `test_projects.py`・`test_config_union.py` の「数えない」 | `CCNAVI_PROJECTS=""` | `projects/` を作らないワークスペースで確かめる |
| `CCNAVI_PROJECT_HOME` を入れていた 7 本 | ccnavi ディレクトリの名前を変える | 名前を変える意味のあるテストは消す（その口が無くなった）。層の読み方を見ているだけなら既定の `.ccnavi` にする |
| `CCNAVI_TICKETS_APPROVED`・`CCNAVI_TICKETS_PROPOSAL` を入れていた 5 本 | 置き場を変える | 同上 |
| `CCNAVI_LOG`・`CCNAVI_STATE` を入れていた 3 本 | 記録と控えを一時的な場所へ | `--root` の下の `logs/` を見るか、診断のフラグ `--log` / `--state` に寄せる |
| `test_push_approved_sh.py` の正規化の 2 本 | `CCNAVI_PROJECTS=/`・`CCNAVI_TICKETS_APPROVED=.` | 消す（正規化ごと無くなる）。代わりに A9 と同じ形で「値を読まない」を 1 本置く |
| `test_wrapguard.py` の `CCNAVI_PROJECTS=/x` | コマンド行で置く形を止める見本 | **残す**。止める対象は「`CCNAVI_*` を置く形」全般で、変数が読まれなくなっても形は止める（ADR-0077） |

## 7. 文書（docs フェーズ）

- `README.md` の環境変数の表から 6 行を消し、表の前後に「置き場は固定（ADR-0084）」を 1 行
- `README.md` の `CCNAVI_PROJECT_HOME` を引いている他の段落（「ルールは 3 層の和で当たる」など）を
  「`.ccnavi`」に直す
- `ccnavi.md` の置き場の表（§11 ほか）から `CCNAVI_…` の注記を消す。`CCNAVI_PROJECT_HOME` の既定を
  前提にした「以下この章で `.ccnavi/` と書くのは既定のままの綴り」も直す
- 拡張の `README.md` の「置き場は `env.CCNAVI_PROJECTS`、無ければ `projects`」を直す

## 8. やらないこと

- 診断のフラグ（`--log` `--state` `--approved` `--tickets` `--projects` `--project-home`）の廃止。別のチケット
- `CCNAVI_BIN_PATH`・`CCNAVI_WORKSPACE` の変更
- `.ccnavi/`・`wip/proposals/`・`logs/` の名前のぶつかりの検査
- 環境に値が残っていることの `--lint` での指摘（ADR-0084 の採らなかった案）
- 載せ忘れを ccnavi の側で直すこと（導入スクリプトや拡張が `git rm --cached` を打つ、`.gitignore` に
  `/projects/` を足す）。索引とワークスペースの `.gitignore` を変えるのは人（§4.4・§4.5）
