> **この文書は到達点であって、現在の実装ではない。**
>
> 書かれているのは ccnavi が最終的に備える姿で、今そこに届いているのは
> ツール実行前のルール照合だけである。読む前に、以下を先に読むこと。
>
> | いま正なもの | 場所 |
> |---|---|
> | 外から観測できる約束 | [requirements.md](requirements.md) |
> | 実装の使い方と設定 | [README.md](README.md) |
> | 実装の現状と次の一手 | [HANDOVER.md](HANDOVER.md) |
>
> 実装を進めるなかで、この文書と食い違う判断をした箇所がある。
> 食い違いには 2 種類あり、扱いが違う。
>
> **まだ作っていないだけのもの。** レイヤ合成、確認の記憶、セッション開始時の提示、
> 診断コマンドの大半。この文書のとおりに作ってよい。
>
> **入ったが、この文書とは形が違うもの。** 実行後の監視（§18）、チケット（§9）、
> 承認ゲート（§17）。どれも requirements.md は満たしているが、土台が違うぶん
> 作りが変わっている。食い違いの一覧は HANDOVER.md の「判断の経緯」にある。
> とくにチケットは、この文書が `target_directories` と allow / ask / deny の
> 上に乗せているのに対し、実装は「宣言した範囲の外を止める」だけの、
> 絞る方向 1 本になっている。
>
> **判断が変わったもの。この文書が古い。**
>
> | この文書の記述 | 現在の判断 |
> |---|---|
> | Python のスクリプトとして hook に登録する | 言語は Python のままだが、登録するのは PyInstaller で組んだ実行ファイル。使う側にランタイムの導入を求めない。実行時の third-party 依存は持たず、判定中に外部プロセスを起こさない |
> | 設定は `.claude/hooks/config.yaml` に置く | `.claude/settings.json` の `env` ブロックで環境変数として渡す。Claude Code の設定スキーマが独自キーを拒むため |
> | 判定は deny / ask / allow の 3 値 | それに加えて、判定しない・警告・ブロックの 3 つの動作モードを持つ |
| ask を明示的／暗黙的に二分類する（§13） | 「暗黙的 ask」という考え方をやめた。どのルールも言及しない呼び出しについて ccnavi は判定を持たず、Claude Code の権限モードに従う。`auto` は classifier に渡し、人が居るモードは確認に出し、`dontAsk` と `bypassPermissions` は通さない。渡した回は記録の `decision: handover` に残る。理由コードも `IMPL_UNDECLARED` / `EXPL_ASK` から `UNDECLARED` / `RULE_ASK` に改めた |
> | ― | ルールは実行ファイルの外から注入し、正規表現を知らなくても書ける記法を持つ |
> | ― | 判定した呼び出しは、通したものも判定しなかったものも 1 件 1 行で記録する |
>
> 食い違いを見つけたら、requirements.md を正として直す。
> この文書を実装に合わせて書き直すのは、実装がここへ追いついてからでよい。

## 0. 概要

| 項目 | 内容 |
|---|---|
| システム名 | Claude Code Ticket Guard |
| 目的 | チケット単位の動的スコープ制御による、本番DB・保護ファイル・プロジェクト外領域の破壊防止 |
| 構成要素 | PreToolUse Hook（静的解析）＋ PostToolUse Hook（事後状態監視）＋ セッション承認キャッシュ ＋ チケット承認ゲート |
| 設定ソース | Claude Code settings.json + Hook 組み込み不変ルール + .claude/hooks/config.yaml + .current-ticket.md + 環境変数 |
| 基本原則 | ① Fail-Safe（deny > ask > allow）<br>② 明示宣言された制約は下位レイヤから緩められない |

## 1. サマリ

### 1.1 システムが提供する機能

| # | 機能 | 目的 |
|---|---|---|
| 1 | シェル構文パースによる構造解析（PreToolUse） | 文字列パターンマッチでは検知できないコマンド構造を、トークン分解した結果に基づいて判定するため |
| 2 | 実行後の Git 差分監視（PostToolUse） | 間接実行・副作用による保護領域の汚染は実行前に検知できないため、事後に検知して復元させる |
| 3 | チケット単位の動的スコープ | 作業ごとに必要最小限の権限だけを開示し、影響範囲を局所化するため |
| 4 | ask の二分類と承認キャッシュ | 定義漏れ由来の確認疲れを抑制しつつ、意図的な確認ポイントは維持するため |
| 5 | 明示宣言に基づく権限上限 | チケット承認が形骸化しても、プロジェクトが明示宣言した制約は破られないようにするため |

### 1.2 権限モデルの骨格

権限設定は 4 レイヤから構成される。

```
Layer 0-A : Claude Code settings.json        ← プラットフォームによる強制
Layer 0-B : Hook 組み込み不変ルール（3 パス）  ← コード固定
Layer 1   : .claude/hooks/config.yaml        ← 人間が PR + レビューで管理
Layer 2   : .current-ticket.md               ← AI 生成 / 人間承認
Layer 3   : 環境変数 TICKET_GUARD_*           ← 実行時
```

### 1.3 宣言済み領域と未宣言領域

判定は以下の 2 種類の領域を明確に区別する。

| 領域 | 定義 | チケットの権限 |
|---|---|---|
| 宣言済み領域 | Layer 0 / Layer 1 が明示的に deny / ask / allow を書いた対象 | ❌ 緩められない（縮小のみ可） |
| 未宣言領域 | どのレイヤも言及していない対象（sandbox 内に限る） | ✅ チケットが allow を宣言すれば allow<br>チケットが沈黙していれば暗黙的 ask |

```
実効権限 = 宣言済み領域 → strictest( 上位レイヤの宣言, チケットの要求 )
           未宣言領域   → チケットの要求（無指定なら暗黙的 ask）
           sandbox 外   → write/exec: deny 固定、read: ask 固定
```

この設計を採る理由：ファイルシステム全域を事前に列挙することは不可能であり、未宣言領域をすべて ask に固定すると確認疲れで承認が形骸化する。一方、守るべき対象は有限であり列挙可能である。したがって「守るべきものはプロジェクトが明示的に列挙し、それ以外はチケットに委譲する」というデニーリスト型のモデルを採用する。

この帰結として、プロジェクト設定の列挙品質が防御力を直接決定する。列挙漏れを検知・是正するため、逸脱の可視化（§16）とリスクスコア（§17）を併設する。

## 2. 解決する課題

### 2.1 ワイルドカードパターンマッチの構造的限界

`Bash(psql * DROP *)` のようなコマンド文字列の単純なテキスト合致では、以下のすり抜けを防げない。

| すり抜け手法 | 実例 | 突破理由 |
|---|---|---|
| 順序逆転（パイプ） | `echo "DROP TABLE users;" \| psql -d mydb` | DROP が psql より前に出現し、パターンにマッチしない |
| ヒアドキュメント | `psql -d mydb <<EOF`<br>`DROP TABLE users;`<br>`EOF` | 改行を挟むためワイルドカードが到達しない |
| ファイル経由実行 | `psql -d mydb -f /tmp/clean.sql` | コマンド文字列に DROP の語が一切出現しない |
| スクリプト経由実行 | Write で act.sh を生成 → `bash act.sh` | 同上。全層を通過する |

対処：シェル構文をトークン分解し、コマンド・オプション・対象パスを構造として抽出したうえで判定する（§12.3）。

### 2.2 過度なルールによる誤検知

すり抜けを塞ごうとパターンを広げる（例：`Bash(*DROP*)`）と、`git commit -m "Fix DROP bug"` や `grep -rn "DROP TABLE" ./src` のような正常操作まで一律ブロックされる。

対処：クォート保全・コメント除外・コマンド名との共起判定により、文字列としての出現と実行としての出現を区別する（§12.3）。

### 2.3 プロンプト指示の確実性の低さ

`CLAUDE.md` への記述は AI の判断に依存するため、長時間タスクの途中や外部入力に影響された文脈では指示が希薄化し、決定論的なブロックにならない。

対処：Hook による機械的な遮断を防御の主体とし、プロンプト指示は補助的な位置づけとする。

### 2.4 タスク文脈を持たない静的な権限管理

タスクごとに必要な権限は異なるが、固定的な許可リストでは「今回の作業に必要な範囲」を表現できない。また、ツール実行後の副作用で保護領域が書き換わったことを検知する仕組みも必要である。

対処：チケット単位の動的スコープ（§9）と PostToolUse による事後検証（§18）。

### 2.5 確認要求（ask）の質の不均質性

ask には性質の異なる 2 種類が混在する。設定者が意図的に置いた重要な確認ポイントと、単に設定に書き漏れただけの定義漏れ由来の確認である。両者を同列に扱うと後者が頻発して確認疲れを招き、ユーザーは内容を読まずに承認するようになり、前者の確認まで形骸化する。

対処：ask を明示的／暗黙的に二分類し、UI とキャッシュポリシーの両面で分離する（§13）。

### 2.6 チケット承認の形骸化

本システムは「作業開始時にエージェントがチケットを生成し、人間が承認する」運用を前提とするが、レビュー疲れ・流し読み・プロンプトインジェクション・生成ロジックのバグにより、過剰な権限要求が素通りしうる。

人間の承認を防御の唯一の根拠にできない。

対処：プロジェクトが明示宣言した制約はチケットから緩められない構造とし（§11）、加えて逸脱を必ず可視化する（§16）。

### 2.7 防御機構自身の保護と、その柔軟性の両立

設定ファイル・Hook スクリプトが無防備であれば他のあらゆる保護は無意味になる。一方で、`.claude/` 配下にはスキル定義・スラッシュコマンド・サブエージェント定義など、エージェント自身が正当に編集する対象も含まれる。これらを一律にコード固定で禁止すると、正当な運用が阻害される。

対処：組み込み不変ルールは「いかなるプロジェクトでも書き換えを許してはならない最小限」に限定し（§7.2）、`.claude/` 配下の保護範囲はプロジェクト設定に委ねる。プロジェクトがガードを外した場合は、その変更自体が人間の承認（PR レビュー・チケット承認ゲート）を通ることで担保する。加えて Hook 完全性検証（§7.4）により、実際に改変された場合は次回セッション開始時に人間の確認を強制する。

## 3. 脅威モデル

### 3.1 想定する主体

| 主体 | 説明 | 対象 |
|---|---|---|
| A: ドリフトする LLM | 悪意はないが、文脈の希薄化・誤解・幻覚により破壊的操作を行う | ✅ 主対象 |
| B: 誘導された LLM | プロンプトインジェクション（外部ドキュメント・Issue 本文・依存パッケージ経由）により攻撃者の意図に沿って動く | ✅ 主対象 |
| C: 事故 | 正常なコマンドの副作用、ビルドスクリプトの想定外挙動 | ✅ 主対象 |
| D: 実行環境を掌握した攻撃者 | 同一ユーザー権限で任意のシェルを実行できる | ❌ 対象外（§23.3 の OS/IAM 層で対処） |

主体 D を対象外とする理由：Hook と同一ユーザー権限で動作するファイルベースの防御は、任意のシェル実行が可能な主体に対して原理的に境界とならない。本システムは LLM のドリフトと事故に対するガードレールとして設計する。

### 3.2 保護対象資産

| 優先度 | 資産 | 想定被害 | 保護レイヤ |
|---|---|---|---|
| 🔴 最高 | 承認台帳・Git メタデータ・承認キャッシュ | 防御機構の偽装・履歴改竄 | Layer 0-B（不変） |
| 🔴 最高 | 本番 DB | データ消失 | Layer 1（deny_commands / DB 検知） |
| 🔴 最高 | クラウドインフラ（RDS、K8s、Terraform state） | サービス停止 | Layer 1（deny_commands） |
| 🟡 高 | 認証情報（.env、secrets/**） | 漏洩・横展開 | Layer 1（target_directories） |
| 🟡 高 | 設定・Hook 本体（.claude/hooks/**、settings.json） | 防御機構の無効化 | Layer 1（推奨テンプレート）＋ 完全性検証 |
| 🟡 高 | プロジェクト外領域（$HOME、/etc、他リポジトリ） | 影響範囲の拡大 | Layer 1（sandbox_root） |
| 🟢 中 | Git 履歴・保護ドキュメント | 復旧可能だが手戻り | Layer 1 |

### 3.3 対処する攻撃面

| ID | 攻撃面 | 対処箇所 |
|---|---|---|
| T-1 | チケットによる tools の昇格 | §8.3 strictest 合成 |
| T-2 | より深いキーによる deny サブツリーのくり抜き | §11.3 上位レイヤ単独ツリーでの判定 |
| T-3 | プロジェクト外領域への allow 追加 | §8.2 sandbox_root |
| T-4 | 設定・Hook 本体の書き換え | §8.5 推奨保護 ＋ §7.4 完全性検証 ＋ §17.5 承認台帳 |
| T-5 | approval_cache の緩和 | §8.7 上限クランプ |
| T-6 | シンボリックリンクによるサンドボックス脱出 | §8.2 実体解決 ＋ sandbox 境界 |
| T-7 | 承認キャッシュの内容差し替え悪用 | §14.6 内容ハッシュ |
| T-8 | 確認漏れによる過剰権限の承認 | §16 逸脱の可視化 / §17 承認ゲート |
| T-9 | プロジェクトの列挙漏れ領域への allow | §16.3 逸脱表示 / §17.3 リスクスコア / §18 事後監視 |


## 4. 設計原則

| # | 原則 | 内容と理由 |
|---|---|---|
| P1 | Fail-Safe（安全倒し） | 権限の競合時は常に最も厳しいルールを採用する（deny > ask > allow）。解析不能・判定不確定はすべて ask に倒す。判定できない状況を許可に倒すと、解析漏れが即座に脆弱性になるため。 |
| P2 | 明示宣言の不可侵性 | 上位レイヤが明示的に書いた deny / ask は、下位レイヤ（ticket / env）から緩められない。信頼度の低いレイヤに、運用者の明示的な意思を覆す権限を与えないため。 |
| P3 | 未宣言領域の委譲 | どのレイヤも言及していない領域は、チケットの宣言に従う。全域の事前列挙は不可能であり、未宣言領域を一律 ask にすると確認疲れで承認が形骸化するため。ただし逸脱は必ず可視化する。 |
| P4 | 最小の不変ルール | コード固定の不変 deny は、いかなるプロジェクトでも書き換えを許してはならない対象に限定する。正当な運用まで阻害しないため。 |
| P5 | 判定根拠の構造化 | すべての判定は reason_code / access / subject を持つ根拠オブジェクトとして返す。キャッシュキー・監査ログ・LLM フィードバックを同一の情報源から導出し、一貫性を保つため。 |
| P6 | 確認疲れの抑制 | 本質的な危険性の表明でない暗黙的 ask は、根拠単位で 1 セッション 1 回に集約する。明示的 ask は原則毎回確認する。確認の質を維持するため。 |
| P7 | 逸脱は必ず可視化 | 却下された昇格試行、および想定委譲範囲からの逸脱を握り潰さない。起動時表示・LLM への通知・監査ログの 3 経路で人間が事後に必ず気づけるようにするため。 |
| P8 | キャッシュは deny を緩和しない | 承認キャッシュは ask → allow の昇格にのみ作用し、deny 判定には一切影響しない。キャッシュを権限昇格の踏み台にしないため。 |
| P9 | 多層検証（静的 × 動的） | 実行前のコマンド構造解析と実行後の Git 状態検証を併用する。静的解析で検知不能な間接実行・副作用を捕捉するため。 |
| P10 | LLM 向け自己修復フィードバック | 拒否・確認発生時、理由と代替手段を返して AI に自律的な手段修正を促す。同じ拒否の繰り返しを避け、タスク完遂率を維持するため。 |

## 5. アーキテクチャ

### 5.1 全体構成

```
┌ 設定レイヤ（上位ほど強い）────────────────────┐
│ Layer 0-A : .claude/settings.json      （Claude Code が強制）│
│ Layer 0-B : Hook 組み込み不変ルール      （コード固定・3 パス）│
│ Layer 1   : .claude/hooks/config.yaml   （人間が PR で管理）  │
│ Layer 2   : .current-ticket.md          （AI 生成 / 人間承認）│
│ Layer 3   : 環境変数 TICKET_GUARD_*      （実行時）           │
└──────────────────────────────────────────────┘
                    │
                    ▼ 宣言済み／未宣言の区別 + config_hash 算出
┌ セッション開始処理 ──────────────────────────┐
│  - Hook スクリプト完全性検証                    │
│  - チケット完全性検証（承認台帳とのハッシュ照合） │
│  - 昇格試行 / 逸脱の検出 → 起動時レポート表示 + LLM 通知 │
└──────────────────────────────────────────────┘
                    │
                    ▼
┌ 1. PreToolUse Hook ─────────────────────────┐
│                                               │
│  Stage 1: 全チェック完走（根拠の収集）           │
│    ① Layer 0-B 不変 deny 照合                  │
│    ② sandbox_root 境界検査                     │
│    ③ ツール権限判定                            │
│    ④ パス権限判定（read / write / exec 分離）   │
│    ⑤ シェル構文パース（演算子/削除/移動/sed/実行）│
│    ⑥ DB 破壊キーワード検知                      │
│    ⑦ deny_commands 正規表現照合                 │
│              ↓                                 │
│    Decision { deny[], ask_explicit[], ask_implicit[] } │
│                                               │
│  Stage 2: 承認キャッシュ照会（deny[] が空の場合のみ）│
│  Stage 3: ユーザー確認 → キャッシュ登録          │
└──────────────────────────────────────────────┘
                    │ allow
                    ▼
              [ ツール実行 ]
                    │
                    ▼
┌ 2. PostToolUse Hook ────────────────────────┐
│  - git status --porcelain による差分検証        │
│  - 保護領域（Layer0-B / Layer1 write:deny）の汚染検知 │
│  - 原因となったキャッシュエントリの無効化         │
│  - additionalContext 注入で LLM に復元指示       │
└──────────────────────────────────────────────┘

┌ 永続データ ──────────────────────────────────┐
│  - approvals-<session_id>.json    （承認キャッシュ 0600）│
│  - .claude/approved-tickets.jsonl （承認台帳 / 不変deny）│
│  - .claude/hooks/.integrity       （Hook ハッシュ）      │
│  - 監査ログ                                              │
└──────────────────────────────────────────────┘
```

### 5.2 Layer 0-A と Hook の関係

`.claude/settings.json` の permissions は Claude Code 本体が評価する。Hook はその内側で動作し、さらに絞ることしかできない。

```
┌──────────────────────────┐
│ ツール呼び出し ─→│ Layer 0-A: settings.json      │
│                  │   permissions.deny に該当？    │─→ 🔴 遮断（Hook 到達せず）
│                  └──────────────────────────┘
│                            │ 通過
│                            ▼
│                  ┌──────────────────────────┐
│                  │ PreToolUse Hook（本システム）  │─→ deny / ask / allow
│                  └──────────────────────────┘
```

役割分担

| レイヤ | 担う判断 |
|---|---|
| Layer 0-A（settings.json） | 「このプロジェクトではそもそもこのツールを使わせない」というツール単位の恒久的遮断。例：WebFetch、NotebookEdit |
| 本 Hook | ツール内部の引数・対象・構造に依存した動的判断。settings.json では表現できないコマンド構造解析・パス別権限・チケットスコープ |

Layer 0-A は Hook が起動する前に評価されるため、Hook のバグや設定ミスの影響を受けない。恒久的に不要なツールは settings.json 側で落とすことを推奨する。

## 6. Layer構成の概要

### 6.1


| Layer | ソース | 変更主体 | 変更頻度 | 信頼度 |
|---|---|---|---|---|
| 0-B | Hookコード内ハードコード | 開発者(リリース) | 極低 | 最高 |
| 1 | .claude/hooks/config.yaml | 人間(PR + Code Ownerレビュー) | 低 | 高 |
| 2 | .current-ticket.md frontmatter | AI生成→人間承認 | 作業ごと | 低 |
| 3 | 環境変数 TICKET_GUARD_* | 実行者・CI | 毎回 | 中 |

### 6.2 合成規則の総表

| 設定項目 | 合成規則 | ticketが緩和可能か |
|---|---|---|
| Layer 0-A permissions.deny | Claude Codeが強制 | 不可(Hook到達前に遮断) |
| Layer 0-B不変deny(3パス) | コード固定 | 不可(変更手段が存在しない) |
| sandbox_root / sandbox_extra_roots | Layer1、immutable推奨 | 不可 |
| tools(Layer1に記載あり) | strictest(project値, ticket値) | 不可(縮小のみ) |
| tools(Layer1に記載なし) | ticket値をそのまま採用。無指定ならdefault_tool_ceiling | 可 |
| target_directories(Layer1に該当ルールあり) | strictest(project値, ticket値) | 不可(縮小のみ) |
| target_directories(Layer1に該当ルールなし) | ticket値をそのまま採用。無指定なら暗黙的ask | 可(sandbox内に限る) |
| deny_commands | 全Layerの配列結合(追加のみ) | 不可(削除不能) |
| approval_cache.* | max_*による上限クランプ | 不可(縮小のみ) |
| approval_cache.prefix_denylist | 配列結合(追加のみ) | 不可 |
| approval_cache.content_hash_commands | 配列結合(追加のみ) | 不可 |
| expected_roots | Layer1のみ(判定に影響しない) | ― |
| approval_ui | Layer1、immutable推奨 | 不可 |
| immutable | Layer1のみ。自己参照的に保護 | 不可 |

### 6.3 strictest() の定義

```
strictest(a, b) = より厳しい方

厳しさ順序(左が厳しい):
  判定値      : deny < ask < allow
  cache mode  : off < implicit < all
  path_scope  : exact < prefix
  数値上限    : min(a, b)
  真偽(制約)  : true(制約有効)が厳しい
```

### 6.4 immutableセクション

Layer 1に「この設定は下位レイヤから一切変更を受け付けない」を宣言する文法を用意する。運用者が「ここは絶対に動かさない」という意図を設定ファイル上で表現できるようにするため。

```yaml
immutable:
  - sandbox_root
  - tools                              # セクション丸ごと
  - tools.Bash                         # 個別キー
  - target_directories.write.".env"
  - approval_cache
  - approval_ui
  - immutable                          # 自己保護
```

| 挙動 | 内容 |
|---|---|
| 対象キーへの下位レイヤ記述 | マージ時に完全に無視 |
| 無視した記述 | §16のレポートに記録(緩和方向・縮小方向を問わず記録) |
| immutable自体への記述 | 下位レイヤからの追記・削除は不可 |

### 6.5 config_hash

統合後の設定ツリー全体を正規化(キーソート・空白正規化・コメント除去)してシリアライズし、SHA-256を取る。

含めるもの:Layer 0-Bのバージョン識別子/Layer 1の全内容/Layer 2のfrontmatter全内容/Layer 3の有効値(クランプ後)

| 用途 | 内容 |
|---|---|
| 承認キャッシュの失効判定 | 値が変われば全エントリを破棄(§14.9) |
| 監査ログの相関キー | どの設定下での判定かを特定 |

---

## 7. Layer 0:プラットフォーム層と組み込み不変ルール

### 7.1 Layer 0-A:.claude/settings.json

Claude Code本体が評価する権限設定。Hookより外側で強制されるため、Hookのバグ・設定ミス・Hook自体の改変の影響を受けない最も硬い層である。

```json
{
  "permissions": {
    "deny": [
      "WebFetch",                       // 恒久的に使わせないツール
      "Bash(sudo:*)",
      "Bash(curl:*)",
      "Read(./.env)",
      "Read(./secrets/**)"
    ],
    "ask": [
      "Bash(git push:*)"
    ]
  },
  "hooks": {
    "PreToolUse":  [{ "matcher": "*", "hooks": [{ "type": "command", "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/pre_tool_use.py" }] }],
    "PostToolUse": [{ "matcher": "Bash|Write|Edit|MultiEdit", "hooks": [{ "type": "command", "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/post_tool_use.py" }] }]
  }
}
```

**この層に置くべきもの**

| 種別 | 例 | 理由 |
|---|---|---|
| 恒久的に使わないツール | WebFetch, NotebookEdit | Hookによる動的判断が不要であり、より外側で落としたほうが確実なため |
| 絶対に触らせない静的パス | Read(./secrets/**) | 二重防御。Hookが無効化されても遮断が残るため |
| 環境依存の危険コマンド | Bash(sudo:*) | 同上 |

**この層に置かないもの**:チケットごとに変動する権限、コマンド構造に依存する判断。これらは静的なパターン記述では表現できないためHookが担う。

settings.json自体の書き換え保護はLayer 1に委ねる(§8.5)。ただしClaude Codeは原則としてセッション開始時に設定を読み込むため、セッション途中の書き換えが即座に反映されるとは限らない。この時間差は緩和要因ではあるが保証ではないため、Layer 1でのwrite deny宣言を強く推奨する。

### 7.2 Layer 0-B:組み込み不変deny

この3つに限定する理由:いずれも「本システムの判定結果そのものを偽装できる」対象であり、書き換えを許すとシステムの出力が信用できなくなる。逆に言えば、それ以外の保護対象はプロジェクトごとに正当な編集需要がありうるため、コード固定にせずプロジェクト設定に委ねる。

| パス | access | 保護理由 |
|---|---|---|
| .claude/approved-tickets.jsonl | write | 未承認チケットを承認済みに偽装可能になるため |
| .git/** | write | pre-commit等への任意コード注入と履歴改竄が可能になり、PostToolUseの差分検証(§18)の前提が崩れるため |
| ${XDG_RUNTIME_DIR}/ticket-guard/** | write | 任意の承認エントリを注入して確認をすべて迂回できるため |

**.git/** write denyの適用範囲**

Gitバイナリ経由の通常操作は影響を受けない。遮断対象はファイルパスとしての直接書き込みである。

| 操作 | 判定 |
|---|---|
| git commit -m "..." / git add . / git checkout | 影響なし |
| Write: .git/config | 🔴 deny |
| sed -i 's/x/y/' .git/hooks/pre-commit | 🔴 deny |
| echo "..." > .git/hooks/pre-push | 🔴 deny(リダイレクト遮断でも捕捉) |

### 7.3 .claude/** をプロジェクト設定に委ねる理由

.claude/配下には以下が混在する。

| 対象 | 正当な編集需要 |
|---|---|
| .claude/skills/** | エージェント自身がスキル定義を追加・改善する運用がある |
| .claude/commands/** | スラッシュコマンドの追加 |
| .claude/agents/** | サブエージェント定義の調整 |
| .claude/hooks/** | 本システム自体の改修 |
| .claude/settings.json | 権限設定の変更 |

これらを一律にコード固定で禁止すると、正当な運用が阻害される。したがって保護範囲の決定はプロジェクトに委ねる。

**プロジェクトがガードを外した場合の担保**

| 経路 | 担保手段 |
|---|---|
| config.yamlから保護宣言を削除する | Layer 1の変更はPR + Code Ownerレビューを通る |
| チケットが.claude/**へのwrite allowを要求する | 承認ゲートでリスクスコア加点+逸脱表示(§16, §17) |
| 実際にHookが書き換えられた | 次回セッション開始時の完全性検証で人間の確認を強制(§7.4) |
| 設定が書き換えられた | config_hash変更により全承認キャッシュが失効(§14.9) |

### 7.4 Hookスクリプトの完全性検証

```
起動時:
 1. Hookスクリプト群(*.py)とconfig.yamlのハッシュを算出
 2. .claude/hooks/.integrity と照合
    ├ 一致    → 通常起動
    ├ 不一致  → 【人間の確認ゲート】
    │            差分内容を提示し、承認された場合のみ
    │            .integrityを更新して続行。
    │            承認されなければ全操作deny。
    │            承認キャッシュは全件破棄。
    └ ファイル無し → 初回として現在のハッシュを記録し、警告を表示
```

フェイルクローズではなく人間の確認ゲートとする理由:Hookの改修はプロジェクトの正当な運用として発生しうる。無条件のフェイルクローズは開発を停止させる。一方、無警告の通過は改変の検知機会を失う。したがって「必ず人間の目に触れさせる」設計とする。

hook_integrity: strict をLayer 1に設定した場合のみ、不一致時に無条件フェイルクローズする(本番近傍・監査対象環境向け)。

---

## 8. Layer 1:プロジェクト設定

.claude/hooks/config.yaml。人間がPR + Code Ownerレビューを経て変更する、明示宣言による権限上限の定義。

### 8.1 全体構造

```yaml
sandbox_root: "."                        # ①サンドボックス境界
sandbox_extra_roots: []
default_tool_ceiling: ask                # ②ツール権限
tools: { ... }
expected_roots: { ... }                  # ③想定委譲範囲(可視化専用)
target_directories: { ... }              # ④絶対防衛線
deny_commands: [ ... ]
approval_cache: { ... }                  # ⑤承認キャッシュ
approval_ui: { ... }                     # ⑥チケット承認UI
hook_integrity: warn                     # ⑦完全性検証モード
immutable: [ ... ]                       # ⑧変更不可宣言
```

### 8.2 ①sandbox_root ― プロジェクト外への脱出防止

未宣言領域をチケットに委譲する設計(P3)を採る以上、「委譲してよい世界の外縁」を定義する境界が必須となる。これがsandbox_rootである。

```yaml
sandbox_root: "."                        # 既定:プロジェクトルート
sandbox_extra_roots:                     # 明示的に許可する外部領域(既定:空)
  - "/tmp/ticket-guard-workspace"
```

| 対象 | write | read | exec |
|---|---|---|---|
| sandbox_root配下 | 通常判定 | 通常判定 | 通常判定 |
| sandbox_extra_roots配下 | 通常判定 | 通常判定 | 通常判定 |
| それ以外 | deny固定 | ask固定 | deny固定 |

sandbox外のreadをdenyではなくaskとする理由:システムライブラリやツールチェーン、他プロジェクトの参照など正当な読み取りが発生しうるため、人間の判断に委ねる。書き込みと実行には正当な需要が乏しいためdenyとする。

**シンボリックリンクの扱い(T-6)**
- パス正規化はシンボリックリンクの実体解決後に行う。src/link → /etc/passwd は /etc/passwd として評価され、sandbox外→write denyとなる。
- 正規化前後でsandboxの内外が変化した場合はIMPL_SYMLINK_ESCAPE(暗黙ask)を必ず追加し、キャッシュ対象外とする。境界を跨ぐアクセスは、結果的に許可される場合でも人間が毎回認識すべきであるため。

**sandbox_extra_rootsの禁止値(起動時にエラー)**

`"/" "/etc" "/usr" "/var" "/bin" "/sbin" "$HOME" ".." "~"`

### 8.3 ②tools ― ツール権限

```
Layer1.tools[T] に記載あり → strictest( Layer1値, ticket値 )
                              ※ ticket無指定ならLayer1値をそのまま採用
Layer1.tools[T] に記載なし → ticketに指定あり → その値
                              ticket無指定       → default_tool_ceiling
```

```yaml
default_tool_ceiling: ask     # project / ticket ともに言及しないツールの扱い

tools:
  Read:        allow
  Glob:        allow
  Grep:        allow
  Edit:        allow
  Write:       allow
  Bash:        allow     # 個別コマンドはdeny_commands / 構文解析で制御
  WebSearch:   ask
  Task:        ask
```

| Layer1 | ticket | 実効 | 説明 |
|---|---|---|---|
| allow | (未指定) | 🟢 allow | ticketの沈黙は「縮小しない」を意味する |
| allow | allow | 🟢 allow | |
| allow | ask | 🟠 ask | 縮小は有効 |
| ask | allow | 🟠 ask | 明示宣言は緩められない(T-1) |
| deny | allow | 🔴 deny | 同上 |
| (未記載) | allow | 🟢 allow | 未宣言領域の委譲(P3) |
| (未記載) | (未指定) | 🟠 ask | default_tool_ceiling |

ticket無指定を「縮小しない」と解釈する理由:チケットの役割は必要な権限の宣言であって、使用しないツールをすべて列挙させることではない。無指定をaskとすると、AIが全ツールを機械的に列挙するチケットを生成するようになり、承認画面の情報量が増えてレビュー品質が下がる。

帰結:ツールを恒久的に制限したい場合、Layer 1のtoolsに明示的に記載するか、Layer 0-Aのsettings.jsonで落とす必要がある。default_tool_ceilingはチケットも沈黙している場合のフォールバックにすぎない。

### 8.4 ③expected_roots ― 想定委譲範囲(可視化専用)

権限判定には一切影響しない。チケットが宣言したallowがこの範囲を逸脱した場合に、承認画面での強調表示・リスクスコア加点・キャッシュ粒度の降格を行うための基準である。

```yaml
expected_roots:
  read:  ["src", "tests", "docs", "public"]
  write: ["src", "tests"]
  exec:  ["node_modules/.bin", "scripts"]
```

| 用途 | 効果 |
|---|---|
| 承認UI(§17.2) | 範囲外へのallowを「想定外領域」として強調表示 |
| リスクスコア(§17.3) | 範囲外1件につき+15 |
| 起動時レポート(§16.3) | SCOPE_DEVIATIONとして列挙 |
| 承認キャッシュ(§14.5) | 範囲外はprefix→exactに強制降格し、広域承認の生成を防ぐ |

判定に影響させない理由:範囲外をaskに丸める設計は、列挙漏れのたびに作業が止まり確認疲れを招く。一方、逸脱の可視化だけであれば運用コストなしに列挙漏れを検知できる。強制が必要な場合はtarget_directoriesに明示ルールを書くという役割分担とする。

**厳格モード(オプトイン)**

```
strict_delegation: false  # 既定
```

trueにするとexpected_rootsが権限判定に作用し、範囲外へのチケットallowはaskに丸められる。本番近傍・監査対象環境で、確認疲れを許容してでも委譲範囲を強制したい場合に使用する。

### 8.5 ④target_directories / deny_commands ― 絶対防衛線

チケットから緩められない制約はここに書く。ここに書かれていない領域はチケットに委譲されるため、守るべき対象の列挙品質が防御力を直接決定する。

```yaml
target_directories:
  read:
    ".env": deny
    ".env.*": deny
    "secrets": deny
    "infra/production": deny
    "config":
      decision: ask
      ask_once: true
  write:
    # ― 認証情報・インフラ ―
    ".env": deny
    ".env.*": deny
    "secrets": deny
    "infra": deny
    "docs": deny
    # ― 本システム自身の保護(推奨) ―
    ".claude/hooks": deny
    ".claude/settings.json": deny
    ".claude/settings.local.json": deny
    ".current-ticket.md": deny
    # ― 依存関係・マイグレーション ―
    "package-lock.json": ask
    "migrations": ask
    "node_modules": deny

  exec:
    ".claude": deny     # 設定ディレクトリ内スクリプトの実行禁止(推奨)

deny_commands:
  - "aws\s+rds\s+(delete-db-cluster|delete-db-instance)"
  - "terraform\s+(destroy|apply)"
  - "kubectl\s+delete\s+(ns|namespace)"
  - "gh\s+(repo|secret)\s+delete"
  - "npm\s+publish"
  - "git\s+push\s+.*--force"
  - "git\s+reset\s+--hard"
```

.claude/hooks / settings.json / .current-ticket.md を推奨テンプレートに含める理由:これらはコード固定の不変ルールから外した対象であるが、大多数のプロジェクトでは書き換えを許す必要がない。既定で保護し、必要なプロジェクトだけが意図的に外す構成とする。.claude/skills等はテンプレートに含めないため、スキル編集は既定で可能である。

**ルール記述形式**

| 形式 | 記法 | 意味 |
|---|---|---|
| スカラー | "src": ask | decision: ask, ask_once: false と等価 |
| オブジェクト | "config": { decision: ask, ask_once: true } | 明示的にオプション指定 |

| フィールド | 値 | 既定 | 説明 |
|---|---|---|---|
| decision | allow / ask / deny | 必須 | 判定結果 |
| ask_once | true / false | false | decision: askのとき、明示的askをキャッシュ対象に含めるか(§14.3) |

**パスパターン**

| 記法 | 意味 | 例 |
|---|---|---|
| "src" | プレフィックス一致(配下すべて) | src/a.ts, src/b/c.ts |
| "src/config" | より深いプレフィックス(最長一致で優先) | src/config/db.json |
| ".env.*" | globパターン(同階層のみ) | .env.local, .env.production |
| "**/node_modules" | 再帰glob | 任意階層のnode_modules |

アクセス種別の分離:read / write / execを独立したツリーで管理する。「読めるが書けない」(docs)「読めるが実行できない」(scripts)のような非対称な権限を表現する必要があるため。

### 8.6 ⑤approval_cache

```yaml
approval_cache:
  # ― 既定値(ticket / envが縮小可能) ―
  mode: implicit
  path_scope: prefix
  ttl_minutes: 60
  max_entries: 100
  negative_cache: true

  # ― 上限(ticket / envはこれを超えられない) ―
  max_mode: implicit
  max_ttl_minutes: 60
  max_path_scope: prefix
  max_entries_limit: 100
  force_disable_when_non_interactive: true

  # ― 追加のみ可能(削除不可) ―
  prefix_denylist: ["/", "/etc", "/usr", "/var", "$HOME"]
  content_hash_commands:
    [bash, sh, zsh, python, python3, node, ruby, source, psql, mysql, kubectl]
```

**クランプ規則**

```
effective_mode        = strictest_mode(requested, max_mode)      # off < implicit < all
effective_ttl         = min(requested, max_ttl_minutes)
effective_scope       = strictest(requested, max_path_scope)     # exact < prefix
effective_max_entries = min(requested, max_entries_limit)
```

prefix_denylistとcontent_hash_commandsを「追加のみ可能」とする理由:これらはセキュリティ制約であり、下位レイヤから削除できると防御が無効化されるため。

### 8.7 ⑥⑦⑧その他

```yaml
approval_ui:
  high_risk_threshold: 40                     # このスコア超で二段階承認
  require_id_input_on_high_risk: true
  show_diff_from_previous_ticket: true
  max_ticket_rules: 40                        # ルール総数の上限(超過でチケット拒否)
  require_rationale_for_write_allow: true

hook_integrity: warn        # warn(人間の確認ゲート) / strict(フェイルクローズ)

immutable:
  - sandbox_root
  - tools
  - target_directories
  - approval_cache
  - approval_ui
  - hook_integrity
  - immutable
```

---

## 9. Layer 2:チケット設定

.current-ticket.md のYAML Frontmatter。AIが生成し、人間が承認する。

### 9.1 構造

```yaml
---
ticket: PROJ-1234
title: ユーザー設定画面のリファクタリング
rationale: |
  src/components/Settings配下のコンポーネント分割を行う。
  設定値の読み込みロジック確認のためconfig/のreadが必要。

tools:
  Bash: ask                # 今回はコマンド実行を慎重に扱うため自主的に縮小

deny_commands:
  - "npm\s+run\s+deploy"        # 追加のみ可能

target_directories:
  read:
    "src": allow
    "docs": allow
  write:
    "src/components/Settings": allow
    "src/components": ask
---

## 作業内容
...(本文は権限判定に影響しない)
```

### 9.2 チケットの権限範囲

| できること | できないこと |
|---|---|
| ✅未宣言領域(sandbox内)にallowを宣言する | ❌Layer 0-A / 0-Bのdenyを覆す |
| ✅上位レイヤの宣言より厳しいask / denyを設定する | ❌Layer 1が明示宣言したdenyをask/allowにする |
| ✅deny_commandsにパターンを追加する | ❌Layer 1が明示宣言したaskをallowにする |
| ✅approval_cacheをより厳しくする | ❌deny_commandsからパターンを削除する |
| ✅記載のないツールにallowを宣言する | ❌approval_cacheを緩める |
| ― | ❌sandbox外にwrite/execのallowを置く |
| ― | ❌sandbox_root / expected_roots / immutable / approval_uiを変更する |

### 9.3 チケット生成時にAIが守るべきガイドライン

CLAUDE.mdに記載してAIに遵守させる。これは補助であり、Layer 1の明示宣言のみが強制力を持つ。

```
## チケット生成ルール

1. 権限は「作業に必要な最小限」を宣言すること。迷ったら狭く。
2. write allowは、実際に編集するディレクトリだけに限定すること。
   親ディレクトリを一括でallowにしない。
3. .claude/hooks/config.yamlのexpected_rootsを必ず読み、
   その範囲内でのみallowを宣言すること。
   範囲外が必要な場合はrationaleにその理由を明記すること。
4. toolsは、使用するツールのうち制限が必要なものだけを記載すること。
   すべてを機械的に列挙しない。
5. ルール総数が20件を超える場合、作業の分割を検討すること。
6. .claude/配下・.git/配下へのwrite allowは、
   それ自体が作業目的である場合を除いて宣言しないこと。
```

### 9.4 チケットのバリデーション(読み込み時)

| # | 検証項目 | 違反時の挙動 |
|---|---|---|
| 1 | YAMLとしてparse可能か | チケット読み込み失敗→全操作askにフォールバック |
| 2 | ticketフィールドが存在するか | 同上 |
| 3 | ルール総数 ≤ max_ticket_rules | チケット拒否→再生成を要求 |
| 4 | パスに ../絶対パス/~/$ を含まないか | 該当ルールを無視+INVALID_PATHとして記録 |
| 5 | immutable対象キーへの記述がないか | 該当記述を無視+IMMUTABLE_IGNOREDとして記録 |
| 6 | sandbox_root/expected_roots/approval_uiへの記述がないか | 同上 |
| 7 | 承認台帳のハッシュと一致するか | 未承認扱い→§17.5の降格処理 |

ルール総数に上限を設ける理由:大量のルールを列挙して人間のレビューを困難にし、その中に過剰権限を紛れ込ませる手口を防ぐため。

---

## 10. Layer 3:環境変数

### 10.1 一覧

| 環境変数 | 値 | 既定 | 方向性 |
|---|---|---|---|
| TICKET_GUARD_APPROVAL_CACHE | off / implicit / all | implicit | 縮小は即適用、緩和はmax_modeでクランプ |
| TICKET_GUARD_APPROVAL_SCOPE | exact / prefix | prefix | 同上(max_path_scope) |
| TICKET_GUARD_APPROVAL_TTL | 整数(分) | 60 | 同上(max_ttl_minutes) |
| TICKET_GUARD_APPROVAL_MAX_ENTRIES | 整数 | 100 | 同上(max_entries_limit) |
| TICKET_GUARD_ASK_FALLBACK | deny / allow | deny | 非対話時のaskの扱い |
| TICKET_GUARD_LOG_LEVEL | error/warn/info/debug | warn | 監査ログ詳細度 |
| TICKET_GUARD_STRICT | 0 / 1 | 0 | 1で全キャッシュ無効+全askを厳格化 |

### 10.2 上限クランプの適用

```
縮小方向の指定 → 即座に適用(最優先)
緩和方向の指定 → Layer 1の上限でクランプ+逸脱として記録
```

環境変数を無条件に最優先としない理由:環境変数はCI設定・シェル初期化ファイル・依存ツールなど多様な経路から設定されうるため、プロジェクトが定めた上限を超える緩和を許すと防御が容易に無効化されるため。

### 10.3 TICKET_GUARD_APPROVAL_CACHE

| 値 | 明示的ask | 暗黙的ask | 用途 |
|---|---|---|---|
| off | キャッシュしない | キャッシュしない | 監査対象作業、本番環境近傍、キャッシュ挙動のデバッグ時 |
| implicit(既定) | キャッシュしない(ask_once: trueの項目のみ例外) | キャッシュする | 通常の開発作業 |
| all | キャッシュする(ask_onceを無視) | キャッシュする | 大量の反復作業。信頼できる環境のみ |

既定をimplicitとする理由:定義漏れ由来の確認疲れを抑制しつつ、設定者が意図的に置いた確認ポイントは維持するというP6の目的に最も合致するため。allを指定してもdenyは一切キャッシュ・緩和されない(P8)。

### 10.4 TICKET_GUARD_APPROVAL_SCOPE

ファイルパスを対象とする承認のキャッシュ一致粒度の既定値を決める。

| 値 | 動作 | 例:Read: /repo/tmp/build/out/report.jsonを承認 |
|---|---|---|
| exact | そのファイルパスのみ | report.jsonのみヒット |
| prefix(既定) | 直上の親ディレクトリ配下 | /repo/tmp/build/out/配下すべてがヒット |

既定をprefixとする理由:AIは同一ディレクトリ内の複数ファイルを連続して操作することが多く、exactではヒット率が低く確認疲れの抑制効果が得られないため。

**prefixの制約**

| 制約 | 理由 |
|---|---|
| 丸め上げるのは直上の親ディレクトリ1階層のみ。祖先には遡らない | 深いパスの承認が広域承認に化けることを防ぐため |
| 対象がディレクトリならそのディレクトリ自身をsubjectとする | 同上 |
| 親がprefix_denylistに該当する場合exactへ降格 | システム領域への広域承認を防ぐため |
| expected_roots外はexactへ降格 | 想定外領域への広域承認を防ぐため |
| content_hash_commands該当時は常にexact | 内容ハッシュによる同一性保証と両立しないため |

対話セッションでは環境変数の値がプロンプト上の既定選択肢を決めるだけで、ユーザーは個別に選び直せる。

### 10.5 設定例

```bash
# 【厳格】監査対象作業
export TICKET_GUARD_STRICT=1

# 【既定】通常の開発
export TICKET_GUARD_APPROVAL_CACHE=implicit
export TICKET_GUARD_APPROVAL_SCOPE=prefix

# 【慎重】キャッシュは使うがファイル単位で厳密に
export TICKET_GUARD_APPROVAL_SCOPE=exact

# 【CI】キャッシュは自動無効化される
export TICKET_GUARD_ASK_FALLBACK=deny
```

---

## 11. 権限判定の算出仕様

### 11.1 パス権限の決定順序

上から順に評価し、最初に該当したものを採用する。

| 順 | 条件 | 判定 | 種別 |
|---|---|---|---|
| 1 | Layer 0-B不変denyに該当 | deny | hard |
| 2 | sandbox外 | write/exec→deny、read→ask(明示的) | hard |
| 3 | Layer 1ツリー単独で最長一致するルールが存在 | strictest( Layer1値, ticket/env値 ) ※ticket無指定ならLayer1値 | hard |
| 4 | Layer 1に該当なし、ticket/envに明示ルールあり | その値 | 委譲 |
| 5 | Layer 1にもticket/envにも該当なし | ask(暗黙的) | フォールバック |
| 6 | strict_delegation: trueかつ④の値がallowかつexpected_roots外 | ask(暗黙的)に丸める | オプトイン |

順3の判定をLayer 1ツリー単独で行う理由(T-2への対処):マージ後のツリーで最長一致を取ると、チケットがsecrets/keys: allowのようなより深いキーを追加することで、Layer 1のsecrets: denyを回避できてしまう。Layer 1ツリーだけで最長一致を先に確定させることで、チケットが後から深いキーを追加してもLayer 1の宣言が必ず適用される。

### 11.2 ツール権限の決定順序

| 順 | 条件 | 判定 |
|---|---|---|
| 1 | Layer 0-A(settings.json)でdeny | Hook到達前に遮断 |
| 2 | Layer1.tools[T]に記載あり | strictest( Layer1値, ticket値 ) ※ticket無指定ならLayer1値 |
| 3 | 記載なし、ticketに指定あり | その値 |
| 4 | いずれも指定なし | default_tool_ceiling(既定ask、暗黙的) |

### 11.3 具体例による検証

**設定**

```yaml
# Layer 1
sandbox_root: "."
expected_roots:
  write: ["src", "tests"]
tools:
  Bash: ask
target_directories:
  read:  { "secrets": deny }
  write: { "secrets": deny, "docs": deny, ".claude/hooks": deny, "migrations": ask }
```

```yaml
# Layer 2(チケット)
tools:
  Bash: allow
  WebSearch: allow
target_directories:
  read:
    "secrets/keys": allow      # denyサブツリーのくり抜き試行
  write:
    "src/components": allow    # 正当な要求(expected_roots内)
    "tmp/scratch": allow       # 未宣言領域(expected_roots外)
    "migrations": allow        # Layer1の明示askを緩めようとする
    "/": allow                 # 広域
```

判定結果


---

## 12. PreToolUse判定ロジック

### 12.1 判定結果のデータ構造

すべてのチェックは判定値ではなく判定根拠のリストを返す(P5)。

```
Decision {
  deny:         [ Reason, ... ]
  ask_explicit: [ Reason, ... ]
  ask_implicit: [ Reason, ... ]
}

Reason {
  code:      reason_code       # 付録B
  access:    read | write | exec | tool
  subject:   正規化済みの対象
  layer:     platform | builtin | project | ticket | env
  detail:    人間向け説明テキスト
  hint:      LLM向けの代替手段提案
  cacheable: bool              # §14.3で決定
}
```

**最終判定**

```
deny[] が空でない        → deny
それ以外でask_* が空でない → ask(キャッシュ照会へ)
すべて空                 → allow
```

根拠を構造化して保持する理由:同一の判定結果でも、キャッシュキーの生成・監査ログの記録・LLMへの説明生成という3つの異なる用途があり、それらを一貫した情報源から導出するため。

すべてのチェックを完走させる理由:最初のdenyで打ち切ると、AIは1つずつ問題を修正して再試行し、往復回数が増える。全根拠をまとめて返すことで、AIは一度に手段を組み替えられる(P10)。

### 12.2 パス権限判定

| ツール種別 | 対象ツール | 参照するツリー | access |
|---|---|---|---|
| 参照系 | Read, Glob, Grep | target_directories.read | read |
| 編集系 | Write, Edit, MultiEdit | target_directories.write | write |
| Bash実行対象 | bash x.sh, python x.py 等 | target_directories.exec → 未定義時はreadにフォールバック | exec |

処理手順

```
1. パス正規化（シンボリックリンク実体解決 → ../ 解決 → 絶対パス）
   ├ 正規化前後で sandbox の内外が変化 → IMPL_SYMLINK_ESCAPE を追加（非キャッシュ）
   └ 存在しないパス（新規作成）は親ディレクトリを基準に判定
2. §11.1 の決定順序で判定
3. Reason を生成
```

### 12.3 Bashコマンド解析

① シェル構文のトークン分解と誤検知防止

| 処理 | 内容 | 目的 |
|---|---|---|
| トークナイズ | shlex によるコマンド分解 | 文字列マッチではなく構造として解析するため |
| コメント除外 | `#` 以降を解析対象から切り捨て | `python app.py # cat << EOF` を誤検知しないため |
| クォート保全 | `"..."` / `'...'` 内部の `>` `<<` 等は文字列データとして扱う | `git commit -m "Fix > bug"` を誤検知しないため |
| 連鎖コマンドの分割 | `&&`, `\|\|`, `;`, `\|`, `&` で分離し個別に全数走査 | `npm run build && terraform destroy` の後段を検知するため |
| サブシェル展開 | `$(...)`, `` `...` `` の内部も再帰的に解析 | サブシェル経由の隠蔽を防ぐため |
| 解析不確定の検出 | 変数展開（`$TARGET`）がパス位置に現れた場合 `IMPL_PARSE_UNCERTAIN` | 対象が確定できない場合に許可へ倒さないため（P1） |

クォート保全は、演算子の文字が語の一部として現れたかどうかで見分ける。`shlex` は
演算子を独立したトークンとして返すので、`"regex: '(>"` の `>` は語の中の文字、
`echo x>f` の `>` はリダイレクトになる。前者は両側に区切りの印を置いて渡すので、
書き込み先を見るルールが引用された文字列に当たることはない。

限界は 1 つ残る。引用符の中身が演算子の文字だけでできている 1 語は、演算子と
区別が付かない。`shlex` はどちらだったかを問い合わせる手段を公開していないため。
そのため `grep -n "<<" README.md` はヒアドキュメントの始まりと区別が付かず、
閉じない本文として解析不確定に倒れ、②の deny に当たる。

これは**許容する誤検知**とする。判定が止まる側に倒れること、返る文面が
「読めなかったので文字列に当てた」と名乗って本来の禁止と混ざらないこと、
対象をファイルへ逃がせば回避できることの 3 つが揃っているため。§23.1 L-7 を参照。

② 不正ファイル書き込みの遮断

クォート外の以下を検知した場合、**DENY_REDIRECT（即時 deny）** とする。

- リダイレクト `>`, `>>`, `&>`, `2>`
- ヒアドキュメント `<<`, `<<-`
- `tee` コマンド

一律 deny とする理由：リダイレクトによる書き込みは対象パスの動的生成が容易でパス解析の信頼性が低い。「ファイル生成は Write / Edit ツール経由に統一させる」ことで、すべてのファイル生成を構造化された判定経路に通す。hint で AI に Write ツールの使用を促す。

`sed -i` / `awk -i inplace` は対象パスが構造的に抽出可能であるため対象外とし、③ のパス判定へ回す。

③ コマンド別パス権限検証

| 対象コマンド | 解析ロジック | access |
|---|---|---|
| 削除 `rm` / `rmdir` / `git rm` | オプションフラグ（-f, -r, -rf 等）を除外して削除対象パスを抽出 | write |
| 移動 `mv` / `git mv` | 移動元（削除）と移動先（書き込み）の双方を抽出。片方でも deny/ask ならその判定 | write × 2 |
| コピー `cp` | コピー先を write、コピー元を read として判定 | 先 write / 元 read |
| インプレース編集 `sed -i` / `awk -i inplace` | フラグやスクリプト文（`s/.../.../`）を除外して対象パスを抽出 | write |
| スクリプト実行 `bash` / `sh` / `python` / `node` / `source` / `.` | 実行対象ファイルパスを抽出 | exec |
| ワイルドカード検出 | 上記で `*`, `?`, `[...]` を含む場合 | `IMPL_GLOB_UNRESOLVED`（暗黙 ask） |

移動元・移動先の双方を判定する理由：保護領域からの持ち出しと保護領域への持ち込みの両方が権限境界の侵害となるため。

④ DB破壊操作の独立多角検知

記述順序（パイプ）や改行（ヒアドキュメント）による回避を防ぐため、2 段階の独立判定を行う。

```
Step 1: コマンド文字列全体に、単語境界つきで DB クライアントが含まれるか
          psql / mysql / mongosh / mongo / sqlite3 / redis-cli

Step 2: 含まれる場合、DOTALL + IGNORECASE で破壊的キーワードを全文検索
          DROP / TRUNCATE / DELETE / ALTER / GRANT / REVOKE / UPDATE
          FLUSHALL / FLUSHDB / dropDatabase

両者が同時成立 → 出現位置・順序を問わず DENY_DB_DESTRUCTIVE（即時 deny）
```

2 段階に分離する理由：単一の正規表現でコマンドとキーワードの順序関係を規定すると、順序逆転（`echo "DROP..." | psql`）で回避される。共起のみを条件とすることで、順序と改行の影響を完全に排除できる。同時に、DB クライアントコマンドとの共起を必須とすることで、`grep -rn "DROP TABLE" ./src` や `git commit -m "fix DROP bug"` のような正当な操作を誤検知しない。

ファイル入力経路の補完：`psql -f x.sql` / `mysql < x.sql` / `mongosh x.js` のようにコマンド文字列にキーワードが現れない形式は、Step 2 では検知できない。この場合は §14.6 の内容ハッシュ対象コマンドとして扱い、対象ファイルの中身を読んで Step 2 を再実行する。ファイルが読めない場合は `IMPL_HASH_UNAVAILABLE`（暗黙 ask・非キャッシュ）とする。

⑤ deny_commands パターン照合

全 Layer の正規表現リスト（結合済み）に対し、コマンド全体（改行跨ぎ・DOTALL）でマッチングし、一致すれば `DENY_COMMAND_PATTERN`（即時 deny）。

正規表現は起動時にコンパイル検証し、不正なパターンがあれば起動時エラーとする。実行時に silently 無視すると、防御が抜けたことに気づけないため。

---

## 13. askの二分類

### 13.1 定義

| 区分 | 記号 | 発生条件 | 意味 | 既定でキャッシュ |
|---|---|---|---|---|
| 明示的 ask（ask:explicit） | 🟠E | 設定ファイル（Layer 1 / Layer 2）に ask と明示的に記述されている。または sandbox 外 read | 「ここは毎回人間が見るべき」という意図的な確認ポイント | しない<br>（ask_once: true 時のみする） |
| 暗黙的 ask（ask:implicit） | 🟠I | どのレイヤも言及していない／解析不能／default_tool_ceiling によるフォールバック | 「判断材料が足りないので安全側に倒した」だけ。本質的な危険性の表明ではない | する |

### 13.2 分離する理由

暗黙的 ask は設定の穴に起因するため、その穴が塞がるまで同じ問いが繰り返される。これは情報量ゼロの繰り返しであり、確認疲れの主因となる。

一方、明示的 ask は設定者が意図してそこに置いたチェックポイントであり、繰り返し確認されること自体に価値がある（例：migrations を触るたびに人間が内容を目視する）。

両者を同じ扱いにすると、前者のノイズに後者が埋もれる。したがって UI とキャッシュポリシーの両面で分離する（P6）。

### 13.3 プロンプト表示

暗黙的 ask

```
🟠 確認が必要です（暗黙的 ask：どの設定にも定義がありません）

  ツール ： Read
  対象   ： /repo/tmp/build-output/report.json
  理由   ： IMPL_PATH_UNDEF
             project 設定・チケットのいずれにも該当ルールなし
             expected_roots.read の範囲外

  ヒント ： 恒久的に許可する場合は、以下のいずれかを検討してください
             - チケットの target_directories.read に追加（今回の作業限り）
             - config.yaml の expected_roots.read に追加（想定範囲の更新）
             ※ config.yaml の変更には人間の PR が必要です

[1] 今回だけ許可
[2] このセッション中、このファイルへの read を許可          （exact）
[3] このセッション中、tmp/build-output 配下の read を許可   （prefix）★既定
[4] 拒否
[5] 拒否 + このセッション中、同じ対象は自動拒否
```

明示的 ask

```
🟡 確認が必要です（設定で確認が指定されています）

  ツール ： Edit
  対象   ： /repo/migrations/0042_add_index.sql
  理由   ： EXPL_PATH_ASK
             project 設定 target_directories.write の "migrations" が ask
             （チケットは allow を要求しましたが、明示宣言が優先されます）

[1] 許可
[2] 拒否
```

`ask_once: true` または `mode=all` の場合のみ、明示的 ask にも `[3] このセッション中は再確認しない` が追加される。

### 13.4 混在時の扱い

| 状況 | 挙動 | 理由 |
|---|---|---|
| 明示的 ask がキャッシュ対象外 | キャッシュ照会をスキップして必ずユーザーに確認 | 意図的な確認ポイントを、暗黙的 ask のキャッシュヒットで飛ばさないため |
| ユーザー承認後 | 暗黙的 ask の根拠は通常どおりキャッシュに登録 | 次回は明示的 ask のみが残り、確認内容が本質的なものに絞られる |

---

## 14. セッション承認キャッシュ

### 14.1 キー設計方針

キャッシュキーは「なぜ ask になったのか」という判定根拠を構造化した正規形とする。

コマンド文字列そのものをキーにしない理由：

| 問題 | 内容 |
|---|---|
| ヒット率が低い | `rm src/a.ts` と `rm src/b.ts` が別扱いになり、AI はほぼ毎回異なる引数を出すため実質ヒットしない |
| 過剰一致の危険 | `bash /tmp/act.sh` を承認後、中身を書き換えて再実行するとフリーパスとなる |

### 14.2 キー構造

```
cache_key = SHA-256( reason_code | access | subject | scope | config_hash )
```

| フィールド | 役割 |
|---|---|
| reason_code | ask の発生源（付録 B）。異なる根拠は必ず別キーとなる |
| access | read / write / exec / tool。read の承認が write に波及しない |
| subject | 正規化済みの対象（絶対パス / ツール名 / パス＋内容ハッシュ） |
| scope | exact / prefix |
| config_hash | 統合後設定のハッシュ。設定変更で全キャッシュが自動失効する |

`session_id` はキーに含めず、キャッシュファイル自体をセッション単位で分離する。

### 14.3 キャッシュ対象の決定

```
[ 最終判定が ask ]
        │
   ┌────┴────┐
   ▼               ▼
ask_explicit を含む   ask_implicit のみ
   │               │
   ▼               ▼
┌──────────────┐  ┌──────────────┐
│ mode = all ?        │  │ mode = off ?        │
│  YES → キャッシュ可  │  │  YES → 都度 ask     │
│  NO  → ask_once:true │  │  NO  → キャッシュ可 │
│        の項目のみ可  │  └──────────────┘
└──────────────┘
```

| mode | 暗黙的 ask | 明示的 ask（ask_once: false） | 明示的 ask（ask_once: true） |
|---|---|---|---|
| off | ✗ | ✗ | ✗ |
| implicit（既定） | ✓ | ✗ | ✓ |
| all | ✓ | ✓ | ✓ |

常にキャッシュ対象外となる根拠

| reason_code | 理由 |
|---|---|
| IMPL_SYMLINK_ESCAPE | sandbox 境界を跨ぐアクセスは毎回人間が認識すべきであるため |
| IMPL_HASH_UNAVAILABLE | 内容ハッシュが取得できない対象は同一性を保証できないため |
| EXPL_SANDBOX_READ | sandbox 外の読み取りは範囲が予測不能であるため |
| すべての DENY_* | deny はキャッシュによる緩和対象外であるため（P8） |


### 14.4 subjectの正規化

| 対象種別 | access | 正規化方法 |
|---|---|---|
| ファイルパス | read / write | シンボリックリンク実体解決 ＋ `../` 解決 → 絶対パス。scope=prefix なら直上の親ディレクトリへ丸める |
| ディレクトリパス | read / write | そのディレクトリの絶対パス（親には遡らない） |
| ツール名 | tool | 大文字小文字を保持したまま完全一致 |
| 実行対象スクリプト | exec | 絶対パス@sha256:&lt;ファイル内容ハッシュ&gt;（§14.6） |
| glob パターン | read / write | パターン文字列 ＋ 展開結果の実ファイル集合のソート済みハッシュ |

### 14.5 scopeの決定順序

```
1. content_hash_commands に該当        ──→ exact（強制）
2. 親ディレクトリが prefix_denylist に該当 ──→ exact（強制降格）
3. 親ディレクトリが expected_roots 外    ──→ exact（強制降格）
4. 対話セッション かつ ユーザーが選択     ──→ ユーザーの選択値
5. 上記以外                            ──→ effective path_scope
```

3 の降格を行う理由：`expected_roots` 外はプロジェクトが想定していない領域であり、そこに prefix 承認を発行すると、想定外領域への広域許可がセッション内に残り続けるため。判定自体は allow に倒しても、キャッシュの影響範囲は最小化する。

### 14.6 実行系コマンドの内容ハッシュ

防ぐ手口

```
1回目: bash /tmp/act.sh   → ユーザーが承認、キャッシュ登録
2回目: /tmp/act.sh の中身を破壊的コマンドに書き換え
3回目: bash /tmp/act.sh   → キャッシュヒットでノーチェック実行
```

対策

| コマンド分類 | subject | scope |
|---|---|---|
| 通常のパスアクセス（Read/Write/Edit） | パスのみ | 環境変数に従う |
| スクリプト実行（bash, sh, python, node, source, .） | パス + sha256(内容) | exact 固定 |
| ファイル入力実行（psql -f, mysql <, kubectl apply -f） | パス + sha256(内容) | exact 固定 |

内容が 1 バイトでも変われば別キーとなり、再度 ask される。

ファイルが読み取れない場合（権限不足・サイズ超過・不在）は `IMPL_HASH_UNAVAILABLE` を追加し、キャッシュ登録を行わない（毎回 ask）。

ハッシュ計算の対象サイズには上限を設ける（既定 10 MB）。上限超過時も `IMPL_HASH_UNAVAILABLE` として扱う。Hook の実行時間が肥大化すると、ツール呼び出しごとの遅延が体験を損なうため。

### 14.7 承認プロンプトと記録内容

| ユーザーの選択 | 記録 |
|---|---|
| 今回だけ許可 | 記録しない |
| このセッション中、この対象を許可（exact） | decision: allow, scope: exact |
| このセッション中、配下すべてを許可（prefix） | decision: allow, scope: prefix |
| 拒否 | 記録しない（次回また聞く） |
| 拒否 + 同じ対象は自動拒否 | decision: deny, scope: exact（ネガティブキャッシュ） |

拒否を既定で記録しない理由：一度拒否した操作でも、状況が変われば許可したくなる場合があるため。恒久的な自動拒否は明示的な選択肢として分離する。

### 14.8 複数根拠の扱い（AND条件）

1 回の呼び出しで複数の ask 根拠が同時発生する。

```
Bash: mv /repo/tmp/a.ts /repo/var/b.ts
  → IMPL_PATH_UNDEF (write, /repo/tmp)
  → IMPL_PATH_UNDEF (write, /repo/var)
```


| 状況 | 挙動 |
|---|---|
| 全根拠が allow でヒット | 🟢 allow（全エントリの hit_count を加算） |
| いずれか 1 つが deny でヒット | 🔴 deny（ネガティブキャッシュが優先） |
| 一部のみヒット | 🟠 ask。未承認の根拠のみをプロンプトに提示 |
| 全て未ヒット | 🟠 ask。全根拠を提示 |

承認時は、その呼び出しで発生した全根拠を個別レコードとして登録する。

### 14.9 失効条件

| トリガ | 動作 |
|---|---|
| config_hash 変更 | 全件失効（config.yaml 編集・チケット編集・チケット切替・環境変数変更） |
| チケット未承認検出 | 全件失効（§17.5） |
| Hook 完全性検証の不一致 | 全件失効 |
| TTL 超過 | 該当エントリのみ失効（既定 60 分、登録時刻からの経過。last_hit_at では延長しない） |
| 件数上限超過 | LRU（last_hit_at が古い順）で追い出し |
| セッション終了 | キャッシュファイル削除 |
| mode=off 検出 | 起動時にキャッシュファイル削除 |
| 非対話セッション | force_disable_when_non_interactive: true のとき読み書きとも無効 |
| PostToolUse による事後無効化 | 保護領域汚染の原因となったエントリを削除（§18.3） |

TTL を登録時刻基準とし、ヒットによる延長を行わない理由：頻繁に使われる承認ほど長く生き続けると、長時間セッションで実質的に無期限の承認となるため。

### 14.10 データ構造

`${XDG_RUNTIME_DIR:-/tmp}/ticket-guard/approvals-<session_id>.json`　ファイル権限: 0600　ディレクトリ権限: 0700

```json
{
  "schema_version": 1,
  "session_id": "sess_01J...",
  "ticket": "PROJ-1234",
  "ticket_hash": "sha256:7c1e...",
  "config_hash": "a1b2c3d4e5f6",
  "effective_settings": {
    "mode": "implicit",
    "path_scope": "prefix",
    "ttl_minutes": 60,
    "max_entries": 100,
    "source": {
      "mode": "env:TICKET_GUARD_APPROVAL_CACHE",
      "path_scope": "project:config.yaml"
    },
    "clamped": [
      { "key": "mode", "requested": "all", "applied": "implicit",
        "by": "project.approval_cache.max_mode" }
    ]
  },
  "created_at": "2026-09-03T10:00:00+09:00",
  "entries": [
    {
      "key": "sha256:9f2a...",
      "decision": "allow",
      "ask_kind": "implicit",
      "reason_code": "IMPL_PATH_UNDEF",
      "access": "read",
      "subject": "/repo/tmp/build-output",
      "scope": "prefix",
      "approved_at": "2026-09-03T10:03:12+09:00",
      "expires_at": "2026-09-03T11:03:12+09:00",
      "hit_count": 4,
      "last_hit_at": "2026-09-03T10:41:08+09:00",
      "origin_call": "Read(tmp/build-output/report.json)"
    },
    {
      "key": "sha256:c71b...",
      "decision": "allow",
      "ask_kind": "implicit",
      "reason_code": "IMPL_PATH_UNDEF",
      "access": "exec",
      "subject": "/repo/scripts/seed.sh@sha256:44d..."
    }
  ]
}
```


---

## 15. 判定フロー統合図

```
[ ツール呼び出し ]
        │
        ▼
┌──────────────────────────┐
│ Layer 0-A: settings.json permissions │
│  deny 該当 → Hook 到達せず遮断        │
└──────────────────────────┘
        │
        ▼
┌──────────────────────────┐
│ 事前検証                             │
│  - Hook 完全性 OK？                  │
│      NG → 人間の確認ゲート（warn）    │
│           / 全 deny（strict）        │
│  - チケット承認済み？                 │
│      NG → 全 allow を ask に降格      │
└──────────────────────────┘
        │
        ▼
┌──────────────────────────┐
│ Stage 1: 全チェックを完走             │
│  ① Layer 0-B 不変 deny               │
│  ② sandbox 境界                      │
│  ③ ツール権限                        │
│  ④ パス権限（read/write/exec）        │
│  ⑤ シェル構文解析                    │
│  ⑥ DB 破壊キーワード                 │
│  ⑦ deny_commands                     │
└──────────────────────────┘
        │
Decision { deny[], ask_explicit[], ask_implicit[] }
        │
        ▼
   ┌──────────┐
   │ deny[] が空？ │
   └──────────┘
    NO │        │ YES
       ▼        ▼
🔴 deny 即停止   ┌──────────────┐
・キャッシュ無視  │ ask_* がすべて空？ │
・理由+代替案を   └──────────────┘
  LLM へ返却      YES │        │ NO
・監査ログ            ▼        ▼
                  🟢 allow  ┌──────────────┐
                            │ Stage 2:            │
                            │ キャッシュ対象判定    │
                            │ (mode / ask_once)   │
                            └──────────────┘
                        対象外 │        │ 対象
                              ▼        ▼
                        🟡/🟠 ask   ┌──────────┐
                          （毎回）   │ キャッシュ照会 │
                                    └──────────┘
                     全根拠 allow ヒット │   │ 未/部分ヒット
                                       ▼   ▼
                                  🟢 allow   🟠 ask
                                  hit_count++（未承認根拠のみ提示）
                     いずれか deny ヒット │   │
                                       ▼   ▼
                                    🔴 deny ← [ 対話？ ]
                                            YES │   │ NO (CI)
                                                ▼   ▼
                                    [ユーザー選択] [ASK_FALLBACK]
                                                     既定 deny
                                                │
                                                ▼
                                        ┌──────────┐
                                        │ Stage 3:      │
                                        │ キャッシュ登録 │
                                        │ scope 決定    │
                                        └──────────┘
```

---

## 16. 昇格試行・逸脱の検知と可視化

目的（P7）：未宣言領域をチケットに委譲する設計では、プロジェクト設定の列挙漏れが直接的な穴となる。列挙漏れを運用の中で検知・是正できるよう、逸脱を必ず人間の目に触れさせる。

### 16.1 検知タイミング

| タイミング | 対象 |
|---|---|
| セッション開始時 | `.current-ticket.md` の全記述 |
| チケット切替時 | 同上 |
| 環境変数解決時 | `TICKET_GUARD_*` の全指定 |

### 16.2 記録される事象

A. 昇格試行（却下されたもの）

| reason | 内容 |
|---|---|
| CEILING_TOOL | ticket の tools が Layer1 の明示宣言より緩い |
| CEILING_PARENT_DENY | ticket が Layer1 の deny サブツリー内に allow/ask を要求 |
| CEILING_EXPLICIT_ASK | ticket が Layer1 の明示 ask を allow にしようとした |
| CEILING_SANDBOX | ticket が sandbox 外に write/exec の allow を要求 |
| CEILING_BUILTIN | ticket が Layer 0-B 不変 deny 領域に allow を要求 |
| CEILING_CLAMP | approval_cache の値が max_* でクランプされた |
| IMMUTABLE_IGNORED | immutable 対象キーへの記述が無視された |
| INVALID_PATH | `..` / 絶対パス / `~` / `$` を含むパス指定 |
| RULE_LIMIT_EXCEEDED | ルール総数が max_ticket_rules を超過 |

B. 逸脱（許可されたが想定外のもの）

| reason | 内容 |
|---|---|
| SCOPE_DEVIATION | ticket が expected_roots 外に allow を宣言し、それが有効になった |
| SENSITIVE_AREA_GRANT | ticket が `.claude/**`・`.github/**`・`ci/**` 等の高影響領域に write allow を宣言し、有効になった |

B を独立して記録する理由：A は却下されるため実害は生じないが、B は実際に許可されている。したがって B の方が運用上の注意を要する。列挙漏れの是正候補として、A よりも優先的に人間へ提示する。

### 16.3 セッション開始時の表示

```
⚠  Ticket Guard: 権限に関する注意事項があります

ticket : PROJ-1234      risk : 58 / 100  (HIGH)

● 許可されたが想定範囲外の権限（2 件）★要確認

  1. [HIGH] write "tmp/scratch" → allow で有効
     SCOPE_DEVIATION
     expected_roots.write = [src, tests] の範囲外です。
     意図した作業であれば expected_roots への追加を検討してください

  2. [HIGH] write ".claude/skills" → allow で有効
     SENSITIVE_AREA_GRANT
     エージェント定義領域への書き込みが許可されています。
     スキル編集が作業目的でない場合、チケットを再生成してください

● 上限により却下された要求（3 件）

  3. [MED ] read "secrets/keys": allow → deny
     CEILING_PARENT_DENY  親 "secrets" が project 設定で deny

  4. [MED ] tools.Bash: allow → ask
     CEILING_TOOL  project が ask を明示宣言

  5. [LOW ] approval_cache.mode: all → implicit
     CEILING_CLAMP (max_mode = implicit)

────────────────────────────────
▸ 想定範囲外の許可が意図したものである場合
    config.yaml の expected_roots を PR で更新してください。
▸ 却下された要求が必要な場合
    config.yaml の target_directories / tools を PR で更新してください。
▸ いずれも意図していない場合
    チケットを再生成してください。risk が HIGH のため、
    生成ロジックまたは入力の点検も推奨します。
```

### 16.4 LLMへの通知

同内容を `additionalContext` として注入する。AI が利用不能な権限を前提とした試行を繰り返すことを防ぎ、代替手段の検討を促すため（P10）。

```
[Ticket Guard] 現在の実効権限は以下のとおりです。

■ 利用できない権限（要求は却下されました）
  - secrets/ 配下の read: プロジェクト設定で deny です。
    → 認証情報が必要な場合は環境変数経由での参照を検討してください。
  - Bash ツール: allow ではなく ask で動作します。
    → コマンド実行のたびに確認が入ります。

■ 無確認で書き込み可能な領域
  - src/**, tests/**, tmp/scratch/**

■ 上記以外への書き込みは確認が入ります。
■ ファイル生成はリダイレクト（>）ではなく Write ツールを使用してください。
```

### 16.5 監査ログ

```json
{
  "event": "TICKET_PERMISSION_REPORT",
  "ticket": "PROJ-1234",
  "ticket_hash": "sha256:7c1e...",
  "config_hash": "a1b2c3d4",
  "timestamp": "2026-09-03T10:00:00+09:00",
  "risk_score": 58,
  "risk_level": "HIGH",
  "deviations": [
    {
      "key": "target_directories.write.\"tmp/scratch\"",
      "applied": "allow",
      "reason": "SCOPE_DEVIATION",
      "detail": { "expected_roots_write": ["src", "tests"] },
      "severity": "high"
    },
    {
      "key": "target_directories.write.\".claude/skills\"",
      "applied": "allow",
      "reason": "SENSITIVE_AREA_GRANT",
      "severity": "high"
    }
  ],
  "rejections": [
    {
      "key": "target_directories.read.\"secrets/keys\"",
      "requested": "allow", "applied": "deny",
      "reason": "CEILING_PARENT_DENY", "detail": { "parent": "secrets" },
      "severity": "medium"
    },
    {
      "key": "tools.Bash",
      "requested": "allow", "applied": "ask",
      "reason": "CEILING_TOOL",
      "severity": "medium"
    },
    {
      "key": "approval_cache.mode",
      "requested": "all", "applied": "implicit",
      "reason": "CEILING_CLAMP", "detail": { "max": "implicit" },
      "severity": "low"
    }
  ]
}
```

運用方針：このログを外部ストレージへ転送し、以下を継続監視する。

| 監視対象 | 示唆 |
|---|---|
| deviations が繰り返し同一パスで発生 | expected_roots の列挙漏れ。設定を更新すべき |
| SENSITIVE_AREA_GRANT の発生 | エージェント定義領域への書き込み。意図の確認が必要 |
| risk_score >= 40 の頻発 | チケット生成プロンプトの問題、またはプロンプトインジェクションの兆候 |

---

## 17. チケット承認ゲート

### 17.1 設計方針

frontmatter 全文を目視させる設計を採らない。人間には「何が新たに許可されるか」「前回との差分」「リスク」だけを提示し、認知負荷を最小化することで承認の質を維持する。

未宣言領域をチケットに委譲する設計では、この承認画面が実質的な最後の人間判断ポイントとなる。したがって、判断に必要な情報を過不足なく、かつ短く提示することが要件となる。

### 17.2 承認画面

```
┌ Ticket 承認リクエスト ─────────────────
│ PROJ-1234: ユーザー設定画面のリファクタリング
│
│ ■ 理由（AI 記述）
│   src/components/Settings 配下のコンポーネント分割。
│   設定値の読み込みロジック確認のため config/ の read が必要。
│
│ ■ 無確認で書き込み可能になる領域
│   + src/components/Settings/**
│   + tmp/scratch/**              ⚠ expected_roots 範囲外
│
│ ■ 確認付きで書き込み可能になる領域
│   ~ src/components/**
│
│ ■ 無確認で読み取り可能になる領域
│   + src/**   docs/**
│
│ ■ ツール（project 設定からの変更のみ表示）
│   Bash: allow → ask に縮小（チケットによる自主制限）
│   WebSearch: allow（project 未指定領域）
│
│ ■ 前回チケット（PROJ-1198）からの差分
│   + src/components/Settings/**   新規
│   + tmp/scratch/**               新規
│   - tests/**                     削除
│
│ ■ 却下される要求: 0 件
│ ■ 想定範囲外の許可: 1 件（tmp/scratch）
│
│ ■ リスクスコア: 23 / 100  (MEDIUM)
│
│ [a] 承認   [e] 編集して承認   [r] 再生成   [d] 全文表示
│ [q] 中止
└──────────────────────────────────
```

「無確認で書き込み可能になる領域」を最上部に置く理由：これが実質的な被害上限を決める情報であり、人間が最初に見るべき項目であるため。

### 17.3 リスクスコア

| 要素 | 加点 | 理由 |
|---|---|---|
| 新規 write allow ルール 1 件につき | +5 | 影響範囲の広さに比例させるため |
| expected_roots 外への write allow 1 件につき | +15 | 委譲設計における主要な穴であるため |
| expected_roots 外への read allow 1 件につき | +5 | 書き込みより影響が小さいため |
| 高影響領域（.claude/**, .github/**, ci/**, Dockerfile, *.tf）への write allow | +35 | 実行環境・CI・エージェント定義への波及があるため |
| Layer1 の deny サブツリー内への allow 要求 1 件につき | +25 | 通常の作業では発生しえない要求であるため |
| Layer 0-B 不変 deny 領域への要求 1 件につき | +50 | 防御機構の偽装を試みる要求であるため |
| Layer1 の明示 ask を allow にする要求 1 件につき | +15 | 意図的な確認ポイントの無効化を試みるため |
| tools の緩和要求 1 件につき | +20 | |
| approval_cache の緩和要求 | +25 | 確認機構そのものの緩和であるため |
| sandbox 外を含む要求 | +30 | |
| ワイルドカード / `/` / `..` / `$HOME` を含むパス | +25 | 影響範囲が不定であるため |
| immutable 対象への記述 | +20 | |
| ルール総数が 20 件超 | +10 | レビュー困難化による承認形骸化を招くため |
| 前回チケットから write allow 領域が 2 倍以上に拡大 | +15 | 段階的な権限拡大の検知 |

| スコア | レベル | 挙動 |
|---|---|---|
| 0–19 | LOW | 通常承認 |
| 20–39 | MEDIUM | 通常承認（該当項目を警告表示） |
| 40–69 | HIGH | 二段階承認（§17.4） |
| 70+ | CRITICAL | 二段階承認 ＋ 監査ログへ severity: critical で記録 |


### 17.4 二段階承認

リスクスコアが `high_risk_threshold`（既定 40）を超える場合、`[a] 承認` のワンキーを無効化し、チケット ID の手入力を要求する。反射的な承認操作を物理的に阻止するため。

```
⚠ リスクスコア 58 (HIGH)。以下の高リスク項目が含まれます：

  ▸ .claude/skills への write allow（許可されます）
      エージェント定義の書き換えが可能になります
  ▸ tmp/scratch への write allow（許可されます）
      expected_roots の範囲外です

  これらは「却下される要求」ではなく「実際に許可される権限」です。
  作業内容に照らして必要か確認してください。

  承認する場合、チケット ID を入力してください: █
```

### 17.5 承認台帳とチケット完全性検証

承認台帳

```
.claude/approved-tickets.jsonl      ← Layer 0-B 不変 deny (write)
{"ticket":"PROJ-1234","frontmatter_hash":"sha256:7c1e...","config_hash":"a1b2c3d4","approved_
```


承認時、frontmatter を正規化（キーソート・空白正規化・コメント除去）して SHA-256 を算出し、台帳へ追記する。config_hash も記録する理由：同じチケットでも、その後 config.yaml が変更されていれば実効権限が変わるため、両者の組み合わせで承認の同一性を判定する。

実行時検証（毎回の PreToolUse）

```
現在の frontmatter_hash + config_hash を算出
├─ 台帳に一致するエントリあり ──→ 通常判定
└─ 一致なし ──────────────→ 【未承認チケット】
     ・全承認キャッシュを即時破棄
     ・すべての allow を ask に降格
     ・sandbox 外は従来どおり deny
     ・人間へ再承認を要求
     ・監査ログに TICKET_UNAPPROVED を記録
```

### 17.6 この対策の限界

承認台帳は Hook と同じユーザー権限で書かれるため、任意のシェル実行が可能な主体（脅威モデル D）に対しては防壁にならない。

| 防げる | 防げない |
|---|---|
| AI が Write / Edit ツールでチケットを書き換える | 未知の手段で deny をすり抜けたシェル書き込み |
| AI が sed -i / リダイレクトで書き換える | 外部プロセスによる直接改竄 |
| 承認前のチケットで作業が始まる事故 | — |
| チケット切替の見落とし | — |

本質的な保護には §23.3 の OS/FS 権限分離を併用する。

---

## 18. PostToolUse：事後変更監視

### 18.1 目的

PreToolUse の静的解析には原理的な限界がある。

| 検知できない経路 | 例 |
|---|---|
| ビルドツールの副作用 | npm run build が .env.production を生成する |
| 間接実行 | 許可されたスクリプトが内部で保護領域を書き換える |
| 解析漏れ | 想定していないコマンド形式・シェル構文 |

これらは実行前には判定不能であるため、実行後にファイルシステムの実態を検証する（P9）。

### 18.2 処理フロー

1. `git status --porcelain` を実行 → 未コミットの変更・新規追加・削除ファイル一覧を取得
2. 各変更ファイルについて権限を再判定 → Layer 0-B 不変 deny / Layer 1 の write:deny に該当するものを抽出 ※ ここでは Layer 1 までで判定する。チケットや承認キャッシュの影響を排除し、「プロジェクトが守ると宣言した領域」だけを見るため
3. 該当ありの場合: a. 応答 JSON の additionalContext に警告と復元指示を注入 b. 原因となった承認キャッシュエントリを無効化（§18.3） c. 監査ログに POST_VIOLATION を記録 d. 設定に応じて自動復元を実行（§18.4）
4. 該当なしの場合: → 何もせず終了

Git 管理外のファイル変更（.gitignore 対象、リポジトリ外）は本方式では検知できない。この限界は §23 に明記する。

### 18.3 承認キャッシュの事後無効化

保護領域の汚染が検知された場合、その実行を許可したキャッシュエントリを特定して削除する。

直前の PreToolUse でヒットしたキャッシュエントリを記録しておき、POST_VIOLATION 発生時に該当エントリを削除する。加えて、同一 subject の prefix エントリも削除する。

理由：一度でも保護領域の汚染を引き起こした承認は、以降も同じ結果を招く可能性が高い。キャッシュに残したまま繰り返し許可すると、被害が累積する。

### 18.4 自動復元

```yaml
post_tool_use:
  auto_restore: warn        # off / warn / auto
  restore_targets:
    - "Layer0B"             # 常に対象
    - "project_write_deny"
```

| モード | 挙動 |
|---|---|
| off | 検知と通知のみ |
| warn（既定） | 通知 ＋ LLM への復元指示。実際の復元は AI に行わせる |
| auto | `git checkout -- <path>` / 新規ファイルは削除を Hook が直接実行 |

既定を warn とする理由：自動復元は、正当な変更まで巻き戻すリスクがある。まず人間と AI に状況を伝え、判断の機会を与える。

### 18.5 LLM への通知

```
[Ticket Guard / PostToolUse] 保護領域が変更されました。

変更されたファイル:
  M  .env.production          (project 設定で write: deny)
  A  infra/terraform.tfstate  (project 設定で write: deny)

直前の実行: Bash(npm run build)

これらの変更は許可されていません。以下を実行してください:
  1. git checkout -- .env.production
  2. rm infra/terraform.tfstate
  3. ビルド設定を見直し、保護領域に出力しないよう修正する

この操作に対する承認キャッシュは無効化されました。
```

### 18.6 監査ログ

```json
{
  "event": "POST_VIOLATION",
  "ticket": "PROJ-1234",
  "config_hash": "a1b2c3d4",
  "timestamp": "2026-09-03T10:31:44+09:00",
  "triggering_call": "Bash(npm run build)",
  "violations": [
    { "path": ".env.production", "git_status": "M",
      "rule": "project.target_directories.write.\".env.*\"", "decision": "deny" },
    { "path": "infra/terraform.tfstate", "git_status": "A",
      "rule": "project.target_directories.write.\"infra\"", "decision": "deny" }
  ],
  "invalidated_cache_keys": ["sha256:c71b..."],
  "auto_restore": "warn"
}
```

---

## 19. 非対話セッション（CI/CD）制御

### 19.1 検出と挙動

対話的な確認ができない環境では、ask を人間に問えない。この場合の既定挙動を明確に定める。

非対話の判定条件（いずれか成立で非対話）

- 標準入力が TTY でない
- CI 環境変数が設定されている（CI / GITHUB_ACTIONS / GITLAB_CI 等）
- TICKET_GUARD_NON_INTERACTIVE=1 が明示指定されている

非対話時の挙動

| 項目 | 挙動 | 理由 |
|---|---|---|
| ask の解決 | TICKET_GUARD_ASK_FALLBACK（既定 deny） | 確認できない操作を許可に倒すと、CI が事実上の無制限実行環境になるため |
| 承認キャッシュ | force_disable_when_non_interactive: true のとき読み書きとも無効 | 承認する人間が存在しない環境でキャッシュを持つ意味がなく、誤って持ち込まれたキャッシュが悪用されうるため |
| チケット承認ゲート | 承認済みチケットのハッシュ照合のみ実施。未承認なら全 allow を ask に降格 → ASK_FALLBACK により deny | 事前に人間が承認したチケットのみが CI で有効になる |
| 昇格試行・逸脱レポート | 画面表示は省略し、監査ログと標準エラー出力に記録 | |
| Hook 完全性検証 | hook_integrity の設定に関わらずフェイルクローズ | 人間の確認ゲートが機能しないため |

### 19.2 CI 向け推奨設定

CI では「事前に承認された狭いチケット」を使い、ask が一切発生しない状態を目指す。ask が発生する＝設定不備、として扱う。

```yaml
# .claude/hooks/config.yaml（CI 用オーバーレイ）
approval_cache:
  mode: off
  force_disable_when_non_interactive: true
hook_integrity: strict
post_tool_use:
  auto_restore: auto
```

```bash
export TICKET_GUARD_ASK_FALLBACK=deny
export TICKET_GUARD_LOG_LEVEL=info
```

### 19.3 CI でのデバッグ支援

ask による deny が発生した場合、通常の deny と区別できるようログとエラー出力に明記する。

```
[Ticket Guard] 操作が拒否されました（非対話セッション）
  ツール : Write
  対象   : /repo/reports/output.json
  理由   : IMPL_PATH_UNDEF → ASK_FALLBACK=deny

対話環境であれば確認プロンプトが表示される操作です。
CI で実行する場合、チケットの target_directories.write に
"reports" を追加してください。
```

---

## 20. 判定例カタログ

### 20.1 前提設定

```yaml
# Layer 0-A: settings.json permissions.deny
#   WebFetch, Bash(sudo:*)

# Layer 1: config.yaml
sandbox_root: "."
expected_roots:
  read:  ["src", "tests", "docs"]
  write: ["src", "tests"]
tools:
  Bash: allow
  Write: allow
  Edit: allow
  Read: allow
target_directories:
  read:
    ".env": deny
    "secrets": deny
  write:
    ".env": deny
    ".env.*": deny
    "secrets": deny
    "infra": deny
    "docs": deny
    ".claude/hooks": deny
    ".claude/settings.json": deny
    ".current-ticket.md": deny
```

> 転記注記: この YAML は原文の画面下端で `".current-ticket.md": deny` の行まで写っており、その先（ticket 側の宣言など）は提供された写真に無い。

### 20.2 パスアクセス

| # | 操作 | 判定 | reason_code | 説明 |
|---|---|---|---|---|
| 1 | `Read: src/App.tsx` | 🟢 allow | — | 未宣言領域 + ticket allow |
| 2 | `Read: .env` | 🔴 deny | DENY_PATH | Layer1 明示宣言 |
| 3 | `Read: /usr/lib/python3/foo.py` | 🟠 ask | EXPL_SANDBOX_READ | sandbox 外 read。非キャッシュ |
| 4 | `Write: src/components/Btn.tsx` | 🟢 allow | — | ticket allow + expected_roots 内 |
| 5 | `Write: tmp/work/note.md` | 🟢 allow | — | ticket allow。expected_roots 外 → 逸脱として記録 |
| 6 | `Write: tmp/other/x.txt` | 🟠 ask | IMPL_PATH_UNDEF | どのレイヤも未言及 |
| 7 | `Write: docs/api.md` | 🔴 deny | DENY_PATH | Layer1 明示宣言 |
| 8 | `Write: migrations/0042.sql` | 🟠 ask | EXPL_PATH_ASK | Layer1 明示 ask。毎回確認 |
| 9 | `Write: .claude/skills/foo.md` | 🟠 ask | IMPL_PATH_UNDEF | .claude/hooks 等は deny だが skills は未宣言 |
| 10 | `Write: .claude/hooks/pre_tool_use.py` | 🔴 deny | DENY_PATH | Layer1 明示宣言 |
| 11 | `Write: .claude/approved-tickets.jsonl` | 🔴 deny | DENY_BUILTIN | Layer 0-B 不変 |
| 12 | `Write: /etc/hosts` | 🔴 deny | DENY_SANDBOX | sandbox 外 write |
| 13 | `Write: ../other-repo/x.ts` | 🔴 deny | DENY_SANDBOX | 正規化後 sandbox 外 |
| 14 | `Read: src/link（→/etc/passwd）` | 🟠 ask | IMPL_SYMLINK_ESCAPE<br>EXPL_SANDBOX_READ | 実体解決で sandbox 外。非キャッシュ |

### 20.3 Bashコマンド

| # | コマンド | 判定 | reason_code | 説明 |
|---|---|---|---|---|
| 15 | `npm run build` | 🟢 allow | — | 該当ルールなし。ただし PostToolUse で出力先を検証 |
| 16 | `git commit -m "Fix DROP bug"` | 🟢 allow | — | クォート内。DB クライアント非共起 |
| 17 | `grep -rn "DROP TABLE" ./src` | 🟢 allow | — | DB クライアント非共起 |
| 18 | `rm -rf src/components/old` | 🟢 allow | — | ticket allow 領域 |
| 19 | `rm -rf docs/legacy` | 🔴 deny | DENY_PATH | Layer1 write deny |
| 20 | `mv src/a.ts docs/a.ts` | 🔴 deny | DENY_PATH | 移動先が deny |
| 21 | `mv docs/a.md src/a.md` | 🔴 deny | DENY_PATH | 移動元が deny |
| 22 | `cp src/a.ts tmp/work/a.ts` | 🟢 allow | — | 元 read / 先 write ともに許可 |
| 23 | `echo "x" > src/a.txt` | 🔴 deny | DENY_REDIRECT | リダイレクト一律遮断。Write ツール使用を hint |
| 24 | `sed -i 's/a/b/' src/a.ts` | 🟢 allow | — | インプレース編集はパス判定へ |
| 25 | `sed -i 's/a/b/' .env` | 🔴 deny | DENY_PATH | |
| 26 | `psql -d mydb -c "SELECT 1"` | 🟢 allow | — | 破壊的キーワードなし |
| 27 | `echo "DROP TABLE u;" \| psql -d db` | 🔴 deny | DENY_DB_DESTRUCTIVE | 順序逆転でも共起判定で捕捉 |
| 28 | `psql -d db <<EOF` / `DROP TABLE u;` / `EOF` | 🔴 deny | DENY_REDIRECT<br>DENY_DB_DESTRUCTIVE | 2 経路で捕捉 |
| 29 | `psql -d db -f clean.sql`（中身に DROP） | 🔴 deny | DENY_DB_DESTRUCTIVE | ファイル内容を読んで再判定 |
| 30 | `psql -d db -f clean.sql`（読取不可） | 🟠 ask | IMPL_HASH_UNAVAILABLE | 非キャッシュ。毎回確認 |
| 31 | `npm run build && terraform destroy` | 🔴 deny | DENY_COMMAND_PATTERN | 連鎖分割で後段を検知 |
| 32 | `git push --force origin main` | 🔴 deny | DENY_COMMAND_PATTERN | |
| 33 | `bash scripts/seed.sh` | 🟠 ask | IMPL_PATH_UNDEF (exec) | 内容ハッシュ付き・exact |
| 34 | `bash scripts/seed.sh`（内容変更後） | 🟠 ask | 同上 | ハッシュ変化で再確認 |
| 35 | `rm -rf $TARGET` | 🟠 ask | IMPL_PARSE_UNCERTAIN | 変数展開で対象不定 |
| 36 | `rm -rf src/*.tmp` | 🟠 ask | IMPL_GLOB_UNRESOLVED | 展開結果に依存 |
| 37 | `sudo rm -rf /` | 🔴 遮断 | — | Layer 0-A。Hook 到達せず |
| 38 | `echo "x" > .git/hooks/pre-commit` | 🔴 deny | DENY_REDIRECT<br>DENY_BUILTIN | 2 経路で捕捉 |

### 20.4 ツール

| # | 操作 | 判定 | 説明 |
|---|---|---|---|
| 39 | `Bash(...)` | 🟢 allow | Layer1 allow + ticket 無指定 |
| 40 | `WebFetch(...)` | 🔴 遮断 | Layer 0-A |
| 41 | `WebSearch(...)` | 🟠 ask | Layer1 未記載 + ticket 無指定 → default_tool_ceiling |
| 42 | `Task(...)` | 🟠 ask | 同上 |

### 20.5 キャッシュ挙動

| # | シナリオ | 挙動 |
|---|---|---|
| 43 | Write: tmp/other/a.txt を prefix 承認 → Write: tmp/other/b.txt | 🟢 allow（キャッシュヒット） |
| 44 | 上記の後 Read: tmp/other/a.txt | 🟠 ask（access が異なる） |
| 45 | 上記の後 Write: tmp/another/c.txt | 🟠 ask（subject が異なる） |
| 46 | Write: migrations/x.sql を承認 → 再度同じ操作 | 🟡 ask（明示的 ask はキャッシュされない） |
| 47 | 承認後に config.yaml を編集 | 全キャッシュ失効（config_hash 変更） |
| 48 | 承認後に 61 分経過 | 該当エントリ失効（TTL） |
| 49 | ネガティブキャッシュ登録後、同一操作 | 🔴 deny（確認なし） |

---

## 21. 攻撃・すり抜けパターン別 防御マトリクス

| # | パターン | 検知層 | 判定 | 検知理由 |
|---|---|---|---|---|
| 1 | `psql * DROP *` の順序逆転 | PreToolUse ⑥ | 🔴 deny | コマンドとキーワードの共起判定（順序非依存） |
| 2 | ヒアドキュメント経由 | PreToolUse ②⑥ | 🔴 deny | リダイレクト遮断 + DOTALL 全文検索 |
| 3 | SQL ファイル経由（-f） | PreToolUse ⑥ | 🔴 deny | ファイル内容を読んで再判定 |
| 4 | スクリプト生成 → 実行 | PreToolUse ③ + 内容ハッシュ | 🟠 ask | exec 判定 + 内容変更で再確認 |
| 5 | 承認済みスクリプトの中身差し替え | 承認キャッシュ §14.6 | 🟠 ask | 内容ハッシュが変化し別キーになる |
| 6 | 連鎖コマンドの後段に破壊操作 | PreToolUse ① | 🔴 deny | `&&` `;` `\|` で分割して全数走査 |
| 7 | サブシェル `$(...)` 内に隠蔽 | PreToolUse ① | 🔴 deny | 再帰的に展開して解析 |
| 8 | シンボリックリンクで sandbox 脱出 | PreToolUse ② | 🔴 deny / 🟠 ask | 実体解決後に境界判定 + 境界変化を検知 |
| 9 | 相対パス `../` で外部アクセス | PreToolUse ② | 🔴 deny | 正規化後に境界判定 |
| 10 | チケットで tools を昇格 | §11.2 | 🟡 ask | Layer1 明示宣言との strictest |
| 11 | チケットで deny サブツリーをくり抜き | §11.1 順 3 | 🔴 deny | Layer1 単独ツリーで先に最長一致を確定 |
| 12 | チケットで明示 ask を allow 化 | §11.1 順 3 | 🟡 ask | strictest により ask 維持 |
| 13 | チケットで sandbox 外に allow | §11.1 順 2 | 🔴 deny | sandbox 境界が優先 |
| 14 | チケットで approval_cache を緩和 | §8.6 | クランプ | max_* で上限固定 |
| 15 | 環境変数で確認機構を無効化 | §10.2 | クランプ | 緩和方向は Layer1 上限でクランプ |
| 16 | チケット自身を書き換えて権限追記 | §17.5 | 全 ask 降格 | frontmatter_hash 不一致 → 未承認扱い |
| 17 | 承認台帳を書き換えて偽装 | Layer 0-B | 🔴 deny | 不変 deny |
| 18 | 承認キャッシュファイルに直接注入 | Layer 0-B | 🔴 deny | 不変 deny |
| 19 | `.git/hooks/pre-commit` に注入 | Layer 0-B | 🔴 deny | 不変 deny |
| 20 | Hook スクリプトを書き換え | Layer1 推奨 + §7.4 | 🔴 deny + 検知 | write deny + 完全性検証 |
| 21 | settings.json を書き換え | Layer1 推奨 | 🔴 deny | write deny |
| 22 | ビルド副作用で .env.production 生成 | PostToolUse | 🔴 検知 | git 差分で事後検知 + 復元指示 |
| 23 | 大量ルールでレビュー疲れを誘発 | §9.4 / §17.3 | 拒否 / 加点 | max_ticket_rules + リスクスコア |
| 24 | 段階的な権限拡大 | §17.3 | 加点 | 前回チケットとの差分比較 |
| 25 | `.claude/skills` への書き込みで挙動改変 | §16.2 / §17.3 | 🟠 ask + 可視化 | 未宣言なら ask。allow 宣言時は SENSITIVE_AREA_GRANT + 35 点 |
| 26 | expected_roots 外への allow を紛れ込ませる | §16.2 / §17.2 | allow + 可視化 | SCOPE_DEVIATION として承認画面と起動時に強調表示 |

---

## 22. セキュリティ不変条件と保証範囲

### 22.1 不変条件

実装は以下を常に満たさなければならない。テストで検証可能な形で記述する。

| # | 不変条件 |
|---|---|
| I-1 | Layer 0-B の 3 パスへの write は、いかなる設定・チケット・環境変数・キャッシュによっても許可されない |
| I-2 | sandbox 外への write / exec は、いかなる設定・チケット・環境変数・キャッシュによっても許可されない |
| I-3 | Layer 1 が明示宣言した deny は、チケット・環境変数によって ask / allow にならない |
| I-4 | Layer 1 が明示宣言した ask は、チケット・環境変数によって allow にならない |
| I-5 | 承認キャッシュは deny 判定を allow に変えない |
| I-6 | deny_commands は下位レイヤから削除・無効化されない |
| I-7 | approval_cache の実効値は Layer 1 の max_* を超えない |
| I-8 | 解析不能・判定不確定な入力は必ず ask 以上の厳しさになる |
| I-9 | チケットの frontmatter_hash と config_hash の組が承認台帳に存在しない場合、すべての allow が ask 以上に降格する |
| I-10 | 却下された昇格試行と、expected_roots を逸脱した許可は、必ず監査ログに記録される |
| I-11 | 内容ハッシュ対象コマンドの承認は、対象ファイルの内容が変化した時点で無効になる |

### 22.2 保証する範囲

| 保証内容 | 前提条件 |
|---|---|
| Layer 1 が明示宣言した保護領域は、チケット承認が完全に形骸化しても守られる | Layer 1 の変更が PR + レビューを経ること |
| sandbox 外への書き込みは発生しない | sandbox_extra_roots に広域パスを設定しないこと |
| 防御機構の判定結果の偽装は発生しない | 脅威モデル D が成立しないこと |

### 22.3 保証しない範囲

| 保証しない内容 | 理由 | 緩和策 |
|---|---|---|
| Layer 1 が列挙していない領域の保護 | 未宣言領域はチケットに委譲する設計であるため | expected_roots による逸脱可視化、リスクスコア、PostToolUse |
| Git 管理外ファイルの事後検知 | git status に現れないため | 重要な生成物は Git 管理下に置く、または target_directories に明示宣言する |
| 任意シェル実行が可能な主体への防御 | 同一ユーザー権限では原理的に境界にならない | §23.3 の OS/FS 権限分離 |
| Hook 自体のバグによる判定漏れ | — | Layer 0-A（settings.json）による二重防御 |
| ネットワーク経由の情報流出 | ファイルシステム操作の制御が対象範囲であるため | Layer 0-A で WebFetch を deny、ネットワークポリシー |

---

## 23. システムの限界と最終防衛策

### 23.1 設計上の限界

| # | 限界 | 影響 | 対応方針 |
|---|---|---|---|
| L-1 | プロジェクト設定の列挙品質が防御力を決める | target_directories に書き漏れた保護対象は、チケットが allow を宣言すれば許可される | テンプレート提供（付録 C）、CI での lint、expected_roots による逸脱監視、定期的な監査ログレビュー |
| L-2 | シェル構文解析の網羅性に限界がある | 想定外の構文・エイリアス・シェル関数による回避 | 解析不確定は ask に倒す（I-8）、PostToolUse による事後検証 |
| L-3 | Git 管理外の変更を検知できない | .gitignore 対象ファイルの汚染 | 重要ファイルは Git 管理下に置く |
| L-4 | 同一ユーザー権限では防御機構自体を守りきれない | 任意シェル実行が成立した時点で全防御が無効 | §23.3 |
| L-5 | ネットワーク経由の情報流出を防げない | 認証情報の外部送信 | Layer 0-A + ネットワーク層の制御 |
| L-6 | Hook の実行時間がツール呼び出しごとに加算される | 体験の劣化 | 内容ハッシュのサイズ上限、正規表現の事前コンパイル、判定結果のメモ化 |
| L-7 | 引用符の中身が演算子の文字だけの 1 語（`"<<"`）を素の演算子と区別できない | `grep -n "<<" README.md` のように、ヒアドキュメントに言及するだけのコマンドが deny になる | 許容する。止まる側に倒れ、文面が解析不確定であることを名乗る。対象をファイルへ逃がせば回避できる。§12.3 ① |

L-2 と L-7 は失敗の向きが逆で、扱いも逆になる。L-2 は素通り（実行を見逃す）で、
起きたことに気づけないので塞ぎ続ける。L-7 は誤検知（実行でないものを止める）で、
理由が返るので書き直せる。判断が付かないときに倒す先は常に L-7 の側とする。

### 23.2 運用上の推奨事項

| 推奨 | 目的 |
|---|---|
| config.yaml に CODEOWNERS を設定し、Code Owner レビューを必須にする | Layer 1 の変更が無審査で通ることを防ぐ |
| CI で config.yaml を lint する（sandbox_extra_roots の禁止値、expected_roots の広域指定など） | 設定ミスの早期検知 |
| 監査ログを外部ストレージへ転送し、SCOPE_DEVIATION / SENSITIVE_AREA_GRANT / risk_score >= 40 を継続監視する | 列挙漏れと異常なチケット生成の検知 |
| 恒久的に不要なツールは settings.json の permissions.deny に記載する | Hook より外側での二重防御 |
| チケットは作業単位で細かく分割する | 権限スコープの最小化とレビュー品質の維持 |
| 本番近傍・監査対象の作業では TICKET_GUARD_STRICT=1 を使用する | 確認機構の全面有効化 |

### 23.3 最終防衛策：OS / インフラ層

本システムはアプリケーション層のガードレールであり、それ自体を最終防衛線にしてはならない。以下を併用する。

| 層 | 対策 | 防ぐもの |
|---|---|---|
| ファイルシステム | `.claude/hooks/**`、settings.json、承認台帳を Claude Code 実行ユーザーとは別のオーナーにし、chmod 444 とする | 防御機構自身の書き換え（脅威モデル D への部分的対処） |
| 実行環境 | コンテナ / VM 内で実行し、ホストのファイルシステムをマウントしない | プロジェクト外領域への波及 |
| ユーザー権限 | 専用の低権限ユーザーで実行し、sudo を与えない | 特権昇格 |
| IAM | 開発環境の認証情報に本番リソースへの権限を与えない。本番操作は別クレデンシャル・別経路とする | クラウドインフラの破壊 |
| DB | 開発用接続には DROP / TRUNCATE 権限を付与しない。本番 DB への直接接続経路を持たせない | データ消失 |
| ネットワーク | 外部通信を許可リスト方式で制限する | 情報流出 |
| バックアップ | 本番 DB の PITR、リポジトリのミラーリング | 万一の際の復旧 |

IAM と DB 権限の設計が最も重要である。本システムがすべて突破されても、実行環境の認証情報が本番リソースを破壊できなければ、最悪の被害は発生しない。

---

## 付録A. 判定チートシート

### A.1 パス権限の決定順序

```
① Layer 0-B 不変 deny        → deny
② sandbox 外                 → write/exec: deny  /  read: ask（明示）
③ Layer1 に該当ルールあり     → strictest(Layer1, ticket)
                                 ※ ticket 無指定なら Layer1 値
④ ticket に明示ルールあり     → その値
⑤ いずれも該当なし            → ask（暗黙）
```

### A.2 ツール権限の決定順序

```
① settings.json permissions.deny → 遮断（Hook 到達せず）
② Layer1.tools に記載あり        → strictest(Layer1, ticket)
③ ticket に指定あり              → その値
④ いずれも指定なし               → default_tool_ceiling（既定 ask）
```

### A.3 「何を書けば何が守られるか」

| 守りたいもの | 書く場所 | 効果 |
|---|---|---|
| 恒久的に使わせないツール | settings.json の permissions.deny | Hook 到達前に遮断。最も硬い |
| 絶対に書き換えさせないファイル | config.yaml の target_directories.write: deny | チケットから緩められない |
| 毎回人間が見るべき対象 | config.yaml の target_directories: ask | チケットから allow にできない |
| 危険なコマンドパターン | config.yaml の deny_commands | 削除不能。全レイヤで結合 |
| 委譲してよい世界の外縁 | config.yaml の sandbox_root | 外部への write/exec を固定 deny |
| 想定作業範囲（強制なし） | config.yaml の expected_roots | 逸脱時に可視化・加点・キャッシュ降格 |
| 今回の作業で使う領域 | チケットの target_directories | 未宣言領域なら allow が成立 |

### A.4 askの分類早見

| 発生源 | 分類 | 既定でキャッシュ |
|---|---|---|
| 設定に ask と明記 | 明示的 | ✗（ask_once: true なら ✓） |
| sandbox 外の read | 明示的 | ✗（常に非キャッシュ） |
| どのレイヤも未言及 | 暗黙的 | ✓ |
| default_tool_ceiling | 暗黙的 | ✓ |
| 変数展開・glob で対象不定 | 暗黙的 | ✓ |
| シンボリックリンクの境界変化 | 暗黙的 | ✗（常に非キャッシュ） |
| 内容ハッシュ取得不可 | 暗黙的 | ✗（常に非キャッシュ） |

---

## 付録B. reason_code一覧

### B.1 deny系

| code | access | 発生条件 |
|---|---|---|
| DENY_BUILTIN | write | Layer 0-B 不変 deny に該当 |
| DENY_SANDBOX | write / exec | sandbox 外 |
| DENY_PATH | read / write / exec | Layer 1 または ticket が deny を宣言 |
| DENY_TOOL | tool | ツール権限が deny |
| DENY_REDIRECT | write | リダイレクト / ヒアドキュメント / tee |
| DENY_DB_DESTRUCTIVE | exec | DB クライアントと破壊的キーワードの共起 |
| DENY_COMMAND_PATTERN | exec | deny_commands の正規表現に一致 |

### B.2 明示的ask系

| code | access | 発生条件 |
|---|---|---|
| EXPL_PATH_ASK | read / write / exec | 設定に ask と明記 |
| EXPL_TOOL_ASK | tool | tools に ask と明記 |
| EXPL_SANDBOX_READ | read | sandbox 外の読み取り。常に非キャッシュ |

### B.3 暗黙的ask系

| code | access | 発生条件 | キャッシュ |
|---|---|---|---|
| IMPL_PATH_UNDEF | read / write / exec | どのレイヤも該当ルールを持たない | ✓ |
| IMPL_TOOL_UNDEF | tool | default_tool_ceiling によるフォールバック | ✓ |
| IMPL_PARSE_UNCERTAIN | exec | 変数展開等で対象パスが確定できない | ✓ |
| IMPL_GLOB_UNRESOLVED | read / write | ワイルドカードで対象が展開時に決まる | ✓ |
| IMPL_SYMLINK_ESCAPE | 全種 | 正規化前後で sandbox の内外が変化 | ✗ |
| IMPL_HASH_UNAVAILABLE | exec | 内容ハッシュが取得できない | ✗ |
| IMPL_TICKET_UNAPPROVED | 全種 | チケット未承認による allow → ask 降格 | ✗ |

### B.4 レポート系（判定ではなく記録）

| code | 内容 |
|---|---|
| CEILING_TOOL | ticket の tools 緩和要求が却下された |
| CEILING_PARENT_DENY | Layer1 の deny サブツリー内への要求が却下された |
| CEILING_EXPLICIT_ASK | Layer1 の明示 ask への allow 要求が却下された |
| CEILING_SANDBOX | sandbox 外への allow 要求が却下された |
| CEILING_BUILTIN | Layer 0-B 不変 deny への要求が却下された |
| CEILING_CLAMP | approval_cache が max_* でクランプされた |
| IMMUTABLE_IGNORED | immutable 対象への記述が無視された |
| INVALID_PATH | 不正なパス指定が無視された |
| RULE_LIMIT_EXCEEDED | ルール総数が上限を超過した |
| SCOPE_DEVIATION | expected_roots 外への allow が有効になった |
| SENSITIVE_AREA_GRANT | 高影響領域への write allow が有効になった |
| POST_VIOLATION | PostToolUse が保護領域の変更を検知した |
| TICKET_UNAPPROVED | チケットのハッシュが承認台帳と一致しない |
| HOOK_INTEGRITY_MISMATCH | Hook スクリプトのハッシュが不一致 |

---

## 付録C. 設定テンプレート

### C.1 .claude/settings.json

```json
{
  "permissions": {
    "deny": [
      "WebFetch",
      "Bash(sudo:*)",
      "Bash(curl:*)",
      "Bash(wget:*)",
      "Read(./.env)",
      "Read(./.env.*)",
      "Read(./secrets/**)"
    ]
  },
  "hooks": {
```


### C.2 .claude/hooks/config.yaml

```yaml
# ────────────────────────────────
# ① サンドボックス境界
#     ここで指定した範囲の外への write / exec は常に deny。
# ────────────────────────────────
sandbox_root: "."
sandbox_extra_roots: []

# ────────────────────────────────
# ② ツール権限
#     ここに記載したツールは、チケットから緩められない。
#     記載のないツールはチケットの宣言に従う。
# ────────────────────────────────
default_tool_ceiling: ask

tools:
  Read:      allow
  Glob:      allow
  Grep:      allow
  Edit:      allow
  Write:     allow
  MultiEdit: allow
  Bash:      allow
  Task:      ask
  WebSearch: ask

# ────────────────────────────────
# ③ 想定作業範囲（判定には影響しない）
#     チケットがこの範囲外に allow を宣言した場合、
#     承認画面での強調表示・リスク加点・キャッシュ降格を行う。
# ────────────────────────────────
expected_roots:
  read:  ["src", "tests", "docs", "public", "scripts"]
  write: ["src", "tests"]
  exec:  ["node_modules/.bin", "scripts"]

strict_delegation: false    # true にすると expected_roots が判定に作用する

# ────────────────────────────────
# ④ 絶対防衛線
#     ここに書いたものはチケットから緩められない。
#     ★ 守るべき対象をここに列挙することが防御の中核 ★
# ────────────────────────────────
target_directories:
```

> 転記注記: C.2 の YAML は原文のスクリーンショットが `target_directories:` の行で切れている。⑤〜⑧ に当たる残りの設定項目は取り込めていない。原本から補うこと。

### C.3 .current-ticket.md

```markdown
---
ticket: PROJ-1234
title: ユーザー設定画面のリファクタリング
rationale: |
  src/components/Settings 配下のコンポーネントを責務ごとに分割する。
  設定値の読み込みロジックを確認するため config/ の read が必要。

target_directories:
  read:
    "src": allow
    "docs": allow
  write:
    "src/components/Settings": allow
---

## 作業内容

1. Settings.tsx を SettingsForm / SettingsPreview に分割
2. 対応するテストを tests/components/Settings 配下に追加
3. Storybook のストーリーを更新

## 完了条件

- [ ] `npm test` が通ること
- [ ] 既存の振る舞いが変わっていないこと
```

作業中

- ファイル生成にはリダイレクト（`>`）を使わず、Write ツールを使うこと。
- 拒否された場合は同じ手段を繰り返さず、返された hint に従って代替手段を検討すること。
- PostToolUse から保護領域の変更を通知された場合は、作業を中断し、指示された復元手順を実行してから原因（ビルド設定等）を修正すること。
- 確認プロンプトが頻発する場合は、無理に回避しようとせず、必要な領域をチケットに追記して再承認を求めること。

やってはいけないこと

- Hook スクリプト・設定ファイル・承認台帳の書き換え。
- `.current-ticket.md` の自己編集による権限の追記。
- 制約を回避する目的でのスクリプト生成・間接実行。


---

## 付録D. トラブルシューティング

### D.1 症状別の対処

| 症状 | 原因 | 対処 |
|---|---|---|
| すべての操作で確認が出る | tools セクションが未記載で default_tool_ceiling: ask に落ちている | config.yaml の tools に使用ツールを明記する |
| 同じファイルで何度も確認が出る | 明示的 ask（ask_once: false）である／mode: off になっている／内容ハッシュ対象で内容が変化している | 意図した確認であればそのまま。不要なら ask_once: true に変更 |
| セッション途中で突然全部聞かれ出した | config.yaml またはチケットが編集され config_hash が変わった／TTL 超過 | 意図した編集であれば承認し直す。意図しない場合は差分を確認 |
| チケットに書いた allow が効かない | Layer 1 が同じパスに deny / ask を明示宣言している | 起動時レポートの CEILING_* を確認。必要なら config.yaml を PR で更新 |
| 「未承認チケット」と表示され全部 ask になる | frontmatter が編集された／config.yaml が変更された／チケットを切り替えた | 承認ゲートで再承認する |
| Hook 完全性エラーが出る | Hook スクリプトまたは config.yaml が変更された | 差分を確認し、意図した変更であれば承認して .integrity を更新 |
| CI だけ失敗する | 非対話セッションで ask が deny に落ちている | エラー出力の ASK_FALLBACK 表示を確認し、該当パスをチケットまたは config.yaml に追加 |
| `>` によるファイル生成が拒否される | DENY_REDIRECT（仕様） | Write ツールを使用する |
| psql が実行できない | DENY_DB_DESTRUCTIVE（破壊的キーワードとの共起） | 参照系のみなら該当キーワードを含めない。破壊操作が必要なら人間が別経路で実行 |
| ツール呼び出しが遅い | 内容ハッシュ計算・シンボリックリンク解決のコスト | max_hash_file_size_mb を下げる。content_hash_commands を必要最小限にする |

### D.2 診断コマンド

```bash
# 現在の実効権限を表示（判定は行わない）
python .claude/hooks/pre_tool_use.py --explain

# 特定の操作がどう判定されるかを試験
python .claude/hooks/pre_tool_use.py --dry-run \
  --tool Write --path src/components/A.tsx

python .claude/hooks/pre_tool_use.py --dry-run \
  --tool Bash --command 'rm -rf docs/legacy'

# config.yaml の妥当性検証
python .claude/hooks/pre_tool_use.py --lint

# 現在の承認キャッシュ一覧
python .claude/hooks/pre_tool_use.py --show-cache

# 承認キャッシュを手動クリア
python .claude/hooks/pre_tool_use.py --clear-cache

# チケットの承認状態とリスクスコアを確認
python .claude/hooks/pre_tool_use.py --check-ticket
```

`--explain` の出力例

```
Ticket Guard — 実効権限レポート
ticket : PROJ-1234（承認済み / risk 23 MEDIUM）
config_hash : a1b2c3d4e5f6
session : 対話 / cache=implicit scope=prefix ttl=60m

■ ツール
allow : Read Glob Grep Edit Write MultiEdit Bash
ask   : Task WebSearch
deny  : (settings.json) WebFetch

■ write
allow : src/components/Settings/**
ask   : package-lock.json migrations （明示）
```


### D.3 段階的な導入手順

いきなり全設定を有効にすると確認が頻発して定着しないため、以下の順で導入する。

| 段階 | 設定 | 目的 |
|---|---|---|
| 1. 観測 | tools を全 allow、target_directories は最小限（認証情報のみ deny）、post_tool_use.auto_restore: off、TICKET_GUARD_LOG_LEVEL=info | 実際にどのパス・コマンドが使われるかを監査ログで把握する |
| 2. 列挙 | 監査ログをもとに expected_roots を実態に合わせて設定。target_directories.write に保護対象を追記 | 逸脱検知の基準線を作る |
| 3. 遮断 | deny_commands を有効化。post_tool_use.auto_restore: warn | 破壊的コマンドの遮断を開始 |
| 4. 委譲 | チケット運用を開始。承認ゲートを有効化 | 作業単位のスコープ制御へ移行 |
| 5. 強化 | immutable の設定、hook_integrity: strict（本番近傍のみ）、CI での lint | 設定自体の保護 |

各段階で 1〜2 週間の運用を経て、SCOPE_DEVIATION の発生パターンから列挙漏れを是正してから次へ進む。

### D.4 設定lintの検証項目

CI で `--lint` を実行し、以下を検証する。

| # | 検証項目 | レベル |
|---|---|---|
| 1 | sandbox_extra_roots に禁止値（`/`, `/etc`, `$HOME` 等）が含まれていないか | error |
| 2 | expected_roots に `.` や `/` が含まれていないか | error |
| 3 | deny_commands の正規表現がコンパイル可能か | error |
| 4 | target_directories のパスに `..` や絶対パスが含まれていないか | error |
| 5 | max_* が既定値より緩くなっていないか | warn |
| 6 | tools セクションが存在するか | warn |
| 7 | `.claude/hooks` / settings.json / `.current-ticket.md` が write deny に含まれているか | warn |
| 8 | expected_roots と target_directories.deny が重複していないか | warn |
| 9 | immutable に主要項目が含まれているか | info |

---

## 補遺：本設計の要点

本システムの防御は、以下の 3 層構造で成り立つ。

```
┌────────────────────────────────┐
│【硬い層】変更に人間の PR を要する               │
│ - settings.json permissions                     │
│ - Layer 0-B 組み込み不変 deny（3 パス）          │
│ - config.yaml の target_directories / deny_commands │
│ - sandbox_root                                  │
│ → チケットからは一切緩められない                 │
└────────────────────────────────┘
┌────────────────────────────────┐
│【柔らかい層】チケットに委譲                     │
│ - 上記が言及していない sandbox 内の領域          │
│ - config.yaml の tools に記載のないツール        │
│ → チケットの宣言がそのまま有効                   │
│ → 沈黙していれば暗黙的 ask                       │
└────────────────────────────────┘
┌────────────────────────────────┐
│【監視層】判定に影響せず、逸脱を可視化            │
│ - expected_roots による逸脱検知                  │
│ - リスクスコアと二段階承認                       │
│ - PostToolUse による事後検証                     │
│ - 監査ログ                                       │
│ → 硬い層の列挙漏れを運用の中で是正する            │
└────────────────────────────────┘
```

硬い層に何を書くかが防御力を決め、監視層がその列挙漏れを見つける。柔らかい層の存在は、確認疲れによる承認の形骸化を避けて、硬い層と監視層を実際に機能させ続けるための設計上の選択である。

そのうえで、本システムはアプリケーション層のガードレールにすぎない。最終的な被害の上限は、§23.3 の IAM・DB 権限・実行環境の分離が決定する。
