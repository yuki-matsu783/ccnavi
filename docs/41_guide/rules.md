---
title: ルールの書き方
type: guide
description: rules.yml の書き方。id・レイヤー・強さ・additionalContext 系の欄・match と glob と regex・except と when・繰り返しの拒否と失敗の連絡
tags: [user-guide, rules, config]
keywords: [ルール, rules.yml, deny, ask, allow, id, レイヤー, additionalContext, additionalContextOnce, every, Stop, additionalContextFile, glob, regex, except, when, match, 権限モード, CCNAVI_DENY_REPEAT, CCNAVI_FAIL_STREAK_COUNT, CCNAVI_GUARD_UNWATCHED]
---

# ルールの書き方

ルールはccnaviが操作を止める・確認する・通すときに、何に適用して何を返すかを決める設定である。
ここはルールを書く人に要ることだけを書く。判定の中身は [設計書の 5 章](../21_design/rules.md)と [6 章](../21_design/pre-check.md)にある。
Bashの読み方とパスの見方は [Bashの読み方とパスの見方](bash-reading.md)に分けた。

## ルールファイルの形

ルールファイルはYAMLで、`deny` `ask` `allow`の 3 つのタイプに分かれる。
1 件のルールは、適用するツール・探すもの・見つけたときに返す文を 1 組で持つ。
`message`は`deny`だけに書く（必須）。止められたモデルに「なぜ止めたか、代わりに何を
するか」を伝える文で、`permissionDecisionReason`として届く。`ask`と`allow`に書くと
`--lint`がerrorにする（`ask`の文面はユーザの確認ダイアログにしか出ず、`allow`の文面はどこにも出ない）。
モデルに伝えたいことはタイプによらず`additionalContext`に書く。

```yaml
version: 1

deny:
  - id: force-push
    match: Bash
    glob: "*git push*--force*"
    message: リモートの履歴を書き換えます。送り直したい理由をユーザに伝えてください。

ask:
  - id: migrations
    match: Write|Edit
    glob: "*/migrations/*"
    additionalContext: 移行ファイルは実行前にユーザが中身を見る。何が変わるかを先に言うこと。

allow:
  - id: source
    match: Write|Edit
    glob: "*/src/*"
    additionalContext: src の下は自由に直してよい。ただし公開 API の名前を変えたら docs/api.md も直すこと。
```

`glob`と`regex`は必ず引用符で囲む。囲まないとYAMLが先に解釈する
（`<<`はマージキー、`*`はエイリアス、`&`はアンカー、`!`はタグ）。

### `id` と表記の決まり

- `id`は必須。書かないルールは読み込まず、`--lint`がerrorで名指しする（記録と文面にこの名前が出るため、名前の無いルールは記録も報告も指せない）
- `id`にコロンは書けない（レイヤーの名前を添えた`self:id` / `<プロジェクト名>:id`と見分けが付かないため）。
  `--lint`がerrorにし、その 1 件は読み込まれない。ルール・フェーズ定義・リスクの配点の 3 本とも同じ
- `glob`も`regex`も、どの機械でも大文字小文字を区別せずに適用する。`*/.ccnavi/*`は`.Ccnavi/`にもヒットする。
  `allow`も表記違いにまでヒットするので、既存のルールを持ち込むときは`--test-expectations`で、通したくないものが
  通っていないことを確かめる
- 区別が要る`regex`は、区別したい部分だけを`(?-i:...)`で囲む。`[\\/](?-i:strict)[\\/]`は
  `/strict/`にだけヒットする。否定の文字クラスは要注意で、`\.[^c\\/]`は`.C`まで除外して除外の側が広がる。
  `\.(?-i:[^c\\/])`と囲む
- コマンドの名前も区別しない。`CAT file`は`cat file`と同じ判定になる。区別する機械で`CAT`という別の
  実行ファイルが在れば、`cat`向けの`allow`がそれにもヒットする
- `common`と`self`はレイヤーの名前として予約してある。`projects/common/`や`projects/self/`は、表記違い（`projects/Self/`）を
  含めてレイヤーとして数えない。そこに置いた宣言は 1 件も適用されず、そのプロジェクトへの書き込みは共通レイヤーだけで判定される。
  `--lint`がerrorで名指しし、`ccnavi-user-agree.sh`はその`project:`を承認しない

### ルールは 3 つのレイヤーの和でヒットする

ルールとリスクの配点は 3 種のレイヤーに置ける。ヒットするのは共通レイヤー + そのツリーのレイヤーの和で、
足すだけ、上書き無し、厳しいほうが採られる。フェーズ定義も 3 種のレイヤーに置け、使うのは共通レイヤー + 親の`project:`が指すレイヤー
（空ならワークスペース自身のレイヤー）の和。設計は [11.4](../21_design/multi-repo/rules.md)、要求は
[要件REQ-MLT](../11_requirements/multi-repo.md)。

| レイヤー | 置き場 | 何を置くか |
|---|---|---|
| 共通レイヤー | `.ccnavi/config/common/{rules,phases,risks}.yml`（置き場は固定） | どのツリーにも適用するもの |
| ワークスペース自身のレイヤー | `<ワークスペースルート>/.ccnavi/config/project/{rules,phases,risks}.yml` | ワークスペース自身のツリーにだけ適用するもの |
| プロジェクトのレイヤー | `projects/<名前>/.ccnavi/config/project/{rules,phases,risks}.yml` | そのプロジェクトのツリーにだけ適用するもの |

作業・レビュー計画のテンプレートは、共通が`.ccnavi/config/common/templates/`、プロジェクトが`.ccnavi/config/project/templates/`に置く。この表の 3 本には入らず、`*/.ccnavi/*`の一般の保護で守られる。

3 レイヤーとも置き場は固定で、環境変数では動かない（[設定](settings.md)の「置き場は固定」）。
自身のレイヤーとプロジェクトのレイヤーは形が同じで、どちらもgitプロジェクトルートの直下に置く。

| ツール | ヒットするレイヤー |
|---|---|
| パスを持つツール: `Read` / `Grep` / `Glob` / `Write` / `Edit` / `NotebookEdit` | 共通レイヤー + 行き先の 1 レイヤー。行き先がプロジェクトの中ならそのレイヤー、ワークスペースのツリー（ワークスペースルートと、そこから切ったワークツリー）なら自身のレイヤー。`Grep`と`Glob`が探す場所を省いたときは`cwd`が行き先 |
| パスを持たないツール: `Bash` / `PowerShell` / `WebFetch` / `Skill` / `Agent` | 共通レイヤー + 自身のレイヤー + 全プロジェクトのレイヤー。どこに`cwd`があっても、`cd`を挟んでも同じ判定になる。あるレイヤーの`allow`は他のプロジェクトの作業にも適用される |

順は 共通レイヤー → 自身のレイヤー → プロジェクトのレイヤー（名前順）で、`deny` `ask` `allow`の順は変わらない。

つまずきやすい点は 2 つある。

- 読むのは元リポジトリにcheckoutされている版だけ。ワークツリーでレイヤーを直しても、統合されるまで反映されない。
  ワークツリーにしかないファイルは`--lint`がwarnで知らせる。承認済みの領域（`.ccnavi/approved/`の承認済みチケット・
  マーカー・子の記録・フロー）は数えない。そこは親のワークツリーの版が読まれる
- 同じ`id`で中身が違うルールは両方とも適用され、`--lint`がwarnで知らせる。`deny`と`ask`は増えるほうになる。
  リスクの配点も両方を数え、後ろのレイヤーの項目は`<レイヤー>:<id>`という名前になる。
  フェーズ定義は、共通レイヤーと同じ`id`で中身が違うか、`title`が重なれば`--lint`がerrorで言い、
  レイヤーの側のその定義だけを使わない（同じ`id`なら共通の定義を使い、そのレイヤーのほかの定義は使う。全欄が同じならinfoで、レイヤーの側を破棄する）

ファイルが無いのは「設定が無い」正常で、空として扱う。不正なレイヤーも空として扱い、`--lint`がerrorにする。
`id`に付く名前（共通は裸の`id`、自身は`self:id`、プロジェクトは`<名前>:id`）、同じ宣言の重複を 1 本にまとめる規則、
プロジェクトの`.ccnavi/config/common/`にコピーされるミラー、記録に出る名前の形は
[設計 11.4](../21_design/multi-repo/rules.md)と [共通設定の配布（11.12）](../21_design/multi-repo/distribute-common.md)にある。

できないこと: プロジェクトのレイヤーで共通レイヤーの`ask`を`allow`に緩める、共通レイヤーの`allow`をプロジェクトごとに外す、
他のプロジェクトのレイヤーをパスを持つツールの判定に足す（「AのルールをBにも」は共通レイヤーへ上げる）。

ccnaviディレクトリの下（既定なら`.ccnavi/`。共通レイヤーの`.ccnavi/config/common/`も入る）は、ルールに書かなくても書き込みが止まる
（設定 3 本と配点のスクリプトは判定の中身そのものなので。[コアファイルを守る](hooks.md)、設計は [self-guard](../21_design/self-guard.md)）。

## 利用場面でガイドを届ける

ルールは「止める」ためだけのものではなく、適切な作業へ導くためにも使う。すべてのルールや詳しい手順を最初からモデルのコンテキストに入れるのではなく、
そのルールが関係する操作や作業の場面で、必要な理由・注意点・手順を届ける。

- 操作の判定はccnaviのhook処理が行う。モデルのコンテキストの外で行うので、モデルがルールを読んで自発的に守ることには依存しない。
  「ルールをコンテキストに入れてから判定する」のではなく、「判定はコンテキストの外で行い、関係する場面で説明やガイドを届ける」
- 届け方は、止めたとき（`message`）、通すが踏まえてほしいとき（`additionalContext`）、確認のあと（`ask`はYesのときだけ届く）、ターンの終わり（`Stop`のルール）。
  この節のあとに続く 4 つの節の仕組みで、初回だけ（`additionalContextOnce`）、そのルールにヒットしたN回に 1 度（`every`）、
  文のあとにファイルの本文（上限 4000 字）を添える形に絞れる。同じ説明を毎回重ねないための設定で、減る量や守られる率は保証しない
- セッションの開始でも、作業の決まりの要点・文書の引き方・プロジェクトのスキル目録は短く渡す。詳しいことは要る場面で届けるか、引いてもらう（設計は [起動と設定](../21_design/startup-config.md)）
- 詳しい文書やスキルの本文は、エージェントが必要なときに検索して読む（[ドキュメントの索引](docs-search.md)）。これは届くきっかけが違う別の仕組みである

届け方の細部は [設計 6.6](../21_design/pre-check.md#66-モデルへ渡す文) にある。

## 通す・聞く・止めるのどれでも、一言添える

`additionalContext`は、そのルールにヒットしたときにモデルへ渡す文。`message`が止められた側への言葉なのに対し、
こちらは「通すが、これを踏まえて進めろ」を書く。どのタイプにも書ける。

| タイプ | どう届くか |
|---|---|
| `allow` | 応答の`additionalContext`として届く。判定の理由は無いので、これだけが届く |
| `ask` | ユーザがYesを押したときだけ`additionalContext`が届く。Noなら拒否の定型文だけが届いてターンが終わり、文は届かない。ダイアログには`[ccnavi] RULE_ASK (rule: …)`とsubjectが出る |
| `deny` | `permissionDecisionReason`（`message`）と一緒に`additionalContext`として届く |
| `dry-run`のとき | 止める代わりに返す文に続けて届く |

同じタイプに複数ヒットすれば、全部の文を空行で区切って並べる。`--test`と`--test --json`の
`response`で、書いた文が何と一緒に届くかが見える。

`additionalContextOnce`は、1 つの文脈で最初にヒットしたときだけ届く文。文脈はセッション 1 本で、
サブエージェントはその 1 回の起動ごとに別に数える（親で渡した文は子にも 1 度届く）。
セッションの開始（起動・再開・compactの後）で忘れ、改めて 1 度届く。記憶を置く場所が無いときは毎回届く。

`additionalContext`と両方書けば、初回は 2 つを空行で並べて届け、2 回目からは
`additionalContext`だけが届く。

```yaml
allow:
  - id: source
    match: Write|Edit
    glob: "*/src/*"
    additionalContext: src の下を直したら docs/api.md も見直すこと。
    additionalContextOnce: >-
      src の下は自由に直してよい。公開 API の名前を変えたら docs/api.md も直し、
      テストは tests/ に同じ名前で置く。CHANGELOG は締めるときにまとめて書く。
```

## 何回かに 1 度だけ渡す

`every: N`は「渡す回」の間隔を決める欄。そのルールがヒットした回数が`N`の倍数になった回だけが
渡す回になる。書かなければ間隔は 1。

| 欄 | いつ渡るか | `every: 5`のとき |
|---|---|---|
| `additionalContext` | 渡す回のたび | 5・10・15… 回目 |
| `additionalContextOnce` | 渡す回の最初の 1 回 | 5 回目だけ。10 回目には渡さない |

```yaml
allow:
  - id: nudge-rule-check
    match: Write|Edit|NotebookEdit
    glob: "*"
    every: 5
    additionalContext: >-
      ここまでに 5 件の編集があった。次へ進む前に、この変更が CLAUDE.md と
      docs/30_adr/ の決まりに沿っているかを Agent ツールで見てもらうこと。
```

- この例は`--lint`のwarn（「広いallowにadditionalContextがある」）を 1 件出すが、`every`を書いた
  ルールには該当しない指摘なので、`glob`は広いままでよい
- 数えるのは「そのルールがヒットした回数」。同じファイルを 5 回直せば 5。`Bash`を`match`に書かない限り
  シェルやビルドが書いたぶんは数えない
- 数えた回数は`SessionStart`（起動・再開・compactの後）で 0 に戻る。`additionalContext`に添えたときは届くのが
  遅れ（N回に届く前に 0 に戻ることが続けば一度も届かない）、`additionalContextOnce`に添えたときはセッション全体で 1 度より多く届く。
  正確にN回ごとを守る欄ではない
- `additionalContextOnce`に添えると、1 回目ではなくN回目に届く。「1 回目に言いたいこと」も要るならルールを 2 件に分ける
- 回数は文脈ごと（セッションと、サブエージェントならその起動）に数える。記憶を置く場所が無いときは数えずに毎回届く
- `0`・負・整数でない値はerrorで名指しし、判定は 1 として扱って通す。
  渡すものが 1 つも無い`every`はwarn

## ターンの終わりに止めて渡す

`match: Stop`の`allow`のルールは、メインエージェントのターンの終わり（`Stop`）にヒットする。
渡す回（`every`で決まる回）にだけターンを止め、ルールの文を渡す。
振り返り（`.ccnavi/docs/skill-review.md`）のための仕組みで、文の前にはccnaviが決まった前置きを付ける。
前置きの中身は、タスクの続きではないこと、振り返りだけをすること、ユーザへの問いで終わったターンならその問いを最後に書き直すこと、何も無ければ「振り返り: 無し」と書くこと。
適用する文字列は無いので`glob: "*"`と書く。

```yaml
allow:
  - id: skill-review-every-10
    match: Stop
    glob: "*"
    every: 10
    additionalContextFile: .ccnavi/docs/skill-review.md
```

- 読むのは共通レイヤーと自身のレイヤーだけ（外のリポジトリの 1 行でメインのターンを止めさせないため）。レイヤーの前置き（`self:`など）を除いたidが同じなら、先に読んだ共通レイヤーの 1 本だけ（同期で配ったコピーを二重に数えないため）
- `every`が 2 より小さいものは使わない。本文のファイルはワークスペースルートの版だけを読む
- 同じ`Stop`で`finish`の促し・レビュー依頼までの道の促し・サブエージェント結果の促し・直接作業の`integrate`の促し（[ターンの終わりの報告](hooks.md)）が止めるなら、そちらだけを出し、このルールの回数は進めない
- `dry-run`でも止める（止めないと文がモデルに届かないため）
- `--test Stop "(stop)"`と期待の`tool: Stop`で、使われるルールを確かめられる
- `deny` / `ask`に書いたもの（`Bash|Stop`なら`Stop`の部分）、プロジェクトのレイヤーに書いたもの、`(stop)`にヒットしない表記、`every`の無いものは`--lint`がwarnで知らせる

回数の記録の扱い、止めない場合（サブエージェント、記録を置けないときなど）は [設計 6.6](../21_design/pre-check.md#ターンの終わりに渡す文) にある。

## ファイルの本文を渡す

`additionalContextFile`と`additionalContextOnceFile`は、文の代わりに（または文に続けて）
ファイルの本文を渡す。それぞれ`additionalContext`と`additionalContextOnce`の後ろに、空行で区切って並ぶ。
onceの記憶は文とファイルで分けず、ルール 1 件で 1 度と数える。

```yaml
allow:
  - id: tests
    match: Write|Edit
    glob: "*/tests/*"
    additionalContextOnce: テストの決まりは次のとおり。
    additionalContextOnceFile: docs/testing.md
```

- パスはワークスペースルートからの相対で書く。絶対パスと`..`で上に出るパスは
  `--lint`がerrorにし、実行時も読まない
- 行き先（Bashならcwd）がワークツリーの中なら、まずワークツリーの同じパスを見る。
  無ければその元リポジトリ、最後にワークスペースルート
- ファイルが無ければ何も足さない。`--lint`はそのことをwarnで知らせる
- 読むのは先頭 4000 文字まで（固定）。超えたら先頭だけを載せ、末尾に「先頭だけを載せた。
  続きはこのファイルを読むこと」と添える。`--lint`は上限を超えるファイルにもwarnを出す
- `--test`と「判定を試す」には、読んだ本文がモデルに届くものと同じ形（切った状態）で出る

広い`allow`には書かない。ヒットした回ごとに同じ文がコンテキストに積まれる。`--lint`は、何にでもヒットする`allow`
（`glob: "*"`など）と選択肢が 3 つ以上ある`regex`（`(ls|cat|sed)`）に書いた
`additionalContext`をwarnにする。狭いルールに分けて、そのルールにだけ書く。

## 強さ

強い順に`deny` `ask` `allow`。どれにもヒットしなければ、ccnaviは判定を持たない。

| ヒットしたタイプ | 呼び出しはどうなるか |
|---|---|
| `deny` | 止まる |
| `ask` | ユーザに確認が出る（`RULE_ASK`） |
| `allow` | ccnaviは何も返さない。その先はClaude Codeの権限モードが決める |
| どこにもヒットしない | Claude Codeの権限モードに従う（`UNDECLARED`） |

1 件でも`deny`にヒットすれば拒否で、弱いタイプは見に行かない。同じタイプに複数
ヒットしたら全部の文面を返す。

`allow`はClaude Codeに「許可」を返すのではない。ccnaviが`permissionDecision`を返すのは`deny`と`ask`の
ときだけで、`allow`では何も返さない（`additionalContext`があればそれだけを添える）。その先はClaude Codeの
権限モードと`settings.json`の`permissions`が決めるので、`allow`を足してもClaude Code側の確認は飛ばせない。

`allow`を書くと消えるのは、ccnavi自身が出す確認と拒否。言及の無い呼び出しは、
知らないモードではccnaviが確認を出し、`dontAsk` / `bypassPermissions`では通さない
（次の節）。`allow`にヒットすればどちらも起きない。`auto` / `default` / `acceptEdits` / `plan`
は権限モードに渡すので変わらない。記録には`decision`が`allow`の行が残り、
`additionalContext`を適用する先にもなる。

ワークツリーに結び付いた承認済みチケットがあれば、その範囲の判定とも比べて強い側を採る。
ルールの`allow`にヒットしても、範囲の外なら止まる（[判定のキーはファイルの行き先](../21_design/tickets/judging-by-path.md)）。

`ask`にヒットした呼び出しは、権限モードによらず確認に出す（ユーザが意図して置いた確認ポイントなので）。

## ルールが言及していない呼び出し

ccnaviは判定を返さず、Claude Codeの権限モードに従う。

| `permission_mode` | 呼び出しはどうなるか |
|---|---|
| `auto` | classifierが判断する |
| `default` / `acceptEdits` / `plan` | Claude Code自身の権限の仕組み（`settings.json`の`permissions`と、モードごとの既定）が決める |
| 不明なモード / モードが来ない | ユーザに確認が出る |
| `dontAsk` / `bypassPermissions` | 通さない。`CCNAVI_GUARD_UNWATCHED=disable`なら委ねる |

記録の`decision`は、権限モードに渡した回が`handover`、ユーザに確認を出した回が`ask`、通さなかった回が`deny`で、渡した回は聞いた回と混ざらない。集計の例は [設計 6.5](../21_design/pre-check.md#65-ルールが言及しない呼び出し) にある。

確認に答える者が居ないモードでは、askを返すと誰も答えないまま通ってしまうため、通さない（REQ-PRE-08）。
知らないモードの名前は確認にする。

`CCNAVI_GUARD_UNWATCHED=disable`と書いたレイヤーでは、`dontAsk`と`bypassPermissions`でも判定を返さず、
そのモードの取り決めに委ねる。`--lint`が、切れていることをwarnで知らせる。

読み切れなかったコマンド（`PARSE_UNCERTAIN`）は、この設定でも権限モードに委ねない
（読めなかったという事実が委ねた先に伝わらないため）。

既定は許可ではなく、判断を誰かに渡すこと。`allow`は「このプロジェクトで普通にやること」を並べる場所で、
権限を配る場所ではない。1 行足すたびに、ccnaviが何も言わない範囲が広がる。

## `match` に書けるツールと対象

判定が対象を取り出せるのは次のツールだけ。名前はClaude Codeの権限ルール
`ToolName(指定子)`から括弧の中を除いたものに揃えてある。

| `match`に書く名前 | 適用する対象 |
|---|---|
| `Bash` `PowerShell` | コマンド（`Bash`はシェルとして読んでから適用する） |
| `Read` `Edit` `Write` `NotebookEdit` | ファイルのパス（行き着く先まで解いてから適用する） |
| `Grep` `Glob` | 探す場所のパス（`path`。省略されていれば呼び出し側のcwd） |
| `Skill` | スキル名 |
| `Agent` | 起動の見出し（`description`、無ければ`prompt`） |
| `WebFetch` | URL |
| `Stop` | 適用する文字列は無い（固定の`(stop)`。`glob: "*"`）。ツールではなく、`allow`に書いてターンの終わりに文を渡すためだけのもの（上の「ターンの終わりに止めて渡す」） |

`WebSearch`のように対象を取り出せないツールは判定されないまま通るので、
`allow`に書いても意味の無い行になる（`--lint`が指摘する）。`match`は
名前をそのまま比べるので、`Bash`のルールは`PowerShell`に及ばない。
及ぼしたければ`Bash|PowerShell`と並べる。

## `glob` の書き方

`glob`の意味は標準的なワイルドカードそのまま。`*`が任意の文字列、
`?`が 1 文字、`[abc]`が文字クラス。

文字列全体にヒットする。部分一致が欲しければ前後に`*`を自分で書く。
`git push`は素の`git push`にしかヒットせず、`cd /repo && git push`にはヒットしない。
`*git push*`と書けばヒットする。

語の切れ目は入らない。`*sed*`は`sedate`にもヒットする。右側だけなら空白を
書いて`*sed *`とすれば切れ目を表せる。左側はglobでは書けない（`*git push*`は`legit push`にもヒットし、
`* git push*`は行頭の`git push`が外れる）。左の切れ目が要るルールは`regex`を使う。

区切り文字だけは正規化する。`/`と書けば`\`にもヒットする（標準のワイルドカードの外で足している唯一の処理）。
`*/secrets/*`は`C:\repo\secrets\key`にもヒットする。

`{root}`はワークスペースルートに置き換わる。hookなら`CLAUDE_PROJECT_DIR`、ターミナルなら`--root`の
実パス。globなら`{root}/wip/*`、regexなら`^{root}[\\/]`のように書く。
区切りは`/`と`\`のどちらにもヒットし、大文字小文字はどの機械でも区別しない。
文面（`message` / `additionalContext` / `additionalContextOnce`）に書いた`{root}`も、モデルへ渡すときに
実パス（区切りは`/`）になる。拒否の文面でshを案内するときは`{script:ccnavi-git.sh}`のように`{script:<名前>}`と書く（`sh <引用したパス>`に置き換わる）。名前は`[A-Za-z0-9._-]+`だけである。形が違うものは置き換えず、スクリプトが無いものは置き換える。どちらも`--lint`がwarnする。`{root}`から書いてもよい。
`--explain`と`--test`は書いた表記のまま`{root}`を出す。このリポジトリのルールでは、
ワークスペースルート直下のWrite / Editと、シェルの書き込み先を止める`workspace-root`がこれを使っている。

## `regex` の書き方

書けないものは`glob`の代わりに`regex`に正規表現を書く。両方書いたルールは受け付けない。
先読み・後読み・後方参照は受け付けない（エンジンをまたいで同じ意味に保ち、組み合わせ爆発を避けるため。
判定の途中で止まったままのhookは期限に達し、呼び出しはそのまま通ってしまう）。
書き方の決まりは [設計 5.3](../21_design/rules.md#53-記法) にある。

## `except:` で除く

「AだがBは除く」は`except:`で書く。値は`glob`か`regex`のどちらか 1 つだけを持つマッピングのリストで、
本体にヒットしても、どれかにヒットすればそのルールはヒットしない。各パターンは本体と同じ読み方をする
（`{root}`・`{!root}`・大文字小文字の無視・先読みの禁止）。

```yaml
  - id: workspace-root
    match: Write|Edit|NotebookEdit
    regex: '^{root}[\\/]'
    except:
      - regex: '^{root}[\\/]wip[\\/]handoff(?:[\\/]|$)'
      - regex: '^{root}[\\/]\.claude[\\/]worktrees(?:[\\/]|$)'
    message: ...
```

適用する文字列は本体と同じで、`Bash`ならコマンド列の全体になる。除く語をコマンドのどこかに足すだけで外れるので、
`Bash`の`deny` / `ask`には付けないか、語の位置まで狭く書く（`--lint`がwarnで言う）。語の位置まで狭くとは、コマンド名から始めて
`[^\x00]*`でつなぎ、コマンドの切れ目を越えない形にすること（`ccnavi-worktree\.sh\b[^\x00]*\s--dry-run(\s|$)`）。この形の`except`は`--lint`が言わない。
ただし本体がヒットしたのとは別のコマンドで、同じコマンド名に除く語を付けた形にもヒットしうる。そのような複合コマンドで外れないかは、`rule-expectations.yml`で確かめる。
空のリスト、`glob`と`regex`の両方がある要素、知らないキーを持つ要素は、ルールごと読み込まずに`--lint`がerrorにする。

## `when:` で組み合わせる

「AかB」「AかつB」のような組み合わせは、`glob` / `regex`の代わりに`when:`で組み合わせ条件として書く。
ルールが持てるのは`glob` / `regex` / `when`のどれか 1 つ。ノードは次のどれか 1 つのキーだけを持つマッピングで、
`glob: '…'`と`regex: '…'`が個別条件（本体と同じ読み方）、`all: [ノード, …]`は全部に、`any: [ノード, …]`はどれかに
ヒットすればヒットし、`not: ノード`はヒットしなければヒットする。

```yaml
  - id: secrets
    match: Write|Edit
    when:
      any:
        - glob: '*.pem'
        - all:
            - glob: '*/secrets/*'
            - not: {glob: '*.md'}
    message: ...
```

葉が適用する文字列は本体と同じで、`Bash`ならコマンド列の全体。`except:`も併せて書け、組み合わせ条件にヒットしても除くものに
ヒットすればヒットしない。入れ子は 8 段、ノードは 64 個まで。知らないキー、キーが 0 個・2 個以上のノード、空のリスト、
型の違う値はルールごと読み込まずに`--lint`がerrorにする。何にでもヒットする・何にもヒットしないノードと、同じ個別条件を肯定と否定で並べた`all` / `any`と、
`Bash` / `PowerShell`の`deny` / `ask`で`not`を使った組み合わせ条件はwarnになる。

`except`と`when`の使い分け。「ここは除く」だけなら`except`のほうが読みやすい（このリポジトリの
`workspace-root`がこの形）。組み合わせが要るとき、除外の中にさらに例外があるときに`when`を使う。

## 同じ呼び出しを繰り返し止めたとき

止められたエージェントは、言い回しを少し変えて同じことを打ち直すことがある。同じルールが同じ呼び出しを
`CCNAVI_DENY_REPEAT`回（既定 3）止めたら、その回から拒否の文面の末尾に「言い換えて打ち直さず、ユーザに相談する」一文を足し、
ターンの終わりの`systemMessage`にもルールのidと回数を載せる。**判定は変わらない**（止めたものは止めたまま）。

- 数えるのは`enable`で実際に止めた`deny`だけ。`dry-run`と`ask`は数えない
- 引用符の違いと空白の量の違いは同じ呼び出しとして数える。大文字と小文字は別に数える
- 数えるのに失敗しても判定は変わらない

数えるキーと記録の置き場は [設計 6.4](../21_design/pre-check.md#64-判定の文面) にある。

## 失敗や拒否が続いたとき

エージェントは、コマンドが失敗したりccnaviに止められたりしても、資料を引かずに試行錯誤を重ねることがある。
同じセッションでツール呼び出しの失敗かccnaviの拒否が`CCNAVI_FAIL_STREAK_COUNT`回（既定 3）続けて起きたら、
その回に「資料の索引（`--docs`）を引く」案内を返す。**判定は変わらない**。

- 数えるのは、ツール自体の失敗（ユーザの中断を除く）と、`deny`。
  `deny`は`dry-run`でも数える（`dry-run`は判定を変えないので、案内を出してよい）。`ask`は数えない
- 成功した呼び出しで 0 に戻す。案内は同じ連続の中で 1 回だけで、連続が途切れると次の連続でまた出る
- 案内は失敗の回か、拒否の文面の末尾に付く。拒否の迂回は勧めず、止まった理由と決まった手順を
  資料で確かめ、分からなければユーザに相談するよう書く
- サブエージェントの中の呼び出しは、そのサブエージェントの連続として別に数える（案内はその文脈のモデルにしか届かないため）
- `0`にすると数えも案内もしない

数え方の細部は [設計 6.4](../21_design/pre-check.md#64-判定の文面) にある。環境変数の一覧は [設定](settings.md)にある。
