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
- `_projects` に **追跡の検査** を足す（§4）
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

## 4. `projects/` のぶつかりを知らせる

### 4.1 条件

ワークスペースの git が `projects/` の下のファイルを 1 本でも追跡している。

```
git -C <root> ls-files -- projects/
```

の出力の 1 行目（無ければぶつかっていない）。`lint.py` の既存の `_tracked(root, rel)` をそのまま使える。
git が無い・失敗したときは「ぶつかっていない」として何も言わない（`_tracked` の既存の振る舞い）。

**プロジェクトが 1 つも無くても見る。** 今の `_projects` は `tree.projects(...)` が空なら何も
言わずに返すが、ぶつかりは「これからプロジェクトを置こうとした人」に要る知らせなので、
その早期リターンより前に見る。

### 4.2 `--lint` の出し方

| 追跡がある | プロジェクトがある | 無視されていない | 出すもの |
|---|---|---|---|
| はい | どちらでも | どちらでも | ぶつかりの warn だけ |
| いいえ | はい | はい | 既存の「無視されていない」warn |
| いいえ | はい | いいえ | 何も言わない |
| いいえ | いいえ | — | 何も言わない（今どおり） |

予約名と `.claude/` の検査（各プロジェクトごと）は、ぶつかりの有無に関わらず今どおり出す。

名札は `(projects)`。重さは warn。文面は ADR-0084 の案を使い、例のファイルには `_tracked` が返した
1 行目を入れる。

**VS Code 拡張が文面の先頭で見分けている**（`projects-render.ts` のコメントにある「`.claude/` を持つ」
と同じ作り）。拡張がこの warn を見分けるのに使う先頭の句を決めておく。

> 先頭: `` `projects/` はワークスペースの git が追跡している ``

`lint.py` 側にこの句が拡張から見られていることをコメントで残す。

### 4.3 VS Code 拡張

- プロジェクト画面は、`--lint --json` の `(projects)` の苦情のうち、上の先頭の句で始まるものを
  バナー（warn）で出す
- そのとき「`.gitignore` に `/projects/` を足す」ボタンと、「無視されていない」のバナー
  （`page.ignored` が偽のときのもの、`webview/projects/App.tsx`）を出さない
- 「置き場が無効（`CCNAVI_PROJECTS` が空）」の分岐・文面を消す（`App.tsx`・`projects-panel.ts`・
  `projects-view.ts` のコメント）。ただし `board.settings.projects` は実行ファイルの答えなので、
  診断のフラグで空が来うる — 拡張はフラグを渡さないので、ここでは空が来ないものとして扱ってよい
- `projectsRel === "" ? "projects" : projectsRel` の分岐（`watchProjects`・`tour-sample.ts`）は
  `projectsRel` が空にならないので単純にしてよい

### 4.4 導入スクリプト

入れ終わったところ（報告をまとめる箇所）で §4.1 と同じ条件を見て、同じ文面を出す。止めず、
終了コードも変えない。`--check` でも出す（「揃っていない」には数えない — 導入の不足ではないため）。

sh と `--lint` が同じ文面を持つので、`tests/sh/test_setup.py` の側で「`--lint` の文面の先頭の句が
導入スクリプトの出力にも含まれる」ことを確かめて、ずれを捕まえる。

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
| A6 | 追跡が無く、プロジェクトがあり、無視されていないときは、今どおり「無視されていない」が出る | 同上 |
| A7 | 導入スクリプトが、既存の `env` にある 6 つを外し、既定と違う値だけを名指しする。既定と同じ値は黙って外す | `tests/sh/test_setup.py` |
| A8 | 導入スクリプトが `projects/` の追跡を見つけたとき、`--lint` と同じ先頭の句を出し、終了コードを変えない | `tests/sh/test_setup.py` |
| A9 | sh（写す版）が env の値を読まない: `CCNAVI_PROJECTS=/x` を入れても `ccnavi_project` が `projects/<名前>` を返す。`CCNAVI_STATE=/x` を入れても `ccnavi-review.sh` が `logs/state` を見る | `tests/sh/` |
| A10 | 拡張: ぶつかりの苦情があるとき、プロジェクト画面がバナーを出し、`.gitignore` のボタンと「無視されていない」のバナーを出さない | `vscode-extension/ccnavi-board/test/projects/` |

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
