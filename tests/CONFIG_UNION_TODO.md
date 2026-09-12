# config-union で変える必要がある既存テストの一覧

設計 `wip/design/config-union.md`（§25 改版）を実装するときに、既存テストのうち
置き場の変更（`config/rules.yml` → `.ccnavi/config/rules.yml`）、`CCNAVI_PROJECT_RULES` の廃止、
selfguard の対象の拡張、`--explain` の書き直しで**変える必要があるもの**。ここでは変えない。
実装フェーズ（config-union-03 以降）が直す。

新しい振る舞いそのものは `tests/test_config_union.py` / `tests/test_config_union_phases_risk.py` /
`tests/test_config_union_guard.py` が固定している。ここに挙げるのは、今の置き場や今の出力の
綴りに依存していて、実装が入ると赤になる（か、意味がずれる）既存の 1 件ずつ。

## 1. 置き場が `config/rules.yml` から `.ccnavi/config/rules.yml` に変わる

| ファイル | テスト | 何が変わるか |
|---|---|---|
| `tests/test_projects.py` | モジュール docstring、`ProjectsTest.project()`（149 行） | プロジェクトのルールの置き場を `config/rules.yml` から `.ccnavi/config/rules.yml` に。docstring の「それぞれ `config/rules.yml` を持つ」も直す |
| `tests/test_projects.py` | `test_unreadable_project_rules_fall_back_for_writes_and_drop_out_of_the_union` | 壊すファイルの綴り（279 行）。振る舞いも変わる: 書き込みは「組み込みの既定」ではなく「層が空 + `fallback` に `app`」になり、`built-in defaults` の文面は出ない。Bash の和からは外れたまま（`detail` の文面 `unreadable project rules: app` は実装が決める）。共通層の deny が app のファイルにも効くようになる |
| `tests/test_projects.py` | `test_lint_names_project_wiring_problems` | 壊すファイルの綴り（365 行）。`--lint` の文面は「ルール `config/rules.yml` が無い」の warn が消え（`.ccnavi/config/` が無いことは言わない）、壊れた層は error になる。`--explain` の `■ プロジェクト（2 件` の見出しは層ごとの `■ rules lib` の形に変わる（§25.9） |
| `tests/test_projects.py` | `test_project_rules_file_swaps_one_projects_rules_for_diagnostics_only` | `--project-rules-file` は rules にだけ効かせたまま残す（設計「実装の入口ごとの変更点」settings.py）。差し替え先の綴りが `.ccnavi/config/rules.yml` になるだけで、テストの筋は残る。`--lint --json` の `where` が `(projects/lib)` で始まる約束は保つか、層の名前に変えるなら合わせる |
| `tests/test_projects.py` | `test_writes_are_judged_by_the_rules_of_the_project_they_land_in` | 判定が「プロジェクトの 1 本」から「共通層 + 行き先の層」の和になる。`record["rules"] == ["app:schema"]` は保てるが、ワークスペースの `guard-approved`（`*/.claude/ccnavi/*`）が app / lib にも効くようになる。lib の schema/ に app の deny が届かないことは変わらない |
| `tests/test_projects.py` | `test_writes_into_the_workspace_use_the_workspace_rules` | ワークスペースへの書き込みは「共通層 + 自身の層」になる。自身の層を置かない fixture なら結果は同じ。docstring の「ワークスペースのルール」の意味が「共通層」になる |
| `tests/test_projects.py` | `test_post_monitoring_reads_the_project_tree_the_call_touched` | 実行後の監視が「共通層 + そのツリーの層」の和になる（§25.7）。`app:schema` の名指しは残る。共通層の deny の場所も保護領域に数えられるようになる |
| `tests/test_selfguard.py` | `test_プロジェクトのルールファイルの写しも対象になる` | 内側の関数 `selfguard.targets(root, rules, bin, [("lib", path)])` を呼んでいる。引数が `layers: list[tuple[層, kind, パス]]` に変わり、写しの相対が「切り元の git プロジェクトルートから」になる（`projects/lib/config/rules.yml` の綴りの写しは無くなり、lib から切った作業ツリーの `.ccnavi/config/rules.yml` が対象になる）。黒箱の版は `test_config_union_guard.py` の `RestoreTest.test_copies_in_a_worktree_cut_from_a_project_are_restored` |
| `tests/test_selfguard.py` | `test_root_の外を指すルールファイルには写しが無い`、`settings_copies()` | `selfguard.targets` の引数が変わる。共通層の phases / risk（既定は root の下の `.claude/ccnavi/`）が対象に入るので、作業ツリーの写しの一覧が settings.json 2 つだけではなくなる（`_worktree_copies` は存在を見ずに並べる）。期待の一覧に phases / risk の写しを足すか、`--phases` / `--risk` を root の外に向ける |

## 2. `CCNAVI_PROJECT_RULES` が廃止される

| ファイル | テスト | 何が変わるか |
|---|---|---|
| `tests/test_setup.py` | `RETIRED_ENV` 定数、`test_does_not_write_retired_env` | 廃止した名前の一覧に `CCNAVI_PROJECT_RULES` を足す。導入スクリプトが書いてはいけない |
| `tests/test_setup.py` | `test_all_writes_the_settings_that_have_defaults` | `--all` の env に `CCNAVI_PROJECT_HOME: ".ccnavi"` が足される（§25.9）。確かめる行を足す。`CCNAVI_PHASES` は共通層の綴り `.claude/ccnavi/phases.yml` のままで変わらない |
| `tests/test_projects.py` | `ProjectsTest.ccnavi()` | `--project-rules` フラグは無くなる（今は渡していないので直す行は無い）。`cli.py` の `--project-rules` を消すなら、`--project-home` を足すかどうかを決めて、テストは env `CCNAVI_PROJECT_HOME` で渡す（`test_config_union.py` はそうしている） |

## 3. selfguard の対象が増える（各層の 3 本、共通層の phases / risk、`*/.ccnavi/*` の deny）

| ファイル | テスト | 何が変わるか |
|---|---|---|
| `tests/test_selfguard.py` | `SelfGuardTest.setUp()` と `run_hook()` | 共通層の phases / risk が中核に入る。fixture は `--phases` / `--risk` を渡していないので既定 `.claude/ccnavi/phases.yml` / `risk.yml`（無い）が対象になる。無いものは対象から外れる（REQ-SLF-03）ので今の形のまま通るはずだが、`test_置いていない設定ファイルについては何も言わない` の `stderr == ""` が守られることを確かめる |
| `tests/test_selfguard.py` | `test_記録に何をしたかが残る` | `guarded` の鍵が `rules:restored` のままか、層の名前付き（`rules:self:restored` のような形）になるか。共通層は裸のままなら変更なし |
| `tests/test_setup.py` | `DeploysWhatTheProjectNeeds.make_source()` | 配り元に `.claude/ccnavi/risk.yml` と `.ccnavi/config/phases.yml` を足す。無いと次の 2 件が「まだ無いもの」を出して落ちる |
| `tests/test_setup.py` | `test_says_nothing_is_missing_after_it_copied` | `note_missing` に 2 本が足されるので、配り元に 2 本が無いと「まだ無いもの」が出る |
| `tests/test_setup.py` | `test_check_is_settled_once_everything_is_there` | `--check` が 2 本の欠けを「揃っていない」に数えるなら、配り元に 2 本が要る |
| `tests/test_setup.py` | `test_names_what_the_source_does_not_have` | 配り元に無いものの一覧に `risk.yml` と `phases.yml` が並ぶ。`rules.yml` の名指しは残る |
| `tests/test_setup.py` | `test_leaves_no_temporary_file` | `--no-deploy` では `.claude/` に settings.json しか無いことを見る。導入スクリプトが `.ccnavi/config/` を配るのは `--deploy` のときだけなので変わらないはずだが、`.ccnavi/` を作る位置を `.claude/` の隣に置く実装なら `names` の期待が変わる |
| `tests/test_fallback.py` | `test_既定はシェルから設定を書き換えさせない`、`test_既定でもシェルからの書き込みは綴りを変えても止まる` | `_PLACES` / `_COPY_PLACES` に `\.ccnavi[\\/]` が足される。今の見本は変えなくて通るが、`.ccnavi/config/rules.yml` への書き込みを 1 件足す（`test_config_union_guard.py` の `DenyTest.test_shell_writes_into_project_home_are_denied` が黒箱で見ている） |
| `tests/test_acceptance.py`、`tests/fixtures/rules.yml` | — | 変更なしの見込み。見本の `guard-ccnavi-config`（`.claude/ccnavi/rules.yml`）に `.ccnavi/config/` を足すかどうかは実装が決める。fixtures の rules.yml には `guard-ccnavi-config` が無い |

## 4. `--explain` / `--explain --json` / `--lint` の出力が変わる

| ファイル | テスト | 何が変わるか |
|---|---|---|
| `tests/test_board.py` | `test_shape_matches_the_extension_fixture` | `--explain --json` に層ごとの `layers`（`test_config_union.py` の `ExplainTest.test_explain_json_carries_every_layer` が固定する形）を足すと、`vscode-extension/ccnavi-board/test/fixtures/board.json` の上位の鍵の集合と食い違う。`CCNAVI_BOARD_FIXTURE=1` でフィクスチャを書き直し、`BOARD_VERSION` を上げる |
| `tests/test_ticket_control.py` | 84〜88 行（`フェーズ（.claude/ccnavi/phases.yml）` / `リスクの配点（.claude/ccnavi/risk.yml）` の文面） | `--explain` を層ごとの書式に書き直す（§25.9）と、この文面が動く可能性がある。共通層の綴りは変わらないので、文面を残すなら変更なし |
| `tests/test_lint.py` | `test_json_は同じ苦情を機械可読な形で返し終了コードも同じ` | `severities == {"error", "warn"}` は空のワークスペースでは変わらない。`info` の深刻度が増えるので、`errors + warns == len(problems)` の数え方（`warns = len(problems) - errors`）を info を含めるか分けるか決める。`counts()` が読む最後の行 `error N 件、warn M 件` に info を足すなら `tests/test_lint.py` の `counts()` も直す |
| `tests/test_phases.py` | `test_lint_reports_a_broken_phases_file` | 共通層の `phases.yml` を壊す形。共通層自身が壊れた扱いは変わらないので変更なしの見込み。`title` の重なりの error は層をまたいでも同じ文面（`表示名`）にすると、`test_config_union_phases_risk.py` の `test_title_overlap_across_layers_is_an_error` と揃う |
| `tests/test_risk.py` | `DefinitionTest.test_definition_is_validated`（`script` の case） | `risk.parse(text)` を内側から呼んでいる。`parse` に層（共通 / 層）を渡す形にすると呼び方が変わる。共通層向けの既定を保てば変更なし |
| `tests/test_risk.py` | `test_lint_reports_a_broken_definition_and_builtin_takes_over` | 共通層の risk が壊れたときの組み込みへの退避は変わらない（§25.2）。変更なしの見込み |

## 5. 移行（§25.12）で動くもの

| ファイル | テスト | 何が変わるか |
|---|---|---|
| `.claude/ccnavi/phases.yml`（このリポジトリ自身の設定） | `tools/check_rules.py` などの回す皮 | 7 種を `.ccnavi/config/phases.yml` に移す。tests/ の中でこのファイルの綴りを固定しているものは無い（`test_phases.py` は自前の PHASES を書く） |
