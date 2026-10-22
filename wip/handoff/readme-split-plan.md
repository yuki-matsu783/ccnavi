# README 分割の計画（段階 1）

README.md（3522 行、383KB）を、利用者向け・エージェント向け・開発者向けに分ける計画である。段階 1 のためファイルは変更していない。承認を受けるまで移動しない。
調べた結果の詳細は、末尾の付録 A〜C（担当範囲ごとの節の表、既存文書の分類、参照の影響）に置いた。本文はその要約と、決めてほしい点である。

## 1. README の節の一覧と読み手（1-1）

見出しは 103 個。節の粒度は付録 A の表（1 行 1 話題、行範囲・行数・読み手・既存文書との重複・移動先）にある。ここでは大きなまとまりだけ示す。

| まとまり | 行の範囲 | 読み手 | 既存文書との重複 | 主な移動先 |
|---|---|---|---|---|
| 冒頭・コア機能・設計原則・支える仕組み | 1-62 | 利用者 | docs/design.md 1.1.1、docs/requirements.md に言い換え | README に残す |
| 導入（5 段階） | 63-161 | 利用者（jq の例は混在） | なし | 要点は README、詳細は guide/install.md |
| 開発（テスト・build・hook 登録・配布する sh・配布物） | 162-265 | 開発者 | docs/claude/testing.md、ADR-0002 と一部重複 | docs/development.md |
| 実測で分かった落とし穴 | 266-304 | 開発者（一部は利用者もつまずく） | 付録 C に約 7 件。残りは付録 C に無い | docs/design/appendix.md（付録 C）。利用者がつまずく項目は guide/troubleshooting.md にも要点 |
| 設定（settings.json、hook の 8 イベント、置き場、環境変数） | 305-495 | 混在 | docs/design/startup-config.md 4.x と大半重複 | 利用者が設定する内容は guide/settings.md。内部処理は startup-config.md へ |
| 実行ファイルとルールを配る | 496-595 | 混在 | startup-config.md 4.6、multi-repo/ | 打ち方・挙動は guide/install.md。launcher と配布の内部は startup-config.md |
| ルール（id、レイヤー、additionalContext 系、強さ、言及の無い呼び出し、except・when、繰り返しの拒否） | 597-1010 | 混在 | design/rules.md、pre-check.md、multi-repo/rules.md | 書き方は guide/rules.md。内部仕様は設計へ（重複は消す） |
| ワークツリーを VS Code から見えるように | 1011-1055 | 混在 | extensions/vscode/ccnavi-board/README.md と重なる | 前半は guide/install.md または extensions.md、後半は拡張の README へリンク |
| Bash の読み方・パス・探すツール・.gitignore | 1056-1364 | 混在（止まる例と通る例が主） | design/pre-check.md、ADR-0050 | 振る舞いは guide/bash-reading.md。.gitignore の細部は付録 C |
| 動作モード・ツール実行後チェック・自動復元・コアファイルを守る・見えないもの | 1365-1519 | 混在 | design/post-check.md、self-guard.md | 利用者に要る部分は guide/hooks.md。内部仕様は重複なので消してリンク |
| チケットによる作業範囲（置き場、書式、承認、判定の鍵、フェーズ、複数 PC） | 1520-2026 | 混在 | design/tickets/*.md、requirements/tickets.md、.ccnavi/docs/ticket-*.md | 利用者の操作は guide/tickets.md と guide/multi-pc.md。重複は消す |
| レビューの依頼と確認・マージ後の片付け | 2027-2208 | 混在 | design/tickets/review.md、close.md、.ccnavi/docs/worktree-cleanup.md | 操作は guide/review.md。README にしか無い細部は設計へ移してから消す |
| リスク・issue/MR 指定・サブエージェント・プロジェクトのスキル・参考にした運用 | 2209-2281 | 混在（開発者向けが多い） | design/tickets/risk.md、subagents.md | 重複を消してリンク。guide には短い要点 |
| ルールファイルが読めないとき・記録・Stop の AI 判定・記録の後始末 | 2282-2372 | 混在 | design/appendix.md 付録 B、stop-review.md | guide/records.md。欄の表は付録 B |
| ルールが何に当たるかを確かめる・設定の検証 | 2374-2665 | 混在 | design/diagnostics.md、multi-repo/diagnostics.md | 操作は guide/check.md。指摘の一覧は決定事項 D3 |
| 候補・試験・lint・ボード・承認・残った指摘・版の JSON | 2474-2959 | 開発者 | requirements/extension-if.md が README を指している | requirements/extension-if.md またはその下に新設（決定事項 D1） |
| 作業・レビュー計画のテンプレート | 2960-3159 | 混在 | design/tickets/templates.md、.ccnavi/docs/templates.md | 操作は guide/tickets.md。下書き手順はエージェント向けに既にあるので消す |
| 学びの蓄積 | 3160-3184 | 混在 | design/tickets/learning-wiki.md、.ccnavi/docs/skill-wiki.md | 概要は guide/records.md |
| ドキュメントの索引 | 3185-3307 | 混在 | design/diagnostics.md L26、ADR-0112 | 使い方は guide/docs-search.md。実装は設計へ |
| 生の git は止めてラッパースクリプトへ寄せる | 3308-3356 | 混在 | self-guard.md の該当節 | 振る舞いは guide/git.md。通す・止める形の一覧は design/git-wrapper.md（新設） |
| 構成 | 3357-3514 | 開発者 | 現物とずれている | docs/development.md と docs/design/modules.md（新設） |
| 配布物の条件 | 3515-3522 | 開発者 | startup-config.md | 重複を消し、残りを startup-config.md へ |

読み手の混在が主なので、README の大半は「分ける」作業になる。利用者向けに残る分量は、体感で全体の 3 割前後と見込む。

## 2. 既存の文書の分類（1-2）

- `docs/design/**`、`docs/requirements/**`、`docs/adr/**`、`docs/claude/**`、`docs/CONTEXT.md` は、ディレクトリ単位で開発者向けである。例外は無い。現状 `docs/` に利用者向けの文書は無い（`docs/guide/` は新設）。
- `.ccnavi/docs/*.md` は 22 本。大半はエージェント向け。開発者向けが混ざる文書は 8 本ある。
  - 今回の範囲で直す案: `grow-{phases,risks,rules,templates}.md`（「次に読む」と ADR 番号が、配布先に無い docs/design・docs/adr へのリンクになっている）、`worktree-start.md` L34-36（branch_to_id の三実装と tests/fixtures）、`scratchpad.md` L43（テストのフィクスチャ）、`ticket-flow.md`「更新したときの変更点」（リリースノート）
  - 別作業に回す案: `ccnavi-md.md`「判定の仕組み」、`frontmatter.md`「プロジェクト」「索引のファイル」（内部形式）
- `.ccnavi/CCNAVI.md` は直す行が無い。`docs/guide/` は配布先に無いので、CCNAVI.md の表に足してはいけない。
- `extensions/chrome/ccnavi-approval/README.md` は全体が開発者向け。`extensions/vscode/ccnavi-board/README.md` は L9-33 が利用者向けで、L79-270（拡張開発ホスト・手動確認）が開発者向け。後者は拡張内の docs/ へ移す案。今回の範囲に入れるか別作業にするかは決定事項 D6。
- `CLAUDE.md` の「詳細」の表に 1 行足す（案: `docs/development.md` を開く行）。

### frontmatter の type
- 新設の `docs/guide/*.md` と `docs/development.md` は `type: guide`。新しい type の値は増やさない。`docs/claude/frontmatter.md` の `guide` の行の「対象」に `docs/guide/*.md`・`docs/development.md` を足す。
- 読み手の区別は、任意の tag `user-guide` で行う案（`tags` の使い分けの表に 1 行足す）。
- `docs/guide/README.md` を置くと frontmatter 免除扱いになるので、guide の入口は `docs/guide/index.md` にする。
- type の許可リストは無い。frontmatter を付け忘れると `NOTICE_DOC_FRONTMATTER` が 1 度出る。新設でテストが壊れることは無い（tests/core/test_docsearch.py L916-、`NO_FRONTMATTER_PLACES` と表の一致だけ）。

## 3. 移動先の案と新設ファイル（1-3）

節ごとの移動先は付録 A。違う判断をした節の主なものは次のとおり。

- 依頼の目安では launcher の仕組みは `multi-repo/scripts.md` だが、そこは 11.8「保護済みスクリプト」の文書で launcher の記述が無い。実際は `docs/design/startup-config.md` 4.6 にほぼ全文がある。そちらを移動先にした。
- 「探すツールが読むファイル」「.gitignore を当てにしてよい範囲」は目安に無い。利用者の判断材料としては guide に結論だけ残し、細部は付録 C と ADR-0050 へリンクする。
- 「ボードの JSON」ほか JSON の節は、extension-if.md が REQ-ID の表の体裁なので、欄の表を足すと性格が変わる（決定事項 D1）。

### 新設するファイル

| ファイル | 内容 | 目次案 |
|---|---|---|
| `docs/guide/index.md` | guide の入口 | 読み手別の案内表 |
| `docs/guide/install.md` | 導入と dry-run から enable まで、配布の打ち方 | dry-run で立てる／普段どおり開発する／記録を読む／ルールを直す／enable へ／ccnavi-setup.sh／VS Code からワークツリーを見る |
| `docs/guide/settings.md` | 環境変数と hook の登録 | settings.json の例／hook の 8 イベント／置き場／環境変数の表／動作モード |
| `docs/guide/rules.md` | ルールの書き方 | id／レイヤー／additionalContext・once・every・Stop・File／強さ／言及の無い呼び出し／match・glob・regex／except・when／繰り返しの拒否／失敗の連絡 |
| `docs/guide/bash-reading.md` | Bash の読み方とパスの見方（rules.md が 500 行近くになるため分ける） | 実行される部分だけを見る／文字として書く／読み切れないとき／書き直しを求める形／実行役のコマンド／ヒアドキュメント／パス／探すツール |
| `docs/guide/hooks.md` | hook の挙動のうち利用者に要る部分 | ツール実行後チェック／ターン終わりの報告／自動復元／コアファイルを守る／見えないもの |
| `docs/guide/tickets.md` | チケット・承認・フェーズ・テンプレート | 置き場と状態／書式の要点／承認／判定の鍵／フェーズ／テンプレート／リスク／issue・MR の依頼 |
| `docs/guide/review.md` | レビューの依頼と確認、マージ後 | request・confirm・decide／必要な道具／ready と MR／片付け／早めに閉じる |
| `docs/guide/multi-pc.md` | 複数人・別 PC・別セッション | 承認の push／受け取る側の fetch・sync |
| `docs/guide/records.md` | 記録と振り返り | 記録の欄の見方／Stop の AI 判定／記録の後始末／学びの蓄積 |
| `docs/guide/check.md` | 設定の確かめ方 | 何に当たるか／期待する判定／記録から候補／設定の検証（指摘の一覧は D3） |
| `docs/guide/docs-search.md` | ドキュメントの索引 | 使い方／frontmatter の付け方への案内 |
| `docs/guide/git.md` | 生の git が止まる理由と打ち方 | 振る舞い／ラッパーの使い方 |
| `docs/guide/extensions.md` | VS Code と Chrome の拡張の使い方 | 画面ごとの説明（拡張の README の利用者向け部分へリンク） |
| `docs/guide/troubleshooting.md` | つまずきやすい点 | Windows でワークツリーを消せない／`env` が再読み込みされない／`CCNAVI_BIN_PATH` が消えると hook が起動しない など（落とし穴 14 件から選ぶ） |
| `docs/development.md` | 開発者向けの入口 | 開発環境／テスト／整形・lint／build／lint-py・test-py などの hook 登録／配布する sh の確かめ方／配布物／ディレクトリ構成／案内表（docs/claude、design、requirements、adr、CONTEXT） |
| `docs/design/modules.md` | モジュールごとの構成表 | 「構成」の表（現物に合わせて直してから移す） |
| `docs/design/git-wrapper.md` | 通す・止める git の形 | 一覧 |

利用者の指定した目安（install・settings・rules・tickets・records・extensions・troubleshooting）に、分量の都合で bash-reading・hooks・review・multi-pc・check・docs-search・git を足した案である。多すぎるなら統合できる（D2）。

## 4. 影響を受ける参照（1-4）

詳細は付録 C。要点だけ書く。

- **テスト:** ルート README.md の中身を読むテストは無い。分割でテストは壊れない。`tests/core/test_adr_numbers.py:103` が読むのは `docs/adr/README.md`。
- **docsearch:** ルート README.md は `src/ccnavi/hook/docsearch.py:419` の `NO_FRONTMATTER_NAMES` で名前除外されている。
- **壊れるリンク:** `docs/requirements/extension-if.md` の `README.md#…` 6 つ、`docs/design.md:16-17`。ほかに `docs/design/diagnostics.md` など約 10 文書が JSON 節のアンカーを指す。README 自身から出る相対リンク 33 本は `../` の調整が要る。
- **README 内の相互参照:** 別ファイルに分かれるものが約 16 件。README 唯一のアンカーリンク `](#実測で分かった落とし穴)`（2106 行）は、元からリンク切れ。
- **利用者に見える出力:** `src/ccnavi/cli_usage.py` の 7 箇所（20, 29, 68, 88, 116, 138, 219）と `.ccnavi/scripts/ccnavi-clean.js:102`・`.sh:270` が README の節名を案内している。rules.yml の message、CCNAVI.md、`.ccnavi/docs/`（frontmatter.md 以外）には README の節名は無い。
- **コメント・docstring:** src/・scripts/・tests/・extensions/ に「README「○○」」が約 45 箇所。見出し名を変えなければ、直すのは文面だけ。
- **ADR 内の参照（ADR は変更しないため直せない）:** `0104:194,196`（README.md の行番号 2224・2152）、`0037:53`、`0054`、`0098`、`0080` など。行番号が外れるのは 0104 の 2 件だけで、残りは節名の記述。
- **CLAUDE.md の表:** `docs/development.md` の行を足す。
- **別途確認:** `.claude/skills/commit/SKILL.md:82`（commit の `docs` 対象）と `phases.yml:38`（scope `docs/*` が `docs/guide/` に当たるか）。

## 5. 分割後の目次案（見出しだけ）

### README.md（目標: 現在の 1/10 以下。380 行以内。実際は 250 行前後を見込む）
- ccnavi（冒頭の説明）
- コア機能（4 行の表）
- 1 つ目と 3 つ目の違い
- 設計原則
- コア機能を支える仕組み（表）
- 導入の流れ（5 段階の要点。詳細は guide/install.md）
- 最小限の設定とルールの例
- 詳しい手引き（docs/guide/ への案内表）
- 開発に参加する人は [docs/development.md](docs/development.md) へ（1 行）

### docs/development.md
- 概要と読む順
- 開発環境
- テスト（要点。詳細は docs/claude/testing.md）
- 整形・lint・型
- build と配布物（onedir。理由は ADR-0002）
- 配布する sh の確かめ方
- 開発用の hook（lint-py / test-py / mark-ext / test-ext）
- ディレクトリ構成（モジュールごとの表は design/modules.md）
- 文書の案内表（docs/claude、design、requirements、adr、CONTEXT、guide）

## 6. 判断に迷った点と、決めてほしいこと

### 決めてほしいこと
- **D1: JSON の形の置き場。** 候補・試験・lint・ボード・承認・残った指摘・版の JSON（約 300 行）。(a) `docs/requirements/extension-if.md` に足す（目安どおり。ただし REQ-ID の表の性格が変わる）、(b) `docs/requirements/` の下か `docs/design/` に専用ファイル（例 `json-shapes.md`）を新設し、extension-if.md から指す。**推奨は (b)。** 7 本のアンカーは新ファイルへ張り替える。
- **D2: guide のファイル数。** 上の 15 本のまま進めるか、統合するか（例: hooks を settings に、review と multi-pc を tickets に）。
- **D3: 「設定の検証」の指摘一覧（約 90 行）の置き場。** (a) guide/check.md に一覧ごと置く、(b) 使い方と見方だけ guide に置き、一覧は `docs/requirements/diagnostics.md` に置く、(c) 領域ごとの設計に分ける。**推奨は (b)。**
- **D4: 「README にしか無い」情報を先に設計へ移すこと。** `DENY_SUBAGENT_PARENT_TREE`、`CCNAVI_WRITE_AND_PUSH_TIMEOUT`、`CCNAVI_FETCH_TIMEOUT` の既定 15 秒、導入の `--ticket-control`、`NUDGE_TICKET_FINISH`、`ready` の打ち直し・退避の上書き禁止・`ccnavi_git_auth` など。依頼文の「仕様は原文のまま移す」に沿って、消す前に設計文書へ移す。この前提でよいか。
- **D5: 食い違いの扱い。** 下の一覧のうち、README が古いと確かめられたもの（env の数、配布する sh、`.gitignore` の行、`/scratchpad`、`.gitignore` を書き換えない記述、構成表、compat の数、skip の理由数、欄数）は、勝手に決めず一覧にして報告する決まりなので、正しい側を指示してほしい。
- **D6: 今回の範囲に入れるか。** (1) vscode 拡張の README の開発者向け部分（L79-270）の移動、(2) `.ccnavi/docs/` の混在 8 本のうち「今回直す案」の 4 組、(3) 別作業に回す案の 2 本。
- **D7: 変更要求の表の正本。** GitLab/GitHub の変更要求表が README 2082-2086、`docs/design/tickets/review.md` 227-230、`.ccnavi/docs/decisions.md` 69-70 の 3 か所にある。**推奨は design を正本にして、guide は利用者に表示される文面だけ残す。**
- **D8: 実測の落とし穴 14 件の分け方。** Claude Code の hook・settings の挙動は付録 C、Windows・uv・jq・Docker などの環境固有は `docs/claude/environment.md`、GitLab 実物の挙動は付録 C の新節、とする案。
- **D9: lint-py / test-py / mark-ext / test-ext の登録手順（README 189-224 行）の置き場。** `docs/development.md` に置くか、`docs/claude/testing.md` に節を足すか。**推奨は development.md。**
- **D10: 作業の形。** 段階 2 の編集は、ccnavi の実行ファイルが未ビルドでワークツリーを切れないため、作業ブランチ `claude/readme-split-plan-ls0dnt` で直接行う案。ビルドしてワークツリーを使うほうがよいか。保護対象の編集は、止められたら `ccnavi-stage.sh` で `wip/handoff/` に写す。
- **D11: 段階 1 の計画書自体にも、依頼の「リモート取り込み → haiku 調査 → 日本語修正 → 敵対的レビュー → MR」を通すか。** 段階 2 の後に通す想定で、計画書には通していない。

### 食い違い（README と既存文書で合わないもの）
1. settings.json の例の env が README は 5 つ。導入スクリプト（`scripts/ccnavi-setup.sh` 848-849）と startup-config.md 4.6 は 7 つ（`CCNAVI_GUARD_UNWATCHED`、`CCNAVI_TICKET_CONTROL` が README に無い）。
2. 配布する sh の列挙（README 549）に `ccnavi-dirs.sh` と `ccnavi-session-start.sh` が無い。
3. `.gitignore` に足す行に、`/scratchpad` が README に無い。
4. README 168 行の先頭の例が全件 1 本の `discover` で、`docs/claude/testing.md` の「全件を 1 本で回さない」と矛盾する。
5. hook 表（README 404-414）と startup-config.md 4.2 の差。`PostToolUse` の `NOTICE_DOC_FRONTMATTER`、`SessionStart` の索引の差分更新・却下件数・`additionalContextOnce` の記憶の捨て方が README に無い。
6. `## 記録` の「全 23 欄」は、付録 B では 24 欄（`hinted`、`guarded`）。`skip` の理由は README が 9、付録 B が 10（`internal-error`）。
7. README 3235-3239 の「実行ファイルは `.gitignore` を書き換えない」は古い。実際は `**/ccnavi-index.jsonl` を足す（`docs/design/diagnostics.md` L26、ADR-0112、`docsearch_index.py`）。
8. `docs/design/tickets/user-entry.md` L86 の compat 15 は古く、実際は 18。
9. 構成表（README 3357-3514）は、存在しない `lint_rules.py` を載せ、45 本の `.py` と数本の sh が載っていない。
10. 承認 JSON の `--yes` の答えに `written` が README に無い（REQ-EXT-14 と `core_approve.py` にはある）。`--version` の鍵に `features` が REQ-EXT-04 に無い。
11. README 1543 行「最後の行（`dry_run` の条件）は…」は、参照先の表が欠けている。
12. Stop の AI 判定の「約 10 秒、約 0.0025 ドル」は、`stop-review.md` の測定（+8.9 秒、0.0025〜0.0046 ドル）の下限だけを書いている。
13. README 内のリンク切れ（2106 行）。
14. 文体: README 1104-1105、1782、1786 は「です・ます」調が混ざる。移すときに常体へ揃える。
15. `.ccnavi/docs/ccnavi-md.md` と README で条件の足し方の経路が 2 つ（配布先と ccnavi の開発）。矛盾ではないが、移動先で区別して書く。

数値は、4000 字、`every` の下限 2・既定 3、入れ子 8 段と 64 ノード、`$( )` の入れ子 16 段、実行役の深さ 4・語数 2000、onedir 約 220ms など、設計書と一致した。

### 未確認
- 「参考にした運用」の `参考/issue-mr-ticket-workflow` が現存するか。
- ヒアドキュメントの代わりに Write を使う、ルートからの Grep が `projects/` へ降りない、の手順が `.ccnavi/docs/` に既にあるか。「生の git」と「ドキュメントの索引」のエージェント向けの説明が足りるか。
- 「フェーズの局面表示」（README 1972-1975）の対応先（`phase-management.md` を読み切れていない）。
- 「確認を取り直さない」の記述が `.ccnavi/docs/ticket-flow.md` にあるか。
- `scripts/check_rules.py`（README 141 行）は配布先の利用者が持たない可能性が高い。`ccnavi --test-expectations` との関係を確かめてから guide に書く。

## 7. 段階 2 のコミット分割案
1. 設計文書への合流（README にしか無い情報、食い違いの修正）
2. `docs/development.md` と関連（CLAUDE.md の表、frontmatter.md の追記）
3. `docs/guide/` の各ファイル（まとまりごとに 3〜5 コミット）
4. JSON の形の移動（D1 の決定に従う）
5. `.ccnavi/docs/` の外出し（D6 の決定に従う）
6. README の縮小
7. 参照の修正（docs、src の文面、cli_usage.py、clean の案内、テスト）

---

# 付録 A. README の節ごとの表（担当範囲ごと）

付録の表は、各担当が書いたままの形で載せている。範囲の端で重なる行がある（part3 と part4 の 2027〜2100 行、part4 と part5 の 2801〜2812 行）。重なりは移動先が同じである。

## 付録 A-1（part1）


調べた範囲: README.md 1〜700 行（`##` `###` の見出しごと。見出しの下が長い `## 設定` `## 開発` `## 実測で分かった落とし穴` は、内容の塊で区切って行を分けた）。
既存文書の確認は docs/design, docs/requirements, docs/claude, docs/adr, .ccnavi/docs, scripts/, src/ を grep して行った。
前提: `docs/guide/` と `docs/development.md` は現時点で存在しない（`docs/design.md` の冒頭表は「開発の手順、実測で分かった落とし穴」を README#開発 に向けているので、移したらここも直す）。

注意: 次の見出し `### 利用場面でガイドを届ける` は 701 行なので、`### ルールは 3 つのレイヤーの和で当たる` は 648〜700 行できれいに閉じている。

### 表

| 見出し | 行範囲 | 行数 | 読み手 | 既存文書に同じ内容があるか | 移動先の案 | 理由 |
|---|---|---|---|---|---|---|
| (冒頭) `# ccnavi` | 1-6 | 6 | 利用者 | なし（`docs/CONTEXT.md` 12 行に 4 コア機能の 1 文はあるが別物） | README に残す | 冒頭説明。目安どおり |
| `## コア機能`（4 行の表） | 7-15 | 9 | 利用者 | あり。`docs/requirements.md` 14-28 行（同じ 4 機能の表）、`docs/design.md` 1.1.1（37-54 行） | README に残す | 目安どおり。要求・設計に言い換えた表があるが、README が利用者向けの出どころ。重複は許容し、要求・設計側から README へリンクするほうが保守が楽 |
| `### 1 つ目と 3 つ目の違い` | 16-28 | 13 | 利用者 | あり。`docs/design.md` 1.1.1 の 48 行「ガイドと検索の違いは、届くきっかけにある」が短く言い直している | README に残す | 設計原則に近い考え方の説明。hook の処理が「モデルのコンテキストの外で判定する」という記述はあるが、内部構造ではなく性質の説明なので利用者向けでよい |
| `### 設計原則` | 29-41 | 13 | 利用者 | あり。`docs/design.md` 1.1.1（52-54 行）、`docs/requirements.md` 23 行 | README に残す | 目安どおり。40 行の「前提と制約は『複数人・別 PC・別セッションで進める』（チケットの節）に書く」は、チケットの節の移動先（guide）が決まったらリンクを直す |
| `### コア機能を支える仕組み` | 42-62 | 21 | 混在。表（46-57 行）は利用者向けの概要。ただし 48-51 行・53 行にコマンド名（`ccnavi-fetch.sh` `CCNAVI_RESTORE_IF_DENY` `--lint` など）が入る。59-61 行の文書リンクは入口 | 部分的にあり。`docs/design.md` 1.1.1 の「支える仕組み」列（43-46 行）、`docs/requirements.md` 28 行に同種の表 | README に残す（表の語は利用者が見る名前だけにする） | 目安は「コア機能を README に残す」。表は 1〜4 のどれを支えるかの地図なので残す。内部名はモジュール名・関数名ではなく利用者が打つ名前なので guide に出してよい。59-61 行は新しい文書構成に合わせてリンクを書き換える |
| `## 導入`（前文） | 63-67 | 5 | 利用者 | なし | README に残す（要点）。詳細は docs/guide/install.md | 目安どおり。導入の全体像の 1 段落として README に置く |
| `### 1. dry-run で立てる` | 68-95 | 28 | 混在。利用者の手順が主。70 行の「8 つのイベントに登録」と 84 行の括弧書き（チケットの関門は dry-run でも止める）は仕様の細部 | 一部あり。dry-run の挙動は `docs/design/startup-config.md` 4.4（65-82 行）、`docs/requirements/common.md` 2.2。「手順」としての記述はどこにもない | docs/guide/install.md | 導入の詳細手順。README には「dry-run で始め、記録を読み、直し、enable へ」の流れを 3〜5 行で書く。84 行の括弧書きは guide では短くして設計書へリンク |
| `### 2. 普段どおり開発する` | 96-100 | 5 | 利用者 | なし | docs/guide/install.md | 5 行しかないので、3 と一緒に 1 本の手順として移す |
| `### 3. 記録を読む` | 101-127 | 27 | 混在。jq の例（105-120 行）は利用者向け。`decision` `code` `paths` `subject` など記録の欄名（103-126 行）は記録の形式そのもの | 部分的にあり。欄の定義は `docs/design/startup-config.md` 4.7（204-212 行）と `docs/design/appendix.md` 付録 B。jq の例そのものは docs 全体になし（`uniq -c` の grep で 0 件） | docs/guide/install.md（手順としての記録の読み方）。欄の意味は設計書の付録 B へリンク | 欄名は利用者が jq で書くので guide に出してよい（内部ファイル形式ではなく利用者向けの出力）。ただし「ローテートした分は `decisions*.jsonl`」（123 行）は guide/troubleshooting にも書いてよい。`.ccnavi/docs/grow-rules.md` 20 行はこの流れの要約を持つので、エージェント向けにはそちらを正とする |
| `### 4. ルールを直す` | 128-147 | 20 | 利用者 | 部分的にあり。`.ccnavi/docs/grow-rules.md`（エージェントがルールを育てる流れ。`--suggest` `check_rules.py` `--explain` に言及）。`docs/design/diagnostics.md` に `check_rules.py` | docs/guide/install.md（短く）＋ docs/guide/rules.md へリンク | 「記録に出るもの → 直す先」の表（130-134 行）は利用者向け。`/ccnavi-config` と `--suggest` への言及（145-146 行）は guide/rules.md の「ルールを育てる」とつなぐ。`uv run python scripts/check_rules.py`（141 行）は ccnavi のリポジトリを開発する人向けの打ち方に見えるので、配布先の利用者向けでは `ccnavi --test-expectations` に置き換えるかを要確認 |
| `### 5. enable へ切り替える` | 148-161 | 14 | 利用者 | 部分的にあり。`disable` の扱いは `docs/design/startup-config.md` 4.3（57-61 行）、`docs/requirements/common.md` REQ-CMN-04 | docs/guide/install.md | 導入手順の最後。158-160 行の `disable` は env に書いても効かない、は guide/settings.md の動作モードにも 1 行置く |
| `## 開発`（前文〜テスト・整形・組み立てのコマンド） | 162-181 | 20 | 開発者 | 部分的にあり。`docs/claude/testing.md` が `python -m tests`、グループ分け、`.claude/skills/commit/references/test-groups.md` を持つ。`uv run --with ruff ...` の一覧と `build.py` のコマンドはなし | docs/development.md（コマンド一覧）。テストの回し方は docs/claude/testing.md へリンク | 目安どおり。168-170 行は testing.md と重なる（全件を `discover` の 1 本で回さない決まりが testing.md にあり、README のコマンド先頭行 `unittest discover -s tests` は「全件 1 本」を勧める形で食い違う。後述の「食い違い」参照） |
| (同) 手で 1 回動かす・`knowledge/`・手で hook を叩く例 | 180-187 | 8 | 開発者 | なし（`knowledge/` は `.claude/skills/commit/SKILL.md` に言及のみ） | docs/development.md | 開発者向けの小ネタ |
| (同) lint-py / test-py / mark-ext / test-ext の 4 本の説明 | 189-224 | 36 | 開発者（ccnavi 自身のリポジトリの hook の話） | 部分的にあり。`docs/adr/0036-tests-at-stop.md`、`docs/adr/0061-ext-tests-by-group-at-stop.md`（理由と経緯）。使い方の文書は `docs/claude/` に無い | docs/development.md（使い方）。経緯は ADR へリンク | ADR にあるのは判断の経緯で、「settings.local.json に足す」手順（214-223 行の JSON）は ADR 側に無い。docs/claude/testing.md には入れない（テストを回す決まりではなく、hook の登録手順なので）。判断に迷う点を参照 |
| (同) build.py のコピー先・ワークツリーでの組み立て・`CCNAVI_BIN_PATH` で dist を直に起動 | 225-244 | 20 | 開発者 | あり（大部分）。`docs/design/startup-config.md` 4.6（164-175 行）に `build.py` の `install()` `.new` `_swap` `.old`・ワークツリーで組み立てた場合・バックアップとの食い違いが同じ内容で載っている | 重複なので消してリンク。ただし 241-244 行（`dist/` を直に起動する `settings.local.json` の `env`、`ccnavi.settings.local.json` の `bin` キー）は設計書に無いので docs/development.md に残す | 233 行の出力例（`dist/ は新しい。…`）は startup-config 170 行が言い換えて持つ。239 行の「ワークスペースルートで組み立てたらセッションを開き直す」は development.md に残す価値がある |
| `### 配布する sh を確かめる` | 246-255 | 10 | 開発者 | なし（`test_e2e_sh`、`CCNAVI_SH_DIR` は docs, .ccnavi/docs, .claude/skills に 0 件） | docs/development.md。テストを回す決まりとして docs/claude/testing.md に 1 行足してもよい | 目安は「開発」節 = development.md と testing.md。`CCNAVI_SH_DIR` は ccnavi の開発専用の変数で、250 行の `sh =` を確かめる手順もテストの回し方 |
| `### 配布物` | 256-265 | 10 | 混在。onedir を使う理由・数値は設計判断。利用者には「実行ファイルは約 17 MB のフォルダ」程度で足りる | あり。`docs/adr/0002-onedir.md`（220 ms / 1000〜1500 ms / 17 MB / 7 MB の表が同一）、`docs/design/startup-config.md` 4.1（31 行「配布物は PyInstaller の onedir」） | 重複なので消してリンク（ADR-0002 へ）。docs/development.md に 1 行だけ残す | 数値表まで ADR と同じ。CLAUDE.md の決まりで、文書・コメントで ADR に触れたくなったら `docs/claude/environment.md` を読むこと。リンクの書き方はそこに従う |
| `## 実測で分かった落とし穴`（前文＋箇条 14 件） | 266-292 | 27 | 開発者が主。利用者がつまずくのは「`env` は再読み込みされない」（276 行）・「`${CLAUDE_PROJECT_DIR}` は `env` で展開されない」（275 行）・「`.claude/settings.json` は未知のトップレベルキーを拒否する」（278 行）の 3 件 | 部分的にあり。`docs/design/appendix.md` 付録 C（94-121 行）に、`${CLAUDE_PROJECT_DIR}`・`env` 再読み込み・Bash に `CLAUDE_PROJECT_DIR` が来ない・未知キー拒否・Windows の実行中ファイル・`shlex` の挙動（3 件）の行が既にある。`docs/claude/environment.md` に bash 3.2 と `$( )` 内の `case` の決まりがある（14-15 行） | 付録 C にある 7 件程度は重複として消し、リンクにする。付録 C に無いもの（下に列挙）は docs/design/appendix.md の付録 C か docs/claude/environment.md へ移す。利用者向けの 3 件は docs/guide/troubleshooting.md にも 1 行ずつ | 付録 C に無い項目: uv のハードリンクでワークツリーが消せない（270-274 行）、PyInstaller がスクリプトをパッケージの外で走らせる（279 行）、Python の識別子に空白は入らない（285 行）、Windows のコンソールの CP932 と `jq`（286-287 行）、Windows の `gitdir:` の表記（288-289 行）、Docker Desktop と GitLab のメモリ（290-291 行）。このうち `jq` の実体の場所（287 行）と CP932 は環境の決まりなので docs/claude/environment.md、`gitdir:` は docs/design/multi-repo/trees.md に既に記述があるのでリンク |
| `## 実測で分かった落とし穴` の続き（GitLab 実物 CE 18.5.4 の表） | 293-302 | 10 | 開発者 | なし。`request_changes` `GIT_CONFIG_COUNT` `make_gitlab_tokens` は docs 全体に 0 件。`docs/design/tickets/review.md` と `docs/design/multi-repo/to-measure.md` が近い話題 | docs/design/appendix.md（付録 C に GitLab の節を足す）。または docs/design/tickets/review.md | 実測の前提なので設計書の付録 C が自然。CE 限定機能・credential helper・トークン作りは review の設計に効く |
| (同) `dist/` `build/` `logs/` は git 管理外 | 303-304 | 2 | 混在 | なし | docs/development.md に 1 行。「記録に絶対パスとコマンド全文が入るのでコミットしない」は利用者にも要る（guide/troubleshooting.md か guide/install.md に 1 行） | 2 行だけなので分けず、記録の注意だけ利用者向けにも残す |
| `## 設定`（前文：env で設定する理由・ccnavi-setup.sh の挙動） | 305-340 | 36 | 混在。305-333 行は利用者向け（`ccnavi-setup.sh` の打ち方、`--check` `--force` `--no-vscode` `--no-fetch`）。334-339 行は `projects/` の索引の案内（`--lint` の `(projects)` との対応）で細かい | あり。`docs/design/startup-config.md` 4.6（127-136 行、185-188 行）に導入スクリプトの動作が載っている。`projects/` の案内は `docs/design/multi-repo/diagnostics.md` と `startup-config.md` 4.6 | 利用者向けは docs/guide/install.md と docs/guide/settings.md。細部（334-339 行）は docs/design/startup-config.md 4.6 へ | 「厳密には導入スクリプトの動作の仕様」なので、guide には打ち方と結果だけを書き、`--lint` の `(projects)` 条件は設計書に任せる |
| (同) settings.json の完全な例（hooks と env） | 341-397 | 57 | 混在。env 部分（388-394 行）は利用者向け。hooks 部分（343-387 行）は登録の形で、導入スクリプトが書くので利用者が手で書く必要は通常ない | 一部あり。`docs/design/startup-config.md` 4.1・4.6（hook を 8 イベントに登録）。JSON の全文はなし | docs/guide/install.md に env の例と「8 つのイベントに登録される」程度。hooks 全文は docs/guide/settings.md の折りたたみ的な付録、または docs/design/startup-config.md 4.6 | 手で書く人向けの参考として hooks 全文は guide/settings.md に 1 か所残す。env の例は食い違いあり（後述） |
| (同) 8 イベントの説明文 | 398-403 | 6 | 混在。`matcher` を絞ってはいけない理由は利用者向け。`ccnavi-fetch.sh` が fast-forward で取ってくる説明（400-402 行）は利用者向けだが細部 | あり。`docs/design/startup-config.md` 4.2 の 48-49 行（`matcher` は全ツール） | docs/guide/settings.md | `matcher` を絞ると書かなかったツールで hook が起動しない、は利用者のつまずきなので guide/troubleshooting.md にも要点を足す |
| (同) 8 イベントの表（SessionStart〜SubagentStop と「登録しないと」） | 404-414 | 11 | 混在。1 列目と 4 列目（登録しないと）は利用者向け。2 列目「ここで何をするか」は hook の内部処理（`state/…outcome`、`notified` の印、`.unchecked`、区分 A〜D など） | あり。`docs/design/startup-config.md` 4.2（36-46 行）が同じ表を、より詳しく持つ。README の表は 4.2 の要約 | 内部処理の列は重複なので消してリンク（docs/design/startup-config.md 4.2）。guide/settings.md には「登録しないと何が欠けるか」の列だけ（2 列表）を置く | 目安どおり（hook の内部処理は startup-config）。README の列は実際には 4.2 とほぼ同じ内容だが、書き方に差が出ている（後述）。1 行が非常に長く（SessionStart 406 行は 1 行で約 700 字）、README の表として読めない |
| (同) `SessionStart` で渡す作業の決まり（`.ccnavi/CCNAVI.md`） | 415-431 | 17 | 混在。415-423 行は説明（利用者とエージェントの両方に関わる）。425-430 行「条件を足す手順（ccnavi の開発）」は開発者向け | あり。`.ccnavi/docs/ccnavi-md.md`（書き方・条件の表・判定の仕組み）。「実行ファイルを触らず `ccnavi_cond_<名前>` で足す」は配布先向けで README の 427 行（`src/ccnavi/hook/ccnavimd.py` に関数を書く）とは別の経路 | 415-423 行は重複なので消してリンク（.ccnavi/docs/ccnavi-md.md、docs/guide/settings.md から）。425-430 行は docs/development.md | 実行ファイル側の条件の足し方は ccnavi-md.md に無い（grep: `CONDITIONS` `flags_for` `test_ccnavimd` は .ccnavi/docs に 0 件）。`docs/design/startup-config.md` 38 行に `hook/ccnavimd.py` の説明はあるが手順ではない。開発者向けの手順として development.md が受ける |
| (同) `command` を `${CCNAVI_BIN_PATH}` で書く理由 | 432-434 | 3 | 混在 | あり。`docs/design/startup-config.md` 4.6（148 行）、`docs/design/self-guard.md` | docs/guide/settings.md（`CCNAVI_BIN_PATH` が消えると hook が起動しない、の 1 文）。理由は設計書へ | 利用者のつまずき（env から 1 行消えると hook が起動しない）は guide/troubleshooting.md にも要点 |
| (同) 置き場は固定（表と廃止された 6 つの環境変数・フラグ） | 435-464 | 30 | 混在。表（437-445 行）は利用者がファイルを探すので利用者向け。452-457 行（診断に限るフラグ、sh が引数をそのまま渡すので…）は内部の理由 | あり。`docs/design/multi-repo/places.md` 11.2（11-73 行）が置き場の表を持つ。`docs/adr/0098-places-are-fixed-to-defaults.md` | 表は docs/guide/settings.md（置き場の一覧）。452-457 行と 459-461 行（廃止した 6 変数）は重複または設計寄りなので places.md と ADR-0098 へリンク。447-450 行（`projects/` の作業はワークスペースルートから起動）は guide/troubleshooting.md にも | places.md は表の項目が README より多い（`logs/session/`、`rule-expectations.yml` など）。README の表は利用者向けの抜粋になっている |
| (同) 環境変数の表（`CCNAVI_MODE` ほか 20 行） | 465-486 | 22 | 混在。変数名・意味・既定値は利用者向け。`CCNAVI_GUARD_TICKET_APPROVAL`（481 行）の説明は、どの入口をどの理由コードで止めるかまで書いており、内部仕様（`DENY_TICKET_APPROVAL_CLI`、`xargs` など） | あり。`docs/design/startup-config.md` 4.3・4.4、`docs/design/pre-check.md`（`CCNAVI_DENY_REPEAT` 258 行、`CCNAVI_FAIL_STREAK_COUNT` 267 行、`CCNAVI_GUARD_UNWATCHED`）、`docs/design/stop-review.md`、`docs/design/tickets/learning-wiki.md` 256-257 行（`CCNAVI_LEARNING_*`）。要件の既定値は `docs/requirements/common.md` | docs/guide/settings.md（変数名・意味・既定値・書ける値だけ）。内部の止め方（`DENY_TICKET_APPROVAL_CLI` の列挙、`xargs`、`ccnavi-user-decide.sh` など 481 行の後半、472 行の「承認済みの置き場…は、この値では切れない」）は設計書（docs/design/tickets/approval.md か pre-check.md）へ | 目安どおり「設定の利用者向け内容は settings.md」。表は 22 行だが 1 セルが長い行が多く、利用者向けは 1 セル 1〜2 文に縮める。`CCNAVI_STATE_KEEP_DAYS` `CCNAVI_LOG_*` の既定値 14 は src/ccnavi/records/prune.py と一致した |
| (同) `${CLAUDE_PROJECT_DIR}` と env、ワークスペースルートの決まり方 | 487-495 | 9 | 混在。487-488 行は利用者のつまずき。490-492 行（ワークスペースルートの決め方）は仕様。494 行の `standalone-clone-gaps.md` A2 は設計の宿題 | あり。487-488 行は `docs/design/appendix.md` 付録 C（`${CLAUDE_PROJECT_DIR}` と `env` の行）。490-492 行は `docs/design/startup-config.md` 4.1（25-27 行）。494 行は同 4.1 の 29 行に同文 | 487-488 行は重複なので消し、guide/troubleshooting.md に要点。490-495 行も 4.1 と同文なので重複として消してリンク | 494 行は startup-config.md 29 行と一字一句同じ文。片方を消す |
| `### 実行ファイルとルールを配る`（前文・コマンド） | 496-512 | 17 | 利用者 | 一部あり。`docs/design/startup-config.md` 4.6（127-188 行）に `ccnavi-setup.sh` の配布動作 | docs/guide/install.md | 目安どおり（配布の打ち方）。`build.py` で先に組み立てる点は ccnavi のリポジトリから配る利用者に必要 |
| (同) 振り分けの sh と置き場の木・名前の決まり・終了コード 126/127 | 513-541 | 29 | 混在。513-516 行と 518-524 行の木は利用者が読む。526-527 行（隣の `scripts/<os>-<arch>/` は探さない理由）・538-541 行（`uname` の実測 9 ms、VS Code 拡張が sh を通さず自分で選ぶ）は launcher の内部 | あり。`docs/design/startup-config.md` 4.6（138-175 行）にほぼ同じ木・探し方・127/126。launcher の仕組みは `docs/design/multi-repo/scripts.md` 11.8 には無い（11.8 は保護済みスクリプトと `{root}`）。`docs/adr/0041-launcher-per-machine.md`、`0044-launcher-in-scripts.md` | 利用者向け（置き場の木、128 のような終了コードの意味、別の機械の組み立てを配る）は docs/guide/install.md。launcher の仕組み（526-527 行、538-541 行）は重複なので startup-config 4.6 へリンク | 依頼文の「launcher の仕組みは docs/design/multi-repo/scripts.md」は実際には 11.8 保護済みスクリプトの文書で、launcher の記述は startup-config.md 4.6 にある。移動先を startup-config.md にするか、scripts.md に launcher の節を新設するかは要判断（迷った点） |
| (同) 配布元と配布先の表・設定ひな形の配布・`--force`・実行ビット・`.gitignore` | 543-595 | 53 | 混在。配布の挙動（`--force`、実行ビットが外れたら付け直す、`--lint` が error にする）は利用者向け。配布対象の sh の名前の長い列挙（549 行）、`.gitignore` の行の否定・CRLF の扱い（569-572 行）は実装の細部 | あり。`docs/design/startup-config.md` 4.6 の表（130-136 行）、`docs/design/multi-repo/distribute-common.md` 11.12（これはミラーの話で別物）、`docs/design/multi-repo/rules.md` 11.4.1 | 利用者向けの動作（何がどこへ配られ、既にあるものはどうなるか）は docs/guide/install.md。549 行の sh 名の列挙と 568-572 行の `.gitignore` 判定の細部は重複なので startup-config.md 4.6 へリンク | 549 行は列挙が startup-config 135 行より短い（`ccnavi-dirs.sh` `ccnavi-session-start.sh` が無い）。「食い違い」参照 |
| `## ルール`（前文・YAML の例・引用符の注意） | 597-630 | 34 | 利用者 | あり。`docs/design/rules.md` 5.1（13-34 行）に書式の表（`id` `match` `glob` `regex` `message`）と `ask`/`allow` に `message` を書くと error、が同じ。YAML の引用符の注意（628-629 行）は設計書に 0 件 | docs/guide/rules.md | 目安どおり（ルールの書き方）。602-604 行の `permissionDecisionReason` `additionalContext` は Claude Code の hook の用語で、利用者には必要だが、内部の欄名ではなく出力の形なので許容。`ask` と `allow` に `message` を書くと error の理由（603 行）は付録 C 参照なので guide では一言にとどめる |
| `### id と表記の決まり` | 631-647 | 17 | 混在。`id` の必須・コロン不可・大文字小文字を区別しない `glob`/`regex` は利用者向け。`(?-i:...)` の使い方は利用者向け。644-646 行の `common` `self` 予約は仕様寄り | あり。`docs/design/multi-repo/rules.md` 11.4（`id` のコロン、予約名、`(?-i:...)`）、`docs/adr/0051-regex-ignores-case.md`、`docs/requirements/multi-repo.md`、`docs/CONTEXT.md` に「予約してある」 | docs/guide/rules.md（`id`・大文字小文字・`(?-i:...)` の書き方）。予約名の内部の挙動（`user agree` が承認しない等）は重複なので docs/design/multi-repo/rules.md 11.4 へリンク | 目安は「ルールの書き方は guide/rules.md、判定の内部仕様は design/…/rules.md」。`id` 以降の項目のうち 633-635 行の「記録と文面にこの名前が出るため」は guide では短く |
| `### ルールは 3 つのレイヤーの和で当たる`（レイヤーの表・当たる範囲の表） | 648-672 | 25 | 混在。レイヤーの種類と置き場（655-659 行）、パスを持つツールと持たないツールで当たるレイヤーが違う（666-669 行）は利用者が知るべき仕様 | あり。`docs/design/multi-repo/rules.md` 11.4（11-50 行）、`docs/design/multi-repo/places.md` 11.2、`docs/requirements/multi-repo.md` REQ-MLT | 利用者向けの概念（3 つのレイヤー、足すだけで上書きは無い、厳しいほうが採られる、できないこと 695-696 行）は docs/guide/rules.md。判定の内部は重複なので docs/design/multi-repo/rules.md へリンク | README 自身が 652 行で設計 11.4 と要件 REQ-MLT を指している（設計書に同内容がある印）。guide には表 2 つと「できないこと」だけ置く |
| (同) 箇条（読む版・無いのと不正なのは違う・id の付け方・重複・同 id・ミラー） | 673-693 | 21 | 開発者寄り。ミラー（689-693 行）の `ccnavi-git.sh` でのコミットまで含め、判定の内部仕様 | あり（ほぼ全部）。`docs/design/multi-repo/rules.md` 11.4（読む版 = 8 行、無いのと不正なのは違う = 55-62 行、`id` の付け方 = 26-27 行、重複 = 40-48 行）、`docs/design/multi-repo/distribute-common.md` 11.12（ミラー）、`docs/design/multi-repo/places.md` 11.2（ファイルが無いのは正常） | 重複なので消してリンク。ただし利用者が必ずつまずく 2 点（673-675 行「ワークツリーで直しても統合されるまで反映されない」、684-688 行の「同じ id で中身が違うと両方適用され `--lint` が warn」）は docs/guide/rules.md か guide/troubleshooting.md に要点を残す | 1 項目が 4〜8 行あり、`fallback`・`--explain`・決定ログの欄名など記録の内部形式が混ざる。ミラーの節（689-693 行）は 11.12 と同じ文章（「中身を写す」「`script:` の値は書き換えない」） |
| (同) できないこと・ccnavi ディレクトリは書き込みが止まる | 695-700 | 6 | 利用者 | 一部あり。「コアファイルを守る」は `docs/design/self-guard.md`。「できないこと」は `docs/requirements/multi-repo.md` | docs/guide/rules.md（できないこと）。698-699 行は self-guard.md へリンク | 利用者が「プロジェクトで `ask` を `allow` に緩められない」ことを知る必要がある。698 行の保護は利用者に関わる（ルールに書かなくても `.ccnavi/` に書き込めない）ので 1 行は guide に |

### 食い違い

1. **設定の例と導入スクリプトが書く env の数（README 388-394 行 vs `docs/design/startup-config.md` 132 行・`scripts/ccnavi-setup.sh` 848-849 行）**
   README の JSON の例は env が 5 つ（`CCNAVI_MODE` `CCNAVI_BIN_PATH` `CCNAVI_RESTORE_IF_DENY` `CCNAVI_GUARD_CORE_FILES` `CCNAVI_GUARD_TICKET_APPROVAL`）。startup-config.md 4.6 と setup.sh は 7 つで、`CCNAVI_GUARD_UNWATCHED: "enable"` と `CCNAVI_TICKET_CONTROL` も書く。README の 310 行「下の形は…が書く」と合わない。
2. **配布する sh の列挙（README 549 行 vs `docs/design/startup-config.md` 135 行・`scripts/ccnavi-setup.sh` 159 行）**
   README の `ccnavi-{ticket,review,git,common,fetch,sync,clean,branches,start,stage,worktree,launcher}.sh` には `ccnavi-dirs.sh` と `ccnavi-session-start.sh` が無い。setup.sh の `DEPLOY_SCRIPTS` には両方ある。README の 314 行は `ccnavi-session-start.sh` の存在に触れているので、表だけが古い。また README の `ccnavi-review.sh` は表に入っているが、startup-config.md の `{…}` には `ccnavi-ticket,review,git,…` と同じく有り。
3. **`.gitignore` に足す行（README 568-582 行 vs startup-config.md 136 行・`scripts/ccnavi-setup.sh` 961-963 行）**
   README は `/.ccnavi/bin/<os>-<arch>/` と `**/ccnavi-index.jsonl` の 2 つだけを書く。setup.sh は `/scratchpad` も足す（startup-config.md 136 行に記載あり）。
4. **テストの回し方（README 168 行 vs `docs/claude/testing.md` 11-14 行）**
   README は先頭の例で `uv run python -m unittest discover -s tests -t .`（全件を 1 本）を最初に挙げる。testing.md は「全件を `discover` の 1 本で回さない。数十分かかり打ち切られる」とし、`uv run python -m tests` を勧める。README も 170 行で `python -m tests` を載せているが、同じ表の先頭と矛盾する。
5. **実測の前提の置き場（README 268 行 vs `docs/design.md` 17 行）**
   README は「設計書の付録 C。測り直したときは両方を直す」とする。`docs/design.md` 冒頭の表は「開発の手順、実測で分かった落とし穴」を README#開発 に向ける。落とし穴の移動先を付録 C にするなら design.md 17 行の表を直す。
6. **README と startup-config.md の hook 表の差**
   `PostToolUse`：startup-config.md 41 行にある「md の frontmatter が無ければ案内する（`NOTICE_DOC_FRONTMATTER`）」が README 409 行に無い。`SessionStart`：startup-config.md 38 行にある索引の差分更新（`--docs`）・上位の設定に却下された要求の件数・`additionalContextOnce` の記憶の捨て方が README 406 行に無い。`Stop`：startup-config.md 43 行の agent 型 Stop hook（AI 判定）が README 411 行に無い（env の表 473-475 行にはある）。README が要約として足りない分で、矛盾ではないが、guide に写すと古い仕様になる。
7. **条件の足し方（README 427 行 vs `.ccnavi/docs/ccnavi-md.md` 45 行）**
   README は `src/ccnavi/hook/ccnavimd.py` に関数を書いて `CONDITIONS` に登録。ccnavi-md.md は配布先で `.ccnavi/scripts/ccnavimd.sh` の `ccnavi_cond_<名前>` で足す。経路が 2 つあり、README の節題が「ccnavi の開発」なので矛盾ではないが、`.ccnavi/docs/ccnavi-md.md` の条件の表に `ccnavimd.sh` 側と組み込み側の違いを書いているかは未確認。
8. **sh の移植性の決まりが 2 か所（README 283 行 vs `docs/claude/environment.md` 14-15 行）**
   README は「`$( )` の中の `case` は bash 3.2 と zsh で読みが分かれる。縮退させている」、environment.md は「`$( )` の中には `case` を書かない」。向き（ccnavi が読む側か、書く側か）が違う別の話だが、同じ語で並ぶ。

数値は一致した確認済み: onedir 約 220 ms / 17 MB / onefile 1000〜1500 ms / 7 MB（ADR-0002 と同じ）、`CCNAVI_STATE_KEEP_DAYS` `CCNAVI_LOG_KEEP_DAYS` 既定 14 日（`src/ccnavi/records/prune.py` と同じ）、`CCNAVI_STOP_REVIEW_KEEP_DAYS` 既定 90 日、`CCNAVI_LEARNING_SOURCES` 3・`CCNAVI_LEARNING_NOTICE_COUNT` 5（`docs/design/tickets/learning-wiki.md` と同じ）、`CCNAVI_DENY_REPEAT` 3・`CCNAVI_FAIL_STREAK_COUNT` 3（`docs/design/pre-check.md` と同じ）。hook の timeout は README の JSON で 10 秒、`SessionStart` の入口は 60 秒。setup.sh は `HOOK_TIMEOUT` と `FETCH_TIMEOUT` の変数で持つので、数値は未確認。

### 判断に迷った点

1. **launcher の仕組みの移動先**: 依頼文は `docs/design/multi-repo/scripts.md` を指すが、そこは 11.8「保護済みスクリプトと案内」（`{root}`、git ラッパーの記録）で launcher の記述は無い。launcher は `docs/design/startup-config.md` 4.6（138-175 行）にほぼ全文がある。重複として消してリンクにするか、scripts.md に launcher の節を新設して移すかを決めてほしい（本表は startup-config.md へのリンクを案にした）。
2. **`## 設定` の 8 イベント表（404-414 行）**: 2 列目を設計書へ寄せ、guide には「登録しないと」の列だけ残す案にした。ただし README の文が設計書 4.2 より簡潔で読みやすいため、逆に 4.2 を消して README の表を design に写す選択もある。内容の新しさは 4.2 のほうが上（食い違い 6）。
3. **lint-py / test-py / mark-ext / test-ext の手順（189-224 行）**: 理由は ADR にあるが、使い方（settings.local.json の JSON）の置き場は docs/claude/ に無い。development.md に入れる案にしたが、docs/claude/testing.md に「ターンの終わりにテストを回す hook」の節を足す選択もある。
4. **`uv run python scripts/check_rules.py`（141 行）**: 配布先の利用者は ccnavi の `scripts/` を持たない可能性が高く、guide/install.md にそのまま移せない。`ccnavi --test-expectations` との関係を確かめてから書く。
5. **設定の「置き場は固定」の表（437-445 行）**: 利用者がファイルを探すのに必要で guide/settings.md に置く案だが、`docs/design/multi-repo/places.md` の表と二重管理になる。guide は抜粋、places.md が正、と決めておかないと食い違いが出る。
6. **実測の落とし穴 14 件の分け方**: 付録 C（Claude Code の挙動）と docs/claude/environment.md（開発環境の決まり）の境界が曖昧。Claude Code の hook・settings の挙動 = 付録 C、Windows/uv/jq/Docker の環境固有 = environment.md、GitLab 実物の挙動 = 付録 C の新節、と分ける案にした。
7. **コア機能の 3 点（7-62 行）**: 設計書 1.1.1 と要件書の冒頭に言い換えが既にあるが、README を正とし他を短くするか、README を短くして設計書を正とするかは決めていない。「残す」案にしたのは依頼文の目安による。
8. **範囲の端**: `### ルールは 3 つのレイヤーの和で当たる` は 648〜700 行で終わるが、その続きの 701 行以降は担当外のため、この節が README 全体で 700 行より後ろにも広がっていないことだけ確認した（701 行が次の `###` の見出し）。

## 付録 A-2（part2）


前提: `docs/guide/` は現時点で存在しない（`ls docs/guide` が失敗）。「docs/guide/rules.md」などは新設前提の移動先。
直前の `### ルールは 3 つのレイヤーの和で当たる`（648〜700）は担当外（part1 側）。末尾の `### 自動復元`（1441〜1450）は
`## ツール実行後チェック` の節の続きとして最後の行に含めた。
「重複」は docs/design, docs/requirements, docs/claude, .ccnavi/docs, docs/adr を grep して確かめた結果。
設計のパスは `docs/design/` からの相対で書く。

### 表

| 見出し | 行範囲 | 行数 | 読み手 | 既存文書に同じ内容があるか | 移動先の案 | 理由 |
|---|---|---|---|---|---|---|
| ### 利用場面でガイドを届ける | 701-712 | 12 | 混在。1 段落目と箇条書き 1・2 番目の「届け方」の考え方は利用者。`additionalContextOnce`・`every`・上限 4000 字の細部と、「ドキュメントの索引は別の仕組み」は設計寄り | 一部。`design/pre-check.md` 6.6「モデルへ渡す文」、`design/rules.md` 5.1 書式（`additionalContext*`） | docs/guide/rules.md（冒頭の考え方 6 行ほどに縮めて）。細部は `design/pre-check.md` 6.6 へのリンク | 目安どおり（additionalContext の書き方）。セッション開始時に渡すものの話（710 行）は `.ccnavi/CCNAVI.md` と `design/startup-config.md` の守備範囲なので 1 行リンクに縮める |
| ### 通す・聞く・止めるのどれでも、一言添える | 713-747 | 35 | 混在。タイプ別の届き方の表と yaml 例は利用者。`logs/state/once-<セッション>-<エージェント>.json` と `--state ""`（729-733 行）は内部 | 一部。`design/pre-check.md` 6.6（313〜317 行に once の記憶と `--state ""`）、`design/rules.md` 5.1。「Claude Code 2.1 で実測」の表は `design/appendix.md` 付録 C に近い | docs/guide/rules.md（表・例・once の意味）。state ファイル名と `--state ""` の 3 行は削って `design/pre-check.md` 6.6 を指す | 目安どおり。境界は 728 行目「文脈はセッション 1 本で…」まで利用者、731〜733 行の「記憶は…置く」「state の置き場が無い」から開発者 |
| ### 何回かに 1 度だけ渡す | 748-781 | 34 | 混在。`every` の意味・表・例・0 や負の値の error は利用者。773〜778 行の SessionStart で 0 に戻る細かい挙動と `logs/state/once-*.json`「鍵 → 回数」は内部 | 一部。`design/pre-check.md` 6.6「渡す回」（326 行〜）、`docs/adr/0090-stop-rules-nudge-every-n.md` | docs/guide/rules.md（意味・例・注意点を残す）。state の置き場の 2 項目は `design/pre-check.md` 6.6 へ | 目安どおり。「正確に N 回ごとではない」という利用者に要る注意は guide に残す |
| ### ターンの終わりに止めて渡す | 782-808 | 27 | 混在。`match: Stop` の書き方・例・`every` の使い方・lint の warn は利用者（ルールの書き方）。`reason` の前置きの中身（787 行）、`logs/state/stop-<セッション>.json`、`SubagentStart` の 1 行、`stop_hook_active`、`decision` が `nudge` は内部 | 一致が高い。`design/pre-check.md` 6.6「ターンの終わりに渡す文」（360〜388 行。読むレイヤー・同じ id・every の下限・state・dry-run・lint がほぼ同じ表）、`docs/adr/0090-stop-rules-nudge-every-n.md`。「振り返り」側の使い方は `.ccnavi/docs/skill-review.md` | docs/guide/rules.md に書き方と例だけ残し、799〜807 行の挙動の箇条書きは重複として消して `design/pre-check.md` 6.6 へリンク | 箇条書き 7 本のうち 6 本が設計書の表と同じ内容（確認済み）。エージェント向けの「振り返り」は `.ccnavi/docs/skill-review.md` に既にある |
| ### ファイルの本文を渡す | 809-836 | 28 | 混在。パスの書き方・上限 4000 字・lint の warn・「広い allow に書かない」は利用者。ワークツリー→元リポジトリ→ルートの探索順（826〜827 行）は内部寄り | 一致が高い。`design/pre-check.md` 6.6（357 行に 4000 文字、読む順序）、`design/rules.md` 5.1（`additionalContextFile`）、`docs/requirements/pre-check.md` | docs/guide/rules.md（例とパス規則）。探索順は設計へリンク | 目安どおり。4000 字は両者で一致 |
| ### 強さ | 837-865 | 29 | 利用者（一部エージェント）。`deny` `ask` `allow` の強さの表と「allow は許可を返さない」は利用者に要る | あり。`design/rules.md` 5.2「タイプの強さ」（34 行）、`design/pre-check.md` 6.4〜6.5、`design/appendix.md` 付録 A（`RULE_ASK` `UNDECLARED`） | docs/guide/rules.md | 目安どおり。861〜862 行の承認済みチケットの範囲との比較は、チケット節（`design/tickets/judging-by-path.md`）を指す 1 行リンクにしてよい |
| ### ルールが言及していない呼び出し（全体） | 866-981 | 116 | 混在（下の 6 行に分ける。この節は 116 行あり、見出しの下に 6 つの話題が入っている） | 下の各行 | 下の各行 | 見出し 1 つに 6 話題が入っている。移す前に `###` を切る前提で、guide 側では「言及の無い呼び出し」「match に書けるツール」「glob/regex の書き方」「except」「when」の 5 節に分けるのがよい |
| └ 言及の無い呼び出しの扱い（`permission_mode` の表、handover、`CCNAVI_GUARD_UNWATCHED`、`PARSE_UNCERTAIN`） | 866-894 | 29 | 混在。権限モードごとの結果の表と「既定は許可ではなく渡すこと」は利用者。`handover` の `jq` 集計、`REQ-PRE-08`、`PARSE_UNCERTAIN` は内部寄り | あり。`design/pre-check.md` 6.5「ルールが言及しない呼び出し」（272 行〜）、`docs/adr/0009-undeclared-handover.md`、`docs/adr/0049-unwatched-gate.md`、`design/startup-config.md` と `design/diagnostics.md`（`CCNAVI_GUARD_UNWATCHED`） | docs/guide/rules.md（権限モードごとの結果の表と考え方）。記録の `decision` 列と `jq` は「記録の見方」の節か `design/appendix.md` 付録 B へ | 目安どおり。表の「記録の `decision`」の列だけは利用者向けではあるが内部語なので、guide では括弧書きにとどめる |
| └ `match` に書けるツールと対象 | 895-911 | 17 | 利用者 | あり。`design/rules.md` 5.4「対象の取り出し」（128 行） | docs/guide/rules.md | 目安どおり。重複部分は guide に一本化し、設計側は内部仕様のみにする |
| └ `glob` / `{root}` / `{script:}` の書き方 | 913-933 | 21 | 混在。glob の意味・部分一致・語の切れ目・`{root}` は利用者。`fnmatch` という実装名（913 行）、`{script:<名前>}` の置換規則と許される文字（931 行）は内部 | あり。`design/rules.md` 5.3「記法」（41 行）、`{script:` は `design/rules.md` と `.ccnavi/docs/projects.md` | docs/guide/rules.md（`fnmatch` は「標準的なワイルドカード」に言い換え）。`{script:}` の置換規則は `design/rules.md` 5.3 | 目安どおり。guide の方針（内部名を書かない）に反する語は `fnmatch` のみ |
| └ `regex`（先読み禁止の理由） | 935-937 | 3 | 混在。「先読みは受け付けない」は利用者。理由（エンジンをまたぐ・組み合わせ爆発・hook の期限）は内部 | あり。`design/rules.md` 5.3、`docs/adr/0051-regex-ignores-case.md` | docs/guide/rules.md（禁止の事実と代わりの書き方）。理由は設計へ | 目安どおり |
| └ `except:` | 939-955 | 17 | 利用者 | あり。`design/rules.md`（`except:` の節）、`.ccnavi/docs/grow-rules.md` | docs/guide/rules.md | 目安どおり。953〜954 行の `Bash` の `deny`/`ask` への警告は guide に残す |
| └ `when:` と `except` との使い分け | 957-980 | 24 | 利用者 | あり。`design/rules.md` 5.3（上限は 118 行に「入れ子 8 段・ノード 64 個」。README と一致）、`.ccnavi/docs/grow-rules.md` | docs/guide/rules.md | 目安どおり |
| ### 同じ呼び出しを繰り返し止めたとき | 982-993 | 12 | 混在。`CCNAVI_DENY_REPEAT`・振る舞い（判定は変わらない）は利用者。数える鍵・ハッシュ・`logs/state/denied-<セッション>.json` は内部 | あり。`design/pre-check.md` 6.4「判定の文面」（258〜261 行に `CCNAVI_DENY_REPEAT`・既定 3・鍵・denied ファイル） | docs/guide/rules.md（最初の段落と「判定は変わらない」）。箇条書き 4 本は重複なので消して `design/pre-check.md` 6.4 へリンク | 目安どおり。箇条書きの内容は設計書にほぼ同じ形で在る |
| ### 失敗や拒否が続いたとき | 994-1010 | 17 | 混在。`CCNAVI_FAIL_STREAK_COUNT`（既定 3、0 で無効）と案内の意味は利用者。数える対象のイベント名（`PostToolUseFailure`）と `failstreak-*.json` は内部 | あり。`design/pre-check.md` 6.4（267〜270 行）、`design/startup-config.md`（`PostToolUseFailure` の行と `failstreak`） | docs/guide/rules.md の設定の節（環境変数の一覧があるならそこ）。箇条書き 5 本の仕組みは `design/pre-check.md` 6.4 へリンク | 目安どおり |
| ## ワークツリーを VS Code から見えるようにする | 1011-1055 | 45 | 混在。1011〜1030 行の VS Code 設定（`git.detectWorktrees` など）と `ccnavi-setup.sh` の挙動、1032〜1038 行のボード拡張は利用者。1040〜1054 行の「ルール管理画面」「リスク管理画面」「フェーズ管理画面」の保存前に通す検査（`--lint --project-phases-file` など）と内部コマンド名は開発者寄り | 一部。VS Code 設定の 2 行は `design/startup-config.md`（134 行の表）だけに在る。拡張の画面の説明は `extensions/vscode/ccnavi-board/README.md`（3 画面の説明を grep で確認）。`docs/requirements/extension-if.md` に拡張の要求。ボードの JSON は README 別節 | 1011〜1031 行は docs/guide/（導入・セットアップの節）に移す＝利用者向けと判断。1032〜1054 行の画面の説明は `extensions/vscode/ccnavi-board/README.md` へのリンク 3〜4 行に縮め、`--lint` や `--project-phases-file` の細部は同 README か `docs/requirements/extension-if.md` へ | 依頼の確認事項「VS Code から見えるようにする」は利用者向けと確認した。ただし 1040 行以降は同じ見出しの下でも話題が拡張の機能説明に変わっている。見出しを切ることを勧める |
| ## Bash のコマンドは実行される部分だけを見る（導入〜末尾の段落） | 1056-1106 | 51 | 混在。1056〜1093 行のコード例（止まる例・通る例）と説明は利用者。1094〜1105 行の `\x00` `\x01` の目印、`shlex`、身代わりの文字は内部 | あり。`design/pre-check.md` 6.3（54 行〜。`\x01` は 6.3 に在る）、`docs/requirements/pre-check.md` | 1056〜1093 行は docs/guide/rules.md の「Bash の読み方」（例中心）。1094〜1099 行の `\x00` を使った regex の書き方は「ルールで同じコマンドの中を指す」の 3 行だけ guide に残し、`\x01` と `shlex` の記述（1101〜1105 行）は `design/pre-check.md` 6.3 へ | 目安どおり。guide の方針に反する語は `shlex`、`\x01`。`\x00` はルール作成者に要るので guide に残す |
| ### 文字として書きたいとき | 1107-1121 | 15 | 利用者（止まる書き方と通る書き方の表） | 部分的。`design/pre-check.md` 6.3 に `quoted` の記録。表そのものは README だけ | docs/guide/rules.md | 目安どおり。`DENY_BACKQUOTE` などのコードは括弧書きで残してよい（利用者の画面に出る） |
| ### 読み切れないとき | 1122-1146 | 25 | 混在。縮退の例と「allow は当たらない」は利用者。`shlex`（なし）、記録の `nothing-to-run`、`PARSE_UNCERTAIN` の扱いは内部 | あり。`design/pre-check.md` 6.3・6.5、`design/limits.md`、`docs/adr/0011-no-allow-when-degraded.md` | docs/guide/rules.md（縮退する条件の表と結果）。`nothing-to-run` と「ここは権限モードに委ねない」の理由は `design/pre-check.md` へ | 目安どおり |
| ### 書き直しを求める形は止めて案内する | 1147-1152 | 6 | 利用者（前置き） | あり。`docs/adr/0047-rewrite-forms.md`、`design/pre-check.md` 6.3 の表 | docs/guide/rules.md | 4 つの子節の導入として一緒に動かす |
| #### ブレース展開は語を並べて書く | 1153-1172 | 20 | 利用者（止まる例と通る例） | あり。`design/pre-check.md` 6.3（106 行、117 行）、`docs/adr/0046-brace-expansion.md`、`design/limits.md` 12.2（許容した誤検知） | docs/guide/rules.md | 目安どおり。「広げ方が bash と zsh で分かれるため」の理由は 1 句だけ残す |
| #### コマンド名はそのまま書く | 1173-1189 | 17 | 利用者 | あり。`design/pre-check.md` 6.3（118 行）、`docs/adr/0047-rewrite-forms.md`、`docs/adr/0122-deny-unreadable-args-to-user-entries.md`。`.ccnavi/docs/worktree.md` にもコード名が在る | docs/guide/rules.md。`.ccnavi/docs/worktree.md` 側は既に別途ある（エージェントが `$G add f` のような形を打たない注意） | 目安どおり |
| #### バッククォートは `$( )` か単一引用で書く | 1190-1196 | 7 | 利用者 | あり。`design/pre-check.md` 6.3、`docs/adr/0047-rewrite-forms.md` | docs/guide/rules.md（直前の「文字として書きたいとき」の表と近いので隣に置く） | 目安どおり。1115 行の表と内容が一部重なる |
| #### シェルで読みが割れる形は素直な形で書く | 1197-1211 | 15 | 利用者（表）。1205 行の「16 段」は数値として guide に残す | あり。`design/pre-check.md` 6.3 の表（120 行。16 段は 88 行と一致）、`docs/adr/0047-rewrite-forms.md`、`design/limits.md` 12.2 | docs/guide/rules.md | 目安どおり |
| ### 実行役のコマンドが中で実行するコマンドにも当てる | 1212-1283 | 72 | 混在。止まる例・外す形の一覧（`env` `sudo` など）・当てる先の表（deny/ask/allow）・文面の 1 行は利用者。1243〜1252 行の深さ 4・語数 2000・「一覧は組み込み」の細部、1278〜1282 行の記録欄 `unwrapped`・`degraded`・集計とのずれは内部 | あり。`design/pre-check.md` 6.3.1（129 行〜。深さ 4 と 2000 は 156 行で README と一致）、`docs/adr/0045-inner-commands.md`、`docs/adr/0121-wrapper-cannot-remove-core-paths.md`。`unwrapped` は `design/diagnostics.md` と `design/appendix.md` にも在る | docs/guide/rules.md に例・外す形の表・当てる先の表・文面の例まで（約 45 行）。1243〜1252 行の数値の細部と 1278〜1282 行は `design/pre-check.md` 6.3.1 へ | 目安どおり。当てる先の表は「allow に実行役を前置きしても通らない」という利用者向けの結論なので guide に残す |
| ### ヒアドキュメント自体は既定のルールで止める | 1284-1300 | 17 | 混在。止まる例と代わりの手段（Write ツール）は利用者・エージェント。1298〜1299 行の `shlex` と設計の節番号は内部 | あり。`design/pre-check.md` 6.3（節の見出しは 6.3）、`design/limits.md` 12.2 | docs/guide/rules.md。エージェントに要る「ヒアドキュメントの代わりに Write」は `.ccnavi/CCNAVI.md` か `.ccnavi/docs/` に既に在るか未確認（下の迷った点を参照） | 目安どおり。`shlex` は guide では「引用の有無を区別しない」に言い換える |
| ## ファイルのパスは行き着く先で見る | 1301-1316 | 16 | 利用者（同じ場所を指す別表記で外せない、という事実） | あり。`design/pre-check.md` 6.2「パスは行き着く先で見る」（44 行） | docs/guide/rules.md（例は残す）。解決の手順は設計へ | 目安どおり。見出しが設計書と同名 |
| ## 探すツールが読むファイルは、ルールに届かない | 1317-1345 | 29 | 利用者（保護の穴と、`.gitignore`・`Read()` の `deny` との分担表） | あり。`docs/adr/0050-search-tools-and-ignore.md`（3 層の表）、`design/pre-check.md` 6.2（51〜52 行）。`builtin:credentials` は `design/appendix.md` 付録 A | docs/guide/（セキュリティの節、または rules.md の末尾） | 目安にない節。利用者が自分で `permissions.deny` を足すかを判断する材料なので利用者向け。ADR に同じ表があるので、guide にはまとめを残し、理由は ADR へリンク |
| ### `.gitignore` を当てにしてよい範囲 | 1346-1364 | 19 | 混在。ripgrep の挙動（起点には当たらない、`.git` が無いと読まない、隠しファイルは弾かれない）は利用者に要る実測知識。`--require-git`・`core.excludesFile` は内部寄り | 一部。`docs/adr/0050-search-tools-and-ignore.md` に `--require-git` と `excludesFile` が在る。`docs/design/appendix.md` 付録 C「実測で確かめた前提」に収まる話題 | 「実測で分かった落とし穴」類として `design/appendix.md` 付録 C へ。guide には結論 2 つ（ignore された場所を名指しすると読まれる、git 化していない `projects/<名前>/` では効かない）だけ残す | 目安（落とし穴は appendix か environment）に従う。1361〜1363 行の「ルートからの Grep は projects/ へ降りない」はエージェントにも要る手順なので、`.ccnavi/docs/` に既にあるか未確認（迷った点を参照） |
| ## 動作モード | 1365-1376 | 12 | 利用者 | あり。`design/startup-config.md` 4.4「動作モード」（70 行に `dry-run` の定義）、`docs/adr/0007-mode-names.md` | docs/guide/ の設定の節（環境変数の一覧と一緒に） | 表の `dry-run` 欄は設計書の同じ行と文面がほぼ同じ。guide は表を残し、設計書は内部仕様に絞る |
| ## ツール実行後チェック | 1377-1407 | 31 | 混在。保護領域が `match` から決まること・出力例（`POST_VIOLATION`）・戻し方の表は利用者。1383〜1384 行の ccnavi 自身の書き込みの見分け方、1404〜1406 行の `<git>` ラッパーの選び方は内部 | あり。`design/post-check.md` 7 章全体（7.2 保護領域と違反、7.3、7.5）、`docs/requirements/post-check.md`、`design/multi-repo/post-check.md` | docs/guide/（実行後チェックの節）に、仕組みの 1 段落・出力例・戻し方の表（約 20 行）。1383〜1384 行と 1404〜1406 行は `design/post-check.md` 7.2 / 7.5 へ | 目安にない節。出力例と戻し方は利用者が画面で見るものなので guide に残す |
| ### 前から在った変更は原因にしない | 1408-1413 | 6 | 混在。「前からある変更は差し戻さない」は利用者。`logs/state/` と `POST_PREEXISTING` の 1 度だけは内部 | あり。`design/post-check.md` 7.3、`docs/adr/0020-preexisting.md` | docs/guide/ に 1 文。残りは重複として消して `design/post-check.md` 7.3 へリンク | 全文が 7.3 とほぼ同じ内容 |
| ### ターンの終わりにユーザへ報告する | 1414-1440 | 27 | 混在（主に開発者）。1416〜1419 行の「Stop に登録すると報告する」と `UserPromptSubmit` の関係は利用者。1420〜1434 行は `finish`・依頼までの道・サブエージェントの結果・`integrate` の各促しの発火条件の詳細で、理由コード（`NUDGE_TICKET_FINISH` ほか）と `logs/state/` の記録名が並ぶ内部仕様 | あり。`design/pre-check.md` 6.6（`NUDGE_TICKET_REQUEST` `NUDGE_SUBAGENT_OUTCOME` `NUDGE_INTEGRATE`）、`design/appendix.md` 付録 A、`design/tickets/state-transitions.md`、`design/tickets/subagents.md`、`docs/adr/0087-nudge-finish-at-stop.md`、`docs/adr/0120-nudge-request-path-at-stop.md`、`docs/adr/0150-subagent-outcomes-are-recorded-in-five-kinds.md`、`.ccnavi/docs/direct-flow.md`（`NUDGE_INTEGRATE`） | 1414〜1419 行は docs/guide/ に 5 行ほど。1420〜1434 行（約 15 行、各 1 段落 1000 字級）は丸ごと `design/pre-check.md` 6.6 の該当小節（促しの順序と発火条件）へ移し、README からは「ターンの終わりに ccnavi が次の一手を促すことがある」の 1 行と設計へのリンクにする | README で最も密度が高い部分。ここに 1 段落 600〜800 字の条件の羅列があり、利用者は読めない。設計書に同じ条件が既に在るか、各段落ごとに照合が要る（`NUDGE_TICKET_FINISH` は pre-check.md には無く appendix.md と state-transitions.md に在る点に注意） |
| ### 自動復元（担当範囲の外。ツール実行後チェックの続き） | 1441-1450 | 10 | 混在。`CCNAVI_RESTORE_IF_DENY` の 3 値と「戻すと未コミットは失われる」は利用者。`logs/state/aside/` と `side_by_side` の例外は内部 | あり。`design/post-check.md` 7.5（109 行〜。`would-restore` `aside` `side_by_side` が全部在る）、`docs/adr/0019-restore-three-values.md`、`design/startup-config.md`（環境変数） | docs/guide/ の設定の節（3 値の意味と注意）。1449 行の例外は `design/post-check.md` 7.5 へ | 担当は 1400 行までだが、`ツール実行後チェック` の節として末尾まで読んだので参考に載せる |

### 食い違い

README と既存文書を突き合わせた範囲では、数値や仕様が食い違う箇所は見つからなかった。確認できた一致:
- `additionalContextFile` の 4000 文字、`every` が 2 より小さいと使わない（README 800 行 ↔ `design/pre-check.md` 371 行）、`CCNAVI_DENY_REPEAT`・`CCNAVI_FAIL_STREAK_COUNT` の既定 3、`when` の入れ子 8 段・ノード 64 個、`$( )` の入れ子 16 段、実行役の深さ 4・語数 2000、VS Code の `git.repositoryScanMaxDepth: 2`。

軽微な表現のずれ（食い違いとまでは言えない）:
- README 799 行「同じ id は 1 本だけ」と `design/pre-check.md` 371 行付近の表「レイヤーの前置きを除いた id が同じなら、最初の 1 本だけ」は、README のほうが前置きを除く条件を書いていない。
- README 1104〜1105 行だけ「読みます」「止まります」と文体が違う（本文は「だ・である」調と混在）。CLAUDE.md の応答ルールとは別の話だが、移すときに揃える。

### 判断に迷った点

1. 866 行の節は 116 行で 6 話題を含む。移動先の guide 側で節を割る前提で行を分けて表にした。README 側の見出しは変えていないので、分割の可否は計画側で決めてほしい。
2. 「ワークツリーを VS Code から見えるようにする」は、前半（設定 2 行と `ccnavi-setup.sh`）は利用者向けで guide に移す。後半（ルール・リスク・フェーズの管理画面の説明、1032〜1054 行）は拡張の機能説明で、`extensions/vscode/ccnavi-board/README.md` に既に画面の説明がある（grep で 3 画面の語を確認）。重複の程度は全文照合していないので、移すときに照合が要る。
3. `Stop` の促し（1420〜1434 行）は、設計書に同じ条件が揃っているかを段落ごとには照合していない。理由コードは `design/pre-check.md`・`design/appendix.md`・`design/tickets/*.md` に分散している。`NUDGE_TICKET_FINISH` は `design/pre-check.md` に出てこないので、設計書側へ足す必要があるかもしれない。
4. エージェントにも要る手順（ヒアドキュメントの代わりに Write を使う、ルートからの `Grep` が `projects/` へ降りないときは `cd` してから呼ぶ）が `.ccnavi/docs/` や `.ccnavi/CCNAVI.md` に既に在るかは確かめていない（grep 対象に「Write」「heredoc」では当たりを取っていない）。エージェント向けへ移す分類は付けず、guide に残す案にした。
5. 「探すツールが読むファイルは、ルールに届かない」と「`.gitignore` を当てにしてよい範囲」は、目安のどれにも当たらない。利用者の判断材料としては guide に残す案にしたが、ADR-0050 と内容が重なるので、guide を短くして ADR へ誘導する案もある。
6. `docs/guide/` が存在しないため、移動先のファイル名（rules.md のほか「設定」「実行後チェック」など）は新設前提で書いた。`rules.md` 一本に 500 行近く集まる見込みなので、書き方（additionalContext 系・match/glob/regex/except/when）と Bash の読み方で 2 ファイルに分けることを勧める。

## 付録 A-3（part3）


前提メモ
- `docs/guide/` は現時点で存在しない（`ls docs/guide` が失敗）。「docs/guide/<ファイル>」は新設案。
- 範囲は節の頭から読むため、1377〜2208 行を扱った（`### レビューの依頼と確認` は 2027〜2208 だが、2101〜2208 は part4 担当と思われる。表では 2027〜2100 を担当分として細分した）。
- `## 動作モード`(1365〜1376) は範囲外（part2 担当）。ただし範囲内の節が参照するので、重複確認だけ末尾の「食い違い」に書いた。
- 設計文書の対応は `docs/design/tickets.md` が入口で、`docs/design/tickets/*.md` が 9.x 節ごとに 1 ファイルずつ在る。README の各節は、ほぼ全部がそこに既に書かれている（下の表で節名まで確認済み）。
- 行数は終端を含む。「重複」欄は grep と該当箇所の読みで確認したものだけ。

### 見出し別の表

| 見出し | 行範囲 | 行数 | 読み手 | 既存文書に同じ内容があるか | 移動先の案 | 理由 |
|---|---|---|---|---|---|---|
| `## ツール実行後チェック`（導入〜戻し方の表） | 1377-1407 | 31 | 混在。利用者に要るのは「何を拾うか」「POST_VIOLATION 出力の読み方」「戻し方の表（git restore / rm）」。内部は「`match` に Write/Edit を含むルールが保護領域になる」「`<git>` の解決（ccnavi-git.sh 有無）」「state の扱い」。境界は 1382 行（保護領域の宣言）から前が利用者、以降が内部 | あり。`docs/design/post-check.md` 7.1 何を見るか・7.2 保護領域と違反（戻し方の表、`<git>` の説明 57 行付近まで一致を確認）。要件 `docs/requirements/post-check.md` | 重複なので消してリンク。利用者向けには `docs/guide/hooks.md`（止まったときの見方）へ「実行後チェックが何を拾うか・出力例・戻し方」を 15 行程度で新設 | 目安どおり（フック挙動は利用者に要る部分と内部仕様に分ける）。表の中身は設計に既にある |
| `### 前から在った変更は原因にしない` | 1408-1413 | 6 | 混在。利用者向けは「前からの変更は報告するだけで戻さない（POST_PREEXISTING）」の 2 行。`logs/state/` の置き場は内部 | あり。`docs/design/post-check.md` 7.3（`POST_PREEXISTING` を確認）、`docs/design/appendix.md` の理由コード表、ADR-0020 | 重複なので消してリンク。guide/hooks.md に「前からの変更は戻されない」を 1 行 | 設計に同内容 |
| `### ターンの終わりにユーザへ報告する`（Stop の報告と各種の促し） | 1414-1440 | 27 | 混在。利用者向けは「Stop は UserPromptSubmit が無いと報告しない」「finish の打ち忘れ・レビュー依頼までの道・直接作業の取り込みを促されることがある」「dry-run でも止まる」。内部は `NUDGE_*` の発火条件の詳細、`logs/state/nudged-*.json`、サブエージェント印の 4 時間、順序（1432 行の最後の文） | あり。`docs/design/post-check.md` 7.4、`docs/design/tickets/state-transitions.md`（NUDGE_TICKET_FINISH 74 行、依頼までの道 80 行）、`docs/design/pre-check.md` 6.6「ターンの終わりに渡す文」「レビュー依頼までの道の途中で終えたとき」「直接作業の取り込みを促す」(380〜396 行)、`docs/design/tickets/subagents.md`(4 時間・NUDGE_SUBAGENT_OUTCOME)、`docs/design/appendix.md`(理由コード表)。要件 `docs/requirements/pre-check.md` REQ-PRE-18。ADR-0087/0120/0150。エージェント側の動作は `.ccnavi/docs/direct-flow.md`(NUDGE_INTEGRATE) と `.ccnavi/docs/ticket-flow.md` | 重複なので消してリンク。guide/hooks.md に「ターンの終わりに止められたとき」(促しの種類と、続けるなら理由を書く、の 10 行) を新設 | 全項目が設計/ADR に在る。README の文は 1 文が極端に長い（1430・1434 行）ので、移すときも設計側の既存文を正とする |
| `### 自動復元` | 1441-1450 | 10 | 混在。利用者向けは「CCNAVI_RESTORE_IF_DENY（既定 enable）」「未コミットの変更は失われる」「現れたファイルは `logs/state/aside/` へ退避」「横で作業する場合は戻さない」。`side_by_side yes` の細部は内部 | あり。`docs/design/post-check.md` 7.5（would-restore、`state/aside/<日時>/`、side_by_side 例外 116 行）、`docs/design/startup-config.md` 4.4、ADR-0019。`.ccnavi/docs/direct-flow.md` 94 行に side_by_side | 設定の意味と注意（未コミットが消える）は `docs/guide/hooks.md` に残し、他は重複なので消してリンク | 「設定で変えられること」は利用者に要るので guide に 4〜5 行は残す |
| `### コアファイルを守る`（保護対象の表と バックアップ） | 1451-1476 | 26 | 混在。利用者向けは「守られるファイルの一覧（表の 1 列目）」「`CCNAVI_GUARD_CORE_FILES`」「SessionStart/UserPromptSubmit に登録しないと守られない」。内部は「バックアップを取る時点」、ハッシュ名ストア `logs/state/selfguard/store/`、大きさと更新時刻の比較、3 日で破棄 | あり。`docs/design/self-guard.md` 8.1 守るもの(表)・8.3 戻す側(172〜214 行。3 日、大きさと更新時刻、SessionStart でバックアップ)。要件 `docs/requirements/self-guard.md`。ADR-0021 | 内部仕様は重複なので消してリンク。利用者向けに `docs/guide/hooks.md`（または guide/safety.md）へ「ccnavi が守る 5 種のファイルと、守りを切る env」を 10 行 | 8.1 の表は 4 行で README は 8 行（展開しただけで内容は同じ）。表の粒度を揃える必要あり |
| `### コアファイルを守る`（戻す前に止める側・名指しツール・builtin id） | 1477-1509 | 33 | 開発者寄り。`rm -rf .ccnavi` が止まる程度は利用者にも要るが、`builtin:guard-wrapper-paths`/`DENY_WRAPPER_CORE_PATH`、`restore --source`、`xargs`/`bash --norc` の扱い、`CDPATH` の限界は内部仕様。1490〜1493 行の承認済み置き場・レビュー待ち置き場を消せない件は、利用者に「エージェントに頼まず端末で `rm`」という 1 行だけ要る | あり。`docs/design/self-guard.md` 8.2（guard-project-home・guard-common-layer・名指しツール 4 本・81〜160 行のラッパー節。`builtin:guard-project-home` ほか確認済み）、`docs/design/appendix.md`（DENY_WRAPPER_CORE_PATH、builtin 一覧）、ADR-0121/0158/0144、`docs/design/multi-repo/protection.md` | 重複なので消してリンク。guide には「止まったらエージェントに回避させず、端末で自分で打つ」の 1 行だけ | 設計に在る。README 側のほうが細かい節（1480〜1493 行）があるので、設計に無い断片が無いか、移す前に差分確認が要る（今回は 12.1 の限界記述、`bash -o pipefail` の例が設計にも在るかまで未確認） |
| `### 見えないもの` | 1510-1519 | 10 | 利用者（止まらなかったときの原因）+ 開発者 | あり。`docs/design/post-check.md` 7.6（表の `.gitignore` 行 126 行で確認） | 利用者向けに `docs/guide/hooks.md` へ表をそのまま 1 つ（短いので丸ごと）。設計側は残す | 目安と同じ。表は利用者の「なぜ止まらなかった」に答えるので guide に置く価値がある（README から消すが、guide に転記） |
| `## チケットによる作業範囲`（導入段落） | 1520-1528 | 9 | 利用者（概念紹介） | 部分的に。`docs/design/tickets.md`(9. 入口)、`docs/requirements/tickets.md` | README に残す（2〜3 行に縮める）+ `docs/guide/tickets.md` へリンク | 1523〜1526 行は「チケットとは何か」の紹介なので README の価値が高い。設計/要件へのリンクは guide 経由にする |
| `### 使うかどうかはワークスペースが決める` | 1529-1544 | 16 | 混在。利用者向けは `CCNAVI_TICKET_CONTROL` の書き方、`--ticket-control`、直接作業とチケット作業の違い、`<先頭の語>-<番号>-<slug>`。エージェント向けは 1540〜1543 行（SessionStart で CCNAVI.md の「作業の進め方」を渡す、サブエージェントには渡さない、dry_run 条件の行） | あり。`docs/design/tickets.md`(36 行 直接作業/チケット作業)、`.ccnavi/docs/ticket-work.md`(直接作業とチケット作業の分け方)、`.ccnavi/docs/ccnavi-md.md`(条件 `tickets`)。`--ticket-control` は設計に無い（README 317・1533 行にのみ。grep 済み） | 利用者部分は `docs/guide/tickets.md`。1540〜1543 行は重複なので消して `.ccnavi/docs/ticket-work.md`/`ccnavi-md.md` へリンク | 目安どおり。`--ticket-control` の説明は設計に無いので guide/導入手順に残す |
| `### 置き場と状態`（図・置き場の表） | 1545-1566 | 22 | 利用者（誰が何を動かすか） | あり。`docs/design/tickets/places.md` 9.2、`docs/design/tickets/state-transitions.md` 9.6、`.ccnavi/docs/ticket-flow.md` | `docs/guide/tickets.md` に図と表を移す（スクリプト名は利用者が打つ `ccnavi user agree`、`ccnavi-review.sh confirm` 等に絞る）。設計側は残す | 状態図は利用者の最重要情報。表の `builtin:` 等の内部名は付けない |
| `### 置き場と状態`（箇条書き: 誰が動かせるか・サブエージェント・finish・start・cancel・合流の手順） | 1567-1591 | 25 | 混在。利用者向けは 1567・1588 行（ユーザが動かす、再開は done から doing へ）。エージェント/開発者向けは 1568〜1587 行（`DENY_SUBAGENT_PARENT_TREE`、`cd`/`env -C`/`GIT_DIR`、`python -c` の限界、write-and-push の内部手順、`merged_sha`） | あり（大半）。`docs/design/tickets/subagents.md` 9.12、`docs/design/tickets/state-transitions.md` 9.6、`docs/design/tickets/places.md`、`docs/design/tickets/judging-by-path.md`。サブエージェント側は `.ccnavi/docs/ticket-flow.md`。**ただし `DENY_SUBAGENT_PARENT_TREE` は docs 全体に無い**（grep 済み、README のみ）。親ワークツリー判定の `parent:` 規則 1572 行も要確認 | 1567・1588 行は `docs/guide/tickets.md`。1568〜1587 行は `docs/design/tickets/subagents.md` へ移して `DENY_SUBAGENT_PARENT_TREE` を `docs/design/appendix.md` の理由コード表へ追加（現状は設計未記載） | 設計に無い仕様が README にだけ在る例。消す前に設計へ移すこと |
| `### 書式`（frontmatter 例） | 1592-1623 | 32 | 混在。利用者は提案を直接書かない（エージェントが書く）ので、利用者向けに要るのは「承認画面で何が見えるか」。frontmatter 欄の定義はエージェントと開発者向け | あり。`docs/design/tickets/proposal-format.md` 9.3（20 件、2 段、識別子）、`.ccnavi/docs/frontmatter.md`(frontmatter 全般) | 欄の定義は重複なので消して `docs/design/tickets/proposal-format.md` へリンク。`.ccnavi/docs/ticket-flow.md` が提案の書き方を持つならそこへ。guide には例を載せない | 目安では「仕組みは設計」。この YAML は仕様そのもの |
| `### 書式`（識別子・ブランチ名・深さ・大文字小文字の箇条書き） | 1624-1643 | 20 | 混在。識別子の形と `CCNAVI_BRANCH_PREFIXES` は利用者にも要る。親ワークツリーの `/`→`-` の対応、`branch:` 欄の扱いは内部 | あり。`docs/design/tickets/proposal-format.md`(59・81 行)、`.ccnavi/docs/worktree.md`(CCNAVI_BRANCH_PREFIXES)。`docs/design/multi-repo/standalone-clone-gaps.md` | 識別子の形と `CCNAVI_BRANCH_PREFIXES` は `docs/guide/tickets.md` に 3〜4 行。他は重複なので消してリンク | `.ccnavi/docs/worktree.md` にも命名規則がある。三重にならないよう guide は一文で `.ccnavi/docs/worktree.md` を参照 |
| `### 効くのは承認したものだけ`（導入〜承認コマンド・承認の 4 経路） | 1644-1676 | 33 | 利用者（承認操作） | あり。`docs/design/tickets/approval.md` 9.4、`docs/requirements/approval.md`、`docs/design/tickets/user-entry.md` | `docs/guide/tickets.md`（承認のコマンドと 4 経路の表）。`--digest`・JSON 形式などの内部は設計へ | 利用者が最も使う操作。1664〜1666 行の「承認は中身を変えない」は仕様として設計にも在るので 1 行だけ guide に残す |
| `### 効くのは承認したものだけ`（承認の push、終了コード、`--placed-out` の JSON） | 1677-1717 | 41 | 混在。利用者向けは `ccnavi-user-push-approved.sh` を打つこと、push 先にならないブランチ、終了コード 0/1/2 の意味。`--placed-out` の鍵表、`--record-writes`、`ccnavi-user-agree.sh` の内部呼び出しは開発者 | あり。`docs/design/tickets/approval.md`(132 行付近 push-approved、`placed-out`、終了コード 4 を確認)、`docs/design/tickets/user-entry.md`、`docs/design/tickets/places.md`。`docs/claude/script-output.md`・`docs/claude/exe-boundary.md` に push-approved の言及あり | 利用者部分（承認したら push する、終了コード表）は `docs/guide/tickets.md`。JSON の鍵表と 1709〜1713 行は重複なので消してリンク | 終了コード 0/1/2 表は利用者にも有用だが 4 は内部用 |
| `### 効くのは承認したものだけ`（受け取る側の fetch・sync・統合先の決め方・親のワークツリーの扱い） | 1718-1740 | 23 | 混在。利用者向けは「セッション開始時に fetch され、分かれたら `ccnavi-sync.sh` を打つ」「`CCNAVI_FETCH_TIMEOUT`（既定 15 秒）」「認証失敗時は端末で `git fetch origin` を一度打つ」「親のワークツリーが無い PC は `ccnavi-start.sh --mr`」。内部は `ccnavi_integration` の関数名、`ls-remote --symref`、`origin/HEAD` を向け直す手順 | あり。`docs/design/tickets/places.md`(61・70 行 ls-remote --symref、統合先)、`docs/design/tickets/sharing.md` 9.17、`docs/design/tickets/approval.md`。`docs/design/tickets/places.md` に `ccnavi_integration` 在り。`CCNAVI_FETCH_TIMEOUT` は設計では self-guard.md 158 行に名前が出るだけで、既定 15 秒は設計に無く `.ccnavi/scripts/ccnavi-fetch.sh:35,76` にある | 利用者部分は `docs/guide/multi-pc.md`（複数 PC で進める）。内部は重複なので消してリンク。`CCNAVI_FETCH_TIMEOUT` の既定値と意味は guide に新しく置く | 利用者が設定で変えられる値が設計にも guide にも無いのは README だけ。移すときに落とさない |
| `### 効くのは承認したものだけ`（親ブランチ push の関門 `ccnavi_push_gate` と表） | 1741-1760 | 20 | 開発者向けが主。利用者には「消えた親ブランチを作り直す push は拒否され、迂回は端末で `git push origin <B>`」の 2 行だけ要る | あり。`docs/design/tickets/places.md`(84〜96 行に同表と同判断、60 秒、迂回、`merged` を呼ばない)、`docs/design/tickets/sharing.md`、ADR-0142。**`CCNAVI_WRITE_AND_PUSH_TIMEOUT` は docs に無い**（`docs/CONTEXT.md:259` に `CCNAVI_WRITE_AND_PUSH_*` の総称のみ） | 表は重複なので消してリンク。`CCNAVI_WRITE_AND_PUSH_TIMEOUT` を `docs/design/tickets/places.md` に足してから README を消す。guide には迂回の 2 行 | 設計に未記載の env 名がある |
| `### 効くのは承認したものだけ`（承認前の `--verify`・承認の伝達・`status`） | 1761-1797 | 37 | 混在。利用者向けは「承認後にセッションへ一言送る」「VS Code の［レビュー完了］」。エージェント向けは `--agree --preview --verify`、`status` の使い方と読み方（1774〜1796 行） | あり。`docs/design/tickets/approval.md`(216 行 `--verify`、250〜258 行 status と止まっている理由)、`.ccnavi/docs/ticket-flow.md`(126 行「status で確かめる」)、`.ccnavi/docs/ticket-work.md` | 1768〜1772 行（承認後の連絡）は `docs/guide/tickets.md`。1763〜1766・1774〜1796 行は重複で `.ccnavi/docs/ticket-flow.md` と `docs/design/tickets/approval.md` へリンク | `status` の出力の読み方は、ticket-flow.md 側が簡潔なので本文は不要。1782・1786 行は「です・ます」混在で README 内の文体が揃っていない |
| `### 判定の鍵はファイルの行き先` | 1798-1843 | 46 | 混在。利用者向けは「強い側が採られる」表（止まったときの原因表）と「範囲の外として扱わない場所」。内部は `builtin:ticket-scope` の記録形式、`jq` 例、`limit:` 行の文面、`\` を `/` に直さない件 | あり。`docs/design/tickets/judging-by-path.md` 9.5（表 29〜32 行、`limit:` 59〜60 行、`wip\eli5\x.py` 94 行まで確認）、`docs/design/appendix.md`(`TICKET_ASK`、`RULE_ASK`、`UNDECLARED`、builtin 一覧) | 判定の表と「止まったらルールを緩めずチケットを提案する」(1841〜1842 行) は `docs/guide/tickets.md` か `docs/guide/rules.md`。他は重複なので消してリンク。1819〜1821 行の `jq` 例は `docs/guide/records.md` | 目安は「仕組みは設計」だが、利用者が「なぜ止まった」を知る入口になる表だけ guide に残す価値がある |
| `### フェーズと HITL ポイント` | 1844-1868 | 25 | 混在。利用者向けは `none`/`mr` の意味、誰が先へ進めるか、止めている間 Agent と Bash が止まること（`DENY_PHASE_REVIEW`）。内部は「厳しい側が採られる」の導出 | あり。`docs/design/tickets/hitl.md` 9.8（同じ表、導出、`DENY_PHASE_REVIEW`）。要件 `docs/requirements/tickets.md` | `docs/guide/tickets.md` に利用者向けの「フェーズとレビュー」を 10 行。残りは重複なので消してリンク | 目安どおり |
| `### フェーズ定義と計画`（phases.yml 例と親の plan 例） | 1869-1925 | 57 | 混在。`phases.yml` を書くのは利用者の設定（ユーザが持つ設定）なので利用者向け。ただし `ccnavi-config` スキルが書き方を扱う。エージェントは書き換えない | あり。`docs/design/tickets/phases.md` 9.7（欄の表 22〜26 行、`scope` 20 件）、`.ccnavi/docs/grow-phases.md`(フェーズ定義を育てる)、スキル `ccnavi-config` | `docs/guide/config.md`（phases.yml の書き方。例は 1 つ）。欄の定義は重複なので消してリンク。親の `plan:` 例は `docs/design/tickets/phases.md` へ | 設定の書き方は利用者に要るので guide に例 1 つは残す。長さは縮める |
| `### フェーズ定義と計画`（箇条書き: after・ticket new・--plan-order・合意・改版・ロック） | 1926-1971 | 46 | 開発者向け+エージェント向け。利用者に要るのは「承認の画面の待ちの一覧で書き漏れを確かめる」「合意は全部 user agree」の 2〜3 行 | あり。`docs/design/tickets/phases.md`(`--plan-order` 180 行、`--fill-phases` 257 行、`quick` 例 132 行、フィードバック計画 49 行)、`docs/design/tickets/proposal-format.md`、`docs/design/tickets/templates.md`(ticket new)、`docs/design/tickets/approval.md`(78 行 改版で空にする欄)、`.ccnavi/docs/templates.md`・`worktree-start.md`・`grow-templates.md`(ticket new)、`.ccnavi/docs/skill-review.md`・`grow-phases.md`(plan-order) | 重複なので消してリンク。エージェントが打つコマンドの使い方（`ticket new`、`--plan-order`）は `.ccnavi/docs/templates.md`/`worktree-start.md` に在るので不要。guide は 3 行 | 目安では仕組みは設計、エージェントの決まりは `.ccnavi/docs/`。全項目の対応先を確認済み。`followup_of` の記述が設計にも在るかは未確認 |
| `### フェーズ定義と計画`（`--explain` 等の局面表示） | 1972-1975 | 4 | 混在（診断表示） | 一部。`docs/design/tickets/phase-management.md`(要確認 85 行ファイル)、`docs/design/diagnostics.md` | 重複なので消してリンク（または guide の診断節に 1 行） | 4 行で影響小。局面名は 5 種で、対応は `docs/design/tickets/phase-management.md` を grep し切れていない |
| `### 複数人・別 PC・別セッションで進める` | 1976-2026 | 51 | 混在。利用者向けは「何が git で運ばれるか（表）」「手順 1〜4」「承認者の入口（Chrome 拡張・VS Code ボード）」「前提と制約」。内部は write-and-push の順序、`logs/state/retire`、`expectedHeadOid` | あり。`docs/design/tickets/sharing.md` 9.17（利用の前提・何を共有するか・手順・保証しないこと・`expectedHeadOid`・`ccnavi_push_gate`）、`docs/requirements/approval.md`、`.ccnavi/docs/worktree-merge.md` | `docs/guide/multi-pc.md` に利用者向け（手順と制約）を移す。2007〜2009 行（サブエージェントとは別）は `.ccnavi/docs/ticket-work.md` と重複。内部は消してリンク | 目安どおり。「保証しないこと」は利用者に重要なので guide 側にも残す |
| `### レビューの依頼と確認`（request・ELI5・crit） | 2027-2053 | 27 | 混在。利用者向けは `crit review`/`crit push`、`ELI5 を見る:` の手順、ELI5 を直した後の扱い。エージェント向けは `--eli5` の必須条件、置き場 `wip/eli5/`、HEAD と同じ 100644 | あり。`docs/design/tickets/review.md` 9.10（28〜54 行の確認順、172〜210 行「ELI5 と crit」で `wip/eli5/`・100644・crit 確認済みを確認）、`.ccnavi/docs/ticket-flow.md`、`.ccnavi/docs/decisions.md`(wip/eli5)、`.ccnavi/docs/scratchpad.md` | 利用者部分は `docs/guide/review.md`（レビューの受け方）。エージェントの条件は `.ccnavi/docs/ticket-flow.md` へ（既に在る）。残りは重複で消してリンク | 目安どおり |
| `### レビューの依頼と確認`（request の前提・confirm・受け入れ台帳・レビュー済みにする場所） | 2054-2069 | 16 | 混在。利用者向けは「レビュー済みにする場所」4 つ（端末・ボード・拡張・decide）。内部は `accepted.json`、`via: manual`、拡張がホスト履歴から移動時刻を読む件 | あり。`docs/design/tickets/review.md`(234 行 手で動かした子の確定)、`docs/design/tickets/hitl.md`、`docs/requirements/tickets.md`(accepted.json)、`.ccnavi/docs/ticket-flow.md`(130 行) | レビュー済みの 4 経路は `docs/guide/review.md`。他は重複なので消してリンク | 2062〜2063 行は 1 行が極端に長い。内部仕様が混ざるので guide へは移さない |
| `### レビューの依頼と確認`（decide: 対応しない・直す・issue に回す、変更要求の表） | 2070-2086 | 17 | 混在。利用者向けは decide の 3 つの選択肢と、変更要求が出ている間通らないことの案内（GitLab/GitHub 表）。内部は `ccnavi_op`、60000 字の分割、`--resume`、`digest` | あり。`docs/design/tickets/review.md`(`pending_extra` 84 行、GitLab/GitHub 表 227〜230 行、`--resume`、60000)、`.ccnavi/docs/decisions.md`(69〜70 行に同じ表)、`docs/requirements/tickets.md` | 3 つの選択肢と表は `docs/guide/review.md`（利用者に表示される文面なので）。ただし `.ccnavi/docs/decisions.md` にも同表があり、二重管理を避けて 1 か所を正本にする | 同じ GitLab/GitHub 表が review.md・decisions.md・README の 3 か所にある |
| `### レビューの依頼と確認`（sh と実行ファイルの段の表 request/confirm/decide/comment/ready/close-early/fetch/origin、道具の判定） | 2087-2100（以降 2101-2208 は part4 担当と思われる） | 14 | 開発者向け（`review prepare`/`review requested`/`user reviewed N --accept-unresolved`、`--result <path>`）。利用者に要るのは「`gh`/`glab` か `curl`+トークンが要る」のみ | あり。`docs/design/tickets/review.md`(19〜31 行に sh と実行ファイルの段の表、253〜258 行に道具の判定)、`docs/design/tickets/close.md`(ready、close-early)、`docs/design/tickets/branch-lookup.md`(32 行 gh/glab/curl) | 重複なので消してリンク。道具の前提（gh/glab/curl、jq）は `docs/guide/review.md` の「必要な道具」に 2 行 | 「実行ファイルはネットワークに出ない」は設計原則なので `docs/design/tickets/review.md` 側に在ることを確認済み（19 行付近） |

### 食い違い

README と既存文書で数値・仕様が食い違う箇所は、今回確認した範囲では見つからなかった。確認した項目を示す。

| 確認した項目 | README | 既存文書 | 結果 |
|---|---|---|---|
| サブエージェント印の失効 | 1430 行 4 時間 | `docs/design/tickets/subagents.md:64`(4 時間 `RUNNING_MAX_SECONDS`) | 一致 |
| セッションのバックアップ破棄 | 1474 行 3 日 | `docs/design/self-guard.md` 214 行 3 日 | 一致 |
| 範囲・フェーズ定義の項数、深さ | 20 件、2 段 | `proposal-format.md` 59・81 行、`phases.md` 25 行 | 一致 |
| 形跡があるときの `ls-remote` 時間 | 1750 行 60 秒 | `places.md` 90 行 60 秒 | 一致 |
| 動作モード表 | 1369〜1371 行（範囲外） | `docs/design/startup-config.md` 4.4 | 一致。ただし README は `disable` を `.claude/settings.json` に書いても効かない旨を書く。設計は 57〜61 行に同旨 |
| 自己防衛の保護対象の表 | 1456〜1464 行は 8 行 | `self-guard.md` 8.1 は 4 行（`.claude/settings*.json` を 1 行、共通レイヤー 1 行、自身とプロジェクトのレイヤーを 1 行、BIN_PATH と実行ファイル 1 行） | 内容は同じで書き方が違う。統合するなら README を消して設計の表に揃える |
| README にあって設計・docs に無い項目 | `DENY_SUBAGENT_PARENT_TREE`(1571 行) | docs 全体で 0 件 | 設計の `appendix.md` に理由コードを足す必要あり |
| 同上 | `CCNAVI_WRITE_AND_PUSH_TIMEOUT`(1750 行) | `docs/CONTEXT.md:259` の総称のみ | `places.md` に env 名を足す |
| 同上 | `CCNAVI_FETCH_TIMEOUT` の既定 15 秒(1727 行) | `.ccnavi/scripts/ccnavi-fetch.sh:35,76` は 15、設計は env 名を通す変数として self-guard.md 158 行に出すだけ | 既定値の記載が設計に無い（食い違いではなく欠落） |
| 同上 | 導入スクリプト `--ticket-control`(1533 行) | README 317 行以外に無い | 導入手順の文書が無い |
| README 内部の乱れ | 1543 行「最後の行（`dry_run` の条件）は…」 | 直前に表が無く、指す先が欠落している。`.ccnavi/CCNAVI.md` の「作業の進め方」の表を指すと思われる | 参照先の欠落。移すときに文意を直すこと |
| README 内の文体 | 1782・1786 行は「できます」「異なる場合があります」の「です・ます」調で、周囲は常体 | CLAUDE.md の応答ルールは応答用 | 軽微。移す際に常体へ揃えること |

### 判断に迷った点

1. 同じ内容が README・設計・`.ccnavi/docs/` に三重に在る箇所がある（GitLab/GitHub の変更要求表は README 2082〜2086、`docs/design/tickets/review.md` 227〜230、`.ccnavi/docs/decisions.md` 69〜70）。利用者に表示される文面なので guide に置くか、設計を正本にして guide から参照のみにするかは、分割方針の決定が要る。
2. 「コアファイルを守る」(1451〜1509 行) は 59 行で、利用者向け（守る対象、切る env、端末で自分で打つ）が実は 15 行ほど。残りは設計に既存。分割して guide に置く量が少ないので、guide/hooks.md の 1 節に統合した。
3. 「承認の push」と「受け取る側の fetch/sync」(1677〜1760) は混在度が高く、`docs/guide/` に移す部分と設計へ戻す部分の境界が段落内で入り交じる（例: 1718〜1729 行は利用者の操作、設計の関数名、環境変数が 1 文に同居）。段落を割る作業が要る。
4. `docs/guide/` は存在しないので、`hooks.md`・`tickets.md`・`review.md`・`multi-pc.md`・`config.md`・`records.md` は依頼の目安を元にした仮のファイル名。ユーザ指定の `tickets.md`・`records.md` 以外は新設案。
5. `### フェーズ定義と計画` の `phase-management.md`（局面表示）への対応は、同ファイルを読み切っていないため「要確認」とした。
6. 設計に無い項目（`DENY_SUBAGENT_PARENT_TREE`、`CCNAVI_WRITE_AND_PUSH_TIMEOUT`、`CCNAVI_FETCH_TIMEOUT` の既定値、`--ticket-control`）は、README から消す前に設計へ移すことを前提にした。この前提を外すと情報が消える。
7. 1432・1434 行のように 1 段落が 1000 字を超える箇所は、読み手を分けると文が切れない。設計側の既存の文（`pre-check.md` 380〜396 行）が正本として使えるかを先に確認するのがよい。

## 付録 A-4（part4）


担当範囲は 2101〜2800。範囲の頭は `### レビューの依頼と確認`（2027〜2208）の途中、末尾は `## ボードの JSON`（2754〜2812）の途中。どちらも節の頭／終わりまで読んだ。`## 承認の JSON`（2813〜）以降は担当外。
`docs/guide/` はまだ存在しない（`ls docs/guide` で確認）。
「既存文書」欄の「同内容」は、grep でキーワードを探し、該当節を開いて確かめたもの。

### 表

行数は見出し行から次の見出し行の前までの数。`レビューの依頼と確認` と `設定の検証` は長いので、太字の段落／表の区切りで下位行に分けた（見出しではなく段落の区切り）。

| 見出し | 行範囲 | 行数 | 読み手 | 既存文書に同じ内容があるか | 移動先の案 | 理由 |
|---|---|---|---|---|---|---|
| ### レビューの依頼と確認（全体） | 2027-2208 | 182 | 混在（下の 7 行に分解） | 下の各行を参照。全体として docs/design/tickets/review.md「9.10」と docs/design/tickets/close.md「9.11」がほぼ全文を持つ | 下の各行を参照 | 範囲は 2101 から始まるが、節の頭から読んだ。2027-2100 は part3 担当と重なる可能性があるので、重複させる場合は下の 2101 以降だけ採用する |
| └ request/confirm/decide/comment の説明と sh↔実行ファイルの対応表（担当外の前半、参考） | 2027-2100 | 74 | 混在（利用者の操作＝依頼・確認・decide の選択肢／開発者＝「sh の動き」表の `review prepare`→`review requested` 等の内部段・`--result`・`CCNAVI_GUARD_TICKET_APPROVAL`） | あり。docs/design/tickets/review.md「9.10」（ELI5 と crit の節 172 行〜、`wip/eli5/`・100644・pending_extra・accepted.json・60000 字）、docs/requirements/tickets.md（ELI5・crit・accepted.json）、.ccnavi/docs/ticket-flow.md（confirm/decide の手順表）、.ccnavi/docs/decisions.md（変更要求の案内表） | 利用者の操作（依頼・確認・decide の 3 択）は docs/guide/tickets.md。内部段の表は重複なので消して docs/design/tickets/review.md へのリンク | 目安どおり。「sh の動き」表（2090-2099）だけは内部構造なので guide に書かない |
| └ 道具・認証（gh/glab/curl、origin の読み方、push 認証、`--result`） | 2101-2108 | 8 | 混在。利用者向け＝gh/glab/トークンが要ること、認証の置き場。開発者向け＝`ccnavi_git_auth`（`ccnavi-common-host.sh`）、`GIT_CONFIG_COUNT`、`GIT_TERMINAL_PROMPT`、`CCNAVI_GUARD_TICKET_APPROVAL`。境界は「何が要るか／どこに置くか」まで利用者、環境変数名と関数名から開発者 | 一部。docs/design/tickets/branch-lookup.md:31 が「ホストへの接続」を `ccnavi-common-host.sh` の節として参照。`ccnavi_git_auth`・`git-credential`・`GIT_TERMINAL_PROMPT`・ghe.com の判定は docs/design にも .ccnavi/docs にも無し（docs/design/tickets/review.md と .ccnavi/docs/ticket-flow.md に ghe.com の言及のみ） | 利用者向けの要点（gh/glab か `GITHUB_TOKEN`/`GITLAB_TOKEN`、`jq` が要る、認証の置き場）は docs/guide/tickets.md。接続の仕組み（URL の見分け方・git の認証の足し方）は docs/design/tickets/review.md の「ホストへの接続」に新しく足す | 既存文書に無い内部仕様が README にしか無いので、削る前に design へ移す必要がある（移し先が未整備） |
| └ Draft を外すのは親、マージはユーザ（`ready` の前提） | 2110-2117 | 8 | 混在。利用者向け＝順（finish→push→ready）とマージはユーザ。エージェント向け＝push と Draft を外す前にユーザへ確認を取り直さない | あり。docs/design/tickets/close.md「9.11」に同内容。.ccnavi/docs/ticket-flow.md（手順 12〜13） | 利用者向けの順の説明は docs/guide/tickets.md。重複分は消して docs/design/tickets/close.md へリンク。「確認を取り直さない」は .ccnavi/docs/ticket-flow.md にあるか要確認（未確認） | 目安どおり |
| └ ワークツリーは ccnavi が片付ける | 2119-2145 | 27 | 混在。利用者向け＝何が消え、何が残るか／ブランチの削除はユーザが `!` を付けて打つ／自動モードの分類器に止められたとき。開発者向け＝`ccnavi worktree retire`/`branch`/`sync tidy`、`merged_sha` の祖先判定、`GIT_*` | あり。.ccnavi/docs/worktree-cleanup.md（タイミングと条件、`ccnavi-clean.sh`、`permissions.allow` の例、`ccnavi-user-clean-branch.sh`）、docs/design/tickets/close.md「9.11」、docs/design/tickets/user-entry.md、docs/claude/exe-boundary.md | 重複なので消してリンク（利用者向けの「消えるもの／残るもの」「分類器に止められたら」は docs/guide/tickets.md に 5〜10 行で要約） | worktree-cleanup.md はエージェント向け。利用者向けの要約が guide に無いので、そこだけ新規に書く |
| └ チケットの置き場は既定のブランチに残さない（`logs/archive/` への退避） | 2147-2179 | 33 | 開発者向け中心（退避の順・一時ファイル・マーカー `ready/<親>.json`・write-and-push・`finish` の標準出力 2 行目）。利用者に要るのは「退避は手元のバックアップで、squash マージ後に既定のブランチにチケットが残らない」の 2 行だけ | あり。docs/design/tickets/close.md「9.11」・docs/design/tickets/sharing.md・docs/design/tickets/places.md が `logs/archive/<リポジトリ>/` と退避の流れを持つ。ただし「ready の打ち直し」「退避の上書き禁止」「標準出力の 2 行目 `tree`」「連番と親ワークツリー判定に退避を使う」の細部は design 側の記述が薄い（grep で `tree <退避したツリーのルート>` は review.md 側に無し） | docs/design/tickets/close.md（無い細部を足してから README 側は消す）。利用者向けは guide に 2 行 | 細部が design に無いまま削ると仕様が消える。移すときに review.md／close.md との突き合わせが要る |
| └ MR がマージされたあと（`sync`・`merged`・`retire`・`abandon`） | 2181-2203 | 23 | 混在。利用者向け＝マージ後に何が片付くか、放棄した親は `ccnavi worktree retire --abandon` を端末で打つ。開発者向け＝`retire/<親>.json`・`record yes/no`・`ccnavi sync retired/tidy`・write-and-push の外し条件 | あり。docs/design/tickets/close.md「9.11」（merged／retire／abandon／tidy／`record yes|no` まで同じ）、.ccnavi/docs/worktree-cleanup.md（片付けの条件）、docs/design/tickets/places.md・user-entry.md | 重複なので消してリンク。2200-2203（write-and-push の外し条件、`builtin:guard-records`）は docs/design/self-guard.md か places.md に既にあるか未確認のため、削る前に確認 | 目安どおり |
| └ まだ残っているが早めに閉じたいとき（`ccnavi-user-close-early.sh`） | 2205-2207 | 3 | 混在（利用者の操作＝端末で打つ・y/N。取り消し・マーカー・issue 書き出しは仕組み） | あり。docs/design/tickets/close.md の末尾段落（簡略版）、docs/design/tickets/state-transitions.md、docs/requirements/tickets.md | docs/guide/tickets.md に操作を書き、仕組みは重複なので消してリンク | README のほうが詳しい（レビュー未了フェーズのマーカー・省略マーカー・受け入れの返信）。design の close.md には「取り消しも省略も受け入れも…普段と同じ形で残る」としかない。詳細を design に移してから消す |
| ### 実績のリスク | 2209-2237 | 29 | 混在。利用者向け＝HIGH 以上で人間レビューが要る扱いになる、点の出し方、点が出る場所。エージェント向け＝`judge:` の判定を `record-risk` で記録する手順。開発者向け＝`risk.json` の置き場・`state/risk-judge-<子>.md`・環境変数 `CCNAVI_BASE_SHA` 等。境界は表の「誰が測るか」列と `script:` の環境変数から開発者 | あり。docs/design/tickets/risk.md「9.9」（表 3 行まで一語一句ほぼ同じ）、.ccnavi/docs/grow-risks.md（配点の育て方、境目 20/40/70、`judge` の使い分け）、docs/CONTEXT.md（lines_over の用語） | 重複なので消してリンク（docs/design/tickets/risk.md）。`risks.yml` の書き方の YAML 例（2217-2227）は設定の書き方なので docs/guide/ の設定の節（または .ccnavi/docs/grow-risks.md）に残す。利用者向けには「HIGH 以上は宣言に関わらず人間レビュー」と点の出る場所だけ docs/guide/tickets.md | design 側に YAML 例が無い。例は利用者が設定を書くときに要るので guide 側に必要。`complexity.sh` の例は README にしか無い（grep で他に無し） |
| ### issue・MR を指定された依頼 | 2238-2259 | 22 | 混在。利用者向け＝「#152 を直して」と頼むとエージェントが `ccnavi-start.sh` を打つ、候補が複数なら聞かれる。エージェント向け＝終了コード 1〜4 の扱い。開発者向け＝`UserPromptSubmit` の拾わない文字列（`#fff`、`C#`）、候補の探し方 | あり。docs/design/tickets/branch-lookup.md「9.13」（`UserPromptSubmit` の拾う／拾わない形、終了コード 3・4・1・2、`worktree add` まで同じ）、.ccnavi/docs/worktree-start.md、docs/adr/0103-issue-mr-branch-lookup.md | 重複なので消してリンク。guide には依頼の言い方と、候補が複数のときに聞かれることを 3〜5 行 | 終了コードの扱いは .ccnavi/docs/worktree-start.md にあるか未確認（`ccnavi-branches` の言及は確認済み） |
| ### サブエージェントに渡すもの | 2260-2264 | 5 | 開発者向け（`SubagentStart` で何を渡すか。利用者の操作ではない） | あり。docs/design/tickets/subagents.md「9.12」（表の `SubagentStart` 行、「判定は行き先で決まるので案内でしかない」の同文） | 重複なので消してリンク | 目安どおり |
| ### プロジェクトのスキル | 2265-2277 | 13 | 混在。利用者向け＝`projects/<名前>/skills/<スキル>/SKILL.md` に置く、Claude Code が自動では読まない、ワークスペースから起動したとき `.claude/skills` が読まれるか。開発者向け＝`SessionStart`/`SubagentStart`/`PreToolUse` の添え方、上限 30 本・4000 文字、`^[A-Za-z0-9._-]+$`、`process: true` の写し（設計 11.13）。境界は「どこに置くか／読まれるか」まで利用者、添え方と上限から開発者 | あり。docs/design/multi-repo/skills.md「11.13」（目録の渡し方、30 本・4000 文字、名前の規則、専用の保護なし）、docs/design/multi-repo/standalone-clone-gaps.md（単体 clone の C2）、docs/adr/0091 | 重複なので消してリンク。置き場と注意点（Claude Code 2.1.303 での読み込み条件）は docs/guide/ のプロジェクトの節に 4〜5 行 | 「`projects/` が git で無視されているかで `.claude/skills` が読まれるか変わる」の実測（2.1.303）は skills.md にも同趣旨の文があり二重管理。guide には結論だけ |
| ### 参考にした運用 | 2278-2281 | 4 | 開発者向け（由来の注記） | なし（`参考/issue-mr-ticket-workflow` で grep しても docs 側に見つからず。未確認：`参考/` ディレクトリが現存するか） | 開発者向け文書（docs/development.md か docs/CONTEXT.md）へ 1 行、または削除 | 利用者・エージェントのどちらにも不要。ディレクトリが無ければ削除候補 |
| ## ルールファイルが読めないとき | 2282-2294 | 13 | 混在。利用者向け＝読めなくても止まらず既定で判定する、既定に入っているのは取り返しの付かない操作。開発者向け＝設計 5.6 への対応 | あり。docs/design/rules.md「5.6 ルールが読めないとき」。表の 3 行（Write/Edit は通す・シェルは止める・`add`/`restore --ours`）は要件／design に同趣旨があるか未確認 | 利用者向けの結論（読めなくても止まらない／止まるのは危険な操作）は docs/guide/ のルールの節に 3〜4 行。表は重複なら消してリンク | README は design へのリンクを既に持つので、README 側を短くしてリンク化しやすい |
| ## 記録 | 2295-2333 | 39 | 混在。利用者向け＝`logs/decisions.jsonl` に 1 行 1 件、`decision` の意味、`jq` での集計例、秘密の形は伏せる。開発者向け＝全欄の定義（`code`・`rules`・`degraded`・`unwrapped`・`quoted`・`fallback`・`source`）、`tree`/`project`/`paths`/`detail` の内部。境界は 2305-2321 の欄の表から開発者 | 一部。docs/design/appendix.md「付録 B」（欄の一覧、24 欄）、「付録 A」（理由コード）、docs/design/startup-config.md「4.7」（秘密の伏せ・ローテート・14 日）、docs/adr/0089-rotate-prune-and-redact-records.md | 利用者向け＝docs/guide/records.md（`decision` の意味、jq の例、秘密の伏せ方、置き場）。欄の表は docs/design/appendix.md「付録 B」へ集約して README 側は消す。ただし README の表は付録 B にない説明（各欄の読み方）を持つので、移す前に付録 B へ統合 | **食い違いあり（下）**: 欄数 23／24、`skip` の理由 9／10。表が付録 B より詳しい箇所があるため、単純削除は不可 |
| ### Stop の AI 判定 | 2335-2350 | 16 | 混在。利用者向け＝登録・解除・点検の 1 行、`CCNAVI_STOP_REVIEW` の効き方、基準の差し替え、集計。開発者向け＝`labels.jsonl` の形、費用の実測値 | あり。docs/design/stop-review.md（構成・登録と診断・測定結果）、docs/adr/0153-ai-review-at-stop.md。使い方（`register|unregister|check`、`--model`、基準ファイル、`stop-review report`）は design に書き方が薄い（登録は stop-review.md「登録と診断」に 1 行） | 使い方は docs/guide/records.md（振り返りの一部）または docs/guide/ の設定の節。`labels.jsonl` の形・実測値は重複なので消して docs/design/stop-review.md へリンク | 「記録と振り返り」の guide に置くか、Stop の仕組みとして別 guide にするか迷った（下の「判断に迷った点」） |
| ### 記録の後始末 | 2351-2372 | 22 | 混在。利用者向け＝セッション開始時に自動で片付く、既定の日数／MB（`CCNAVI_LOG_ROTATE_MB` 10・`CCNAVI_LOG_KEEP_DAYS` 14・`CCNAVI_STATE_KEEP_DAYS` 14・`CCNAVI_STOP_REVIEW_KEEP_DAYS` 90）、`ccnavi --prune --preview`／`ccnavi user prune`。開発者向け＝`logs/state/` のどれを消し、どれを消さないか（`ops/`、`stopreview-*`）、`DENY_RECORDS_PRUNE`、`builtin:guard-records` | 一部。docs/adr/0089（ローテート・prune）、docs/design/startup-config.md「4.7」（10 MB・14 日・90 日・prune.py）、docs/claude/logging.md（別物＝sh/Python のログ）、docs/design/self-guard.md（`DENY_RECORDS_PRUNE`）。消す対象の細則は startup-config.md のほうが詳しい（リンク、`<セッション>.json`、UUID の形の名前） | 利用者向け（既定の値と 2 つのコマンド）は docs/guide/records.md。消す／消さないの細則は重複なので消して docs/design/startup-config.md「4.7」へリンク | 目安どおり |
| ## ルールが何に当たるかを確かめる | 2374-2438 | 65 | 混在（目安どおり「利用者の操作と JSON 仕様に分ける」）。利用者向け＝`ccnavi --test`、`--explain` の使い方と見方、`--cwd`/`--permission-mode`/`--agent-id` の意味。開発者向け＝`glob` を翻訳した正規表現、`REQ-DIA-03`、「判定と同じ関数」、`CCNAVI_GUARD_UNWATCHED` の読み方、終了コードの規則。境界は 2398-2413 のフラグ表までが操作、2433-2437 の「dry-run のときに止めるもの」「判定に達せなかったとき」の内部定数・関数の話から開発者 | 一部。docs/design/diagnostics.md「10」（`--test` の引数表、`--explain`）、docs/requirements/diagnostics.md（REQ-DIA）。出力例とフラグの意味は design の表に入っているが、文章の説明は README のほうが詳しい | 操作（出力例・フラグ・`--explain` の見方）は docs/guide/ の「設定の確かめ方」。JSON と内部の話は重複なので消して docs/design/diagnostics.md へリンク | 目安どおり。出力例は guide 側に必要（design には無い） |
| ### 期待する判定で確かめる | 2439-2453 | 15 | 混在。利用者向け＝`ccnavi --test-expectations`、ルールを足したら期待も足す、止めたくないものも置く、期待ファイルはエージェントが書けない。エージェント向け＝`/ccnavi-config` スキルと `wip/handoff/` の流れ。開発者向け＝`check_rules.py` | あり。docs/design/diagnostics.md（`--test-expectations`、`check_rules`）、.ccnavi/docs/grow-rules.md（`check_rules`、`/ccnavi-config` の流れ）、docs/design/tickets/handoff.md | 操作は docs/guide/ の設定の確かめ方。エージェント向けの流れは重複なので .ccnavi/docs/grow-rules.md へリンク。`uv run python scripts/check_rules.py` は開発者向け文書 | 目安どおり |
| ### 記録から候補を起こす | 2454-2473 | 20 | 混在。利用者向け＝`ccnavi --suggest`、候補の 2 種（足す／文面を見直す）、`allow` は勧めない、置くのはユーザ。開発者向け＝拾う条件（5 回以上、`CCNAVI_DENY_REPEAT`）、検証の仕組み | 一部。docs/design/diagnostics.md（`--suggest` の表の 1 行）、.ccnavi/docs/grow-rules.md、docs/design/pre-check.md（`CCNAVI_DENY_REPEAT`）。候補の種類と拾う条件の詳細は design に無し | 操作は docs/guide/ の設定の確かめ方。拾う条件・検証の仕組み（2467-2471）は docs/design/diagnostics.md に移す | 条件が README にしか無いので、削る前に design へ移す |
| ### 候補の JSON | 2474-2485 | 12 | 開発者向け（拡張との契約の JSON） | 一部。docs/requirements/extension-if.md の REQ-EXT-18 が「候補の JSON」へのリンクを持つ（README#候補の-json）。欄の表は他に無し | docs/requirements/extension-if.md（開発者向け）へ移す | 目安どおり。リンク元の extension-if.md:76-78 の README アンカーを新しい場所へ直す必要あり |
| ### 試験の JSON | 2486-2523 | 38 | 開発者向け（`--json` の形。fixture のパス `extensions/vscode/ccnavi-board/test/fixtures/*.json`、テストの名前、`CCNAVI_BOARD_FIXTURE=1`） | 一部。docs/requirements/extension-if.md の REQ-EXT-16 に概要。欄の表は無し | docs/requirements/extension-if.md へ移す | 同上。README#試験の-json へのリンクあり |
| ## 設定の検証（全体） | 2524-2665 | 142 | 混在。利用者向け＝`ccnavi --lint` の使い方・出力の見方・終了コード（error があれば 1）。lint の指摘一覧は「何を拾うか」の参照表で、利用者と開発者の両方が引く | 一部。docs/design/diagnostics.md「10」に `--lint` の概要。個々の指摘（表の約 90 行）は docs/design/multi-repo/diagnostics.md や docs/design/rules.md などに散らばって同趣旨があるが、一覧としては README にしか無い | 下の各行を参照 | 一覧を移す先が決まらないのが最大の論点（下の「判断に迷った点」） |
| └ 使い方と出力例 | 2524-2554 | 31 | 利用者向け | 一部。docs/design/diagnostics.md「10」の表 | docs/guide/ の設定の確かめ方 | 目安どおり |
| └ 表「ルール」 | 2555-2572 | 18 | 利用者向け＋開発者向け（`additionalContextFile` など記述の名前が利用者の書く YAML の欄なので利用者向け） | 一部。docs/design/rules.md と docs/design/pre-check.md に `additionalContextFile` の仕様あり。lint の指摘としての一覧は無し | docs/guide/ の設定の確かめ方（指摘一覧）に残す、または docs/requirements/rules.md へ移して guide からリンク | 指摘は「ルールを書く人が見る」ので利用者向け |
| └ 表「設定とモード」 | 2573-2591 | 19 | 混在。環境変数名（`CCNAVI_MODE` 等）は利用者が設定する値。`ccnavi.settings.local.json` は ccnavi 自身のソースツリー専用なので開発者向け | 一部。docs/design/startup-config.md「4.3 設定の解決」「4.4 動作モード」、docs/design/diagnostics.md（`CCNAVI_BIN_PATH`、`ccnavi.settings.local`）、docs/design/stop-review.md（登録の食い違い） | 利用者向けの行は guide。`ccnavi.settings.local.json`・`CCNAVI_BIN_PATH` の行は開発者向けで docs/design/diagnostics.md へ | 目安と違う点：1 つの表の中で読み手が分かれる |
| └ 表「レイヤー」 | 2592-2610 | 19 | 開発者向け中心（レイヤー合成の衝突・`phases.yml` の 20 件制限など。利用者が複数のレイヤーを持つ構成では利用者も引く） | 一部。docs/design/multi-repo/rules.md、docs/design/multi-repo/diagnostics.md、docs/design/tickets/phases.md | docs/design/multi-repo/diagnostics.md へ移す | レイヤー（common/self/プロジェクト）は設定を配る側の話 |
| └ 表「hook の登録とスクリプト」 | 2611-2621 | 11 | 混在。hook の登録は利用者が直す。`CCNAVI_COMPAT` は開発者向け | 一部。docs/design/startup-config.md「4.2 hook のイベント」、docs/design/diagnostics.md | 同上（guide に指摘一覧、`CCNAVI_COMPAT` の行は開発者向け） | 目安どおり |
| └ 表「チケットとフェーズ」 | 2622-2644 | 23 | 混在（チケットの状態を直す利用者と、仕組みを持つ開発者） | 一部。docs/design/tickets/places.md・phases.md・state-transitions.md・hitl.md が個々の検査の理由を持つ。一覧は無し | docs/guide/tickets.md の「困ったとき」に指摘一覧を移す、または docs/design/tickets/*.md に分散 | 一覧を 1 か所に置く案と分散させる案で迷った |
| └ 表「取り込みと正とする側」 | 2645-2655 | 11 | 開発者向け中心（`ccnavi sync check`、`origin/<統合先>` との差） | 一部。docs/design/tickets/sharing.md、docs/design/multi-repo/tickets.md | docs/design/tickets/sharing.md へ | `logs/state/sync/` は持たない、という内部の注記を含む |
| └ 表「プロジェクト」 | 2656-2665 | 10 | 混在。利用者向け＝`projects/` を `.gitignore` に入れる等の直し方。開発者向け＝gitlink の判定 | 一部。docs/design/multi-repo/diagnostics.md（gitlink の言及あり）、docs/design/multi-repo/standalone-clone-gaps.md、docs/adr/0033-projects.md | 直し方は docs/guide/ のプロジェクトの節。判定の仕組みは docs/design/multi-repo/diagnostics.md | 目安どおり |
| ## lint の JSON | 2666-2697 | 32 | 開発者向け（拡張との契約。`flow_render.render`、`flow.candidates` の読み方） | 一部。docs/requirements/extension-if.md の REQ-EXT-17 とアンカーリンク。欄の表は無し。`flow.candidates` は docs/design/multi-repo/skills.md にも言及 | docs/requirements/extension-if.md へ移す | 目安どおり。README#lint-の-json へのリンクあり |
| ### 子のフローを保存せずに確かめる | 2698-2734 | 37 | 開発者向け（VS Code 拡張が `--flow` を呼ぶ用途。`flow.as_json`、PyYAML 1.1、`$ccnavi` 包み形） | 一部。docs/design/diagnostics.md（`--flow`、フロー編集画面の話）、docs/design/tickets/judging-by-path.md 等 | docs/requirements/extension-if.md または docs/design/diagnostics.md へ | 利用者が打つ形ではない（拡張が呼ぶ） |
| ### 1 つのプロジェクトのルールを保存せずに試す | 2735-2753 | 19 | 開発者向け（`--project-*-file` などの拡張用フラグ） | あり。docs/design/diagnostics.md（`--project-rules-file` 等の記述）、docs/requirements/diagnostics.md、docs/requirements/extension-if.md | 重複なので消してリンク（docs/design/diagnostics.md）。README にしか無い仕様（「`--root` と `--cwd` は 1 度しか渡せない」）は REQ-EXT-02 に既にある | 目安どおり |
| ## ボードの JSON（2800 まで） | 2754-2812（担当は 2800 まで） | 59（担当分 47） | 開発者向け（`tickets[]`/`parents[]` の欄の定義、`attention` の判定式、`src/ccnavi/entry/diagnose_board.py` のようなコード名） | 一部。docs/requirements/extension-if.md の REQ-EXT-12 に最上位の欄の一覧。`tickets[]`・`parents[]` の欄は他に無し（`predecessors_unmet`・`attention`・`archived`・`flow.locked` を grep しても docs/design に無い）。`template_draft` は docs/design/tickets/templates.md | docs/requirements/extension-if.md（目安どおり）。欄の表が長い（約 60 行）ので、同ディレクトリに専用ファイルを切る案も（例 docs/requirements/extension-if-board.md） | 目安どおり。README#ボードの-json へのリンクあり。2800 の次の `parents[]` の表（2800-2807）と、その後の文（2809-2811）は担当外だが同じ節。節単位で動かす |

### 食い違い

| 箇所 | README | 既存文書 | 内容 |
|---|---|---|---|
| `## 記録` 冒頭（2297） | 「全 23 欄」 | docs/design/appendix.md「付録 B」は「24 欄」。欄の名前を数えると 24（`hinted`・`guarded` を含む） | README の表（2305-2321）に `hinted`・`guarded` の欄は無い。どちらかが古い。付録 B の列挙は 24 項目で数が合っている |
| `## 記録` の表 `reason` 行（2311） | `skip` の理由は 9 つ（`mode-disabled`〜`no-turn-baseline`） | docs/design/appendix.md:49 は `internal-error`（ccnavi 自身の例外）を含めて 10 個 | README は `internal-error` を落としている |
| `### 記録から候補を起こす` | 文末で「`/ccnavi-config` の手順で確かめてから置く」 | 該当は .ccnavi/docs/grow-rules.md | 食い違いではないが、README は「エージェント向けの手順」を指しており、利用者向け guide に移すと参照先が変わる |
| `### レビューの依頼と確認`（2106） | 「[実測で分かった落とし穴](#実測で分かった落とし穴)」（README 内アンカー） | README に該当見出しは無い。該当見出しは docs/design/tickets/review.md:266（`## 実測で分かった落とし穴`） | README 内のリンク切れ。移すときに review.md へのリンクに直す |
| `### Stop の AI 判定`（2349） | 「約 10 秒、費用は約 0.0025 ドル」 | docs/design/stop-review.md:93 は「約 +8.9 秒（中央値）・+0.0025〜0.0046 ドル」、:81 は「約 0.0024 ドル」 | 同じ測定の別の言い方。README は下限の値だけを書いている。guide に移すなら「約 10 秒、0.003 ドル前後」のように測定を引用して揃える |
| JSON の版の数 | `## 残った指摘の JSON`（担当外 2875）は「今は 2」。担当範囲の JSON はすべて「いま 1」 | docs/requirements/extension-if.md:78 は「どの JSON も形の版を持つ」 | 食い違いではないが、版が JSON ごとに違うので、移すときは版を表で 1 か所にまとめたほうが読める |
| 他文書から README への依存 | — | docs/requirements/extension-if.md:21,47,76-78,123 が README の `#候補の-json`・`#試験の-json`・`#lint-の-json`・`#ボードの-json`・`#承認の-json`・`#残った指摘の-json`・`#版の-json` を指す。docs/design.md も `README.md#開発` を指す | JSON の節を extension-if.md などに移すと、7 本のアンカーが切れる。移動と同時に直す |

### 判断に迷った点

1. 「設定の検証」の指摘一覧（約 90 行の表）。利用者（ルールを書く人）も引き、開発者（lint を直す人）も引く。案は 3 つ。(a) docs/guide/ に「設定の確かめ方」を作り一覧ごと置く、(b) 利用者向けは使い方と見方だけにして、一覧は docs/requirements/diagnostics.md に置く、(c) 表ごとに領域（ルール→rules、チケット→tickets 等）の design に分散。ここでは (a) か (b) を想定したが、「内部のモジュール名を書かない」方針は、表の中の `CCNAVI_COMPAT`・`ccnavi.settings.local.json`・`builtin:*` などの行に引っかかる。それらの行だけ開発者向けに分ける案にした。
2. 「レビューの依頼と確認」と周辺（2027-2208）は 180 行あり、docs/design/tickets/review.md・close.md とほぼ重複する。ただし README にしか無い細部（`ready` の打ち直し、退避の上書き禁止、`finish` の標準出力 2 行目、`close-early` のマーカーの種類、`ccnavi_git_auth`）がある。「重複なので消す」前に、design 側へ移す作業が要る。差分の洗い出しは別途必要。
3. 「Stop の AI 判定」は、使い方（登録・環境変数）が利用者向けだが、「記録と振り返り」の guide（docs/guide/records.md）に入れるか、別 guide にするか決められなかった。
4. 「ボードの JSON」は 60 行を超える表で、extension-if.md（129 行）に足すと倍近くになる。専用ファイルに分けるほうが読みやすいと思うが、目安では extension-if.md とある。
5. 「参考にした運用」の `参考/issue-mr-ticket-workflow` が現存するかは確かめていない。
6. 範囲の頭（2101）が `### レビューの依頼と確認` の途中なので、2027-2100 の行は参考として表に入れた。part3 と重なる場合は片方を採用してほしい。

## 付録 A-5（part5）


担当範囲の見出しは `##` と `###` だけ（`####` は無い）。2801 行目は「ボードの JSON」(2754-) の途中なので、その節の末尾 2801-2812 も 1 行置く（節の頭からの分は part4 側の担当と想定）。
`docs/development.md` と `docs/guide/` は現時点で存在しない（`ls docs` で確認）。移動先として挙げるものは新規作成になる。

### 表

| 見出し | 行範囲 | 行数 | 読み手 | 既存文書に同じ内容があるか | 移動先の案 | 理由 |
|---|---|---|---|---|---|---|
| ボードの JSON（末尾。`parents[]` の表・VS Code ボードの言葉への置き換え・マージ状況の確認） | 2801-2812 | 12 | 開発者（拡張の作者） | 部分的。`docs/requirements/extension-if.md` REQ-EXT-12 は `parents` などの鍵の名前だけ。欄の意味は無い。`docs/design/tickets/hitl.md` に `gate_closed`/`review_waiting` の説明、`docs/design/diagnostics.md` L81-82 に `sync retired`・`mr-state` の使い方。`phases[]` の欄は他に無い | 節全体（2754-2812）をまとめて `docs/requirements/extension-if.md` に移す。2809 行の「レビュー待ち／準備中」の言葉への置き換え表は `docs/design/tickets/hitl.md` に寄せてもよい | 目安どおり。2809・2811 行は拡張の画面の振る舞いで、本来は `extensions/vscode/ccnavi-board/docs/` の領分でもある |
| 承認の JSON | 2813-2874 | 62 | 開発者。2871 行（エージェントが `--yes` を打つ形は deny が止める）だけエージェント向けだが、`.ccnavi/docs/ticket-flow.md` 等に既にあるはずなので移さない | JSON の鍵の表: `docs/requirements/extension-if.md` REQ-EXT-13/14 に鍵の名前のみ（`written` を含む）。ダイジェストの作り方・不一致・`--digest` 無しは 1: `docs/design/tickets/approval.md` L111-114。`partial` と `overflow`: 同 approval.md が grep で当たる。`verify.reason` と `nothing-pending`: どこにも無い。`plans[]` の欄: 無い（README が 9.4 を指すが approval.md に欄の表は無い） | `docs/requirements/extension-if.md` に「承認の JSON」節として移す。2862-2873 行の照合の決まりは `docs/design/tickets/approval.md` と重なるので、そちらへのリンクにして重複を消す | 目安どおり（形は extension-if）。ただし照合の説明（2862-2873）は design/tickets/approval.md に既にあるので、移すときに重複を片方へ寄せる |
| 残った指摘の JSON | 2875-2913 | 39 | 開発者 | `docs/requirements/extension-if.md` REQ-EXT-15 に鍵の名前。`docs/design/tickets/review.md` L30-31, L76-84, L116（`pending_extra`・指摘の版・`decide`）。`can_issue` は `.ccnavi/docs/ticket-flow.md` L140 と ADR-0157。`mismatch` の形（`changed`・`target`）の JSON 例と、`--resume`・`logs/state/ops/` の記述は他に無い | `docs/requirements/extension-if.md`（JSON の例と鍵の説明）。`pending_extra` などの意味は `docs/design/tickets/review.md` に任せてリンク | 目安どおり |
| 版の JSON | 2914-2959 | 46 | 混在。2914-2955（JSON と鍵の表・互換の版の上げ方・見つける場所の表）は開発者。2956-2958（直し方）は利用者に見える | `docs/requirements/extension-if.md` REQ-EXT-04/05/06 と末尾の注（鍵の名前・3 か所の値）。`docs/design/diagnostics.md` L43-48（互換の版の仕組みと直し方）。`features` と `formats` の説明、見つける場所の表（2950-2954）は design 側にも要約がある。README の「3 か所の場所（ファイル名と定数名）」は REQ-EXT-05 と重複 | 2914-2955 は `docs/requirements/extension-if.md`、重複部（3 か所の値・見つける場所）は REQ-EXT-05 と `docs/design/diagnostics.md` へのリンクに寄せて消す。2956-2958 の直し方は利用者向け（`docs/guide/` のトラブル対処。`ccnavi-setup.sh <ws> --force`・拡張の入れ直し）に 1 段落だけ残し、`uv run --with pyinstaller python build.py` の行は開発者向け | 目安どおり（JSON の版は extension-if）。直し方の分け目は「配布先で配り直す」までが利用者、「リポジトリで組み立て直す」が開発者 |
| 作業・レビュー計画のテンプレート（導入部） | 2960-3033 | 74 | 混在。(a) 利用者: 2962-2979 の打ち方、2998-3008 の置き場の層と同名衝突の扱い、3009-3024 の `.yml` の書式。(b) 開発者: 2978-2979 の「いまの版は…」、2981-2996 の案内・配布の仕組み（ミラー・`DEPLOY_SCRIPTS` 相当・名前の文字制限）、3026-3032 の読み方と検査（256 KiB・別名・`--lint` の場所名）。(c) エージェント: `--templates` の一覧・検索・表示が読むだけで打ってよい（2976-2978） | (a)(c): `.ccnavi/docs/templates.md`（77 行。`--templates`・`--keyword`・`--show`・`--check-file`・`--json`・`--draft` の打ち方は既にある）。(b): `docs/design/tickets/templates.md` の「置き場と統合結果」「読み方」「検査」「案内と配布」（同じ内容を実装名つきで詳しく書いてある）。案内の 1 行の文面は templates.md の「親の `finish` の案内」と一字一句同じ | 利用者向けの操作と書式は `docs/guide/tickets.md` に移す（置き場の 2 層・書式・同名の扱いを 15 行ほどに圧縮）。案内・配布・読み方・検査（2981-2996, 3026-3032）は `docs/design/tickets/templates.md` と重複なので消してリンク | 目安どおり。2026-3032 の細かい検査の列挙（`after` の範囲・終端・延期）は design にあるのでコピーを残さない |
| ### 下書きを作る（`--draft`）と取り込む（`user handoff`） | 3034-3109 | 76 | 混在。利用者: 3 の取り込み操作（3055-3063）と画面の操作。エージェント: 1 の `--draft` の手順と注意（3039-3051）。開発者: `--templates --json` の鍵の表（3071-3106）、`drafts[]` の欄、失敗時の JSON（3108） | エージェント向け: `.ccnavi/docs/templates.md` L39-54（`--draft` のオプション・同名で断る・未取り込みは `ready` を止める）と `docs/design/tickets/templates.md`「溜める側」の表（下書きの置き場・after の正規化・フロー・固有名・`TODO` 印）。取り込み: `docs/design/tickets/handoff.md`（`--expect`・`conflict`・制御文字の `\uXXXX`）。JSON の鍵: templates.md の `--templates` の節と `drafts[]` の段落に鍵の名前（`checked_with`・`exists_in`・`also_in`）。表の「何」の文は無い | `--draft` 手順（3039-3054）は重複なので消してリンク（`.ccnavi/docs/templates.md`）。取り込みの利用者操作（3055-3066）は `docs/guide/tickets.md`。JSON の鍵の表（3068-3108）は `docs/requirements/extension-if.md`（テンプレートの JSON 節。見本 `tests/fixtures/templates-json.json` も同所に書く） | 目安は「テンプレートの利用者操作は guide/tickets.md、仕組みは design/tickets/templates.md」だが、JSON の鍵の表は仕組みの文書より外部への約束なので extension-if を勧める。design/tickets/templates.md はその表に鍵の名前を持っているので、そこに置いてもよい（判断に迷った点 2） |
| ### 使う（`ticket new --template`） | 3110-3147 | 38 | 混在。利用者/エージェント: 打ち方と出力例（3112-3117, 3137-3146）。開発者: 1〜4 の段の内部（`agree_candidates`、`--plan` の足場との関係、`after` の付け直し、子の識別子の決め方） | `.ccnavi/docs/templates.md`（使う側。`ticket new --template` の流れ）。`docs/design/tickets/templates.md`「使う側」の表と「フィードバック計画は既定では書かない」「子の識別子と足場」は README の 1〜4 とほぼ同文（`--with-feedback` の理由・上書きしない・子の足場を作らない）。`docs/design/tickets/proposal-format.md` L100-116 にも `--with-feedback` | 利用者の操作は `docs/guide/tickets.md` に短く（打ち方、`--with-feedback` の意味、書かれるもの）。1〜4 の段の説明は重複なので消して `docs/design/tickets/templates.md` へのリンク。出力例の text ブロックは guide に 1 つ残すか、`.ccnavi/docs/templates.md` に任せて消す | 目安どおり。重複が大きい |
| ### テンプレート管理画面 | 3148-3159 | 12 | 利用者（VS Code 拡張の画面の操作）。3157-3158 の止まる条件は混在 | `.ccnavi/docs/templates.md` L62 以降（管理画面の説明）。`docs/design/tickets/templates.md`「画面での編集」。`docs/design/tickets/handoff.md` L78（テンプレート管理画面と取り込み待ち）。取り込み待ち画面の記述は `.ccnavi/docs/direct-flow.md` など別文書にもある | `docs/guide/tickets.md`（画面で何ができるか。「保存は `--check-file` に error が無く…」のような内部の条件は design/tickets/templates.md に既にあるので外す） | 目安どおり。保存の条件列挙は guide から外す |
| 学びの蓄積（learning-wiki） | 3160-3184 | 25 | 混在。利用者: 3165-3172 の「誰がするか」の表、3177 の既定、3182 の通知を受けたらエージェントに頼む流れ。エージェント: 3178-3181・3183 の `ticket learn`・`/learn`・`consolidate` の使い方。開発者: 3180 の 2000 字、3181 の `<親>-00-NN`・`maintenance: learning-consolidate` 印、3182 の閾値・既定値の数 | `docs/design/tickets/learning-wiki.md`（3 層・流れ・`/learn` の書き先の表・防御・設定と配布の表に `CCNAVI_LEARNING*` 既定 3/5・実装の範囲）。`docs/requirements/learning-wiki.md`（REQ-LRN-02/10/16/17/20）。`.ccnavi/docs/learning-propose.md`（提案の手順）、`.ccnavi/docs/skill-review.md`・`skill-wiki.md`。README の 4 区分の表（3165-3170）は design にそのままの形では無いが、「実装の範囲」の表と対応する | 利用者の概要（誰が何をするか・人間が承認する・既定）は `docs/guide/records.md` に 10 行ほど。コマンドの打ち方（3178-3183）は `.ccnavi/docs/` 側（`/learn` と `ticket learn` の手順があるか要確認。`ticket-work.md` には当たらなかった）。設定の既定値と仕組みは重複なので消して `docs/design/tickets/learning-wiki.md` へリンク | 目安どおり（guide/records.md と design/tickets/learning-wiki.md）。冒頭の「コア機能 4」の言及（3162）は README に残す概要の側で使う |
| ドキュメントの索引 | 3185-3307 | 123 | 混在。利用者向けの使い方: 3185-3218（目的・流れ・実態・ガイドとの違い・コマンド例・`--help`・frontmatter・`NOTICE_DOC_FRONTMATTER`）、3264-3287（絞り方の表と出力形式）。実装: 3220-3262（git ls-files の対象・64 KiB/4 KiB・索引を書く条件・`.git/` 内の一時ファイル・EXDEV・時間予算 3 秒/0.5 秒）、3282-3287 の「外のフラグは 1 で終わる」の一覧、3289-3306（`ccnavi-index.jsonl` の形・古い索引が残る条件・`mtime` の秒精度） | 部分的。`docs/design/diagnostics.md` L26（`--docs` の行に対象 md・索引の書く条件・`.gitignore` 追記・一時ファイルまで書いてある）。`docs/design/startup-config.md` L38・L135-136（SessionStart と `.gitignore`）。`docs/design/pre-check.md` L267。`docs/claude/frontmatter.md`・`.ccnavi/docs/frontmatter.md`（64 KiB・4 KiB、`NOTICE_DOC_FRONTMATTER`、対象外の置き場）。`docs/design.md` L45（コア機能 3）。`MIN_REFRESH_SECONDS`・`START_SECONDS`・EXDEV・`GIT_LITERAL_PATHSPECS`・16 MiB・`mtime` の秒精度・`concept_id` は docs 配下に無い（grep 0 件）＝README だけにある | 使い方（3185-3218, 3264-3287 の表）は `docs/guide/`（`docs.md` か `records.md`）。agent 向けの引き方は `.ccnavi/docs/frontmatter.md` に既にある範囲を除き、鍵の表（3295-3300）と JSON の 1 行（3291-3293）は `.ccnavi/docs/frontmatter.md` へ。実装（3220-3262, 3282-3287, 3302-3306）は `docs/design/diagnostics.md` の `--docs` 節へ移す（新設の `docs/design/docs-index.md` でもよい）。重複する部分（索引を書く条件・一時ファイル）は diagnostics.md にあるのでまとめる | 目安どおり（使い方と実装に分ける）。境界は 3219 行の手前までが使い方、3220 行以降が実装。ただし「食い違い」1 のとおり、`.gitignore` の扱いは README が古いので、移す前に直す |
| 生の git は止めてラッパースクリプトへ寄せる（導入部） | 3308-3330 | 23 | 混在。利用者/エージェント: 目的・打ち方・出力の形・終了コード（3310-3329）。実装: `permissions.allow` の前方一致の説明（3319）、`CCNAVI_GIT_MAX_LINES`（40 行/30 行は sh と一致） | `docs/design/rules.md` L164, L190（`ccnavi-git.sh` を案内）、`docs/design/tickets/places.md` L75-87（git の入口を 1 本に寄せる）。出力の形・終了コード・`logs/git-*.log` は `docs/claude/logging.md` L29 に名前だけ。「出力を要約 1 行＋先頭 40 行、全量を logs/ に」は docs に無い | 利用者の振る舞い（なぜ生の git が止まるか・何を打つか）は `docs/guide/`（git の扱いの章）に 10 行ほど。エージェントへは SessionStart と拒否文が渡すので `.ccnavi/docs/` に 1 つ（`.ccnavi/docs/worktree*.md` は git の例を持つが `ccnavi-git.sh` の通す形の一覧は無い。`.ccnavi/CCNAVI.md` で確認が必要）。出力の仕様（40/30 行・50 本）は開発者向け（`docs/design/` に新設の `git-wrapper.md`） | 目安どおり（利用者の振る舞いと内部仕様に分ける） |
| ### 何を通し、何を止めるか | 3331-3356 | 26 | 混在。利用者/エージェント: 3333-3337 のうち「通る/通らない形」の結論、3349 の「送るのは親だけ」。開発者: 例外の理由（3340 の `-M`/`-C`、3341-3343 の承認済み置き場、3344-3346 の防御対象と `DENY_WRAPPER_CORE_PATH`）、3350 の `GIT_CONFIG_COUNT`/`GIT_EXTERNAL_DIFF`、3352-3353 の限界、3355 の記録 | 部分的。`docs/design/appendix.md` L39-41（`DENY_CHILD_PUSH`・`DENY_WRAPPER_CORE_PATH`）、`docs/design/self-guard.md` L98, L127-130（ラッパーで守る場所を消す・戻す形）、`docs/design/limits.md` L24-25, `docs/design/tickets/review.md` L261-262（子の push を拒む）、`docs/design/tickets/sharing.md` L43・places.md L87（push の関門）、`docs/claude/exe-boundary.md`（`git branch -D`）、`.ccnavi/docs/worktree-cleanup.md`。push の許可形（`--force` 系・refspec・統合先ブランチ名）、fetch/pull の `:`/`+`、`GIT_CONFIG_COUNT`/`GIT_EXTERNAL_DIFF`、`-c` オプション拒否は grep 0 件（README だけ） | 通す/止める形の一覧は `docs/design/`（新設の `git-wrapper.md`。`self-guard.md` の該当節とリンク）。利用者向けには「push は今のブランチを同じ名前で送る形だけ、統合先へは直接送れない、マージはユーザ」の 3 行だけ guide へ。止まったときの案内は拒否文が出すので agent 向けの追加は不要 | 目安どおり。3346 行の「ラッパーを消しても生の git の止めは外れない」は self-guard.md L130 と重複なので消してリンク |
| 構成 | 3357-3514 | 158 | 開発者（すべて）。例外なし | モジュール表: なし（`shellread_scan`・`hookio.py`・`selfguard_targets`・`docsearch_query` などは docs 配下に 0 件）。スクリプト表（3489-3503）: `docs/design/startup-config.md` L135 が配る sh の名前の一覧、`docs/design/multi-repo/scripts.md`。`docs/design/tickets/places.md` に置き場の表。`tests/` 以下の行（3504-3507）と `.claude/hooks/*`（3485-3487）: `docs/claude/testing.md` に一部。`docs/adr/` 行（3513）は `docs/adr/README.md` が索引 | 新設の `docs/development.md`（ディレクトリ構成）と、モジュール表だけ `docs/design/modules.md`（サブパッケージごとに分割してよい）。スクリプトの表（3489-3503）は `docs/design/multi-repo/scripts.md` か startup-config の配布の節へ。`.claude/hooks/`・`tests/` の行は `docs/claude/testing.md` へ | 目安どおり。表が古く、現物と合っていない（食い違い 3）。移す前に作り直すか、自動生成に切り替える方が安全 |
| 配布物の条件 | 3515-3522 | 8 | 開発者 | `docs/design/startup-config.md` L31（PyInstaller の onedir・PyYAML 1 本・`safe_load`）。`docs/requirements/extension-if.md` REQ-EXT-26（依存を標準ライブラリと PyYAML に限る）。`docs/adr/0002-onedir.md`、`0004-pyyaml.md`。「実行前チェックの間に外部プロセスを起動しない」「実行後チェックは git を 1 回」「起動する外部プロセスは git と `sh` だけ」は `docs/design/pre-check.md`・`post-check.md` の周辺に当たるが、1 か所にまとまった文は無い | `docs/design/startup-config.md` の配布物の節に 2 行だけ足して残りは重複として消す。残る 4 行（外部プロセス・同じ入力に同じ判定）は `docs/development.md` | 目安どおり |

### 食い違い

1. **索引の `.gitignore` の扱い（README が古い）。** README 3235-3239 は「実行ファイルは `.gitignore` を書き換えない。ワークスペースには導入スクリプトが足す。プロジェクトには足さない」「どのディレクトリでも無視されていないツリーは索引の対象外」と書く。`docs/design/diagnostics.md` L26 と `docs/design/startup-config.md` L136・ADR-0112・`src/ccnavi/hook/docsearch_index.py`（L510 のコメント、L535 の `_add_ignore`、L702）は、対象外にする前にツリーのルートの `.gitignore` へ `**/ccnavi-index.jsonl` を実行ファイルが 1 行足す（ワークスペースもプロジェクトも同じ）としている。対象外になるのは、否定行がある・足せない・足しても無視されないツリーだけ。README を直してから移す。
2. **互換の版の古い記述。** `docs/design/tickets/user-entry.md` L86 は「`--version --json` は互換の版（`compat`）15 を返す」。実際は 18（`src/ccnavi/entry/version.py` L71、`ccnavi-common.sh` L47、`extensions/vscode/ccnavi-board/src/core/version.ts` L22 の 3 か所とも 18）。README の例（`compat: 18`）は合っている。
3. **構成表が現物と合っていない（README 3357-3514）。** `src/ccnavi/entry/lint_rules.py` は存在しない（表の 3464 行と `lint.py` の説明が参照）。表に載っていない `.py` が 45 本ある（例: `entry/suggest.py`・`version.py`・`status.py`・`cli_handoff.py`・`cli_learnings.py`・`cli_stopreview.py`・`lint_wiki.py`、`hook/stopjudge.py`・`failsafe.py`・`wrapguard.py`、`infra/pathcase.py`、`policy/subjects.py`・`rule_lint.py`、`records/` の `redact.py`・`prune.py`・`repeat.py`・`stopeval.py` ほか、`tickets/` の `handoff.py`・`configsync.py`・`user_only.py`・`via.py`・`workflow.py` ほか）。`.ccnavi/scripts/` も表に無い sh がある（`ccnavi-common-*.sh`、`ccnavi-user-clean-branch.sh`・`ccnavi-user-close-early.sh`・`ccnavi-user-decide.sh`・`ccnavi-user-lib-review.sh`）。一方、`tickets/handoff.py` は 3417 行の説明文の中でしか触れていない。
4. **承認の JSON `--yes` の答えに `written` が無い。** README 2853-2860 の表は `approved[]`/`copies[]`/`lines[]`/`prompt`/`mismatch`/`partial` を挙げるが、`docs/requirements/extension-if.md` REQ-EXT-14 は `written`（この承認で書いた・消したパス）と `partial.written` を返すと書き、`src/ccnavi/tickets/core_approve.py` L232, L447, L479 も返す。`docs/design/tickets/approval.md` L133 は `written` のパスを使うと書いている。README の表に欄が欠けている。
5. **`--version` の鍵の列挙が揃っていない。** README の鍵の表（2930-2939）は `features` を含むが、REQ-EXT-04 は `schema`・`version`・`commit`・`built`・`compat`・`flags`・`formats` で止まり `features` が無い。README の JSON 例（2925-2927）も `features` を載せていない。
6. **README の参照先。** `docs/requirements/extension-if.md`（L16, L46, 末尾の注）、`docs/design/diagnostics.md`（「JSON の形は README の…に定める」）ほか 10 本ほどが `README.md#版の-json`・`#承認の-json`・`#残った指摘の-json`・`#ボードの-json` などのアンカーで README を指す。節を移したときにこのリンクを張り替える（`grep -rn "README.md#" docs`）。食い違いではないが、移動の作業で漏れやすい。

数値が一致して問題の無かったもの: `CCNAVI_LEARNING_SOURCES` 既定 3、`CCNAVI_LEARNING_NOTICE_COUNT` 既定 5、Stop の振り返り 10 回に 1 度（`.ccnavi/config/common/rules.yml` の `every: 10`）、frontmatter 64 KiB/4 KiB と索引 16 MiB（`docsearch_index.py`）、`START_SECONDS` 3 秒・`MIN_REFRESH_SECONDS` 0.5 秒（`docsearch.py`）、`ccnavi-git.sh` の 40 行・50 本（`CCNAVI_GIT_MAX_LINES`・`CCNAVI_GIT_KEEP_LOGS` の既定）、残った指摘の `version` 2 と他の JSON の `version` 1（コードの定数と一致）。

### 判断に迷った点

1. **JSON の形の置き場。** 目安は `docs/requirements/extension-if.md` だが、このファイルは REQ-ID の表（要求）の体裁で、鍵ごとの説明の表を足すと性格が変わる。実際、extension-if.md 自身が「JSON の欄の細かい形は README の各 JSON の節と設計 10 に置き、ここからはそこを指す」と書いている。案は 2 つ。(a) extension-if.md に「付録: JSON の形」を足す（目安どおり）。(b) `docs/design/diagnostics.md`（設計 10 章）の下に `docs/design/json-shapes.md` を新設して欄の表を置き、extension-if.md はそこを指す。どちらでも表の中身は同じ。ここでは目安に従い (a) を第一案としたが、(b) の方が REQ の体裁を崩さない。
2. **テンプレートの JSON の鍵の表（3068-3106）。** `docs/design/tickets/templates.md` が鍵の名前を持っているため、そこに表を足す手もある。画面（拡張）への約束なので extension-if に寄せたが、判断は分かれる。
3. **「構成」を `docs/development.md` と `docs/design/` のどちらに置くか。** 158 行の表のうち、サブパッケージの層の説明（3362 行）とトップレベルの置き場（`main.py`・`build.py`・`scripts/`・`tests/`・`docs/adr/`）だけ `docs/development.md` に残し、モジュールごとの行は `docs/design/modules.md` に分けるのが読みやすいと考えた。現状の表は古い（食い違い 3）ので、手で移すより `find src` で作り直すことを勧める。
4. **エージェント向けの文書が足りるか。** 「生の git」と「ドキュメントの索引」は、エージェントが実際に使う内容だが、`.ccnavi/docs/` に `ccnavi-git.sh` の通す形を書いた文書は見当たらなかった（`--help` が出すとの記述 3315）。`.ccnavi/CCNAVI.md` がどこまで渡しているかは確認していない（担当外）。`.ccnavi/docs/` に足すか、`--help` に任せるかは part の全体で決めてほしい。
5. **学びの蓄積の 4 区分表（3165-3170）。** 利用者向けの概要として guide/records.md に残す価値があるが、一部（`Stop` のルール、`ticket finish` が拒む等）は実装の事実で、guide の「内部構造を書かない」方針とぶつかる。「条件」の列は設計側へ移し、guide には「誰がするか」の列だけ残す案にした。
6. **テンプレートの節を 4 行に分けたか。** `##` 見出しが 2960-3159 の 200 行で、導入部（74 行）に利用者・開発者・エージェント向けが混ざる。導入部に見出しは無いので 1 行として扱い、境界は行番号で書いた（利用者 2962-2979, 2998-3024／開発者 2981-2996, 3026-3032）。

# 付録 B. 既存文書の分類


調べ方: 見出し・description を全件、疑わしい節だけ本文。ファイルは変更していない。
注意: 現状 `docs/` には利用者向け(人間)の文書が無い。利用者向けの中身は今は 383KB のルート README.md にある（本調査の対象外）。

### 1. 判定表

| パス | 読み手 | 根拠 |
|---|---|---|
| CLAUDE.md | 開発者(+開発エージェント) | ccnavi 開発の常時ルール。frontmatter 無し |
| .ccnavi/CCNAVI.md | エージェント | セッション開始に渡す考え方と表。配布先にも配る |
| .ccnavi/docs/ccnavi-md.md | エージェント(混在) | 書き方は配布先向け。「判定の仕組み」「壊れたときと lint」は実装寄り（2 節参照） |
| .ccnavi/docs/decisions.md | エージェント | 提案・承認・役割分担の手順 |
| .ccnavi/docs/direct-flow.md | エージェント | 直接作業の手順 |
| .ccnavi/docs/frontmatter.md | エージェント(混在) | 書き方は配布先向け。「索引のファイル」「プロジェクト」に実装挙動（2 節参照） |
| .ccnavi/docs/grow.md | エージェント | 入口 |
| .ccnavi/docs/grow-phases.md | エージェント(混在) | 流れは配布先向け。「次に読む」が docs/design・docs/adr を指す（2 節参照） |
| .ccnavi/docs/grow-process-skills.md | エージェント | 工程スキルが育つ流れ |
| .ccnavi/docs/grow-risks.md | エージェント(混在) | 同上（docs/design/tickets/risk.md, ADR-0027 へ） |
| .ccnavi/docs/grow-rules.md | エージェント(混在) | 同上（docs/design/rules.md, ADR-0107、本文にも ADR-0107） |
| .ccnavi/docs/grow-templates.md | エージェント(混在) | 同上（docs/design/tickets/templates.md） |
| .ccnavi/docs/learning-propose.md | エージェント | 提案チケットの作り方 |
| .ccnavi/docs/projects.md | エージェント | プロジェクトの置き方・置き場 |
| .ccnavi/docs/scratchpad.md | エージェント(軽い混在) | 置き場の使い分け。「テストのフィクスチャを手で組むとき」(l.43) だけ開発寄り |
| .ccnavi/docs/skill-processes.md | エージェント | 工程スキルと reference の書き方 |
| .ccnavi/docs/skill-review.md | エージェント | 振り返りで拾うもの |
| .ccnavi/docs/skill-wiki.md | エージェント | wiki 層の使い方 |
| .ccnavi/docs/templates.md | エージェント | テンプレートの溜め方・使い方 |
| .ccnavi/docs/terms.md | エージェント | 用語 |
| .ccnavi/docs/ticket-flow.md | エージェント(混在) | 手順表は配布先向け。「更新したときの変更点」は変更履歴、「詰まったとき」に内部状態の話（2 節参照） |
| .ccnavi/docs/ticket-work.md | エージェント | 作業の分け方 |
| .ccnavi/docs/worktree.md | エージェント | ワークツリー手順 |
| .ccnavi/docs/worktree-cleanup.md | エージェント | 片付け手順 |
| .ccnavi/docs/worktree-merge.md | エージェント | 取り込み手順 |
| .ccnavi/docs/worktree-start.md | エージェント(軽い混在) | 着手手順。l.34-36 の `branch_to_id` 三実装と `tests/fixtures` が開発者向け |
| docs/design/** | ディレクトリ単位で開発者 | 実装の構成の説明（type: design）。例外なし |
| docs/design.md | 開発者 | 同上の入口 |
| docs/requirements/** | ディレクトリ単位で開発者 | 外から観測できる約束だが ccnavi 開発の仕様書。例外なし（利用者の入口は guide 側） |
| docs/requirements.md | 開発者 | 同上の入口 |
| docs/adr/** (0001-0159, README.md) | ディレクトリ単位で開発者 | 設計判断の記録。例外なし |
| docs/CONTEXT.md | 開発者 | 用語集（実装・内部概念を含む）。利用者向け用語は guide に別に起こす |
| docs/claude/** (6 本: acceptance-scenarios, environment, exe-boundary, frontmatter, logging, script-output, testing) | ディレクトリ単位で開発者 | CLAUDE.md の「詳細」表が指す開発規約。例外なし |
| extensions/chrome/ccnavi-approval/README.md | 開発者(+導入する人) | 全体が「組み立て」「試験」。利用者向けの使い方は無い |
| extensions/vscode/ccnavi-board/README.md | 混在 | 必要なもの(l.9)は利用者/導入者、組み立てと導入(l.34)は導入者+開発者、拡張開発ホスト(l.79)・手動確認の手順(l.87-270, 約 180 行)は開発者。2 節参照 |

参考(対象外だが同類): extensions/**/docs/**、chrome の VERIFY.md は全て開発者向け。

### 2. 混在文書と分け方

### 今回の範囲で直す(推奨)

| 文書・箇所 | 混在の中身 | 案 |
|---|---|---|
| .ccnavi/docs/grow-{phases,risks,rules,templates}.md「次に読む」(phases l.35-, risks l.42-, rules l.36-, templates 末尾) と本文の ADR 番号 (grow-phases l.30, grow-rules l.34) | docs/design・docs/adr・.claude/skills への参照。配布先にこれらは無く、リンク切れになる | 配布先で意味を持つ行（スキル references、.ccnavi/docs）だけ残し、docs/design・docs/adr の行と本文の `ADR-xxxx` は消すか `ccnavi:if` で ccnavi 開発リポジトリ限定にする。理由は開発者向け側（docs/development.md か該当 design）が持つ。CLAUDE.md の規約（文書で ADR に触れたくなったら docs/claude/environment.md）に当たるので先に環境文書を読む |
| .ccnavi/docs/worktree-start.md l.34-36 | `ccnavi_branch_to_id` / `ticket_ids.branch_to_id` / `branchToId` / `tests/fixtures/branch-to-id.json` | 「識別子はブランチ名の `/` を `-` に置き換えたもの」までを残し、三実装と fixture の一文は docs/design/tickets/branch-lookup.md へ移す |
| .ccnavi/docs/scratchpad.md l.43「テストのフィクスチャを手で組むとき」 | テスト開発者向けの注意（`builtin:guard-setting-files` が止める） | docs/claude/testing.md か docs/development.md へ移す。ただしエージェントが実際に踏む罠なので、1 行の要約と参照を残してもよい |
| .ccnavi/docs/ticket-flow.md「更新したときの変更点」(l.133-) | 互換の版 18・GHE.com 分類・Chrome 拡張の変更など、リリースノート | 配布先のエージェントに毎回要る内容ではない。リリースノート置き場（docs/development.md の更新履歴節、または別ファイル）へ移す。移すと CCNAVI.md 表・他文書からの参照有無を確認（今回の grep では ticket-flow 内のみ） |
| .ccnavi/docs/ticket-flow.md「詰まったとき」l.125 付近 | `rules.yml` を `{root}` 無しで読み直す等、実装挙動の注記 | 振る舞いとして必要な部分だけ残し、実装理由は docs/design/tickets に移す（軽微。別作業でも可） |
| extensions/vscode/ccnavi-board/README.md l.79-270 | 拡張開発ホスト・手動確認手順は開発者向け、l.9-33 は利用者向け | 利用者向けの「必要なもの・導入」だけ README に残し、l.79-270 は extensions/vscode/ccnavi-board/docs/ 配下（例 docs/verify.md。chrome の VERIFY.md と同じ扱い）へ。README は frontmatter 無しのまま |

### 別作業に回す

| 文書・箇所 | 理由 |
|---|---|
| .ccnavi/docs/ccnavi-md.md「判定の仕組み」(l.81-90)「壊れたときと lint」(l.91-) | 関数単位の判定や lint の出し方で実装寄り。ただし「ccnavi:if の名前が真にならない時の見え方」は書き手に要る。節の線引きに判断が要るので、docs/design（startup-config.md など）へ移す別チケットにする |
| .ccnavi/docs/frontmatter.md「プロジェクト」「索引のファイル」(l.59-77) | `ccnavi-index.jsonl` の生成・.gitignore 追記・打ち切りなど内部形式。エージェントが「手で直さない」と知る 1〜2 行は残し、挙動詳細は docs/design/ へ。docs/claude/frontmatter.md の対象外表・テストと絡むので別作業 |
| extensions/chrome/ccnavi-approval/README.md | 全体が開発者向け。移す先（docs/development.md への案内を足す）程度で、今回は触らなくてよい |
| docs/design/**, docs/requirements/** | 読み手は揃っているので移動不要。ただし docs/requirements.md を「利用者向け要件」と誤読されないよう、docs/development.md の目次で開発者向けと明記する |

CLAUDE.md の追記案: 「詳細」表に docs/development.md の行（開発者向けの入口）を 1 行足す。利用者向けは docs/guide/ と書くが、新しい決まりは先に docs 側に書く方針（CLAUDE.md の段階的詳細化）に従うこと。

### 3. frontmatter の type の整合

現状(docs/claude/frontmatter.md l.28-39 の表):
- `guide` = 「使い方と作業の手引き。`.ccnavi/docs/*.md`、`docs/claude/*.md`、`docs/adr/README.md`」
- `design` / `requirements` / `glossary` / `adr` / `skill*` / `learning-wiki` / `report` が他にある。

判定:
- docs/guide/*.md に `type: guide` は表の意味(使い方の手引き)に合う。ただし表の「対象」に docs/guide/ が無いので追記が要る。
- docs/development.md も、docs/claude/*.md が開発者向けで `guide` なのと同じ扱いで `type: guide` にできる。新しい type 値(例 `development`)は増やさない(.ccnavi/docs/frontmatter.md の「同じ意味で表記だけ違う値を増やさない」に反する)。読み手の区別は `tags` で行う（下記）。
- README.md(ルート)と extensions/**/README.md は付けない決まりのまま。docs/guide/ 内に `README.md` を置くと `NO_FRONTMATTER_NAMES` により frontmatter 無し扱いになり索引に type が載らない。入口は `docs/guide/index.md` にして frontmatter を付けるか、README.md にして無しで運用するか、決めておく。

追記案(`docs/claude/frontmatter.md` の guide 行の差し替え):

```
| `guide` | 使い方と作業の手引き。`.ccnavi/docs/*.md`、`docs/guide/*.md`（利用者向け）、`docs/development.md`、`docs/claude/*.md`、`docs/adr/README.md` | 付いている。ルートの`README.md`と拡張の`README.md`（`extensions/**`）には付けていない（理由は表の下） |
```

読み手を区別したいなら tags の表に 1 行（任意。`--tag` で絞れる）:

```
| `user-guide` | 利用者（人間）向けの文書。`docs/guide/**` | 開発者向け・エージェント向けには付けない |
```

同じ文面を docs/claude/frontmatter.md の tags 表に足し、docs/guide/*.md の frontmatter は `type: guide` + `tags: [user-guide, ...]` にする。tag を足さないなら tags 表の追記は不要。

テスト・実行ファイルへの影響(src/ccnavi/hook/docsearch.py と tests/core/test_docsearch.py を確認):
- type の許可リストは無い（`.ccnavi/docs/frontmatter.md`「値はプロジェクトで決める」、docsearch は完全一致で絞るだけ）。docs/guide/ と docs/development.md を新設しても壊れない。
- `NO_FRONTMATTER_NAMES = ("README.md", "CLAUDE.md")` と `NO_FRONTMATTER_PLACES`（wip/proposals, .ccnavi/approved, wip/design/scripts/i*.md, wip/handoff, scratchpad, .claude/skills/yomiyasu）だけが対象外。docs/guide/** と docs/development.md は対象外に入らないので、frontmatter 無しで Write すると `NOTICE_DOC_FRONTMATTER` が 1 度出る(必ず付けること)。
- tests/core/test_docsearch.py l.916-: 「対象外」の表と `NO_FRONTMATTER_PLACES` の一致だけを検査。docs/claude/frontmatter.md の type 表は検査されていない。対象外の置き場を足さない限りテスト変更は不要。
- tests/core/test_adr_numbers.py は docs/adr/ のみ読む。docs/ 直下や docs/guide は見ない。
- 他に docs/guide や docs/development.md を名指しするテスト・実行ファイルは無い(grep 済み)。
- 拡張の README.md は frontmatter を付けないまま。VERIFY.md に移した文書は拡張内 docs/ のため `ccnavi --docs` の索引に載るので frontmatter(type: guide)を付ける。

# 付録 C. 参照の影響


調べた範囲: /home/user/ccnavi（node_modules・.git・.claude/worktrees・dist・build 除く）。README を含むヒット 355 行を全件分類した。生の一覧は scratchpad/readme-hits.txt。
README の ## 見出しは 33 本、### 以下を含めて約 100 本（行番号は 3522 行版）。

### 0. 結論（先に）

- **テストは README.md の中身を照合していない**。実ルートの README.md を読むテストは 0 件（`tests/core/test_adr_numbers.py:103` が読むのは `docs/adr/README.md`）。分割でテストは壊れない。
- **ツール（docsearch・frontmatter 検査・lint）はルート README.md を名前で除外しているだけ**（`src/ccnavi/hook/docsearch.py:419` `NO_FRONTMATTER_NAMES = ("README.md", "CLAUDE.md")`）。分割後の新文書 `docs/guide/*.md`・`docs/development.md` は README という名前ではないので、**frontmatter を付けないと Write の新規作成で `NOTICE_DOC_FRONTMATTER` が出る**。付ける type の値と置き場の扱いを `docs/claude/frontmatter.md` に足す必要がある（5 参照）。
- **直す必要があるのは文面の参照**。数は、実行時に利用者へ出る文面（`cli_usage.py` の `--help`、`ccnavi-clean.js`/`.sh`）が 8 箇所、コメント・docstring が約 45 箇所、docs が約 25 箇所。ADR 内に README の節を指す参照が数件ある（直せない。6 参照）。
- **壊れるリンクは 7 箇所**: `docs/requirements/extension-if.md` の 6 つの `README.md#アンカー`、`docs/design.md:17` の `README.md#開発`。
- README 自身から出る相対リンク 33 本は、移す先のファイルの深さに合わせて `../` を足す必要がある（`docs/guide/x.md` からは `../CONTEXT.md` など）。

### 想定する節の移り先（この文書の表で使う仮の対応）

| 旧 README の節（行） | 新ファイル（案） |
|---|---|
| コア機能・設計原則・仕組み(7-62) | README.md に残す（概要） |
| 導入(63-161) | docs/guide/install.md |
| 開発(162-265)・実測で分かった落とし穴(266-304)・構成(3357-3514)・配布物の条件(3515-) | docs/development.md |
| 設定(305-596)・設定の検証(2524-2665)・動作モード(1365) | docs/guide/settings.md |
| ルール(597-1010)・Bash(1056-1300)・パス(1301)・探すツール(1317)・ツール実行後チェック(1377-1519)・ルールファイルが読めないとき(2282)・ルールが何に当たるかを確かめる(2374-2523)・生の git(3308-3356) | docs/guide/rules.md |
| チケット(1520-2281) | docs/guide/tickets.md |
| 記録(2295-2373)・学びの蓄積(3160)・ドキュメントの索引(3185-3307) | docs/guide/records.md |
| VS Code から見えるように(1011-1055)・各 JSON の節(2486-2523 の試験、2666-2959 の lint/ボード/承認/残った指摘/版、候補の JSON 2474)・テンプレート(2960-3159) | docs/guide/extensions.md（JSON の形を別ファイルにするなら docs/guide/json.md を足す。アンカーが多数の外部参照を持つため） |
| 落とし穴の一部・トラブル | docs/guide/troubleshooting.md |

### 1. README を参照している箇所

分類は 4 つ: A=ルート README.md の節名/アンカー指定あり（要修正）、B=ルート README 全体を指す、C=extensions など別の README、D=無関係（ファイル名として README.md を使うだけ）。

### 1-1. A: 節名・アンカー指定あり（要修正）

実行時に利用者へ出る文面（優先して直す）

| パス:行 | 文面（抜粋） | 指している節 → 移り先 |
|---|---|---|
| src/ccnavi/entry/cli_usage.py:20 | README.md（「版の JSON」） | 版の JSON → extensions/json |
| src/ccnavi/entry/cli_usage.py:29 | README.md（「lint の JSON」） | lint の JSON |
| src/ccnavi/entry/cli_usage.py:68 | README.md（「試験の JSON」） | 試験の JSON |
| src/ccnavi/entry/cli_usage.py:88 | README.md（「ボードの JSON」） | ボードの JSON |
| src/ccnavi/entry/cli_usage.py:116 | README.md（「ドキュメントの索引」） | → records.md |
| src/ccnavi/entry/cli_usage.py:138 | README.md（「作業・レビュー計画のテンプレート」） | → extensions.md か tickets.md |
| src/ccnavi/entry/cli_usage.py:219 | README.md（「候補の JSON」） | 候補の JSON |
| .ccnavi/scripts/ccnavi-clean.js:102 | README.md の「実測で分かった落とし穴」（stderr に出る） | → docs/development.md |
| .ccnavi/scripts/ccnavi-clean.sh:270 | 同じ文面の printf（js と同文） | 同上 |

注意: `cli_usage.py` の `--help` の文面は固定文字列で、`tests/` が一部を照合している可能性がある。文面を変えるときは `grep -rn "「版の JSON」\|README.md（" tests` で確かめること（今回の grep では `cli_usage.py` の README 文面を直接照合するテストは見つからなかった）。

src/ のコメント・docstring（実行時には出ない。直し漏れても動くが、嘘になる）

| パス:行 | 節名 |
|---|---|
| src/ccnavi/entry/lint.py:175 / :320 | lint の JSON |
| src/ccnavi/entry/diagnose_try.py:103 | 試験の JSON |
| src/ccnavi/entry/diagnose_board.py:46 | ボードの JSON |
| src/ccnavi/entry/suggest.py:445 | 候補の JSON |
| src/ccnavi/entry/lint_project.py:159 | 「README の」で文が切れている（`CCNAVI_BIN_PATH` の話。設定の表と思われる。要確認） |
| src/ccnavi/entry/cli_args.py:358 | 「README の設定の表」 → settings.md |
| src/ccnavi/tickets/agree.py:349 / core_approve.py:258, :429 / tests/ticket/test_approve_json.py:864 | 承認の JSON（`partial`） |
| src/ccnavi/infra/gitstate.py:30 | 「この限界は README に書いてある」（節名なし。どの節かは要確認） |

scripts / tests / config のコメント

| パス:行 | 節名 |
|---|---|
| scripts/ccnavi-setup.sh:58 | 設定の検証 |
| scripts/ccnavi-setup.sh:68 | ワークツリーを VS Code から見えるようにする |
| scripts/ccnavi-setup.sh:78 | 設定（の表） |
| scripts/ccnavi-setup.sh:168 | 「README ではなく設定ファイルの中で」（節名なし。直さなくてよい） |
| scripts/ccnavi-setup.sh:951 | ドキュメントの索引 |
| tests/sh/test_setup.py:66 | 設定の表 |
| tests/sh/test_setup.py:90 | 設定の見本（hook の 1 行） |
| tests/sh/test_setup.py:259 | ドキュメントの索引 |
| tests/sh/test_setup.py:410 | テスト名 `..._from_the_readme`（名前だけ。中身は定数 HOOK_COMMAND と比べるだけ） |
| tests/sh/test_setup.py:2409 | ワークツリーを VS Code から見えるようにする |
| tests/config/test_config_union.py:1208 | ボードの JSON |
| tests/core/test_lint.py:709 / tests/ticket/test_flow_lint.py:8 | lint の JSON |
| tests/guard/test_repo_rules.py:14 | 試験の JSON |
| .ccnavi/config/common/rule-expectations.yml:417 | 「Bash のコマンドは実行される部分だけを見る」 |
| .ccnavi/config/common/rule-expectations.yml:431 | 「読み切れないとき」 |

extensions/（コードのコメント。`ccnavi の README「…」` の形）

| パス:行 | 節名 |
|---|---|
| extensions/vscode/ccnavi-board/src/ccnavi.ts:518 | 候補の JSON |
| extensions/vscode/ccnavi-board/src/ccnavi.ts:562 / core/commands.ts:288 / core/lintmodel.ts:3 | lint の JSON |
| extensions/vscode/ccnavi-board/src/core/approvemodel.ts:2, :54, :211 | 承認の JSON（`partial`） |
| extensions/vscode/ccnavi-board/src/core/testmodel.ts:3 | 試験の JSON |
| extensions/vscode/ccnavi-board/src/core/suggestmodel.ts:3 | 候補の JSON |
| extensions/vscode/ccnavi-board/src/core/version.ts:3 | 版の JSON |
| extensions/vscode/ccnavi-board/src/core/decidemodel.ts:2 | 残った指摘の JSON |
| extensions/vscode/ccnavi-board/src/core/board.ts:151 | ボードの JSON |
| extensions/vscode/ccnavi-board/test/helpers/phases.ts:19 | フェーズ定義と計画 |
| extensions/vscode/ccnavi-board/docs/requirements/rules.md:13 | ルールは 3 つのレイヤーの和で当たる |
| extensions/vscode/ccnavi-board/docs/requirements.md:34 | ボードの JSON・試験の JSON |
| extensions/vscode/ccnavi-board/docs/requirements/board.md:104 | ボードの JSON |
| extensions/chrome/ccnavi-approval/README.md:8 | 複数人・別 PC・別セッションで進める |

docs/（ADR 以外）

| パス:行 | 節名 / アンカー |
|---|---|
| docs/requirements/extension-if.md:21 | 「README の各 JSON の節」 |
| docs/requirements/extension-if.md:47 | [版の JSON](../../README.md#版の-json) **リンク** |
| docs/requirements/extension-if.md:76-78 | ボードの JSON / 承認の JSON / 残った指摘の JSON / 試験の JSON / lint の JSON / 候補の JSON の 6 リンク（`../../README.md#…`） |
| docs/requirements/extension-if.md:123 | [承認の JSON](../../README.md#承認の-json) リンク |
| docs/design.md:16 | `[README.md](../README.md)`（使い方と設定、ルール、記録）→ 表の行を docs/guide/ へ向ける |
| docs/design.md:17 | `[README.md](../README.md#開発)` リンク → docs/development.md |
| docs/design/startup-config.md:55 | 「README「設定」」 |
| docs/design/diagnostics.md:24 | 版の JSON |
| docs/design/diagnostics.md:26 | ドキュメントの索引 |
| docs/design/diagnostics.md:54 | 試験/lint/ボード/候補の JSON |
| docs/design/diagnostics.md:74 | 残った指摘の JSON |
| docs/design/multi-repo/standalone-clone-gaps.md:225 | 「README の「プロジェクトの中で起動すると hook が動かない」」（見出しではなく README:447 の段落。新ファイルで探す） |
| docs/design/multi-repo/standalone-clone-gaps.md:61 | 「`to-measure.md`、README」（節名なし） |
| docs/design/tickets/sharing.md:14 | 複数人・別 PC・別セッションで進める |
| docs/design/tickets/proposal-format.md:250 | lint の JSON |
| docs/design/tickets/approval.md:88 | 承認の JSON |
| docs/design/tickets/templates.md:74 | 「答えの形は README の」（行が切れている。次行を確認） |
| docs/design/appendix.md:46 / docs/design/rules.md:223 / docs/design/stop-review.md:31 / docs/design/multi-repo.md:36, out-of-scope.md:19 | 節名なし（README 全体 or 語の説明） |
| docs/requirements.md:20, :21 | 「挙動は README と設計に書く」（B。節名なし） |
| .ccnavi/docs/frontmatter.md:53,55,89 / docs/claude/frontmatter.md:30,45,47 | README.md という名前の扱い（5 参照） |
| .claude/skills/commit/SKILL.md:82 | `docs` の prefix の対象に `README.md` を列挙。**`docs/guide/`・`docs/development.md` は `docs/` にあたる `docs/requirements.md` などの列挙に入っていない**（`docs/design.md`・`docs/CONTEXT.md` などを列挙）ので、足すか「docs/ 全体」と書く |
| .ccnavi/config/project/phases.yml:38 | `scope: ["README.md", "docs/*", ...]`（`docs/*` は glob の `*` が `/` をまたぐかで `docs/guide/x.md` に当たるか要確認） |

### 1-2. B: README 全体を指す（節名なし）

- `src/ccnavi/infra/gitstate.py:30`（「この限界は README に書いてある」）
- `README.md:60`（docs/adr/README.md へのリンク。分割後も有効）
- `docs/requirements.md:20,21`（上記）
- `extensions/vscode/ccnavi-board/docs/design.md:11`、`requirements.md:11`、`extensions/chrome/ccnavi-approval/docs/design.md:11`、`requirements.md:11` は `[README](../README.md)` で**拡張側の README**（C）

### 1-3. C: extensions の README など別ファイル（分割の対象外）

- `extensions/vscode/ccnavi-board/src/*-panel.ts` 冒頭（rules/flow/phases/workflow/templates/handoff/projects/risk/board）、`board-panel.ts:788`、`test/board/card.dom.test.ts:4`、`test/flow/flow*.dom.test.ts`、`docs/design/screens.md:64`（README の 6g）、`docs/design/structure.md:273,281`、`test/shared/test-ids.test.ts:4`: すべて拡張の README の手動確認の表。
- `extensions/chrome/ccnavi-approval/VERIFY.md:275`、`tests/sh/test_review_host_fixture.py:17`: 拡張の README の「ホストの応答の見本」。
- `README.md:1673`（`extensions/chrome/ccnavi-approval/README.md` へのパス。チケット/拡張の節ごと移すならパスは同じで変わらない）。
- `README.md:1037`: 「ccnavi-user-close-early.sh を打つ。組み立て方と使い方はそこの README」（拡張の README）。
- `docs/claude/acceptance-scenarios.md:33,44,95`: `tests/scenarios/README.md`。
- `docs/adr/README.md`: ADR の一覧（`tests/core/test_adr_numbers.py` が読む。分割と無関係）。

### 1-4. D: 無関係（ファイル名として README.md を使うだけ）

tests/ 配下の大半（`test_push_approved_sh.py`・`test_sync_sh.py`・`test_fetch_sh.py`・`test_repo_rules.py` の `grep ... README.md` の引数・`test_root_placeholder.py`・`test_not_root_placeholder.py`・`test_rule_except.py`・`test_rule_when.py`・`test_tree_case.py`・`test_shellread.py`・`test_fallback.py`・`test_internal_error.py`・`test_named_guards*.py`・`test_acceptance.py`・`test_ticket.py` の allow=("README.md", …)・`test_session_conventions.py`・`test_ext_tests.py` ほか、tests/scenarios/UC-*.yml、tests/config/test_*.py）。
どれもテスト用の一時ワークスペースに README.md を自分で書くか、パスの文字列として使うだけで、実ルートの README.md は読まない。ルート README.md を消さない限り影響しない（README.md は概要として残す案なので問題なし）。
`.ccnavi/config/common/rule-expectations.yml:31,341,373,413,427,442`（`/repo/README.md` はテスト用の仮の被写体）も同じ。`README.md` 内の例（1062, 1097, 1099, 1145, 1298 行）の `grep -n "git push" README.md` は例文で、**こうした例文を移した先でも「ファイルが README.md」のまま動く**が、`tests/guard/test_repo_rules.py` などが同じ例文と照合するのは README ではなく rule-expectations.yml 側。

要確認: `tests/scenarios/UC-06-undeclared-handover.yml:14`「README.md を読んで、最初の見出しを教えて」は `projects/test-repo` の README（ccnavi ルートの README ではない）。

### 2. README 内の相互参照（`grep -n '節\|](#' README.md` ＋「下の/上の/次の「…」」）

分割で**別ファイルに分かれる参照**（◎は要修正）を先に。

| 行 | 参照元の節 | 参照先 | 分割後 |
|---|---|---|---|
| 40 | コア機能を支える仕組み | 「複数人・別 PC・別セッションで進める」（チケットの節） | ◎ README → tickets.md |
| 70 | 導入/1 | 「設定」の節 | ◎ install → settings |
| 71 | 導入/1 | 「設定」の表 | ◎ 同上 |
| 82 | 導入/1 | 「ルールファイルが読めないとき」の節 | ◎ install → rules |
| 103 | 導入/3 | 「記録」の節 | ◎ install → records |
| 144 | 導入/4 | 「期待する判定で確かめる」の節 | ◎ install → rules |
| 146 | 導入/4 | 「記録から候補を起こす」の節 | ◎ install → rules |
| 159 | 導入/5 | 「動作モード」の節 | ◎ install → settings |
| 313 | 設定 | 次の節（ワークツリーを VS Code から見えるようにする） | ◎ settings → extensions（隣でなくなる） |
| 321 | 設定 | 「動作モード」の節 | 同じ settings.md なら維持 |
| 332 | 設定 | 「置き場は固定」の段落（435 行の太字） | 同じ節内。維持 |
| 531 | 設定 | 「まだ無いもの」 | 同節内。維持 |
| 802 | ルール/ターンの終わりに止めて渡す | 「ターンの終わりの報告」（ツール実行後チェックの ### 1414） | ◎ rules 内なら維持（同じ rules.md に置く前提） |
| 857 | ルール/強さ | 次の節（ルールが言及していない呼び出し） | rules 内。維持 |
| 906 | ルール | 上の「ターンの終わりに…」 | rules 内。維持 |
| 1035 | VS Code から見えるように | 下の「承認の JSON」 | ◎ extensions → JSON の節 |
| 1036 | 同上 | 下の「残った指摘の JSON」 | ◎ 同上 |
| 1038 | 同上 | 下の「ボードの JSON」 | ◎ 同上 |
| 1049 | 同上 | 「フェーズ定義と計画」の節 | ◎ extensions → tickets |
| 1083 / 1140 | Bash | 下の「書き直しを求める形は止めて案内する」 | rules 内。維持 |
| 1252 | Bash | 上の「コマンド名はそのまま書く」 | rules 内。維持 |
| 1686 | チケット/効くのは承認したものだけ | 次の節（親のブランチが消えているのに作り直す形を止める。見出し順では「判定の鍵はファイルの行き先」） | tickets 内。維持（次の節の実体は要確認） |
| 2106 | チケット/レビューの依頼と確認 | `[実測で分かった落とし穴](#実測で分かった落とし穴)` **リンク** | ◎ tickets → development.md#実測で分かった落とし穴（README で唯一の `](#` 形式） |
| 2133 / 2169 | チケット | 下の「MR がマージされたあと」（見出しではなく太字段落の可能性。2105 付近で要確認） | tickets 内。維持 |
| 708 | ルール | 「この節」 | 自己参照。維持 |
| 804 / 2433 / 2435 / 3208 / 3478 | — | 「節」という語が別の意味（返答の節、`--help` の話題ごとの節、`--explain` の出力の節） | 参照ではない |

README から外へのリンク（相対パス 33 本。移し先の深さで `../` を足す）: docs/CONTEXT.md・docs/requirements.md・docs/design.md・docs/adr/README.md（59-60、概要に残る）、`.ccnavi/docs/ccnavi-md.md`(423)、`.ccnavi/docs/frontmatter.md`(3211)、`tests/fixtures/templates-json.json`(3068)、`docs/design/**`・`docs/requirements/**` の約 25 本(473-2339)。`docs/guide/*.md` から見ると全て `../../` が要る（`docs/design/…` は `../design/…`）。

### 3. テスト・ツールが README の中身と照合していないか

- tests/ と src/ で、実ルートの README.md を `open`/`read_text`/パースするコードを探した（`README`・`readme`・`open`・`read_text`・`ROOT` の組み合わせ）。**見つかったのは次の 1 つだけ**:
  - `tests/core/test_adr_numbers.py:103` `read("README.md")` は `ADR_DIR`（`docs/adr/`）の README。ADR 一覧と置いてある ADR の突き合わせ。ルート README の分割とは無関係。
- `tests/sh/test_setup.py` の「README の設定の表と対になる」という docstring/コメント（66, 90, 410, 2409）は、**README を読んでいない**。`EVENTS`・`HOOK_COMMAND` などの定数をテスト内に持っていて、README の表と手で揃えているだけ。分割しても壊れないが、設定の表を `docs/guide/settings.md` へ移したら、これらのコメントの節名と「対になる」先を直す。
- `tests/core/test_docsearch.py`: 索引の ID や `README.md` を fixture として作る（103, 376-495, 862, 899-900）。`NO_FRONTMATTER_NAMES` と `NO_FRONTMATTER_PLACES` を表（`.ccnavi/docs/frontmatter.md` の「対象外」の表）と比べるテストがある（`docs/claude/frontmatter.md` 末尾が明言）。ルート README を直接開いてはいない。**docs/guide/ を「付けない置き場」にするなら（しない案を推す）、表と `NO_FRONTMATTER_PLACES` と テストの 3 点を同時に直す**。
- 索引（docsearch）: `docsearch.py:419` が README.md を frontmatter 不要の名前にしているだけ。ルート README.md は名前で除外、`docs/guide/*.md` は通常の md として索引に載り、`type` の無いものは `NOTICE_DOC_FRONTMATTER` の対象になる。frontmatter 検査（`--lint` を含む）が README.md だけを対象にしている箇所は見つからなかった。
- CI（`.github/`）・`pyproject.toml`・`build.py`・`scripts/`: README を参照するもの 0 件（`scripts/ccnavi-setup.sh` のコメントを除く）。拡張の `scripts/package.sh:20` は拡張自身の README の相対リンクの話。
- phases.yml の `scope: ["README.md", "docs/*", …]` は書き込み範囲。`docs/guide/` の追加も `docs/*` に入るか（`*` が `/` をまたぐかは ccnavi の glob の定義による。`tests/core/test_glob.py` で確かめること）。

### 4. 拒否の文面・出力が README の節名を案内していないか

src/ccnavi/、.ccnavi/scripts/、scripts/、.ccnavi/config/**/rules.yml の message、.ccnavi/CCNAVI.md、.ccnavi/docs/ を grep した結果:

- **利用者に見える出力に出るのは `--help` 本文（`cli_usage.py` の 7 箇所）と `ccnavi-clean.js`/`.sh` の 1 文だけ**（1-1 の表）。拒否の文面（rules.yml の message、`ticket_guard.py` ほか）に「README の○○の節」は無い。`ticket_guard.py:30` の `grep git README.md` はコメント内の例で無関係。
- `.ccnavi/config/**/rules.yml` の message: README を含むものは 0 件（`extensions/vscode/ccnavi-board/src/webview/tours/rules.yml` の README は被写体のパス `secrets/README.md`）。
- `.ccnavi/CCNAVI.md`: README への言及 0 件。`.ccnavi/docs/*.md`: `frontmatter.md` だけ（README.md の名前の扱い）。配布先へ配る文書の中にルート README の節を引くものは無い。
- 出力に節名が入る別の文書: `docs/claude/script-output.md` は README を引かない（ヒットなし）。

### 5. 表に足す行・直す行の案

### 5-1. CLAUDE.md の「詳細」の表（開発の決まり。docs/claude/）

CLAUDE.md の表は「ccnavi の開発（`docs/claude/`）」用で、ルールは「新しい決まりは、まず `docs/claude/` の文書に書き、表に 1 行足す」。分割で新しく足す**決まり**は少ない。足す案:

| 読むとき | 開くファイル |
|---|---|
| README.md を分けた先（`docs/guide/*.md`・`docs/development.md`）の置き場と、節を足す・動かすときの決まり（アンカーを他の文書が引く） | `docs/claude/docs-layout.md`（新設。中身は 5-3） |

`docs/guide/` は ccnavi の使い方（利用者向け）なので、CLAUDE.md の冒頭の「ccnavi の使い方は `.ccnavi/docs/`（配布先にも配る）」と分けた場所になる。**`docs/guide/` は配布しない**ことを明記しないと、`.ccnavi/docs/` との役割が混ざる。冒頭の説明文（「詳細は `.ccnavi/docs/` と `docs/claude/` に記載」）に 1 語足すなら「利用者向けの手引きは `docs/guide/`」。

直す行（既存）: 
- 表 1 行目 `docs/claude/frontmatter.md`（付け方は `.ccnavi/docs/frontmatter.md`）は変更なし。
- `docs/claude/frontmatter.md:30` の `guide` の行（「ルートの README.md と拡張の README.md には付けていない」）に `docs/guide/*.md`・`docs/development.md` を**付ける対象**として加える。ルート README.md は残るので「付けていない」の記述は維持。
- `docs/claude/frontmatter.md:45,47` と `.ccnavi/docs/frontmatter.md:53,55,89`: README.md 類の説明はそのままでよい。

### 5-2. .ccnavi/CCNAVI.md の表

CCNAVI.md の表は配布先にも配る（ccnavi の使い方）。**`docs/guide/` は ccnavi リポジトリの利用者向け手引きで、配布先には無い**ので、CCNAVI.md の表に `docs/guide/` を足してはいけない（配布先で開けないリンクになる）。足すなら次のどちらか:
- 足さない（案 A。推す）。ccnavi の利用者は README.md から `docs/guide/` へ入る。CCNAVI.md の表は変えない。
- 配布先にも guide を配るなら `.ccnavi/docs/` 側へ移す（別の判断。ここでは範囲外）。

どちらにせよ、`.ccnavi/CCNAVI.md` の直す行は **0 行**（README の節を引いていないため）。

### 5-3. docs/claude/docs-layout.md（新設案）に書く内容

- 置き場: `docs/guide/{install,settings,rules,tickets,records,extensions,troubleshooting}.md` と `docs/development.md`。ルート `README.md` は概要・導入の入口・各 guide への表だけ。
- 節の見出しはアンカーとして外から引かれる。引いている所の一覧（`docs/requirements/extension-if.md` の 7 リンク、`docs/design.md:17`、`cli_usage.py`、`ccnavi-clean.*`、`scripts/ccnavi-setup.sh`、拡張の `.ts` コメント）を grep で確かめてから見出しを変える。
- 見出しの名前は変えない（`## 版の JSON` など。GitHub のアンカーは見出し文字列から作られる）。
- `docs/guide/*.md` には `type: guide` の frontmatter を付ける（ルート README.md だけが付けない）。

### 5-4. その他、足す・直す文書（表以外）

- `docs/design.md:16,17`（読む表）: 「使い方と設定、ルール、記録」→ `docs/guide/`、「開発の手順、落とし穴」→ `docs/development.md`。
- `docs/requirements/extension-if.md` の 7 リンク: `../../README.md#版の-json` などを新ファイルの `../guide/<file>.md#…` に。
- `.claude/skills/commit/SKILL.md:82`: `docs` の対象に `docs/guide/`・`docs/development.md` を足す。
- `.ccnavi/config/project/phases.yml:38` scope: `docs/*` が `docs/guide/x.md` に当たらないなら `docs/guide/*` と `docs/development.md` を足す。

### 6. ADR 内の参照（直せない。ADR は変更しない決まり）

README の節名・行番号・アンカーを指すもの（ルート README の分割で古くなる）

| パス:行 | 文面 | 古くなる度合 |
|---|---|---|
| docs/adr/0104-approval-leaves-the-ticket-as-is.md:194 | 「設計 9.4 と README.md 2224 行（指紋の…）」 | **行番号**。分割で確実に外れる |
| docs/adr/0104-approval-leaves-the-ticket-as-is.md:196 | 「README.md の承認の節と `copy.approved_at`（2152 行付近）」 | 行番号 |
| docs/adr/0037-rewrite-design-doc.md:53 | 「README.md「設定」「設定の検証」」（付録 C/D の移り先） | 節名。履歴の記録なので古くなってよいが辿れない |
| docs/adr/0054-proposal-place-name.md:68 | 「README の env の表」 | 節名（設定の表） |
| docs/adr/0098-places-are-fixed-to-defaults.md:41, :164 | 「README に注記」「README の環境変数の表が 6 行減る」 | 設定の表 |
| docs/adr/0086-state-history-is-an-append-only-aid.md:47 | 「README の方針どおり」（ボードの JSON） | 節名なし |
| docs/adr/0073-origin-tree-is-the-fallback-home.md:50 | 「README は前から『--lint が ERROR で言うのと同じ条件』」 | 節名なし |
| docs/adr/0089-rotate-prune-and-redact-records.md:53 | 「README の `jq` の例」 | 記録の節 |
| docs/adr/0080-deny-turning-off-the-terminal-requirement.md:43 | 「切り方は README の設定の表にだけ書く」 | 設定の表 |
| docs/adr/0071, 0076, 0042, 0130, 0143, 0145, 0101 | 「README・設計書の文面を直す」など、**作業項目としての README 全体** | 影響なし |
| docs/adr/0011:40,44 / 0013:35 / 0031:22 | `grep -n "<<" README.md` など、コマンド例のファイル名 | 影響なし（D） |

拡張の README を指す ADR（ルート README の分割と無関係）: 0062:88,94,102、0070:92、0068:82、0074:73、0093:1043,1764,1815,1841。

対応: ADR は直さない。辿れなくなるのは 0104 の行番号 2 件と 0037 の節名のみ。必要なら README 側の旧節名をそのまま見出しに残す（見出しを変えない）ことで、節名を引く ADR を辿れる状態を保つ。行番号は保てないので、docs-layout.md に「ADR-0104 の README 行番号は分割前の版（git の履歴）を指す」と 1 行書く案。

その他の docs/ 配下: docs/wiki と docs/CONTEXT.md に README への言及は 0 件。wip/proposals/todo/claude-laughing-newton-necssy.md:60,92,256,270 は README.md をファイル名として使う下書き（`docs` フェーズの範囲に入っていると明記）で、README の節は引かない。

### 7. 分割の作業で先にやること（優先順）

1. 見出し文字列を変えずに移す（アンカーを保つ）。
2. `cli_usage.py` の 7 箇所と `ccnavi-clean.js`/`.sh` の 1 箇所の文面を新しい置き場に直す（利用者に見える）。直したら `--help` を照合しているテストを回す。
3. `docs/requirements/extension-if.md` の 7 リンクと `docs/design.md:16-17` を直す。
4. README から出る相対リンク 33 本と、README 内の相互参照 ◎ 16 件前後（2 の表）を、移し先の相対パスと新しい文書名に直す。
5. コメント・docstring（src/・scripts/・tests/・extensions/ のうち 1-1 の表）の「README「…」」を「docs/guide/….md「…」」に直す。実行には影響しない。
6. docs/guide/*.md と docs/development.md に `type: guide` の frontmatter を付ける。docs/claude/frontmatter.md の `guide` の行を直す。
7. 新設の docs/claude/docs-layout.md と CLAUDE.md の表 1 行。
