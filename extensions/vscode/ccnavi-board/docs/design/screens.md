---
title: ccnavi ボードの画面の作り
type: design
description: 承認のオーバーレイの遷移、カードの組み立て（状態の札・ユーザ待ち・詳細）、ボタンの見た目、レビュー完了の流れ、マージ状況の確認、見た目の実装、機能や画面を足すときに触る場所と、画面に中身を渡す段取り
tags: [design-doc, extension, board]
keywords: [設計, 画面, approval-machine, 状態の札, ユーザ待ち, 詳細, card-button, レビュー完了, マージ状況, merge, 見た目, ScreenHost, screenHost, retainedHost, retainContextWhenHidden, screens.ts, 契約, BoardMessage]
---

# 画面の作り

## 承認のオーバーレイ

この画面の遷移は `core/approval-machine.ts` の 1 か所にまとめてある。散らばっていると必要なガード条件を書き漏らすため。「この状態ではこれを受けない」というガード条件は、そのファイルの先頭の表にある。`board-panel.ts` は入力を渡し、返ってきた「やること」を実行するだけ。単体テストは CB-T169〜181 と CB-T378〜380（CB-T181 は、ガード条件を 1 つ消すとテストが失敗することを確かめる変異テスト）。

計画編集タブとの結び付き（未保存のタブがある親を含む一覧は承認を打たない、「計画を編集」は閉じてからタブを開く、保存したあとに提案が変わった親を覚える）も、この遷移のガードと入力に置いた。遷移は VS Code に触れないので、タブの様子（未保存の親・保存したときのハッシュ）は `board-panel.ts` が帳面 `core/workflow-tabs.ts` から引いて入力に添える。押された時点の様子で決めるので、`board-panel.ts` は承認を打つ直前（`approve` のやること）にももう 1 度帳面を見る。その確かめも遷移のファイルの純関数（`blockedBeforeApprove`）にして、変異テストの表に入れてある。

## カードの組み立て

カード 1 枚は、上から、見出し（ID・題・状態の札）、位置、ユーザ待ち、注意、フェーズ行（親だけ）、補助の操作、詳細（畳む）の順に並ぶ。`webview/board/Card.tsx` が描き、何を出すかは `core/board.ts` と `core/status.ts` が決めた値のとおりで、画面は判定し直さない。

**状態の札は「状況」、操作欄（ユーザ待ち）は「ユーザができること」。** 両者は別の規則で決め、同時に出てよい（札「要確認」と［承認］など）。操作してよいかの最終判断は実行ファイルが持つ。

状態の札（`statusOf`）。上から最初にヒットしたものを採る。判定は実行ファイルの JSON が持ち、ここは判定済みの欄を表示の言葉に置き換えるだけ。レビューの要否・ゲートの条件・リスクは判定し直さない。

| 順 | 子 | 親（レビューの状態はフェーズ一覧から見る。親カードの `gateClosed` は使わない） |
|---|---|---|
| 1 | `blocked` か `scattered` → 要確認（理由はツールチップ） | 同左 |
| 2 | `pendingApproval` → 承認待ち | `pendingApproval` → 承認待ち（承認済みの親に改版が出ていれば「承認待ち（改版）」） |
| 3 | 取り消し（`cancelledAt` あり。アーカイブも同じ）→ 取り消し | 同左 |
| 4 | 完了の列か、取り消しでないアーカイブ → 完了 | 完了・アーカイブ → 完了（早めに閉じていれば「完了（早めに終了）」） |
| 5 | `copyStatus === "review"`: 自分のフェーズの一言（レビュー待ち／レビュー準備中／レビュー予定）。それ以外は要確認 | どれかのフェーズがレビュー待ち → レビュー待ち |
| 6 | `copyStatus === "open"`: フェーズが `reviewWaiting` か `gateClosed` なら要確認。`startedAt` が空なら開始前、あれば作業中 | どれかのフェーズがレビュー準備中 → レビュー準備中 |
| 7 | どれにもヒットしない → 要確認（読めた欄をツールチップに出す） | `copyStatus === "open"` → 開始前／作業中 |
| 8 | — | どれにもヒットしない → 要確認 |

フェーズ行の一言（`phaseStatusOf`）。使う欄は PhaseJson のもの。

| 順 | 条件 | 言葉 |
|---|---|---|
| 1 | `gate_closed` かつ `review_waiting` | レビュー待ち |
| 2 | `gate_closed` | レビュー準備中 |
| 3 | `marks.reviewed` がある | レビュー済み |
| 4 | `marks.skipped.deferred_to` があり、その番号のフェーズに `marks.reviewed` がある | レビュー済み |
| 5 | `marks.skipped.deferred_to` がある | レビュー予定 |
| 6 | `marks.skipped` がある | レビュー省略 |
| 7〜9 | `state` が `ended` / `active` / `planned` | 終了 / 進行中 / これから |
| 10 | 上のどれでもない | 要確認 |

`marks` の中身（`skipped.deferred_to`）は `ccnavi --explain --json` の `marks` にそのまま載っているので、ボードは推測で代わりを作らない。

ユーザ待ちは、ユーザができることがあるときだけ出し、主ボタンは 1 つ。親は承認待ちなら「内容を確認して承認してください」と［承認］で、レビューの操作は各フェーズ行に置く。子は承認待ちなら［承認］、札が「レビュー待ち」なら「マージリクエストでレビューしてください」と［MR を開く］（リンク）と［レビュー完了］（主ボタン）の順で 1 つだけ出す。承認は `pendingApproval` だけを条件にし、札が「要確認」でも出す。

注意の行は例外のときだけ短い言葉で出す（不備・書き込み停止中・先行待ち・リスク HIGH 以上・複数の場所にある・ワークツリーなし・未送信）。理由の全文、MR・issue・ブランチ・ワークツリー名・プロジェクト・取り消しの理由、リスクの文言（JSON のまま）、人間レビューの要否と理由は詳細に畳む。base / 合流の sha、マーカー名は画面に出さない。詳細の開閉はカードごと（プロジェクトと ID の組）に `webview/board/state.ts` の状態へ覚え、更新や列の移動でも閉じない。案内の見本の上では、開閉を実際の状態に書かない（案内は `scene.details` で見本のカードを開く）。

カードを押す（または焦点を当てて Enter）とチケットのファイルが開く。ボタン・リンク・詳細（開閉と開いた本文）の上では開かない（`NOT_OPENING`）。

## ボタンの見た目

- 共通の `button.action`（`extensions/shared/styles/button.css`）と Chrome 拡張は変えない。カードの中の操作ボタンと、操作として置いた［MR を開く］リンクにだけ、新しいクラス `card-button`（主ボタンは `card-button primary`）を付ける。マージ状況の帯のボタンとリンクも同じ。
- `Card.css` で文字サイズ（`var(--vscode-font-size)`）・高さ（`min-height: 26px`）・余白を絶対値で明示し、親（カード・フェーズ一覧・列）の文字サイズを引き継がない（`font: inherit` を使わない）。幅に収まらない言葉はボタンの中で折り返す。
- 主ボタンは「ユーザ待ちの操作」だけ。操作のまとまり（「ユーザ待ち」と各フェーズ行）ごとに 1 つまで。補助の操作（フロー・計画編集・テンプレートの下書き）は通常の色。
- カードの中で縦に広がるメニューは使わない。
- 詳細の中のリンクは普通のリンクのまま（`detail-link`）。ボタンの寸法にしない。
- 検査: DOM で、操作ボタンと操作の MR リンクが全部 `card-button` を持つこと、詳細の中のリンクは持たないこと、操作のまとまりごとに主ボタンが 1 つ以下であることを見る（`test/board/card.dom.test.ts`）。寸法と見た目は、拡張開発ホストでの手動確認（README の 6g）に回す。狭い列（220px）・長い題・ズーム 150% で、操作ボタンの高さと文字サイズが揃っていること。

## レビュー完了の流れ

［レビュー完了］（フェーズ行、札が「レビュー待ち」の子のカード）は、`approval-machine.ts` の `decide` に入る。`loadDecide` の効果が `ccnavi-review.sh decide <N> --preview` を読み、`parseDecidePreview` が**厳しく検証する**。頼んだ親・フェーズ・MR の番号（フェーズ行の依頼が持つもの。分かるとき）と照らし、不正な要素は破棄せずに全体をエラーにする。読み取れなければ「指摘を読み取れませんでした（理由）」で確定のボタンを出さない。正しく読めて 0 件なら「フェーズ N に未解決の指摘はありません」と［レビュー済みにする］、1 件以上なら対応方針の画面（`Decide.tsx`）。確定後の表示は実行ファイルの答え（`reviewed`・`pending_extra`・`followup`）に従い、続きの子が起きていないのに追加された指摘があれば、決め直しへ戻れる（`retry`）。

副ボタン「エージェントに相談するプロンプトをコピー」は、遷移が `copyConsult` の効果を返し、`board-panel.ts` がクリップボードに入れて結果（`consultCopied`）を戻す。オーバーレイは閉じず、選びかけの対応方針は React の状態（ダイジェストが同じ間は保たれる）に残る。確定中（`deciding`）と 0 件の画面では組まない。プロンプトは `commands.ts` の `consultPrompt` が組み、許すのは `decide <N> --preview` を単体で実行することだけ。

## マージ状況の確認

遷移は `core/merge.ts` の 1 か所にまとめてある（承認の遷移と同じ形。VS Code に触れないので単体で試せる）。「いまの状態 ＋ 入力 → 次の状態 ＋ やること」で、`board-panel.ts` が持ち直し、描き直し、効果を行う。結果はパネルの状態として拡張ホストが持つ（ボードが開いている間だけ）。頼んだ状態でなければ受けない: 確認ごとに世代（`token`）を振り、閉じたボードへ遅れて返った結果や、前の確認の遅れた答えは破棄する。確認中の「確認」は受けない。

読み取り（`loadList`＝`ccnavi sync retired`、`loadState`＝`ccnavi-review.sh mr-state`）と、書き込み（`sendRetire`＝ターミナルへ送る）は、効果の種類で分ける。送る効果が返るのは、確認のダイアログで「実行する」と答えたあとだけで、`test/shared/read-only.test.ts` がソースの形でその線を保つ。答えの検証は `core/mrstate.ts`（形が想定外なら「出力の形が想定外」、期限切れは拡張の側だけの種類 `timeout`）。互換の版は上げない（`mr-state` は追加だけで、実行ファイルを起こさない）。

## 見た目

実装は body のクラスの下でテーマ変数を上書きするだけ（配色は `src/webview/styles/appearance.css`、切り替えは `src/core/appearance.ts` と `src/webview/appearance.ts`。画面が受けるのは `src/webview/useHostMessages.ts`）。配色もほかの中身と同じ段取り（`ScreenHost`）で送る。組み上がっていない画面と破棄された画面には送らない。保持しない画面には作り直されたあと `ready` で、保持する画面には再表示されたところで送り直す。ハイコントラストのテーマでは Claude の配色を適用しない。設定を書く先は、いま値が定義されている場所（フォルダ → ワークスペース → ユーザ）。

## 機能や画面を足すとき

**ボードに機能を足すときに触る場所。**

| 足すもの | 触る場所 |
|---|---|
| カードに出す項目 | `core/status.ts`（札の言葉と規則）→ `core/model.ts`（実行ファイルとの契約。JSON を増やすなら Python 側の `tests/ticket/test_board.py` が書き出す見本 `test/fixtures/board.json` も）→ `core/board.ts`（出すか出さないかの判断）→ `webview/board/text.ts`（出す言葉）→ `webview/board/Card.tsx`（見せ方）→ `webview/board/Card.css`（CSS）→ テスト（`test/board/model.test.ts`・`board.test.ts`・`status.test.ts`・`card.dom.test.ts`・`render.dom.test.ts`） |
| ユーザが押せる操作 | `core/board-view.ts` の `BoardMessage` → 画面のボタン → `board-panel.ts` の `KNOWN`（形の確認の一覧）と `asMessage`（形の確認）と `handleMessage`（処理）。`KNOWN` か処理を書き忘れると型が合わなくなる（どちらも型で網羅を強制してある） |
| 画面が覚えるもの | `webview/board/state.ts`（形と既定）→ `App.tsx`（読み書き）→ `test/board/board.dom.test.ts` |

**画面を 1 つ足すとき、使い回せるもの。** `webviewScript` / `webviewStyle`（画面の名前で読む。拡張子は関数の側が付ける）、`poster<M>()`、`core/screen-host.ts`（`screenHost` / `retainedHost` と `embedJson`）、共通の部品 `@ccnavi/shared`（`extensions/shared/styles/` の `button.css`・`page.css`・`list.css`。画面の style.css で `button.css`・`../styles/appearance.css`・`page.css` の順に @import する）、`webview/vscode.ts`・`webview/initial.ts`・`webview/appearance.ts`、`webview/useHostMessages.ts`（拡張ホストからのメッセージの受信。見た目もここで受け、`ready` は呼ぶ側が渡す）・`webview/useEscape.ts`（Esc は最も手前の面だけが受ける。層は `ESCAPE_LAYER`、CSS の `z-index` と同じ順）・`webview/Tour.tsx` の `ScreenHeader`（ツールバーと「?」を組む）、テストの共通部品（`test/helpers/dom.ts`・`test/helpers/bundle.ts`）。

画面ごとに要るのは、契約（`*-view.ts`）・入れ物を組む関数・画面の CSS（`webview/<名前>/style.css` と部品ごとの CSS）・`useHostMessages(handler, ready)` の引数（handler と `ready`）・テストの入口（`test/helpers/<名前>.ts`）と、`test/shared/style.test.ts` の `reactPages` への 1 行（足し忘れは CB-T127 が止める）。

**実行ファイルへの往復がある画面（ルール管理）も、やり取りの形は同じ。** 画面は編集中の中身を付けて頼み（`judge` / `expectations`）、拡張ホストが実行ファイルの結果をそのまま返す（`judged` / `expectationsChecked`）。画面は判定の理屈を持たない。VS Code のダイアログが要る欄（`pickFile` → `picked`）も同じで、行を特定するキーは画面が渡し、拡張ホストは読まずにそのまま返す。

**画面を 1 つ足すときに触る場所。**

| 足すもの | 触る場所 |
|---|---|
| バンドルと回り方 | **どちらも直さない。** パスの付け方の約束で決まる。画面は `src/webview/<名前>/main.tsx`、バンドルした出口は `out/webview/<名前>.js`、CSS は `src/webview/<名前>/style.css` → `out/webview/<名前>.css`、テストの入口は `test/helpers/<名前>.ts`、グループは `test/<名前>/`。`bundle-webview.js` と `test-groups.js` が同じ見つけ方でディスクから拾う |
| 回すものの決まり方 | 触ったファイルがどの画面のバンドルに入るかを閉包で見る（スクリプトは `main.tsx` から `import`、CSS は `style.css` から `@import`。置き場のパスでは決めない）。どの画面にも入らないもの（`webview/vscode.ts`・`webview/styles/` など）は全画面に関わると見る。画面と同じ名前のグループは、`test/helpers/<名前>.ts` を作り忘れても必ず回る |
| 契約に置く型 | 画面に渡す形（`ProjectsPage` のような）は契約の側（`*-view.ts`）に置く（`core/` の判定のファイルに置くと、`node:path` を読むファイルを辿って画面の型検査が失敗する） |

**段取りは 2 系統ある。パネルの `retainContextWhenHidden` と対で選ぶ**。

| パネル | 作るもの | 非表示になったとき |
|---|---|---|
| `retainContextWhenHidden: false`（ボード・プロジェクト管理） | `screenHost(surface, render)` | 画面は破棄される。入れ物ごと入れ直す |
| `retainContextWhenHidden: true`（ルール管理・リスク管理・フェーズ管理・テンプレート管理・取り込み待ち・フロー編集・計画編集） | `retainedHost(surface, render)` | 画面は生きている。何もしない |

返る形（`ScreenHost<D>`）と呼び方（`send` / `post` / `ready` / `hidden`）は同じ。取り違えても型では止まらない。保持する画面に `screenHost` を使うと打ちかけが消え、逆だと中身が古いまま更新されない。保持する画面のパネルは、`ScreenHost` を返す関数（`rulesHost`・`riskHost`・`phasesHost`・`templatesHost`・`handoffHost`・`flowHost`・`workflowHost`）で作り、`retainedHost` の形に合わせる。この関数は `panel-common.ts` の `assertRetained(panel, root, 画面の名前)` で `panel.options.retainContextWhenHidden` を見る。偽なら診断ログに残す（保持しない 2 画面は `assertRetained(..., false)`）。

保持する画面には、再表示されたところで知らせ（`lock`・`changed`）と見た目を送り直す。非表示の画面に届くかどうかについて、VS Code の型定義の記述が一致していない。中身（`data`）は送り直さない。

保持する画面に中身（`data`）を渡すのは、画面の編集を破棄してよいときだけ（ユーザが「更新」を押した、保存や作成が通って中身が入れ替わった）。監視がファイルの変化に気づいても中身は渡さず、帯（`changed`）を出してユーザに決めてもらう。

ルール管理・リスク管理・フェーズ管理の 3 画面は、パネルの受け付け・再表示・破棄、監視の反応の遅らせ方、読み直しと描き直し、対象の切り替えを `settings-panel.ts`（`settingsPanel`）にまとめてある。画面ごとに違う仕事（何を読む・何を見せる・どのファイルを見張る・保存できるかをどう聞く）は、`settingsPanel` に渡す `SettingsScreen` のコールバックで注入する。3 画面の差を共通の側に持たせず、残す仕事は画面のパネルに残す。

画面（`*-panel.ts`）どうしは互いを import しない。ある画面から別の画面を開く導線（ルール管理からフェーズ管理の対象を切り替える、など）は、相手のパネルの関数を直に呼ばず、`core/screens.ts` の帳面（`screens().board(...)`）を通す。帳面に入口を載せるのは `extension.ts` だけで、載せ方を見る場所もそこ 1 か所。
画面の様子をほかの画面が知る必要があるときも、相手のパネルを読まずに小さな帳面を通す。ボードのカードの「計画編集」は、いまのボードでそのカードが入口を出しているかを確かめてから、コマンド `ccnaviBoard.openWorkflow`（帳面の `screens().workflow` と同じ入口）を呼ぶ。計画編集タブの未保存と保存したときのハッシュは `core/workflow-tabs.ts` にあり、タブが書いてボードが読む（変わったら知らせを受けて描き直す）。
