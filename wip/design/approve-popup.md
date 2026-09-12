# 承認をポップアップにし、承認の事実を Claude Code に伝える（設計）

親チケット `approve-popup` のフェーズ 1。実装（フェーズ 3）と受入テスト（フェーズ 2）が
迷わない粒度で、JSON の形・hook の控え・組み込み deny・拡張の操作の流れを決める。
決定の番号は親チケットの表と同じ。

## 1. いまと、変えたあと

| | いま | 変えたあと |
|---|---|---|
| 承認内容を見る | 拡張が統合ターミナルに `ccnavi --approve` を送り、端末に束が出る | 拡張が子プロセスで `--approve --preview --json` を打ち、ボードのオーバーレイに出す |
| 承認する | 人が端末で y を押す | 人がオーバーレイの「承認する」を押す。拡張が子プロセスで `--approve --yes <識別子,…> --json` を打つ |
| エージェントが `--approve` を打つ道 | 端末の壁（stdin が tty）と組み込み deny `builtin-guard-ticket-approval` | 同じ組み込み deny（`--yes` も当たる）。端末の壁は `--yes` に無い |
| 承認をモデルに伝える | 人がチャットで打つ | hook が次の UserPromptSubmit / PreToolUse で 1 度伝える。拡張は通知の 2 ボタンで文を渡す |

### 1.1 設計中に分かったこと（決定 1 の補正）

決定 1 は「Bash で `--approve` と `--yes` が同時に付く形だけを止める組み込み deny を足す」だった。
現物を読むと、`phase.ticket_approval_rule`（id `builtin-guard-ticket-approval`、`phase.py`）が
既に Bash / PowerShell からの `ccnavi … --approve` を**形を問わず**止めている
（`CCNAVI_GUARD_TICKET_APPROVAL=enable` のとき。`judge.py:170`）。`--yes` を足しても新しい
ルールは要らない。

一方、決定 1 の「`--preview` はエージェントが見てよい」は、この規則のままだと成り立たない
（`--approve --preview` も当たる）。そこで新しく足すのではなく、**既存の規則から
`--approve --preview` を除く**。結果は決定 1 と同じ（`--yes` は止まる、`--preview` は通る）で、
規則は 1 本のまま。

## 2. 実行ファイル

### 2.1 `--approve --preview --json`（読むだけ）

`approval.approve` の「束を組む」までを共有し、画面を出す代わりに JSON を書いて終わる。
写しは置かない。端末の壁（`_from_terminal`）は通らない（読むだけなので要らない）。
`--json` が無ければ今の `screen()` の文面をそのまま標準出力に出して終わる（端末の人向け）。

```json
{
  "version": 1,
  "root": "C:/…/ccnavi",
  "generated_at": "2026-09-12T19:00:00+0900",
  "batch": [
    {"ticket": "approve-popup", "title": "…", "parent": null, "phase": null,
     "revision": false, "tree": "approve-popup", "path": "C:/…/wip/tickets/todo/approve-popup.md"},
    {"ticket": "approve-popup-01", "title": "…", "parent": "approve-popup", "phase": 1,
     "revision": false, "tree": "approve-popup", "path": "…"}
  ],
  "text": "Ticket 承認リクエスト: 2 件\n\n== approve-popup: …",
  "rejected": [
    {"ticket": "i0002-03", "problems": ["`ccnavi/*` は種類 文書（docs）の範囲 … を超えている"]}
  ],
  "problems": ["wip/tickets/todo/x.md: frontmatter が読めない"]
}
```

| 鍵 | 何 |
|---|---|
| `version` | 承認の JSON の版。整数。`--yes` の答えと同じ番号 |
| `batch[]` | `--approve` が承認する束。`revision` は親の改版。空なら承認待ちが無い（exit 0 のまま。拡張が「無い」と出す） |
| `text` | `screen()` の本文そのまま。オーバーレイはこれを `<pre>` で出す。項目に分けない（画面の組み立ては実行ファイルの仕事のままにし、拡張は並べるだけ） |
| `rejected[]` | 承認の対象にしない提案と、その理由（今は標準エラーに出しているもの） |
| `problems[]` | 読めない提案・写しの説明（今は標準エラーに出しているもの） |

exit は、束が組めた（空でも）なら 0。チケット制御が disable、設定が読めない、は今と同じく 1。

### 2.2 `--approve --yes <識別子,…> --json`（承認する）

`--yes` は値を取る。値は preview で見せた `batch[].ticket` をカンマで並べたもの。
`--tickets` は提案の置き場の指定として既に使われているので使わない。

1. 束を組み直す（preview と同じ関数）
2. 束の識別子の集合と `--yes` の集合が**一致しなければ**承認せず exit 1。JSON に `mismatch` を書く
3. 一致すれば `_apply` を呼び、写しを置く（今の y を押した後と同じ）
4. `prompt`（§2.5）を作って JSON に書く。exit 0

```json
{"version": 1, "approved": ["approve-popup", "approve-popup-01"],
 "copies": ["C:/…/.claude/ccnavi/tickets/approve-popup.md", "…"],
 "lines": ["承認した。… に写しを置いた。", "この範囲は次のツール呼び出しから効く。"],
 "prompt": "…"}
```

```json
{"version": 1, "mismatch": {"expected": ["approve-popup"], "current": ["approve-popup", "approve-popup-01"]}}
```

- `--json` が無いときは今の `_apply` の文面を標準出力に出す。食い違いは標準エラーに 1 行
- `--yes` は端末の壁を通らない。`_from_terminal` の呼び出しを `--yes` の無いときに限る。
  `CCNAVI_GUARD_TICKET_APPROVAL` の意味は変えない（`--reviewed`、`accept`、`wrapup` と、素の
  `--approve` に効いたまま）
- `--yes` と `--preview` を同時に付けたら引数の誤りで exit 1

### 2.3 組み込み deny の変更

`phase._CLI_FORMS` に `--yes\b` の枝を足し、`--approve\b` には「`--preview` を伴わない」の
先読みを付ける。規則は Python の `re` で直接組むので先読みが使える（rules.yml の regex の
制約はここには掛からない）。

```python
_NOT_PREVIEW = r"(?![^\x00;&|\r\n]*--preview\b)"
rf"(--yes\b|--approve\b{_NOT_PREVIEW}|--reviewed\b|…)"
```

**免除は許さない側から書く。** 最初の案は `--approve\b(?![^\x00]*--preview\b)` の 1 本で、
「`--preview` があれば免除」を `--approve` と `--yes` の両方に掛けていた。これには 2 つ穴があり、
敵対的レビュー（読み取り専用サブエージェント）が見つけた。

| 穴 | どうなるか |
|---|---|
| 同じコマンドに両方書く | `ccnavi --approve --preview --yes a` が当たらない。承認そのものが免除される |
| PowerShell | `judge.screen` は Bash 以外を shellread に通さず生の文字列に当てる。`\x00` が無いので、`ccnavi --approve --yes a; ccnavi --approve --preview` の後ろの `--preview` が前の `--yes` を免除する |

直し方は 2 つ。承認そのもの（`--yes`）を独立した枝にして免除の対象から外すこと。免除の範囲を
コマンド 1 本に区切ること（`\x00` に加えて `;` `&` `|` 改行を区切りと見る）。許す側から条件を
書き足すと、条件が増えるたびに免除が広がる。止める側を先に固定して、そこから外れるものだけを
免除する。

見本は `.claude/ccnavi/rule-samples.yml` ではなく `tests/test_ticket.py`（組み込みなので）に置く。

| 見本 | ツール | 期待 |
|---|---|---|
| `uv run python -m ccnavi --approve --yes a,b --json` | Bash | deny |
| `dist/ccnavi/ccnavi --approve` | Bash | deny（今までどおり） |
| `ccnavi --approve --preview --yes a --json` | Bash | deny（同じコマンドでも免除しない） |
| `ccnavi --approve --yes a --preview` | Bash | deny |
| `ccnavi --approve --preview; ccnavi --approve --yes a` | Bash | deny（2 本目に当たる） |
| `ccnavi --approve --yes a,b --json; ccnavi --approve --preview` | PowerShell | deny |
| `ccnavi --approve; echo --preview` | PowerShell | deny |
| `uv run python -m ccnavi --approve --preview --json` | Bash / PowerShell | 当たらない |
| `echo --approve --preview` | Bash | 当たらない（ccnavi の起動ではない） |
| `apt-get install --yes git` | Bash | 当たらない（ccnavi の起動ではない） |

`rules.yml` の `guard-approved-tickets` の文面と `judge.py:519` の「ask the user to run 'ccnavi --approve'」は、
「利用者がボードで承認する」に直す（文書フェーズと一緒でよい）。

### 2.4 hook が「新しい承認」を 1 度だけ伝える

**控え。** `<state>/approved-<session>-<agent>.json`。`once-*.json` と同じ命名（`fsio.safe_name`、
agent は `main`）。中身は `{"known": {"approve-popup": "<印>", …}}` で、印は写しの `approved_at` と
`revised_at` を並べたもの。開いた写しと閉じた写しの両方を持つ。

**識別子だけでは足りない。** 親の改版（`revise_copy`、設計 §24.15.5）は写しを書き換えるだけで
識別子を増やさない。識別子の集合を比べる形では、計画が変わったという新しい合意を検知できない。
印まで見る。印を持たない古い控え（この形になる前のもの）は、識別子を伝えたものとして扱い、
改版とは見なさない。

**起点（決定 10）。** 控えが無い hook で、いまの写しを `known` に書き、何も伝えない。これが
「セッションの最初の hook 時点」。SessionStart は `ctxfile.forget` で `once-*` を捨てるが、
`approved-*` は捨てない（compact のあとに再度伝える文ではない。承認は 1 度知れば足りる）。
掃除は `forget` の古いファイルの間引きと同じ 30 日で一緒に消す。

**伝える。** `UserPromptSubmit` と `PreToolUse` で、印の変わった写しと新しい写しを「新しい承認」と
して文にし、控えに書き戻す。サブエージェント（`agent_id` あり）は控えを別に持つので、
起動後に初めて見た写しが起点になり、何も伝えない（サブエージェントはチケットを起こす立場にない）。

**閉じた写しも見る。** 承認の直後・次の hook の前に子が閉じることがある（承認してすぐ着手して
`done` にした形）。開いた写しだけを見ると、その承認は控えに吸われて誰にも伝わらない。

**壊れた控えは「無い」と同じに扱わない。** 読めるのに壊れているとき、起点として書き直すと、
まだ伝えていない承認ごと黙って消える。何も知らないことにして、その回に全部を伝える。
伝えすぎる側へ倒す。

**競合は受け入れる。** 読み・判定・書きを直列化していないので、同じセッションの hook が同時に
走ると同じ承認を 2 度伝えることがある。取るべきでないのは逆で、競合のために黙る形にはしない。

**どこで。**

- `events.decide_at_prompt`: いまは何も返さない設計。新しい承認があるときだけ
  `hookio.write_context(stdout, USER_PROMPT_SUBMIT, text)` を出す。無ければ今までどおり黙る
- `judge.decide_before`: 判定の応答は 1 つの JSON なので、文は既存の `context` に合流させる。
  `ctxfile.for_rules` の直後で `news = approval.news(stderr, conf, payload)` を取り、
  `context = "\n\n".join(p for p in (news, context) if p)`。ALLOW / DENY / ASK / dry-run のどの経路でも
  `context` は届く。HANDOVER だけは文を返さない経路なので、そこでは `notices` に足す
  （HANDOVER で `notices` が空でも `news` があれば `write_context` する）
- `SessionStart`: 控えが無ければ起点を作るだけ。文は出さない

**関数。** `approval.news(stderr, conf, payload) -> str`。控えの読み書きと差分をここに閉じる。
`conf.state` が空（`--state ""`）なら控えを持てないので何も伝えない（診断で記録を汚さない側に倒す）。

### 2.5 Claude Code に渡す文（決定 8）

`reasons.approved(tickets: list[Ticket], approved_dir: str) -> str`。`--yes` の `prompt` と hook の
`news` が同じ関数を呼ぶ。

```
[ccnavi] チケットが承認され、写しが置かれた（次のツール呼び出しから効く）。
- approve-popup: チケットの承認を拡張のポップアップで行い、…（親）
- approve-popup-01: 承認ポップアップの設計（…）（親 approve-popup、フェーズ 1）
後工程を進める。子は作業ツリー .claude/worktrees/<識別子> を親のブランチから切り、
sh .claude/scripts/ccnavi-ticket.sh start <識別子> で着手する。
```

改版（`revision`）の行は「全体計画を改版した」「フィードバック計画を改版した」に変える。
文は日本語。既存の `ways_of_working` と同じ調子（`[ccnavi]` で始める）。

### 2.6 触るファイル（実行ファイル）

| ファイル | 何 |
|---|---|
| `cli.py` | `--preview`、`--yes <値>` を足す。`--approve` の分岐で `--yes` なら `_from_terminal` を飛ばす |
| `approval.py` | `approve` を「束を組む」「見せる」「承認する」に割る。`preview_json`、`approve_yes`、`news` を足す |
| `reasons.py` | `approved` を足す |
| `phase.py` | `_CLI_FORMS` の `--approve` に `--preview` の除外 |
| `events.py` | `decide_at_prompt` と `decide_at_start` に控えの処理 |
| `judge.py` | `decide_before` で `news` を `context` に合流 |
| `ctxfile.py` | `forget` の間引きに `approved-*` を含める |

## 3. 拡張

### 3.1 消すもの（決定 3、4）

| 何 | どこ |
|---|---|
| コマンド `ccnaviBoard.approve` | `package.json`（commands、menus）、`extension.ts`、`board-panel.ts` の `approveFromPalette` |
| ターミナルに `--approve` を送る経路 | `core/commands.ts` の `approveCommand`、`board-panel.ts` の `sendApprove`。`terminal.ts` は accept / clone / fetch / pull が使うので残す（見出しも直す） |
| README の該当行 | コマンド一覧、「人の承認はボタンから統合ターミナルへ」の段、手動確認 #4 |

### 3.2 子プロセス（`ccnavi.ts`）

既存の `run` を使い、2 つ足す。

```ts
runApprovePreview(root, setting): Promise<RunResult<ApprovePreview>>
  → run(launcher, root, ["--approve", "--preview", "--json"])
runApproveYes(root, setting, tickets: readonly string[]): Promise<ApproveOutcome>
  → run(launcher, root, ["--approve", "--yes", tickets.join(","), "--json"])
```

`ApproveOutcome` は `{ok: true, value: ApproveResult}` / `{ok: false, mismatch: {expected, current}}` /
`{ok: false, error}` の 3 つ。exit 1 で標準出力が `mismatch` を持つ JSON なら 2 つめ。
どちらも `--state` `--log` は外さない（承認は記録に残すもの。試し打ちではない）。

### 3.3 形の読み取り（`core/approvemodel.ts`）

`model.ts` と同じ流儀で `parseApprovePreview(text)` / `parseApproveResult(text)` を置き、
`APPROVE_VERSION = 1` と違えば読まない。フィクスチャは `test/fixtures/` の
`approve-preview.json` / `approve-yes.json` / `approve-mismatch.json` の 3 つ。食い違いの形も拡張が
読むので写す。Python 側 `tests/test_approve_json.py` が `CCNAVI_BOARD_FIXTURE=1` で書き出し、
同じ例で形を確かめる（`test_board.py` と同じ仕組み）。照合は鍵の名前だけでなく値まで見る。
時刻の欄は固定の綴りで書き、形が変わらない限り差分が出ないようにする。

### 3.4 オーバーレイ（決定 3、11）

Webview からのメッセージを 3 つ足し、`approve` は意味を変える。

| Webview → 拡張 | 何が起きるか |
|---|---|
| `approve` | `runApprovePreview` を走らせ、結果を `PanelState.approval` に持って描き直す |
| `approveConfirm {tickets}` | `runApproveYes`。成功なら `approval` を消して描き直し、通知（§3.5）。`mismatch` なら preview を取り直して `approval.notice = "束が変わった。見直してから承認する"` で描き直す。失敗なら `approval.error` に文を置いて描き直す |
| `approveCancel` | `approval` を消して描き直す |

**状態は拡張側（`PanelState.approval`）に持つ。** 監視の更新で HTML が作り直されてもオーバーレイが
消えないようにするため。`renderBoard(board, {nonce, approval})` が `approval` を受け取り、あれば
ボードの上に被せて描く。

```ts
type ApprovalOverlay =
  | { kind: "loading" }
  | { kind: "preview"; preview: ApprovePreview; notice?: string }
  | { kind: "approving"; preview: ApprovePreview }
  | { kind: "error"; error: string };
```

描くもの（`core/render.ts` の `renderApproval`）。

- 見出し「Ticket 承認リクエスト: N 件」と、`batch[]` の識別子・題・親/フェーズの表
- `text` を `<pre>` で（等幅、折り返し、縦にスクロール）
- `rejected[]` を「承認の対象にしない」の枠に、`problems[]` を「読めない提案」の枠に。どちらも無ければ出さない
- `notice`（束が変わった）は上部に黄で
- ボタン「この N 件を承認する」（`approving` の間は押せない。`batch` が空なら出さず「承認待ちは無い」）と「やめる」
- Esc で `approveCancel`

ボードの「承認待ち N 件を承認」とカードの「承認」は今までどおり `approve` を送る（束は常に全部）。

### 3.5 承認後の通知（決定 7）

`runApproveYes` が成功したら `vscode.window.showInformationMessage` に 2 ボタン。押すまで何もしない。

| ボタン | 何 |
|---|---|
| コピー | `vscode.env.clipboard.writeText(result.prompt)`。進行中の Claude Code のセッションに貼る用 |
| 新しいセッションで開く | `vscode.env.openExternal(vscode.Uri.parse("vscode://anthropic.claude-code/open?prompt=" + encodeURIComponent(result.prompt)))`。新しいタブに文が埋まる。送信は人が Enter |

文面は「N 件を承認した。Claude Code に伝える文を用意した」。ボードの読み直しは写しの監視が起こす
（今と同じ）。`vscode://anthropic.claude-code/open?prompt=` は Claude Code の公開の URI で、
送信までは自動にならない（調査済み。走っているセッションへ送る API は無い）。

### 3.6 触るファイル（拡張）

| ファイル | 何 |
|---|---|
| `package.json` | `ccnaviBoard.approve` を消す。`version` を上げる |
| `extension.ts` | 登録を消す |
| `board-panel.ts` | `approval` の状態、3 つのメッセージ、通知 |
| `ccnavi.ts` | `runApprovePreview` / `runApproveYes` |
| `core/commands.ts` | `approveCommand` を消し、`approveArgs(tickets)`（引数の並び）を足す |
| `core/approvemodel.ts` | 新規 |
| `core/render.ts` | `renderApproval` と、スクリプトに confirm / cancel / Esc |
| `test/approvemodel.test.ts`、`render.test.ts`、`commands.test.ts` | CB-T104 以降（main が CB-T103 まで使っていた） |
| `README.md` | §3.1 と、オーバーレイと通知の説明、手動確認 #4 の書き換え |

## 4. 文書に触る場所（フェーズ 4）

| 文書 | 何 |
|---|---|
| `README.md`（実行ファイル） | 「効くのは承認したものだけ」の段に preview / yes。「ボードの JSON」の隣に「承認の JSON」（§2.1、§2.2 の表）。hook が承認を伝えること |
| `ccnavi.md` §17 | 端末の壁の位置づけ。`--yes` は壁を持たず、組み込み deny がエージェントの経路を塞ぐ。dry-run では止まらない（受け入れた）。§17.7 と §17.8 として書いた |
| `ccnavi.md` §24.10 | 「拡張の子プロセスを壁の外に置く」を「承認は拡張の子プロセスが打つ。accept は端末のまま」に |
| `requirements.md` | REQ-APV-06（人が押したことの証拠）、REQ-APV-07（承認を 1 度伝える）、REQ-DIA-10（preview の形） |
| `vscode-extension/ccnavi-board/README.md` | §3.1、§3.4、§3.5（フェーズ 3 で直した） |
| `ccnavi/judge.py` | 範囲外の書き込みを止めたときの英文（「利用者に `--approve` を打つよう頼め」を、ボードでの承認に） |
| `HANDOVER.md` | 経緯 |

## 5. 受入テスト（フェーズ 2 の入力）

Python（`tests/`）。

| # | 何 | 期待 |
|---|---|---|
| A1 | `--approve --preview --json` を承認待ち 2 件で | `batch` に 2 件、`text` に `Ticket 承認リクエスト: 2 件`、写しは置かれない、exit 0 |
| A2 | 同じく承認待ち 0 件 | `batch: []`、exit 0 |
| A3 | `--approve --yes a,b --json`（束と一致） | 写しが 2 枚置かれ、`approved` に 2 件、`prompt` に両方の識別子、exit 0 |
| A4 | `--approve --yes a --json`（束は a,b） | 写しが置かれない、`mismatch.current == [a,b]`、exit 1 |
| A5 | `--yes` を stdin が tty でない状態で | A3 と同じ（壁を通らない） |
| A6 | 素の `--approve` を tty でない stdin で | 今までどおり止まる |
| A7 | Bash `… ccnavi --approve --yes a --json` を PreToolUse で | deny、rule `builtin-guard-ticket-approval` |
| A8 | Bash `… ccnavi --approve --preview --json` を PreToolUse で | その規則に当たらない |
| A7b | 同じコマンドに `--preview` と `--yes` を並べる | deny（免除しない） |
| A7c | PowerShell で、後続のコマンドに `--preview` を書く | deny（免除がコマンドをまたがない） |
| A8b | ccnavi の起動でない `--yes`（`apt-get install --yes git`） | その規則に当たらない |
| A9 | 承認の後の UserPromptSubmit | `additionalContext` に `prompt` と同じ文。もう 1 度 UserPromptSubmit しても出ない |
| A10 | 承認の後の PreToolUse（allow になる呼び出し） | `additionalContext` に同じ文が 1 度だけ |
| A11 | セッションの最初の hook の前に置いた写し | 伝えない |
| A12 | 別の `session_id` | それぞれ 1 度ずつ伝える |
| A13 | `--state ""` | 伝えない、控えも作らない |
| A14 | フィクスチャ | `approve-preview.json` / `approve-yes.json` / `approve-mismatch.json` と鍵も値も一致 |
| A15 | 親の改版を承認した後の UserPromptSubmit | 改版として 1 度だけ伝える |
| A16 | 承認の直後・次の hook の前に閉じた写し | 1 度だけ伝える |
| A17 | 控えを壊してから hook | まだ伝えていない承認を伝える（黙って起点化しない） |

拡張（`test/`、`core` だけ）。

| # | 何 | 期待 |
|---|---|---|
| B1 | `parseApprovePreview` にフィクスチャ | 読める。`version` 違いは読まない |
| B2 | `parseApproveResult` に成功 / mismatch の JSON | それぞれの形に読める |
| B3 | `approveArgs(["a","b"])` | `["--approve","--yes","a,b","--json"]` |
| B4 | `renderBoard(board, {approval: preview})` | オーバーレイに識別子・`<pre>` の本文・rejected の枠。`batch` が空なら承認ボタンが無い |
| B5 | `renderBoard(board, {})` | オーバーレイが無い |
| B6 | `approveCommand` が無い | export されていない（消したことの確認） |

## 6. 実装の順序（フェーズ 3）

1. 実行ファイル: `approval.py` の分割 → `--preview` → `--yes` → `phase.py` の除外 → `news` と hook
2. フィクスチャを書き出す
3. 拡張: `approvemodel.ts` → `ccnavi.ts` → `render.ts` → `board-panel.ts` → 消すもの
4. `pnpm test` と `uv run python -m unittest`、拡張開発ホストで手動確認（承認 → 通知 → コピー / 新しいセッション）
