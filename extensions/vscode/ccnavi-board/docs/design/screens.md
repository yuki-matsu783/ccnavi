---
title: ccnaviボードの画面の作り
type: design
description: 承認のオーバーレイの遷移と目次、列と親ごとの行、カードと右のパネルの組み立て（状態のバッジ・ユーザ待ち・詳細・選んだカード）、ボタンの見た目、レビュー完了の流れ、マージ状況の確認、見た目の実装、拡張全体の答えとサイドパネル、機能や画面を足すときに触る場所と、画面に中身を渡す段取り
tags: [design-doc, extension, board]
keywords: [設計, 画面, 親ごとの行, Lanes.tsx, フェーズを表示, 計画の流れを見る, PlanFlow.tsx, approval-machine, 状態のバッジ, ユーザ待ち, 詳細, 右のパネル, Panel.tsx, 選んだカード, 目次, Toc, card-button, レビュー完了, マージ状況, merge, 見た目, ScreenHost, screenHost, retainedHost, retainContextWhenHidden, screens.ts, 契約, BoardMessage, board-feed, サイドパネル, BoardTarget, select, EditorBar, 上部の並び, target-memory, 開くときの設定]
---

# 画面の作り

## 承認のオーバーレイ

この画面の遷移は`core/approval-machine.ts`の1か所にまとめてある。散らばっていると必要なガード条件を書き漏らすため。「この状態ではこれを受けない」というガード条件は、そのファイルの先頭の表にある。`board-panel.ts`は入力を渡し、返ってきた「やること」を実行するだけ。単体テストはCB-T169〜181とCB-T378〜380（CB-T181 は、ガード条件を1つ消すとテストが失敗することを確かめる変異テスト）。

計画編集タブとの結び付き（未保存のタブがある親を含む一覧は承認を打たない、「計画を編集」は閉じてからタブを開く、保存したあとに提案が変わった親を覚える）も、この遷移のガードと入力に置いた。遷移はVS Codeに触れないので、タブの様子（未保存の親・保存したときのハッシュ）は`board-panel.ts`が帳面`core/workflow-tabs.ts`から引いて入力に添える。押された時点の様子で決めるので、`board-panel.ts`は承認を打つ直前（`approve`のやること）にももう1度帳面を見る。その確かめも遷移のファイルの純関数（`blockedBeforeApprove`）にして、変異テストの表に入れてある。

承認の一覧と対応方針の画面の目次は`webview/board/Toc.tsx`（CSSは`Toc.css`）。目次は行き先の`id`へ`scrollIntoView`するだけで、本文を項目に分けない。行き先の`id`は`Approval.tsx`（`approval-summary`・`approval-tickets`・`approval-plan-<親>`・`approval-legend`・`approval-body`・`approval-rejected`・`approval-problems`・`approval-confirm`）と`Decide.tsx`（`decide-thread-<番号>`・`decide-confirm-area`）が付ける。承認のボタンはページの最後のままで、目次の「承認」はそこへ送る。

文を渡す3ボタン（`Approval.tsx`の`HandOver`）は「新しいセッションで開く」を主ボタンにし、開いたときの焦点もそこに置く。「ターミナルで実行」の名前は`core/approvemodel.ts`の`PROMPT_RUN_LABEL`（「ターミナルで実行（確認なしで動きます）」）。確認なしで動くので、ボタンは`risky`のクラスで警告の見た目（テーマの警告色`--vscode-editorWarning-foreground`の枠と、読める濃さの警告の文字`--warn-text`）にし、［コピー］から16px離す（`Approval.css`）。押す前の確認は足さない。フロー編集の依頼の文（`webview/flow/Proposal.tsx`の`RequestText`）も、主ボタンを「新しいセッションで開く」にそろえる。

## 列と親ごとの行

ボードの本体は`webview/board/Lanes.tsx`（CSSは`Lanes.css`）。1行目に列の見出し（`.column`。畳む切り替え・件数・幅の取っ手。件数は子だけを数えるので「子2」と書き、ツールチップで親を数えないことを言う。畳んだ列の各行の`.folded-count`も「子3」と書き、ツールチップで何の列の子の数かを言う）、その下に親ごとの行（`.lane`）を並べる。列の幅を全部の行で揃えるため、ボードは1つのgridで、`.lane`は箱を作らない（`display: contents`）。列の幅の指定は`tracksOf`がインラインのstyle（`grid-template-columns`）に書く。畳んだ列は`max-content`、固定した列はpx、ほかは`minmax(220px, 1fr)`。ドラッグの最中は、その列だけを差し替えた指定をDOMに直に書き、離したときに`state.ts`の`widths`に覚える。

行に分けるのは`lanesOf`で、キーはカードの`family`とプロジェクトの組（`laneKey`）。親のカードは行の見出し（`CardItem`の`head`。`div.card.parent.lane-head`）、子は列ごとのセル（`.cell[data-cell=<列>]`）に入る。親のカードが無い子の行は`.lane-orphan`の見出しを持ち、選べない。隠れたカード（絞り込み）と畳んだ列のカードもDOMには置き、CSSで隠す（`.card.hidden`、`.folded-cell .cards`）。親も子も見えない行は`.lane.hidden`。見出しの補助の操作（［計画編集］・「フェーズを表示」）は`CardItem`の子として渡し、主な操作（［承認］）と同じ段に並べる。「フェーズを表示」で開いた行は`state.ts`の`opened`（`laneKey`の一覧）に覚え、見出しの下に`LanePhases`がフェーズごとの状態の一言（`PhaseChip.status`）と子の識別子とバッジの言葉を並べる。

開いた行の「計画の流れを見る」は、親のカードの`plans`（`parents[].plans`のまま。`core/board.ts`が親のカードに移す）が空でないときだけ`LanePhases`が出す。押すと`App.tsx`が親の`cardKey`を`useState`に持ち、`webview/board/PlanFlow.tsx`（CSSは`PlanFlow.css`）を`Dialog`で重ねる。図は承認のオーバーレイと同じ`shared/PlanGraph.tsx`を`editable`なしで使う。拡張ホストへは何も送らず、`state.ts`にも覚えない（更新で親が消えるか`plans`が空になったら出さない）。承認のオーバーレイが出ている間は出さない。Escは`ESCAPE_LAYER.overlay`で受ける。図の中身を拡張で組み直さないため、承認済みの親の図も実行ファイルが承認の`plans`と同じ関数（`planedit.ApprovedPlans`）で組む。

完了と取り消しの列は最初から畳む（`core/board.ts`の`FOLDED_FIRST`。`folded`を覚えていなければこれを使う）。案内の見本も、手順が`fold`を書かなければ同じ既定で描く。940px以下（`useNarrow`と同じ条件）では`Lanes.css`のメディアクエリがgridをやめ、行を縦に積み、セルの`.cell-label`（列の名前と件数）を出す。子の無いセルと畳んだ列のセルは出さない。

## カードの組み立て

カード1枚は、上から順に見出し（タイトルが主、IDは補助、状態のバッジは右端。DOMはID・タイトル・バッジの順のままCSSの`order`で並べる）、局面の行（親だけ。`.stage`）、動いた表示、注意（短い言葉）、主な操作（あれば1つ。`.card-primary`）の順に並ぶ。`webview/board/Card.tsx`が描く。

選んだカードの右のパネルは`webview/board/Panel.tsx`（CSSは`Panel.css`。見出し・ボタン・詳細の行の見た目は`Card.css`を共用する）が描く。上から見出し（タイトル・ID・状態のバッジ・［閉じる］）、位置（子は`.where`、親は`.stage`）、MR（`.card-mr`）、ユーザ待ち（`.turn`）、フェーズ行（親だけ）、操作（`.card-actions`。チケットを開く・フロー・計画編集・テンプレートの下書き）、詳細（`.detail-rows`。畳まない）。何を出すかはカードもパネルも`core/board.ts`と`core/status.ts`が決めた値のとおりで、画面は判定し直さない。

選んだカードは`webview/board/state.ts`の`selected`（`cardKey`＝プロジェクトとIDの組）に1つだけ覚える。画面が作り直されても残り、ボードからそのカードが消えたら外す。パネルを置く場所は`App.tsx`が決める。広い幅では列の右（`.board-area.with-panel`）、`Lanes.css`が行を縦に積むのと同じ`(max-width: 940px)`では`matchMedia`で見て、選んだカードのすぐ下（子は`.card-panel-slot`の`<li>`、親は行の見出しの直後）。親は行の見出しにあるので、列を畳んでもパネルは隠れない。パネルはEscで閉じる（`ESCAPE_LAYER.panel`。オーバーレイと案内より奥）。

注意の行（短い言葉）はカードに残す。選ばなくても例外（不備・書き込み停止・先行待ち・リスクHIGH以上など）に気づけるようにするため。理由の全文はパネルの詳細。

**状態のバッジは「状況」、操作欄（ユーザ待ち）は「ユーザができること」。** 両者は別の規則で決め、同時に出てよい（バッジ「要確認」と「承認」など）。操作してよいかの最終判断は実行ファイルが持つ。

状態のバッジ（`statusOf`）。上から最初にヒットしたものを採る。判定は実行ファイルのJSONが持ち、ここは判定済みの欄を表示の言葉に置き換えるだけ。レビューの要否・ゲートの条件・リスクは判定し直さない。

| 順 | 子 | 親（レビューの状態はフェーズ一覧から見る。親カードの`gateClosed`は使わない） |
|---|---|---|
| 1 | `blocked`か`scattered` → 要確認（理由はツールチップ） | 同左 |
| 2 | `pendingApproval` → 承認待ち | `pendingApproval` → 承認待ち（承認済みの親に改版が出ていれば「承認待ち（改版）」） |
| 3 | 取り消し（`cancelledAt`あり。アーカイブも同じ）→ 取り消し | 同左 |
| 4 | 完了の列か、取り消しでないアーカイブ → 完了 | 完了・アーカイブ → 完了（早めに閉じていれば「完了（早めに終了）」） |
| 5 | `copyStatus === "review"`: 自分のフェーズの一言（レビュー待ち／レビュー準備中／レビュー予定）。それ以外は要確認 | どれかのフェーズがレビュー待ち → レビュー待ち |
| 6 | `copyStatus === "open"`: フェーズが`reviewWaiting`か`gateClosed`なら要確認。`startedAt`が空なら着手待ち、あれば作業中 | どれかのフェーズがレビュー準備中 → レビュー準備中 |
| 7 | どれにもヒットしない → 要確認（読めた欄をツールチップに出す） | `copyStatus === "open"` → 着手待ち／作業中 |
| 8 | — | どれにもヒットしない → 要確認 |

着手待ち・作業中・レビュー準備中のバッジには、ツールチップ（title）にだけ、次に誰が何をするかを書く。画面の見える位置には出さない。着手待ちはユーザがClaude Codeに着手を頼むこと、作業中とレビュー準備中はエージェントの番でユーザの操作が要らないこと。

フェーズ行の一言（`phaseStatusOf`）。使う欄はPhaseJsonのもの。

| 順 | 条件 | 言葉 |
|---|---|---|
| 1 | `gate_closed`かつ`review_waiting` | レビュー待ち |
| 2 | `gate_closed` | レビュー準備中 |
| 3 | `marks.reviewed`がある | レビュー済み |
| 4 | `marks.skipped.deferred_to`があり、その番号のフェーズに`marks.reviewed`がある | レビュー済み |
| 5 | `marks.skipped.deferred_to`がある | レビュー予定 |
| 6 | `marks.skipped`がある | レビュー省略 |
| 7〜9 | `state`が`ended` / `active` / `planned` | 終了 / 進行中 / これから |
| 10 | 上のどれでもない | 要確認 |

`marks`の中身（`skipped.deferred_to`）は`ccnavi --explain --json`の`marks`にそのまま載っているので、チケット管理画面は推測で代わりを作らない。

ユーザ待ちは、ユーザができることがあるときだけ出し、主ボタンは1つ。カードには主ボタンだけ（承認待ちなら「承認」、そうでなくバッジが「レビュー待ち」の子なら「レビュー完了」）を出し、パネルの「ユーザ待ち」に説明の文と操作の全部を出す。親は承認待ちなら「内容を確認して承認してください」と「承認」で、レビューの操作は置かない（子だけ）。子は承認待ちなら「承認」、バッジが「レビュー待ち」なら「MRでレビューしてください」と「MRを開く」（リンク）と「レビュー完了」（主ボタン）の順で1つだけ出す。承認は`pendingApproval`だけを条件にし、バッジが「要確認」でも出す。案内の指す先（`data-tour`の`approve-one`・`review-done`）はカードのボタンにだけ付ける。

注意の行は例外のときだけ短い言葉で出す（不備・書き込み停止中・先行待ち・リスクHIGH以上・複数の場所にある・ワークツリー未作成（承認待ち・着手待ちの子）・ワークツリーが見つからない（作業中・レビュー中）・未送信）。理由の全文、issue・ブランチ・ワークツリー名・プロジェクト・取り消しの理由、リスクの文言（JSONのまま）、人間レビューの要否と理由はパネルの詳細に出す。base / 合流のsha、マーカー名は画面に出さない。案内の見本では、選んだカードを実際の状態に書き込まない（案内は`scene.select`で見本のカードを選ぶ）。

カードを押す（または焦点を当ててEnter）とそのカードを選ぶ。ボタン・リンクの上では選ばない（`NOT_SELECTING`）。チケットのファイルはパネルの「チケットを開く」で開く。

## ボタンの見た目

- 押せない`button.action`（`extensions/shared/styles/button.css`。Chrome拡張と共有）は、押せるものと見分けがつくよう`opacity: .4`にし、主ボタン（`primary`）も主ボタンの色をやめて副ボタンの地にする（回り記号の`busy`の間は色を残す）。カードの`card-button`も同じ濃さ。
- それ以外の共通の`button.action`の見た目とChrome拡張は変えない。カードと右のパネルの中の操作ボタンと、操作として置いた「MRを開く」リンクにだけ、新しいクラス`card-button`（主ボタンは`card-button primary`）を付ける。マージ状況の帯のボタンとリンクも同じ。
- `Card.css`で文字サイズ（`var(--vscode-font-size)`）・高さ（`min-height: 26px`）・余白を絶対値で明示し、親（カード・フェーズ一覧・列）の文字サイズを引き継がない（`font: inherit`を使わない）。幅に収まらない言葉はボタンの中で折り返す。
- 主ボタンは「ユーザ待ちの操作」だけ。操作のまとまり（カードの主な操作・パネルの「ユーザ待ち」）ごとに1つまで。補助の操作（チケットを開く・フロー・計画編集・テンプレートの下書き）は通常の色。
- カードの中で縦に広がるメニューは使わない。
- 詳細の中のリンクは普通のリンクのまま（`detail-link`）。ボタンの寸法にしない。
- 検査: DOMで、操作ボタンと操作のMRリンクが全部`card-button`を持つこと、詳細の中のリンクと位置の下のMRのリンク（`.card-mr a`）は持たないこと、操作のまとまりごとに主ボタンが1つ以下であることを見る（`test/board/card.dom.test.ts`）。寸法と見た目は、拡張開発ホストでの手動確認（READMEの6g）に回す。狭い列（220px）・長いタイトル・ズーム150% で、操作ボタンの高さと文字サイズが揃っていること。

## レビュー完了の流れ

「レビュー完了」（バッジが「レビュー待ち」の子のカード）は、`approval-machine.ts`の`decide`に入る。`loadDecide`の効果が`ccnavi-review.sh decide <N> --preview`を読み、`parseDecidePreview`が**厳しく検証する**。頼んだ親・フェーズ・MRの番号（フェーズの依頼が持つもの。分かるとき）と照らし、不正な要素は破棄せずに全体をエラーにする。読み取れなければ「指摘を読み取れませんでした（理由）」で確定のボタンを出さない。正しく読めて0件なら「フェーズNに未解決の指摘はありません」と「レビュー済みにする」、1件以上なら対応方針の画面（`Decide.tsx`）。確定後の表示は実行ファイルの答え（`reviewed`・`pending_extra`・`followup`）に従い、続きの子が起きていないのに追加された指摘があれば、決め直しへ戻れる（`retry`）。

副ボタン「エージェントに相談するプロンプトをコピー」は、遷移が`copyConsult`の効果を返し、`board-panel.ts`がクリップボードに入れて結果（`consultCopied`）を戻す。オーバーレイは閉じず、選びかけの対応方針はReactの状態（ダイジェストが同じ間は保たれる）に残る。確定中（`deciding`）と0件の画面では組まない。プロンプトは`commands.ts`の`consultPrompt`が組み、許すのは`decide <N> --preview`を単体で実行することだけ。

## マージ状況の確認

遷移は`core/merge.ts`の1か所にまとめてある（承認の遷移と同じ形。VS Codeに触れないので単体で試せる）。「いまの状態 ＋ 入力 → 次の状態 ＋ やること」で、`board-panel.ts`が持ち直し、描き直し、効果を行う。結果はパネルの状態として拡張ホストが持つ（チケット管理画面が開いている間だけ）。頼んだ状態でなければ受けない: 確認ごとに世代（`token`）を振り、閉じたチケット管理画面へ遅れて返った結果や、前の確認の遅れた答えは破棄する。確認中の「確認」は受けない。

読み取り（`loadList`＝`ccnavi sync retired`、`loadState`＝`ccnavi-review.sh mr-state`）と、書き込み（`sendRetire`＝ターミナルへ送る）は、効果の種類で分ける。送る効果が返るのは、確認のダイアログで「実行する」と答えたあとだけで、`test/shared/read-only.test.ts`がソースの形でその線を保つ。答えの検証は`core/mrstate.ts`（形が想定外なら「出力の形が想定外」、期限切れは拡張の側だけの種類`timeout`）。互換の版は上げない（`mr-state`は追加だけで、実行ファイルを起こさない）。

## 見た目

実装はbodyのクラスの下でテーマ変数を上書きし、拡大率をbodyの`style.zoom`に当てるだけ（配色は`src/webview/styles/appearance.css`、切り替えは`src/core/appearance.ts`と`src/webview/appearance.ts`。画面が受けるのは`src/webview/useHostMessages.ts`）。配色と拡大率（`ccnaviBoard.zoom`）は、ほかの中身と同じ段取り（`ScreenHost`）で1つのメッセージ`{ type: "appearance", value, zoom? }`に載せて送る（等倍のときは`zoom`を付けない。画面は付いていなければ拡大を外す）。拡大率はHTMLには埋めないので、組み上がる（`ready`）までは等倍で描かれる。組み上がっていない画面と破棄された画面には送らない。保持しない画面には作り直されたあと`ready`で、保持する画面には再表示されたところで送り直す。ハイコントラストのテーマではClaudeの配色を適用しない。設定を書く先は、いま値が定義されている場所（フォルダ → ワークスペース → ユーザ）。

## 拡張全体の答えとサイドパネル

`--explain --json`の答えは拡張全体で1つだけ持つ（`board-feed.ts`。読み直しのまとめ方は`core/feed.ts`）。監視（`core/watch.ts`の`FEED_PATTERNS`）もそこにあり、ボードを閉じていても拡張が動いている間は続ける。聞き手はサイドパネル（`sidebar.ts`）・ボード（`board-panel.ts`）・フロー編集の「提案あり」（`flow-panel.ts`）・チケット制御の入口（`ticket-control.ts`）で、どれも同じ答えを`subscribe`で受ける。ボードは自分で監視も読み込みもしない。読み直しを頼むのは`boardFeed.refresh()`で、重なった頼みは1回にまとめられる。聞き手は`extension.ts`で、チケット制御 → サイドパネルの順に登録してから`watchBoardFeed`で最初の読み込みを始める（サイドパネルの行がチケット制御の値で決まる）。

サイドパネルのツリーの中身（行・押したときに開くもの・バッジの数）は`core/sidebar-tree.ts`が決め、`sidebar.ts`はVS CodeのTreeItemに置き換えるだけ。件数と分類は`inbox`のまま並べる。行を押すと`ccnaviBoard.sidebarOpen`（パレットには出さない）が`screens()`の帳面を通して画面を開く。ボードの中の場所（承認・レビュー完了・カードを選ぶ・MRの状況を確認）は`screens().board(target)`の`BoardTarget`で渡し、ボードは答えが読めたら1度だけ進む。承認とレビュー完了はカードのボタンと同じ入力（`approveTickets`・`startDecide`）を遷移に入れるので、行けるかの確かめも同じ。カードを選ぶ（`select`）は、絞り込み（`filter`）と同じく拡張（`board-panel.ts`の`select`）が覚え、画面に届いたら消すメッセージで送る（作り直し中なら`ready`で送り直す）。画面（`App.tsx`）はそのカードを隠している絞り込み・畳んだ列・アーカイブの表示を外し、カードを押したときと同じ選択（`state.ts`の`selected`）にして焦点を当てる。

## 機能や画面を足すとき

**チケット管理画面に機能を足すときに触る場所。**

| 足すもの | 触る場所 |
|---|---|
| カードかパネルに出す項目 | `core/status.ts`（バッジの言葉と規則）→ `core/model.ts`（実行ファイルとの契約。JSONを増やすならGo側の`go/internal/clitest/ticket/flow/board_test.go`が書き出す見本`test/fixtures/board.json`も）→ `core/board.ts`（出すか出さないかの判断）→ `webview/board/text.ts`（出す言葉）→ `webview/board/Card.tsx`（カード。状態と主な操作だけ）か`webview/board/Panel.tsx`（右のパネル。それ以外）→ `webview/board/Card.css`・`Panel.css`（CSS）→ テスト（`test/board/model.test.ts`・`board.test.ts`・`status.test.ts`・`card.dom.test.ts`・`render.dom.test.ts`） |
| ユーザが押せる操作 | `core/board-view.ts`の`BoardMessage` → 画面のボタン → `board-panel.ts`の`KNOWN`（形の確認の一覧）と`asMessage`（形の確認）と`handleMessage`（処理）。`KNOWN`か処理を書き忘れると型が合わなくなる（どちらも型で網羅を強制してある） |
| 画面が覚えるもの | `webview/board/state.ts`（形と既定）→ `App.tsx`（読み書き）→ `test/board/board.dom.test.ts` |

**画面を1つ足すとき、使い回せるもの。** `webviewScript` / `webviewStyle`（画面の名前で読む。拡張子は関数の側が付ける）、`poster<M>()`、`core/screen-host.ts`（`screenHost` / `retainedHost`と`embedJson`）、共通の部品`@ccnavi/shared`（`extensions/shared/styles/`の`button.css`・`page.css`・`list.css`。画面のstyle.cssで`button.css`・`../styles/appearance.css`・`page.css`の順に @importする）、`webview/vscode.ts`・`webview/initial.ts`・`webview/appearance.ts`、`webview/useHostMessages.ts`（拡張ホストからのメッセージの受信。見た目もここで受け、`ready`は呼ぶ側が渡す）・`webview/useEscape.ts`（Escは最も手前の面だけが受ける。設定は`ESCAPE_LAYER`、CSSの`z-index`と同じ順）・`webview/useSaveKey.ts`（Ctrl+Sでの保存）と`core/save-gate.ts`の`editSaveBlocker`・`saveTitle`（「保存」を押せない理由。ボタンとCtrl+Sが同じ答えを使う）・`webview/shared/RemovedBand.tsx`（削除した行を「元に戻す」帯）・`webview/shared/EditorBar.tsx`（図の編集の上部の並び。対象の名前 → 状態 → 元に戻す・やり直す → 補助の操作 → 主ボタンの順を決める。フロー編集・計画編集・テンプレート管理の編集欄が使う）・`webview/shared/StatusLine.tsx`（直前の操作の一言。`ScreenHeader`の`status`に渡すとツールバーの最後の行に出る）・`webview/Tour.tsx`の`ScreenHeader`（ツールバーと「?」を組む）、テストの共通部品（`test/helpers/dom.ts`・`test/helpers/bundle.ts`）。

画面ごとに要るのは、契約（`*-view.ts`）・入れ物を組む関数・画面のCSS（`webview/<名前>/style.css`と部品ごとのCSS）・`useHostMessages(handler, ready)`の引数（handlerと`ready`）・テストの入口（`test/helpers/<名前>.ts`）と、`test/shared/style.test.ts`の`reactPages`への1行（足し忘れはCB-T127 が止める）。

**実行ファイルへの往復がある画面（ルール管理）も、やり取りの形は同じ。** 画面は編集中の中身を付けて頼み（`judge` / `expectations`）、拡張ホストが実行ファイルの結果をそのまま返す（`judged` / `expectationsChecked`）。画面は判定の理屈を持たない。ただし期待する判定のファイルが無いときは、拡張ホストが実行ファイルを呼ばず案内（`notice`。赤くしない）を返す。VS Codeのダイアログが要る欄（`pickFile` → `picked`）も同じで、行を特定するキーは画面が渡し、拡張ホストは読まずにそのまま返す。

**画面を1つ足すときに触る場所。**

| 足すもの | 触る場所 |
|---|---|
| バンドルと回り方 | **どちらも直さない。** パスの付け方の約束で決まる。画面は`src/webview/<名前>/main.tsx`、バンドルした出口は`out/webview/<名前>.js`、CSSは`src/webview/<名前>/style.css` → `out/webview/<名前>.css`、テストの入口は`test/helpers/<名前>.ts`、グループは`test/<名前>/`。`bundle-webview.js`と`test-groups.js`が同じ見つけ方でディスクから拾う |
| 回すものの決まり方 | 触ったファイルがどの画面のバンドルに入るかを閉包で見る（スクリプトは`main.tsx`から`import`、CSSは`style.css`から`@import`。置き場のパスでは決めない）。どの画面にも入らないもの（`webview/vscode.ts`・`webview/styles/`など）は全画面に関わると見る。画面と同じ名前のグループは、`test/helpers/<名前>.ts`を作り忘れても必ず回る |
| 契約に置く型 | 画面に渡す形（`ProjectsPage`のような）は契約の側（`*-view.ts`）に置く（`core/`の判定のファイルに置くと、`node:path`を読むファイルを辿って画面の型検査が失敗する） |

**段取りは2系統ある。パネルの`retainContextWhenHidden`と対で選ぶ**。

| パネル | 作るもの | 非表示になったとき |
|---|---|---|
| `retainContextWhenHidden: false`（チケット管理画面・プロジェクト管理） | `screenHost(surface, render)` | 画面は破棄される。入れ物ごと入れ直す |
| `retainContextWhenHidden: true`（ルール管理・リスク管理・フェーズ管理・テンプレート管理・取り込み待ち・フロー編集・計画編集） | `retainedHost(surface, render)` | 画面は生きている。何もしない |

返る形（`ScreenHost<D>`）と呼び方（`send` / `post` / `ready` / `hidden`）は同じ。取り違えても型では止まらない。保持する画面に`screenHost`を使うと打ちかけが消え、逆だと中身が古いまま更新されない。保持する画面のパネルは、`ScreenHost`を返す関数（`rulesHost`・`riskHost`・`phasesHost`・`templatesHost`・`handoffHost`・`flowHost`・`workflowHost`）で作り、`retainedHost`の形に合わせる。この関数は`panel-common.ts`の`assertRetained(panel, root, 画面の名前)`で`panel.options.retainContextWhenHidden`を見る。偽なら診断ログに残す（保持しない2画面は`assertRetained(..., false)`）。

保持する画面には、再表示されたところで知らせ（`lock`・`changed`）と見た目を送り直す。非表示の画面に届くかどうかについて、VS Codeの型定義の記述が一致していない。中身（`data`）は送り直さない。

保持する画面に中身（`data`）を渡すのは、画面の編集を破棄してよいときだけ（ユーザが「更新」を押した、保存や作成が通って中身が入れ替わった）。監視がファイルの変化に気づいても中身は渡さず、帯（`changed`）を出してユーザに決めてもらう。

ルール管理・リスク管理・フェーズ管理の3画面は、パネルの受け付け・再表示・破棄、監視の反応の遅らせ方、読み直しと描き直し、対象の切り替え、保存できるかの取り直し、3画面で同じ操作（`ready`・`dirty`・`tourDone`・`reload`・`openHandoff`・`switchTarget`。読めていない画面では読めなかったファイルを開く`openFile`も）の受け付け、保存の流れを`settings-panel.ts`（`settingsPanel`）にまとめてある。保存の流れは、一時ファイルに書く → `--lint` → 作業中のチケットの確認 → 書く → 読み直す → warnの帯 → 通知の順に進む。保存の書き込みはフロー・テンプレートと同じ`core/flow-write.ts`の`writeTreeFile`で、ツリーの外とリンクを断り、一時ファイルの入れ替えで書き、入れ替える直前にもう一度外の変化を確かめる。画面ごとに違う仕事（何を読む・何を見せる・どのファイルを見張る・検証に何を渡す・画面にだけある操作）は、`settingsPanel`に渡す`SettingsScreen`のコールバックで注入する。3画面の差を共通の側に持たせず、残す仕事は画面のパネルに残す。

設定の4画面（ルール・リスク・フェーズ・テンプレート）は、開くときの設定を`target-memory.ts`の`targetToOpen(画面の既定)`で決め、「設定」の欄で切り替わったら`rememberTarget`で覚える（ルール・リスク・フェーズは`settings-panel.ts`の切り替えの受け付け、テンプレートは`templates-panel.ts`）。決め方（覚えたものが選択肢にあるか）は`core/targets.ts`の`openingTarget`で、拡張全体の答え（`boardFeed.latest()`）を読むだけ。開いているパネルは前面に出すだけで、外から設定を切り替えない（入口`screens().rules()`などは引数を持たない）。対象どうしの比べ（`sameTarget`）と欄の値への出し入れ（`targetKey`・`targetFrom`）も`core/targets.ts`の1か所にあり、4画面と`settings-panel.ts`が使う。

画面（`*-panel.ts`）どうしは互いをimportしない。ある画面から別の画面を開く導線（ルール管理からフェーズ管理の対象を切り替える、など）は、相手のパネルの関数を直に呼ばず、`core/screens.ts`の帳面（`screens().board(...)`）を通す。帳面に入口を載せるのは`extension.ts`だけで、載せ方を見る場所もそこ1か所。
画面の様子をほかの画面が知る必要があるときも、相手のパネルを読まずに小さな帳面を通す。チケット管理画面のカードの「計画編集」は、いまのチケット管理画面でそのカードが入口を出しているかを確かめてから、コマンド`ccnaviBoard.openWorkflow`（帳面の`screens().workflow`と同じ入口）を呼ぶ。計画編集タブの未保存と保存したときのハッシュは`core/workflow-tabs.ts`にあり、タブが書いてチケット管理画面が読む（変わったら知らせを受けて描き直す）。
