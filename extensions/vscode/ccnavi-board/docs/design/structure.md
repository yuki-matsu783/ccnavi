---
title: ccnavi ボードの構成
type: design
description: VS Code 拡張 ccnavi ボードのファイルの置き場と役目、テストの ID の決まり
tags: [design-doc, extension, testing]
keywords: [設計, 構成, ファイル, src, core, webview, test, fixtures, テストの ID, CB-T, CB-D]
---

# 構成

```
src/
  extension.ts        画面の入口の登録（core/screens.ts の帳面）、コマンド登録とサイドパネルの登録（vscode に依存する）
  sidebar.ts          左端のアイコンから開くサイドパネルの 5 つの入口。チケット制御が disable なら 2 つ。見た目はタイトルバーの配色のアイコンで、今の値を見出しの横に出す（vscode に依存する）
  ticket-control.ts   CCNAVI_TICKET_CONTROL を設定ファイルから読み、context key に反映する。変化を監視する（vscode に依存する）
  board-panel.ts      ボードの Webview パネルの生成・更新・破棄、監視、操作の受け付け。承認のオーバーレイと動いた表示もここが持つ（画面は作り直されるので持たせない）。1 枚目は HTML ごと、以後は postMessage で中身だけ渡す（vscode に依存する）
  rules-panel.ts      ルール管理画面の Webview パネル（1 枚。対象（共通の設定・ワークスペースの設定・プロジェクトの設定）を切り替える）。判定・検証・保存の受け付け（vscode に依存する）
  risk-panel.ts       リスク管理画面の Webview パネル（ワークスペースに 1 つ）。検証・作成・保存の受け付け（vscode に依存する）
  phases-panel.ts     フェーズ管理画面の Webview パネル（1 枚。対象（共通の設定・ワークスペースの設定・プロジェクトの設定）を切り替える）。検証・保存の受け付け（vscode に依存する）
  flow-panel.ts       フロー編集画面の Webview パネル（子ごとに 1 枚。ボードのカードから開く）。置き場と錠を実行ファイルに聞き、保存を受け付ける。エージェントの下書きの「提案あり」・取り込み・保存のあとの削除と、依頼の文もここ（vscode に依存する）
  prompt-handover.ts  Claude Code に文を渡す 2 つの経路（コピー / 新しいセッションで開く）。ボードとフロー編集画面が使う（vscode に依存する）
  projects-panel.ts   プロジェクト管理画面の Webview パネル。clone の送信、.gitignore への追記（vscode に依存する）
  terminal.ts         「ccnavi」ターミナルの用意とコマンドの送信（vscode に依存する）
  tour.ts             画面ごとの初回の案内を見たかどうか（拡張の globalState に画面の名前ごとに持つ）（vscode に依存する）
  ccnavi.ts           実行ファイルの探索と --explain --json / --test --json / --test-samples --json / --lint（--rules / --project-rules-file / --risk / --phases / --project-phases-file の差し替え）/ --lint --json（--flow でフロー 1 本を確かめる）/ --agree --preview --json / --agree --yes … --json / --suggest --json の実行（Node の子プロセス）
  git.ts              ローカルの git を読み取り専用で起動する（origin を読む。Node の子プロセス）
  webview-asset.ts    バンドルした画面（out/webview/<名前>.js）と CSS（同 .css）を読む。渡すのは画面の名前で、拡張が <script nonce> と <style nonce> に流し込む
  core/
    model.ts          ボードの JSON の形（実行ファイルとの契約）と読み取り
    approvemodel.ts   承認の JSON の形（--agree --preview --json / --agree --yes … --json）と読み取り
    testmodel.ts      試験の JSON の形（--test --json / --test-samples --json）と読み取り
    suggestmodel.ts   候補の JSON の形（--suggest --json）と読み取り。allow の候補は並べない
    lintmodel.ts      lint の JSON の形（--lint --json）と読み取り、プロジェクトごとの苦情とフローの苦情（(flow)）の抜き出し、古い実行ファイルの「知らないオプション」の見分け
    board.ts          列とカードへの組み立て、操作の有無
    board-moved.ts    前の読み直しから列が変わったカードと、新しく現れたカード（いまの状態 ＋ 読めたボード → 次の状態）。持ち直すのは board-panel。VS Code に触れないので単体で試せる
    board-view.ts     ボードの拡張ホストと画面の契約（見せる中身 BoardData、押した操作 BoardMessage、承認のオーバーレイの状態）
    approval-machine.ts 承認のオーバーレイの遷移（いまの状態 ＋ 入力 → 次の状態 ＋ やること）。外へ出る仕事は返すだけで、行うのは board-panel。VS Code に触れないので単体で試せる
    screen-host.ts    画面に中身を渡す段取り（送る / 入れ物ごと / 作り直し中で持ち越し）。VS Code に触れないので単体で試せる
    render.ts         ボードの入れ物の HTML（外部資源なし、テーマ変数だけ）。中身は画面（React）が作る
    rules-view.ts     ルール管理の拡張ホストと画面の契約（ルールの形 SECTIONS / RuleForm / RulesModel、見せる形 RulesPage / RulesData、押した操作 RulesMessage）
    rules-render.ts   ルール管理の入れ物の HTML（外部資源なし）。中身は画面（React）が作る
    rules-doc.ts      rules.yml の読み書き（yaml の Document でコメントを残す）
    risk-view.ts      リスク管理の拡張ホストと画面の契約（配点の形 RiskForm、見せる形 RiskPage / RiskData、押した操作 RiskMessage）
    risk-render.ts    リスク管理の入れ物の HTML（外部資源なし）。中身は画面（React）が作る
    risk-doc.ts       risks.yml の読み書き（同じくコメントを残す）と、組み込みの配点の本文
    phases-view.ts    フェーズ管理の拡張ホストと画面の契約（定義の形 PhasesForm、見せる形 PhasesPage / PhasesData、押した操作 PhasesMessage）
    phases-render.ts  フェーズ管理の入れ物の HTML（外部資源なし）。中身は画面（React）が作る
    phases-doc.ts     phases.yml の読み書き（同じくコメントを残す）
    phases-graph.ts   フェーズの図の点・線・置き場所を定義のリストから組む。VS Code に触れないので単体で試せる
    phases-route.ts   図の線の経路（点を横切らない折れ線）。VS Code に触れないので単体で試せる
    flow-doc.ts       子のフロー（YAML）の読み書きと編集（知らない欄・種類を落とさない）、雛形、入れ子の段の数え方と注意。正しいかは決めない（描けないときだけ断る）
    flow-view.ts      フロー編集の拡張ホストと画面の契約（見せる形 FlowPage / FlowData、押した操作 FlowMessage とその形の確認、錠を実行ファイルの答えから引く flowTargetOf、カードのボタンの言葉、エージェントへの依頼のボタンの言葉と依頼の文）
    flow-render.ts    フロー編集の入れ物の HTML（外部資源なし）。中身は画面（React）が作る
    flow-lint.ts      フローの本文を呼ぶたびに別の名前の一時ファイルに書いて実行ファイル（--lint --json --flow）に確かめさせ、(flow) の error を理由にする。通れば実行ファイルが読んだ中身（flow.data）と、(flow) の warn（パスを直したもの）・渡る手順（rendered）・候補（candidates）を返す
    flow-match.ts     画面の中身と実行ファイルが読んだ中身（flow.data）の見比べ（整数と浮動小数の揃え方、食い違った場所と両者の値の言い方）
    flow-history.ts   フロー編集の元に戻す・やり直すの履歴（直す前の中身の複製を積む。同じ欄への打ち込みをまとめる。上限 100）
    flow-diff.ts      読み込んだフローと編集中のフローの見比べ（未保存の判定と、保存前に見せる足した・消した・変えたノードと線）。下書きの取り込みの前に見せる、値の前後まで並べた差分（textDiff）
    tour-place.ts     吹き出しの案内の置き場所（画面の外に出さない）。DOM に触れないので単体で試せる
    tour-sample.ts    案内の間だけ出す見本（ボードのカード）。チケットがまだ無いワークスペースで、案内が指す先を作る
    yaml11.ts         実行ファイル（PyYAML、YAML 1.1）が文字列以外に読む語の見分け。risk-doc と phases-doc が引用符を足す判断に使う
    targets.ts        ルール管理とフェーズ管理が切り替える設定の対象（共通・ワークスペース・プロジェクト）の一覧と、欄の値
    projects.ts       プロジェクト管理の判断。URL と名前の検査、origin の鍵、clone の行、プロジェクトになっていない .git の探索、.gitignore の行の有無と追記、画面の中身の組み立て
    projects-view.ts  プロジェクト管理の拡張ホストと画面の契約（見せる形 ProjectsPage / ProjectsData、押した操作 ProjectsMessage）
    projects-render.ts プロジェクト管理の入れ物の HTML（外部資源なし）。中身は画面（React）が作る
    hooks.ts          settings.json の hooks の読み取りと、ツール名で走る hook の絞り込み
    lock.ts           保存できるか（doing のチケットの有無。プロジェクトのルールならそのプロジェクトの分だけ）
    commands.ts       ターミナルに送るコマンド行と、承認・残った指摘の対応方針を子プロセスで打つ引数の配列
    decidemodel.ts    残った指摘の JSON（実行ファイルとの契約）の読み取りと、選んだ対応方針の確かめ
    screens.ts        画面の入口の帳面（Root）。どの画面をどう開くかを 1 か所に集める。画面どうしは互いを import せず、ここへ要求を出す
    watch.ts          4 つの画面が見張る場所（提案・承認済みチケットとマーカー・ワークツリーの登録）の glob
    locate.ts         実行ファイルの探索順
    ticket-control.ts CCNAVI_TICKET_CONTROL の読み取り（settings.json と settings.local.json）と、実行ファイルが返す値（ボードの JSON の `settings.ticket_control`）との突き合わせ
  webview/            画面（React）。DOM を触る側で、vscode も node も import しない。tsconfig.webview.json で型を見る
    vscode.ts         acquireVsCodeApi の窓口。画面ごとの契約に依存しない（送り口は poster<M>() で作る）
    initial.ts        埋め込みの JSON（最初の 1 枚）を読む。画面ごとの契約に依存しない
    appearance.ts     見た目の切り替え（body のクラスの付け替え）。5 画面で 1 本
    Tour.tsx          吹き出しの案内と、その出し入れ（useTour）。4 画面（ボード・ルール管理・リスク管理・フェーズ管理）で 1 本。画面ごとの初回（拡張ホストの tour）と、ヘッダ右上の ?（TourButton。4 画面で同じ位置）で出る
    TargetSelect.tsx  ルール管理とフェーズ管理の、設定の対象を切り替える欄
    Tour.css          Tour.tsx の CSS（吹き出しの案内と見本の帯）。各画面の style.css が @import する
    styles/page.css   5 画面で共通の骨組み（本文・ツールバー・帯・欄・脚注）。button.css と appearance.css を @import する
    styles/button.css 5 画面で同じボタン（button.action）
    styles/appearance.css Claude の配色（body のクラスの下でテーマ変数を上書きする）
    styles/list.css   設定 3 画面の一覧（行・開閉・欄名）
    board/style.css   ボード画面の CSS の入口。部品の CSS を @import で並べるだけ
    board/App.css     App.tsx の CSS（列・取っ手・畳んだ列）
    board/Card.css    Card.tsx の CSS（カード 1 枚）
    board/Approval.css Approval.tsx の CSS（承認のオーバーレイ）
    board/post.ts     ボードの送り口。契約に無いものは型で止まる
    board/main.tsx    ボード画面の入口。埋め込みの JSON を読んでマウントする
    board/App.tsx     ツールバー・絞り込み・列・フェーズ・脚注と、拡張ホストからのメッセージの受け
    board/Card.tsx    カード 1 枚（バッジ・属性・フェーズ行・不備・操作）
    board/Approval.tsx 承認のオーバーレイ（一覧・本文・対象外・渡す文）
    board/Decide.tsx   残った指摘の対応方針のオーバーレイの中身（指摘ごとの選択）
    board/state.ts    画面が覚えるもの（絞り込み・畳んだ列・列の幅）の読み書き
    board/text.ts     カードとフェーズ行に出す言葉
    projects/style.css   プロジェクト管理画面の CSS の入口
    projects/App.css     App.tsx の CSS（節・clone の欄・一覧の入れ物）
    projects/Project.css Project.tsx の CSS（カード 1 枚・バッジ・検証）
    projects/post.ts  プロジェクト管理の送り口。契約に無いものは型で止まる
    projects/main.tsx プロジェクト管理画面の入口。埋め込みの JSON を読んでマウントする
    projects/App.tsx  ツールバー（更新）・帯・clone の欄・一覧・認識されない git・ワークスペース（プロジェクト外）と、拡張ホストからのメッセージの受け
    projects/Project.tsx カード 1 枚（名前・バッジ・項目・検証）
    projects/state.ts 画面が覚えるもの（clone の欄）の読み書き
    projects/text.ts  カードに出す言葉（プロジェクトの設定の場所、重ねない苦情）
    risk/style.css    リスク管理画面の CSS の入口
    risk/App.css      App.tsx の CSS（境目の点の枠）
    risk/Factor.css   Factor.tsx の CSS（配点 1 件の行）
    risk/post.ts      リスク管理の送り口。契約に無いものは型で止まる
    risk/main.tsx     リスク管理画面の入口。埋め込みの JSON を読んでマウントする
    risk/App.tsx      帯・ツールバー・リスクレベルの基準点・項目の一覧と、拡張ホストからのメッセージの受け
    risk/Factor.tsx   項目 1 件の行（要約と、開いたときの欄）
    risk/state.ts     編集中の配点（行ごとの鍵）と、開いている行（id で保持する）の読み書き
    risk/text.ts      要約の文・欄の名前・絞り込みが当てる文字列
    phases/style.css  フェーズ管理画面の CSS の入口
    phases/App.css    App.tsx の CSS（枠と見出し）
    phases/Phase.css  Phase.tsx の CSS（定義 1 件の行）
    phases/Graph.css  Graph.tsx の CSS（図・区分の枠・凡例）
    phases/post.ts    フェーズ管理の送り口。契約に無いものは型で止まる
    phases/main.tsx   フェーズ管理画面の入口。埋め込みの JSON を読んでマウントする
    phases/App.tsx    注意の帯・ツールバー・定義の一覧と、拡張ホストからのメッセージの受け
    phases/Graph.tsx  図（点・線・区分の枠・凡例）
    phases/Phase.tsx  定義 1 件の行（要約と、開いたときの欄。scope と成果物は , 区切り、関係は複数選択のセレクトボックスで選ぶ）
    phases/state.ts   編集中の定義（行ごとの鍵）・開いている行（id で保持する）・id の重なり
    phases/text.ts    要約の文・絞り込みが当てる文字列・空のときの言葉
    flow/style.css    フロー編集画面の CSS の入口（先頭で React Flow の CSS を @import する）
    flow/App.css      App.tsx の CSS（部品箱・図・欄の配置と目印）
    flow/Canvas.css   Canvas.tsx の CSS（図・ノード・出口）
    flow/Inspector.css Inspector.tsx の CSS（右の欄）
    flow/SaveReview.css SaveReview.tsx の CSS（保存前の差分の一覧）
    flow/Proposal.css   Proposal.tsx の CSS（前と後の色分け、依頼の文）
    flow/post.ts      フロー編集の送り口。契約に無いものは型で止まる
    flow/main.tsx     フロー編集画面の入口。埋め込みの JSON を読んでマウントする
    flow/App.tsx      錠の帯・ツールバー・注意・部品箱と、拡張ホストからのメッセージの受け
    flow/Canvas.tsx   図（ノード・出口・線）。動かす・繋ぐ・選ぶは呼び手に返す
    flow/Inspector.tsx 右の欄（ノード・線・フローの中身）
    flow/SaveReview.tsx 保存前の差分の一覧（保存する・やめる・次から確かめない）
    flow/Proposal.tsx   エージェントの下書きの差分（値の前後まで）と取り込み、エージェントへの依頼の文（コピー・新しいセッションで開く）
    flow/Preview.tsx  担当に渡る手順のプレビュー（実行ファイルが並べた行をそのまま出す）
    flow/text.ts      ノードの目印（メインに戻る・入れ子）と 1 行の要約
    rules/style.css   ルール管理画面の CSS の入口
    rules/App.css     App.tsx の CSS（タブ・節・判定の欄・判定を試す・hook の 2 つのタブが共有する表）
    rules/Rule.css    Rule.tsx の CSS（ルール 1 件の行・ツールの選択肢）
    rules/Judge.css   Judge.tsx の CSS（判定の結果）
    rules/post.ts     ルール管理の送り口。契約に無いものは型で止まる
    rules/main.tsx    ルール管理画面の入口。埋め込みの JSON を読んでマウントする
    rules/App.tsx     帯・ツールバー・3 つのタブ（ルール・判定を試す・hook）と、拡張ホストからのメッセージの受け
    rules/Rule.tsx    ルール 1 件の行（要約と、開いたときの欄。ツールの選択肢と、ファイルを選ぶ欄）
    rules/Judge.tsx   判定の結果とサンプルの一括判定の表。ここは判定せず、実行ファイルが返した判定を読むだけ
    rules/Hooks.tsx   hook の一覧（表示するだけで書き換えない）
    rules/state.ts    編集中のルール（行ごとの鍵）・開いている行（id で保持する）・開いているタブ
    rules/text.ts     要約の文・絞り込みが当てる文字列・件数の出し方
media/
  icon.svg            アクティビティバーのアイコン
test/
  fixtures/board.json 実行ファイルの出力の実例。Python 側の tests/ticket/test_board.py が書き出す
  fixtures/test.json, samples.json  --test --json / --test-samples --json の実例。tests/core/test_test_json.py が書き出す
  fixtures/suggest.json             --suggest --json の実例。tests/guard/test_suggest.py が鍵の集合を突き合わせる
  fixtures/approve-preview.json, approve-yes.json, approve-mismatch.json  承認の JSON の実例。tests/ticket/test_approve_json.py が書き出す
  helpers/fixture.ts  board.json を読む
  helpers/dom.ts      画面の HTML を happy-dom に読み込み、スクリプトを走らせて postMessage と state を記録する
  helpers/board.ts    ボード画面（React）をバンドルしたものごと happy-dom で開く
  helpers/projects.ts プロジェクト管理画面（React）をバンドルしたものごと happy-dom で開く
  helpers/risk.ts     リスク管理画面（React）をバンドルしたものごと happy-dom で開く
  helpers/phases.ts   フェーズ管理画面（React）をバンドルしたものごと happy-dom で開く
  helpers/rules.ts    ルール管理画面（React）をバンドルしたものごと happy-dom で開く
  helpers/flow.ts     フロー編集画面（React）をバンドルしたものごと happy-dom で開く（図を描くので大きさの偽物を入れる）。フローの見本も持つ
  board/              ボード（board, model, approvemodel と、画面を動かす render.dom / board.dom）
  rules/              ルール管理（rules-doc, hooks, testmodel と、画面を動かす rules.dom。入れ物は rules-render）
  risk/               リスク管理（risk-doc と、画面を動かす risk.dom。入れ物は risk-render）
  phases/             フェーズ管理（phases-doc, phases-layer と、画面を動かす phases.dom。入れ物は phases-render）
  projects/           プロジェクト管理（projects と、画面を動かす projects.dom）
  flow/               フロー編集（flow-doc, flow-view, flow-lint, flow-match, flow-write と、書き出しを PyYAML で読み戻す flow-pyyaml（リポジトリのルートで uv run python か python3 を起動する。起動できなければ飛ばす）、画面を動かす flow.dom、線を引ける先・コピー・貼り付け・履歴・差分の flow-edit-ops、元に戻す・コピー・ミニマップ・保存前の確かめを画面で動かす flow-edit.dom、ボードのカードのボタンを動かす flow-card.dom、エージェントの下書きの取り込みと依頼のボタンを動かす flow-proposal.dom）
  shared/             画面をまたぐもの（locate, commands, lock, layers, yaml11, ticket-control, screens, style, appearance, screen-host, approval-machine）
  */*.test.ts         組み立てと HTML の文字列を見る単体テスト CB-T01〜
  */*.dom.test.ts     happy-dom で動かすテスト CB-D01〜。画面はどれも React なので、描くものも動かして見る（render.dom, projects.dom, risk.dom, phases.dom, rules.dom, flow.dom）
  shared/test-ids.test.ts  ID の決まり（重複しない・名前は ID から始まる）を、テストで守る
scripts/
  bundle.js           esbuild で本体を out/extension.js にバンドルする
  bundle-webview.js   esbuild で画面（React）を画面ごとに out/webview/<名前>.js へバンドルする。画面は src/webview/<名前>/main.tsx があるものを見つける（表で持たない）
  package.sh          vsix の組み立て
```

**テストの ID。** 名前の頭に付ける `CB-T…` / `CB-D…` は「落ちたテストを名指しする」ためのもので、README やチケットの記録から参照する。

| 何 | 決まり |
|---|---|
| どちらを使うか | `CB-D` は happy-dom で動かすもの（`*.dom.test.ts`）、`CB-T` はそれ以外 |
| 番号の採り方 | **最後尾の次を採る。** グループごとの順序や空き番号に差し込まない。画面ごとに別のファイルで採ると、同じ番号を 2 つ付けやすい |
| 枝番 | `CB-T19b` は、既にある ID に後から足した確認。番号は同じでよく、枝番まで込みで一意にする |
| 確かめ方 | `test/shared/test-ids.test.ts`（CB-T163 / CB-T164）が、重複と「ID の無いテスト」を見つける。手で数えるなら `grep -rhoE 'test\("CB-[TD][0-9]+[a-z]*' test --include=*.ts \| sed 's/test("//' \| sort \| uniq -d`（枝番まで数える） |
| 振り直すとき | 後から付けたほうを最後尾の次へ動かす。参照（README・`.ccnavi/approved/` の記録）も追う |
