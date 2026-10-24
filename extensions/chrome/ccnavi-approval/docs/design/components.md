---
title: ccnavi 承認ボードの構成
type: design
description: Chrome 拡張 ccnavi 承認ボードのディレクトリごとの役目
tags: [design-doc, extension, approval]
keywords: [設計, 構成, service worker, Pyodide, Web Worker, hosts.json, ccnavi_chrome.py, core, MEMFS, React, controller]
---

# 構成

入口: [ccnavi 承認ボードの設計](../design.md)

| 場所 | 役目 |
|---|---|
| `src/background/` | service worker。PAT を持ち、ホストの API を呼ぶのはここだけ（画面には PAT を渡さない）。PAT の期限のバッジ |
| `src/board/` | ボード（拡張のページ）。Worker を起動し、読んだ中身を Python に渡し、答えを描く。状態と副作用（読み直し・書く操作・確認が必要な状態・PAT の期限の帯・自動の読み直し）は `controller.ts` が持つ（`chrome`・`window` には、入口の `main.tsx` が渡す外界 `BoardEnv` を通してだけ触る）。描くのは React（`App.tsx` が `useSyncExternalStore` で controller を読み、リポジトリの欄 `Repo.tsx`、親子のチケット `Family.tsx`、レビュー `Review.tsx`（未解決の指摘が残るフェーズは、行き先を決める画面 `Decide.tsx` を開く。開いているか・確定中・結果は controller が持ち、画面は渡された状態を描く）、「始める」`Start.tsx`、要確認 `Attention.tsx` を並べる）。Markdown の本文は `Markdown.tsx` が消毒した断片（`src/core/sanitize.ts`）を ref で差し込む（HTML の文字列には戻さない）。CSS の入口は `style.css`（共通の部品の CSS と、部品 1 つに 1 本のボードの CSS（`App.css`・`Repo.css` など）を読み、ビルドが `board.css` に組む） |
| `src/options/` | 設定画面。通信先（追加すると許可を求め、削除すると PAT と許可（画面と API の origin すべて。GHE.com は 2 つ）を削除する。`Hosts.tsx`）、リポジトリ（統合先の名前・直近の日数・指定のブランチ）と PAT。React で組み、見た目は VS Code 拡張と共通の部品（`extensions/shared` の `@ccnavi/shared`。ボタン・骨組み・一覧の CSS と、Claude の配色で `--vscode-*` を定義する `browser-theme.css`）を使う。CSS の入口は `style.css`（ビルドが `options.css` に組む） |
| `src/worker/` | Pyodide を動かす Web Worker |
| `src/core/` | 画面に依らない部品（通信先と manifest、設定画面で足すホスト（`custom-hosts.ts`）、GitHub の読み書き（`github.ts`）、GitLab の読み書き（`gitlab.ts`）、「始める」（`start.ts`）、画面と service worker の約束、読み取りの流れ、レビュー済みの材料の読み（`reviewed.ts`）、承認・レビュー済みの流れ（`write.ts`）、decide のホスト書き込み（`decide-host.ts`）、PAT の期限、Markdown の消毒） |
| `py/ccnavi_chrome.py` | Pyodide の上の入口。MEMFS に仮のツリーを組んで今の ccnavi を呼ぶ。判定のコア（`ccnavi.tickets.core`）の `plan`・`confirm` の答え（書くもの）を拡張が 1 コミットにする |
| `hosts.json` | 焼き込む通信先（組み込み）。組織ごとのビルドはこれを替える。設定画面で足したホストは `chrome.storage.local` の `customHosts` に持つ（`custom-hosts.ts`） |

## decide のホスト書き込み

残った指摘の行き先を確定するとき、Python の `decide` が返すホストへの書き込みの計画（issue・返信・解決・コメント）を、
`src/core/decide-host.ts` が順序を守って実行する。

- ホストへの操作は `HostOps` を通す。ボードは service worker 経由の `callOps` を渡し、PAT は service worker だけが持つ。
  service worker は操作ごとに、送り手がボードか、登録したリポジトリか、引数の形（thread id は API の経路と GraphQL に使える字、
  題は 1 行、本文は字数の上限）を検査してからホストを呼ぶ
- 書く前に `precheckHostPlan` が、スレッドごとの返信・解決の可否（GitHub は `viewerCanReply`・`viewerCanResolve` と保留中のレビューか、
  GitLab は `resolvable` と差分への指摘か）と、PAT の権限・ロールを読む。足りなければ何も書かない。Chrome で扱えない種類は権限不足と分けて返す。
  fine-grained の PAT の権限は書く前に読めないので、確かめられなかったものとして残し、実際に断られたときに権限の名前を言う
- 実行は issue、返信、解決、コメントの順。issue は作る前に操作 ID の印で探して使い回す（探すのは PAT の持ち主が作ったものに限る）。
  返信の `{issue_url}` とコメントの `{issue_links}` は、ホストが返した URL（通信先の画面の下に限る）で置き換える。
  続きのコメントには `part=<i>` の印を付け、使い回した issue では足りない分だけ付ける。返信の末尾には操作 ID の印を付け、同じ印が既にあるスレッドには返信しない。
  検索が失敗したら作り直さずに止める（404 だけが「無い」）。上限まで読んで見つからなければ作り、結果に探し切れていないことを載せる。
  失敗したらそこで止まり、済ませたことと理由を結果に入れて返す。操作の記録は持たない。同じ計画の実行中の再入は断る
- service worker は送る本文のコードフェンスの外を無害にする（GitLab は行頭の `/`、両ホストは `@` のメンション）。GitHub のスレッドは、登録したリポジトリのその MR のものかを確かめてから書く
- issue の作成には題と本文だけを送る（assignee・labels・milestone は送らない）
- GitLab のスレッドが機構自身のもの（`own`）になるのは、ノートが全部目印で始まり、書き手が全部代表のノートと同じときだけ。
  依頼の投稿者と一致するかは、依頼の記録を持つ Python が比べる

## decide の流れ（`write.ts` の `decidePreview`・`decidePhase`）

`decidePreview` は親子のチケットと MR のスレッド・レビューを読み、Python の `decide`（preview）が通した指摘とダイジェストを返す（書かない）。
各指摘には、スレッドの id（取得したスレッドから、鍵 = URL か id で引く）と、書く前の確かめと同じ見分け方で Chrome で扱えない理由（`unsupported`）を付ける。

`decidePhase` は次の順に進む。ホストへ書くのは `writeLoop` の外で 1 度だけで、ループの中は判定とコミットだけにする。

1. 読み直して `decide`（apply、`carry` なし）を呼び、ホストへ書く計画（`host_plan`）を得る。見せた指摘と違えば `mismatch`、通らなければ `refused` で、何も書かない。`keep`・`issue` が無ければ計画は空なので、この手順は飛ばす
2. `precheckHostPlan` で権限とスレッドの種類を確かめる。足りなければ `refused`（必要な権限名つき）で、何も書かない
3. `runHostPlan` で issue、返信、解決の順に書く（コメントは後段に回す）。止まれば `halted`（済んだ分と理由）で、コミットしない
4. `writeLoop` で、周ごとにスレッドを読み直し、書き終えた `keep`・`issue` の鍵を `carry` に渡して `decide` を呼び、返った `changes` を `confirm` と同じ検査で 1 コミットにする。先頭が動けば読み直して再試行するが、ホストには書かない。`changes` が空なら空のコミットを作らない
5. コミットが済んだら、1 の計画のコメント（1 を飛ばしたときは 4 の応答のもの）の `{issue_links}` を、作った・使い回した issue の URL の一覧に置き換えて MR に投稿する。指摘が 0 件の確定では投稿しない。失敗はコミットを戻さず警告にする

4 の `carry` の確認がホストの読み取りの遅れで通らないときだけ、短く待って最大 3 回取り直し、それでも通らなければ `attention` にする。3 の後に 4 が通らなかったときは、ホストの側は済んでいること・コメントは未投稿であることを結果の文面の先頭に言い、結果に `hostWritten` を付けて画面を閉じさせる。指摘が 0 件のときは 1〜3 を空で通り、マーカーを置くコミットになる。
