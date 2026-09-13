# 設計: 承認済みチケットを運ぶ sh の切り出しと、範囲の超過を判定で止める

親チケット `approve-carry`、フェーズ 1（設計）。改める節は ccnavi.md §9.4・§9.5・§9.7 と、
requirements.md の REQ-TKT-04・REQ-TKT-30。

## 0. 何を変えるか（要約）

| | 今 | 後 |
|---|---|---|
| 承認済みチケットのコミットと push | `ccnavi-approve.sh` の後半だけ。ボードの承認では起きない | `ccnavi-push-approved.sh` に切り出す。端末の承認は sh から、ボードの承認は端末送りで呼ぶ |
| エージェントが運ぶ sh を打つ | ― | `DENY_TICKET_APPROVAL_CLI` で止める |
| 子の範囲が親の範囲を超える | 承認で error（束から外す） | 承認で warn。判定は今までどおり親で切り詰める |
| 子の範囲が種類の scope を超える | 承認で error | 承認で warn。**判定で種類の scope でも切り詰める**（新規） |
| 子の範囲が regex | 承認で error | 承認で warn。判定は実際のパスで親と種類に当てる |
| dry-run | 承認は超過で止まる | 承認は通る。判定は今の dry-run の文面で「enable なら止めた」と言って通す |

## 1. `ccnavi-push-approved.sh`

### 1.1 役割

承認済みチケットの置き場（`$CCNAVI_APPROVED`、既定 `.ccnavi/tickets`）に変更があるツリーごとに、
その置き場だけをコミットし、保護されたブランチでなければ push する。今の
`ccnavi-approve.sh:74-121` をそのまま移す。承認はしない。実行ファイルも起動しない。

名前は処理を言う。対になるのはセッションの頭に取ってくる `ccnavi-fetch.sh`。

### 1.2 形

```sh
sh .ccnavi/scripts/ccnavi-push-approved.sh
```

- 引数は取らない（`-h` / `--help` / `help` だけ）。`ccnavi-approve.sh` と同じ
- ワークスペースルートは `ccnavi-common.sh` の `ccnavi_workspace` で探す。見つからなければ 2
- 数えるツリー: ワークスペース、`$CCNAVI_PROJECTS/*`、`.claude/worktrees/*`（今と同じ）
- ツリーごとに `git status --porcelain -- "$approved"` が空でなければ `git add -- "$approved"` →
  `git commit -m "ccnavi: 承認済みチケットを更新"`。パスを限る。`-a` も `add -A` も使わない
- ブランチの上に居ない（detached）ツリーは名指しして飛ばす
- `main` / `master` / `develop` / `release` / `release/*` は push しない。綴りを出す
- push は `git push --quiet -u origin "$branch"`。落ちても巻き戻さない

### 1.3 出力と終了コード

| 終了コード | いつ |
|---|---|
| 0 | 運ぶものが無い、または全部コミットした（push しなかったブランチを含む） |
| 1 | コミットできなかったツリーか、push が落ちたツリーが 1 つ以上ある |
| 2 | 引数の誤り、ワークスペースルートが見つからない |

- 運んだツリーは 1 行ずつ標準出力、飛ばしたツリーと失敗は標準エラー（今の文面を移す）
- 運ぶものが無ければ `運ぶ承認済みチケットは無い。` を 1 行出す。ボードから端末に送ったとき、
  何も起きなかったのか動かなかったのかを人が見分けられるように

### 1.4 `ccnavi-approve.sh` からの呼び方

```sh
"$@" || exit 1
sh "$(dirname "$0")/ccnavi-push-approved.sh" || :
exit 0
```

承認が通ったあとは今までどおり 0 で終わる（運ぶ失敗で承認が失敗に見えないように。コミットは残り、
もう一度打てば送れる）。`$CCNAVI_APPROVED` / `$CCNAVI_PROJECTS` は環境で引き継ぐ。

### 1.5 配る

- `scripts/ccnavi-setup.sh` の `DEPLOY_SCRIPTS` に加える。ボードは配布先でもこれを端末に送るので、
  配らないと配布先のボードは運べない
- `ccnavi-approve.sh` と `ccnavi-fetch.sh` が今も配られていないのは別の抜け。このチケットでは直さない
  （§7）

### 1.6 エージェントから止める

`phase.ticket_approval_rule` の `script` を広げる。

```python
script = r"(^|\x00|[;&|]\s*)(sh|bash)\s+\S*ccnavi-(approve|push-approved)\.sh\b"
```

文面に 1 文足す: 「承認済みチケットのコミットと push（`ccnavi-push-approved.sh`）も人が打ちます。
ボードで承認したあと端末に送られた 1 行を、利用者が確かめて実行します。」

理由: 承認済みチケットを運ぶことは合意そのものではないが、push は外へ出す操作で、運ぶ時機を
決めるのは人（2026-09-13 の決定）。

## 2. ボード

### 2.1 承認のあと

`board-panel.ts` の `confirmApproval` で `outcome.ok` かつ `outcome.value.approved.length > 0` のとき、
`offerPrompt` より先に端末へ送る。

```ts
runInTerminal(root, pushApprovedCommand(root));
```

`core/commands.ts` に足す。

```ts
/** `ccnavi-push-approved.sh`。承認済みチケットをコミットして push する。ワークスペースルートから打つ */
export function pushApprovedCommand(root: string): string {
  return `sh ${shellQuote(toPosixPath(path.posix.join(toPosixPath(root), ".ccnavi/scripts/ccnavi-push-approved.sh")))}`;
}
```

- 絶対パスで送る。端末は使い回すので、前に `accept` が親の作業ツリーへ `cd` していても届く
- 送るのは 1 行だけで、Enter まで送る（`accept` と同じ `sendText(command, true)`）。y/N は無い
- sh が無い（`fs.statSync` で見る）ときは送らず、`showWarningMessage` で
  「承認済みチケットはまだコミットされていない。`ccnavi-push-approved.sh` が無いので導入スクリプトで配る」
  と言う。送って `No such file` を見せるより、何をすればよいかが先に分かる
- 承認が 0 件（`mismatch` やエラー）のときは送らない

### 2.2 見せ方

`offerPrompt` の通知の文を「N 件を承認した。コミットと push を端末に送った。Claude Code に伝える文を用意した」
にする。

## 3. 承認: 範囲の超過を warn に下げる

### 3.1 下げるもの・残すもの

| 検査 | 今 | 後 |
|---|---|---|
| 子の項が親の範囲を超える（`ticket.subset_problems`） | error | **warn** |
| 子の項が種類の scope を超える（`phasetypes.scope_problems`） | error | **warn** |
| 子の項が regex（親・種類のどちらでも確かめられない） | error | **warn** |
| 親が承認されていない / 親自身が子 | error | error |
| 計画に無い番号 / 種類の定義が読めない | error | error |
| `project:` が置き場に無い、順序（`order_problems`）、改版の検査 | error | error |

下げる 3 つは「範囲の広さ」の検査で、判定が切り詰めるので承認で止める理由が無い。残すものは
「チケットの形」の検査で、判定では補えない（親が無い子はどの範囲で切り詰めるかが決まらない）。

2 つの関数を呼んでいるのは `approval.validate` だけ（`--lint` ほかからは呼ばれていない）なので、
関数の中で severity を変えてよい。

### 3.2 承認画面

今の `■ 記述のうち、判定に効かないもの` に混ぜない。超過は判定に効く（止まる）ので、見出しを分ける。

```
■ 範囲のうち、判定で止まるもの（承認しても書けない）
    `src/a/*` は種類 調査（research）の範囲 wip/research/* を超えている
    `docs/*` は親 i0001 の範囲を超えている
    子の範囲に regex `...` は書けない。親と種類の上限に入るかを判定のときに当てる
```

`Candidate` に `overflow: list[Problem]` を持たせ、`validate` から超過の 3 つを別の並びで返す
（`complaints` と混ぜない）。文面の「承認を拒む」旨の言い回しは消す。

### 3.3 `--approve --preview --json`

- `batch[]` に `overflow[]`（文字列の並び）を足す。空なら `[]`
- `version` は据え置く（鍵を足すだけ。拡張は知らない鍵を読まない）
- 超過だけの子は `rejected[]` から `batch[]` へ移る
- 拡張は `text` を等幅で出しているので、表示は 3.2 の見出しで足りる。`approvemodel.ts` は
  `overflow` を読んでおく（ボードの承認待ちの行に「範囲の超過あり」を出すのは今回入れない）
- `vscode-extension/ccnavi-board/test/fixtures/approve-preview.json` を書き直す
  （`CCNAVI_BOARD_FIXTURE=1`）

## 4. 判定: 種類の scope でも切り詰める

### 4.1 合成

範囲を当てている 3 か所を 1 つの関数に寄せる。

- `judge.ticket_verdict`（実行前）
- `post.ScopeGuard.finding`（実行後の監視）
- `phase.scope_findings`（SubagentStop の差し戻し）

```python
# phase.py
@dataclass
class ScopeVerdict:
    verdict: str          # ALLOW / ASK / DENY / OUTSIDE
    limit: str            # 外に出した上限。"" / "ticket" / "parent" / "type"
    type: phasetypes.PhaseType | None

def scope_verdict(child, parent, pt, rel) -> ScopeVerdict:
    verdict = child.decide(rel)
    limit = "ticket" if verdict in (OUTSIDE, DENY) else ""
    if parent is not None:
        p = parent.decide(rel)
        combined = ticket_mod.combine(verdict, p)
        if not limit and combined in (OUTSIDE, DENY):
            limit = "parent"
        verdict = combined
    if pt is not None and not pt.inherits_scope:
        t = pt.decide(rel)       # ALLOW か OUTSIDE
        combined = ticket_mod.combine(verdict, t)
        if not limit and combined in (OUTSIDE, DENY):
            limit = "type"
        verdict = combined
    return ScopeVerdict(verdict, limit, pt)

def type_for(conf, root, child, parent, types=None) -> PhaseType | None:
    """子の番号の種類。親が計画を持たない、番号が無い、種類が引けないなら None。"""
```

- 順は 子 → 親 → 種類。厳しい側が勝つ（`combine`）ので順は結果を変えないが、`limit` は
  最初に外へ出した上限を名指しする
- 種類の上限は `allow` か `外` しか言わない。子が `ask` と書いた場所が種類の中なら `ask` のまま
- 種類の上限の外は、チケットの範囲の外と同じ `DENY_TICKET_SCOPE`（実行前）/
  `POST_TICKET_SCOPE`（実行後）/ SubagentStop の差し戻し。理由コードは増やさない（記録を読む側、
  拡張の数え方を変えずに済む）。どの上限かは文面の `limit:` 行で言う

### 4.2 種類が引けないとき

| 状況 | 切り詰め | 言うこと |
|---|---|---|
| どの層にも phases.yml が無い（番号だけの挙動） | 種類では切り詰めない | 何も言わない（今と同じ） |
| 種類の scope が `inherit` | 種類では切り詰めない | 何も言わない |
| 親が計画を持ち、その番号の種類が読めない（phases.yml が壊れた・種類を消した） | 種類では切り詰めない。親では切り詰める | 実行前の判定で notice「種類 `<id>` が読めないので、種類の上限では切り詰めていない」 |

壊れたときに deny へ倒さないのは、phases.yml が保護されたコアファイル（§8）でエージェントが
壊せないことと、親の範囲の切り詰めは残ることによる。deny に倒すと、人が phases.yml を編集している
間、全部の子の作業ツリーで書き込みが止まる。代償として、壊れている間は種類の上限が効かない。
notice と `--lint` の error で気づかせる。

### 4.3 文面

実行前（`judge.ticket_verdict`）の `head` に 1 行足す。

```
limit: phase type 設計 (design): wip/design/*, docs/*
limit: parent i0001: src/*, tests/*
```

`limit` が `ticket` のときは足さない（今の `scope:` 行で足りる）。本文は上限ごとに変える。

- `type`: 「This path is inside the ticket's work area but outside what phase type <title> (<id>)
  allows. The ticket was approved with that overflow shown as a warning; writes there stay blocked.
  Do the work in a later phase whose type covers this path, or ask the user to change phases.yml.」
- `parent`: 「This path is inside the ticket's work area but outside its parent <id>. …」
- `ticket`: 今の文面

実行後（`post.ScopeGuard.finding`）の `message` にも同じ区別を入れる。SubagentStop の差し戻しは
パスの後ろに `（種類 <title> の上限の外）` / `（親 <id> の範囲の外）` を添える。

### 4.4 dry-run

新しい処理は足さない。`judge.decide_before` はモードが dry-run なら今も
`[ccnavi dry-run] enable would have denied this call:` を前に付けて通す（`judge.py:369`）。
その下に 4.3 の `limit:` 行が載るので、「dry-run であること」と「どの上限を超えたか」が一緒に届く。
実行後の監視（`events.py:313`）と SubagentStop も今の dry-run の扱いのまま。

### 4.5 読む回数

実行前の判定は Write / Edit / NotebookEdit のたびに、行き先が子の作業ツリーのときだけ
`phase.load_types(conf, root, parent.project)` を 1 回読む。承認済みチケットの走査（`approval.scan`）は
すでに毎回しているので、増えるのは phases.yml（共通層と層の 2 本）の読み込み。実行後の監視は
`ScopeGuard` を作るときに 1 回だけ読み、層ごとに持つ。

## 5. 実装の入口ごとの変更点

| 場所 | 変えること |
|---|---|
| `.ccnavi/scripts/ccnavi-push-approved.sh` | 新規（1 章）。staging で人が写す |
| `.ccnavi/scripts/ccnavi-approve.sh` | 後半を消し、`ccnavi-push-approved.sh` を呼ぶ（1.4）。staging |
| `scripts/ccnavi-setup.sh` | `DEPLOY_SCRIPTS` に加える |
| `ccnavi/phase.py` | `ticket_approval_rule` の `script` と文面（1.6）。`scope_verdict` / `type_for` を足し、`scope_findings` をそれで書き直す |
| `ccnavi/judge.py` | `ticket_verdict` を `scope_verdict` で書き直し、`limit:` 行と文面（4.3）、種類が読めない notice（4.2） |
| `ccnavi/post.py` | `ScopeGuard` に種類を持たせ、`finding` を `scope_verdict` で書き直す |
| `ccnavi/subagent.py` | `at_stop` の差し戻しに上限の名指しを添える |
| `ccnavi/ticket.py` | `subset_problems` の severity を warn に |
| `ccnavi/phasetypes.py` | `scope_problems` の severity を warn に |
| `ccnavi/approval.py` | `validate` が超過を別の並びで返す、`Candidate.overflow`、`screen` の見出し（3.2）、JSON の `overflow[]`（3.3） |
| `vscode-extension/ccnavi-board/src/core/commands.ts` | `pushApprovedCommand` |
| `vscode-extension/ccnavi-board/src/board-panel.ts` | `confirmApproval` で端末に送る、sh が無いときの警告、通知の文 |
| `vscode-extension/ccnavi-board/src/core/approvemodel.ts` | `overflow[]` を読む |
| `vscode-extension/ccnavi-board/test/fixtures/approve-preview.json` | 書き直す |
| README.md | 「承認の JSON」の `overflow[]`、`rejected[]` に載る条件、スクリプトの一覧、ボードの承認の流れ |
| ccnavi.md | §9.4 に「運ぶ」段、§9.5 の表に「範囲の中だが種類の上限の外 → 止まる」、§9.5 末の止める形に `ccnavi-push-approved.sh`、§9.7 の表の `scope` の「効く場所」を「承認（警告）、判定」に |
| requirements.md | 下の 6 章 |

## 6. 受入テストで押さえる振る舞い

### 6.1 承認

1. 種類の scope を超える子は承認でき、承認済みチケットが置かれる。承認画面の本文に
   `判定で止まるもの` と `超えている` が出る（`test_phases.test_child_must_fit_the_phase_type` を書き直す）
2. 計画に無い番号の子は、今までどおり承認されない（同テストの後半は残す）
3. 親の範囲を超える子は承認でき、同じ見出しに出る
4. regex の子は承認でき、同じ見出しに出る
5. `--approve --preview --json`: 超過だけの子は `batch[]` に載り `overflow[]` を持つ。`rejected[]` には
   形の壊れた子（計画に無い番号）だけが載る（`test_approve_json.test_preview_lists_the_batch_...` を書き直す）
6. 大文字小文字だけ違う子は、今までどおり超過にならない（`overflow[]` が空）

### 6.2 判定

7. enable: 種類の上限の外で子の範囲の中への Write は `DENY_TICKET_SCOPE`、文面に `limit: phase type` と種類名
8. dry-run: 同じ Write は通り、`[ccnavi dry-run] enable would have denied` と `limit: phase type` が出る
9. 種類の上限の中で子が `ask` と書いた場所は `TICKET_ASK` のまま
10. 種類の scope が `inherit` なら種類では切り詰めない
11. phases.yml がどの層にも無ければ種類では切り詰めない
12. 親が計画を持ち番号の種類が読めないとき、種類では切り詰めず notice が出る。親の範囲の外は止まる
13. 親の範囲の外で子の範囲の中への Write は `DENY_TICKET_SCOPE`、文面に `limit: parent`
14. regex の子: 実際のパスで親と種類に当たる
15. 実行後の監視: Bash が種類の上限の外に書くと `POST_TICKET_SCOPE`
16. SubagentStop: 種類の上限の外の変更で差し戻し、上限の名指しが付く

### 6.3 運ぶ

17. `ccnavi-push-approved.sh`: 置き場の変更だけをコミットし、同じツリーの他の未コミットは運ばない
18. 運ぶものが無ければ 0 で `運ぶ承認済みチケットは無い。`
19. `main` の上のツリーはコミットして push しない（0、標準エラーに綴り）
20. push が落ちると 1、コミットは残る
21. detached のツリーは飛ばす
22. `ccnavi-approve.sh` が承認のあと運ぶ（今のテストの手順を sh 経由に）
23. エージェントの Bash で `sh .ccnavi/scripts/ccnavi-push-approved.sh`、`bash /abs/.ccnavi/scripts/ccnavi-push-approved.sh`、
    `ls; sh .ccnavi/scripts/ccnavi-push-approved.sh` が `DENY_TICKET_APPROVAL_CLI`
    （`test_ticket.test_cli_paths_are_denied_from_the_shell_unless_disabled` に足す）
24. `test_sh_portability` が新しい sh を拾う
25. 導入スクリプトが `ccnavi-push-approved.sh` を配る（`test_setup`）
26. 拡張: `pushApprovedCommand` の綴り（Windows の区切り、単引用符）

sh のテストは `tests/test_clean_sh.py` と同じく、一時ディレクトリに git と bare のリモートを作って走らせる。

## 7. 今回入れないもの

- 未追跡の承認済みチケットを `ccnavi-fetch.sh` やボードが知らせる検出
- `ccnavi-approve.sh` と `ccnavi-fetch.sh` を配ること（今も配られていない別の抜け）
- 端末で `ccnavi --approve` を直に打ったときに運ぶ 1 行を案内すること
- ボードの承認待ちの行に「範囲の超過あり」を出すこと
- 承認の処理が `CCNAVI_MODE` を読む形
- 種類の上限の外に独自の理由コードを持たせること

## 8. requirements.md

直す。

| REQ | 今 | 後 |
|---|---|---|
| REQ-TKT-04 | ccnavi は、子チケットの範囲を親チケットの範囲の部分集合に限り、超えた部分を承認の対象にせず、超えたことを示すこと | ccnavi は、子チケットの範囲が親チケットの範囲を超えていれば承認の画面でそれを示し、判定では親チケットの範囲の外への書き込みを止めること |
| REQ-TKT-30 | 子チケットの範囲が、そのフェーズの種類の範囲の上限を超えていれば、ccnavi は、承認を拒むこと | 子チケットの範囲が、そのフェーズの種類の範囲の上限を超えていれば、ccnavi は、承認の画面でそれを示し、判定ではその上限の外への書き込みを止めること |

足す候補（番号は付けない）。

- 利用者が VS Code 拡張のボードで承認したとき、ccnavi は、承認済みチケットをコミットして push する手順を利用者の端末に渡すこと
- エージェントが承認済みチケットをコミットして push する sh を実行しようとしたとき、ccnavi は、それを止めること
- フェーズの種類の上限を読めないとき、ccnavi は、その上限で切り詰めていないことを示すこと

受入テストの表（requirements.md 655 行付近の 11）に、上の REQ を足す。
