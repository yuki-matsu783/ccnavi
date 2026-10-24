---
type: guide
title: 置き場の使い分け（wip/handoff/ と scratchpad/）
description: ユーザに渡す物の置き場（wip/handoff/）と、使い捨ての置き場（ワークスペースルート直下の scratchpad/）の使い分け
tags: [state, records]
keywords: [scratchpad, handoff, user handoff, 取り込み, 保護対象, wip/handoff, スクラッチパッド, 下書き, 使い捨て, 一時ファイル, 引き渡し, ワークツリー, セッション, integrate, gitignore]
---

# 置き場の使い分け

置き場は2つある。`wip/handoff/`は、直接作業では`integrate`が取り込み結果から外し、チケット作業では`ready`が片付ける。`scratchpad/`はccnaviが消さないので、終わったら自分で消す。

| | `wip/handoff/` | `scratchpad/` |
|---|---|---|
| 置くもの | チケット作業で、保護対象を直した写し、MRと一緒にユーザへ見せる物 | 直接作業で一時的にユーザへ見せる物、コミットしたくない物（機密、巨大な出力）、再現用スクリプト、コマンド出力 |
| 場所 | ワークツリーの中ならそのワークツリーの`wip/handoff/`。ワークツリーが無ければワークスペースルート直下の`wip/handoff/` | メインのワークスペースルート直下の`scratchpad/`。ワークツリーの中からも、同じこの1か所に置く |
| git | 追跡されてpushされる | `.gitignore`で追跡から外す。pushされない |
| 片付け | `ccnavi-git.sh integrate`が取り込み結果から外す。チケットのワークツリーでは`ready`が`wip/`ごと消す | ccnaviは消さない。作業が終わったら自分で消す |

置いたファイルを渡すときは、パスも必ず伝える。ファイル名は他のセッションの置き物と重ならないようにする（どちらもメインのルートを他セッションと共有する）。

迷ったら、MRやレビューで見せたいものは`wip/handoff/`、その場でユーザに見せるだけのものや追跡したくないものは`scratchpad/`に置く。

## `scratchpad/`の扱い

- ワークスペースルート直下の`scratchpad/`だけが置き場。ワークツリー直下の`scratchpad/`は置き場にしない（ワークツリーごと消えるうえ、メインのルートにある`.gitignore`が効かない）
- `.gitignore`の`/scratchpad`（末尾の`/`なし）で追跡から外す。ディレクトリにもsymlinkにも当たる。コミットにもMRにも載らない
- メインのツリーなので、チケットの範囲は当たらない。`workspace-root`のルールの例外として、ワークツリーを切らなくても書ける（ルートでの編集全体が止まるわけではない）
- `wip/scratchpad/`や`scratchpadX/`のように名前が違う場所は置き場ではなく、ルート直下なら止められる
- ルールの判定は大文字と小文字を区別しない一方、`.gitignore`の`/scratchpad`は区別する（Linuxなど）。`Scratchpad/`のような別の綴りは書き込めてしまうが追跡から外れないので、必ず小文字の`scratchpad/`を使う

## `wip/handoff/`の扱い

- 追跡されてpushされる。履歴に残るので、レビューで見せられる
- ワークツリーの下書きは、`ccnavi-git.sh integrate`が取り込み先へ取り込む結果を作るときに外す。ブランチが持ち込んだ`wip/handoff/`の下のファイルは取り込み結果に入れない。`wip/`のそれ以外（`wip/proposals/`・`wip/eli5/`など）は巻き込まない。取り込み先が元から持つ下書きのうち、ブランチが触らなかったものにも触れない
- チケットのワークツリーでは、`ready`が`wip/`ごと消す
- チケットが結び付いたワークツリーでも、`wip/handoff/`にはそのまま書き込める。実行前チェック・実行後チェック・サブエージェント終了時チェックのどれも、`wip/handoff/`への書き込みをチケットの範囲と照らし合わせないので、範囲に`wip/handoff/*`を足す必要は無い
- 照らし合わせの対象から外れるのは、ツリーのルート直下の`wip/handoff/`の下だけで、大文字と小文字は区別しない。`wip/handoffX/`・`docs/wip/handoff/`・`scratchpad/`や、`wip/handoff`という名前のファイルは普通の作業対象と同じように判定され、チケットの範囲の外なら止められる
- マージリクエストのレビュー依頼には「`wip/handoff/`配下はユーザに渡す写しで、レビューの対象外」と書かれる。レビューでは読まなくてよい
- ルートは他セッションと共有しているので、ファイル名が他の下書きと重ならないようにする
- ワークスペースルートの`wip/handoff/`へは、`workspace-root`のルールの例外として書き込める。ワークツリーを切らなくても書ける（ルートでの編集全体が止まるわけではない）

## テストのフィクスチャを手で組むとき

シェルで`.ccnavi/`や`.claude/`の名前を持つ場所へ書き込むと、場所がワークスペースの外（`/tmp/fx/.ccnavi/`など）でも`builtin:guard-setting-files`が止まる。パスの指す場所までは見ない、止める側に倒した仕様。フィクスチャは`wip/handoff/`の下に作る。保護された場所そのものは、Python など別の手段でも書き換えない。読むだけの`cat`・`grep`・`ls`は止まらない。

## セッションのスクラッチパッドには置かない

Claude Codeのセッションが示すスクラッチパッド（Environment欄の`.../scratchpad`、`/tmp/claude-*`の下）は、置き場ではない。ユーザに渡す物は`wip/handoff/`、使い捨ては`scratchpad/`に置く。セッションのスクラッチパッドへの書き込みは、ccnaviがこの2つへ案内して止める。

## 書き込みを止められている場所を直したいとき

保護対象のファイル（`rules.yml`・hook・ラッパースクリプト・テンプレートなど）や、チェックアウトしたブランチ直下のように書き込みを止められている場所を直したいときは、`wip/handoff/`（cwdがワークツリーの中ならそのワークツリーの、それ以外はワークスペースルートの）に元ファイルを`cp`して、そちらを編集する。手順は1本で、取り込むのはユーザ。
`cp`と案内は`sh <ワークスペースルート>/.ccnavi/scripts/ccnavi-stage.sh <元のパス>...`が行う。下書きは、cwdがワークツリー（`.claude/worktrees/<名前>`）の中ならそのワークツリーの`wip/handoff/`に、それ以外はワークスペースルートの`wip/handoff/`に置かれる。置き場の下では、ワークスペースルートからの相対パスと同じ階層（`wip/handoff/.ccnavi/config/common/rules.yml`など）を保つ。既にある下書きは上書きしない。ワークツリーの下書きはそのブランチに載るので、非対話セッションでpushできる。ワークツリーの`wip/handoff/`へ手で`cp`すると、行き先のパスに守る名前が含まれるときは止められる（例外はワークスペースルートの`wip/handoff/`への`cp`だけ）。`ccnavi-stage.sh`を使う。`--commands`も、下書きを作ったのと同じツリーの中で打つ。
編集が済んだら、全部の下書きがそろってから`ccnavi-stage.sh --commands <元のパス>...`で下書きの状態を確かめる。取り込むのはユーザの手で、スクリプトは元のファイルを動かさない。そのあとはセッションの種類で分かれる。

1. `sh <ワークスペースルート>/.ccnavi/scripts/ccnavi-stage.sh <元のパス>...`で、元のファイルを`wip/handoff/`に写す。下書きは元と同じ相対パス（`wip/handoff/.ccnavi/config/common/rules.yml`など）に置かれ、既にある下書きは上書きしない。新規のテンプレートは写す元が無いので、`ccnavi --templates --draft <親>`で作る（[templates.md](templates.md)）
2. `wip/handoff/`の下書きを編集する。複数のファイルを同時に直すときは、すべてそろえてからターンを終える
3. ユーザが`ccnavi user handoff [<相対パス>...]`で差分を見て、`--yes --expect <ハッシュ>`で取り込む。`user handoff`はユーザ専用で、見るだけの形もエージェントは打てない。状態を見るときは`ccnavi-stage.sh --commands <元のパス>...`を使う

写し先は、下書きがあるツリーの同じ相対パス。取り込める保護対象は次の表の4種類で、表に当たらない下書き（`learnings/`・`inbox/`・再現用のスクリプトなど）は対象外になる。

| 種類 | 反映する先 | 新規 | ボードから |
|---|---|---|---|
| `template` | `.ccnavi/config/{common,project}/templates/<名前>.yml` ほか | できる | 可 |
| `layer-config` | `rules.yml`・`phases.yml`・`risks.yml` | できない | 可（`rules.yml`の`guard-*`のルールか今あるdenyを動かす取り込みは不可） |
| `docs` | `.ccnavi/docs/**/*.md`・`.ccnavi/CCNAVI.md` | できない | 可 |
| `defense` | `.ccnavi/scripts/**/*.sh`・`.claude/hooks/**`・`.claude/settings*.json`・`.claude/skills/**/*.md` | できない | 不可（端末のユーザだけ） |

`wip/handoff/`の下書きは`ccnavi-stage.sh`で写して作る。`ln`でリンクを作ったり、ハードリンクでつながった下書きを書いたりする形は止まる。

写したあとに反映する先が変わった下書き（conflict）や、検査に通らない下書きが1つでもあると、そのときは何も取り込まれない。取り込むと下書きは消える。端末の前のユーザは、`ccnavi-stage.sh --commands`が出す`mv`の代わりに`user handoff`を使える。

- 対話セッション: 下書きの相対パスを挙げて、`ccnavi user handoff`での取り込みをユーザに案内し、ターンを終える。直接作業のワークツリーの下書きなら、作業ワークツリーで打つよう案内する（`cd <ワークスペースルート>/.claude/worktrees/<名前> && ccnavi user handoff`）。取り込んだ変更は作業ワークツリーでコミットしてから`integrate`を打つ。要らない下書きは消してコミットする。`integrate`は、取り込み待ちの下書きが残っていると断る
- 非対話セッション: `wip/handoff/`の編集済みファイルをpushして、ユーザに確認と取り込みを頼み、ターンを終える

未取り込みの下書き（許可リストに当たるもの）が`wip/handoff/`に残っていると、チケットの`ready`が止まる。取り込むか、下書きを消してから打つ。

### 承認の経路で直すとき（`staging`フェーズ）

上の`wip/handoff/`は、ユーザが差し替える前提の写しの置き場。チケットの承認を通して保護済みファイルを直すときは、別の経路になる。
`implement`フェーズは保護済みファイル（`.ccnavi/scripts/`・`.claude/hooks/`・`rules.yml`）を承認しない。承認の対象になるのは`staging`フェーズだけ。

- 親の計画に`staging`のステップを置き、その前に受入テスト作成のステップを置く（`.ccnavi/config/project/phases.yml`の`staging`の定義）
- `staging`の子では、完成品を`wip/design/scripts/`に全文で置く。元のファイルは書き換えない
- 差し替えはユーザが行う。写す順は`ccnavi-common.sh`が先（起動時に読むスクリプトがこれに依るため。Issue #212）
