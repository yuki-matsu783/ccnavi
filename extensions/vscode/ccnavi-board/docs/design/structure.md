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
  sidebar.ts          左端のアイコンから開くサイドパネルの 6 つの入口（取り込み待ちは並べない）。チケット制御が disable なら 2 つ（プロジェクト管理・ルール管理）。タイトルバーの「?」で左パネルの案内を開く。見た目は配色のアイコンで切り替え、今の値を見出しの横に出す（vscode に依存する）
  ticket-control.ts   CCNAVI_TICKET_CONTROL を設定ファイルから読み、context key に反映する。変化を監視する（vscode に依存する）
  board-panel.ts      ボードの Webview パネルの生成・更新・破棄、監視、操作の受け付け。承認のオーバーレイと動いた表示もここが持つ（画面は作り直されるので持たせない）。計画編集タブの帳面（core/workflow-tabs.ts）を見て、未保存の親を画面に渡し、承認を打つ直前にも確かめる。オーバーレイの「計画を編集」でタブを開く（画面の帳面 screens().workflow を通す）。1 枚目は HTML ごと、以後は postMessage で中身だけ渡す（vscode に依存する）。マージ状況の確認の結果（core/merge.ts）もここが持つ（ボードが開いている間だけ）。実行ファイルと sh を子プロセスで呼ぶ読み取りと、片付けの送信（ダイアログのあとだけ）を分けて行う
  rules-panel.ts      ルール管理画面の Webview パネル（1 枚。対象（共通の設定・ワークスペースの設定・プロジェクトの設定）を切り替える）。判定・検証・保存の受け付けと、読み取り専用の統合結果（sums[]）の受け渡し（vscode に依存する）
  risk-panel.ts       リスク管理画面の Webview パネル（1 枚。対象（共通の設定・ワークスペースの設定・プロジェクトの設定）を切り替える）。検証・作成・保存の受け付けと、読み取り専用の統合結果（sums[]）の受け渡し（vscode に依存する）
  phases-panel.ts     フェーズ管理画面の Webview パネル（1 枚。対象（共通の設定・ワークスペースの設定・プロジェクトの設定）を切り替える）。検証・保存の受け付けと、共通の設定の定義（読み取り専用。共通の設定を開いているときは並べない）と、共通の設定の phases.yml を読めないときの error の帯の受け渡し（vscode に依存する）
  templates-panel.ts  テンプレート管理画面の Webview パネル（1 枚。対象（共通の設定・ワークスペースの設定・プロジェクトの設定）を切り替える）。読み直し・本文の取り出し・図の中身の取り出し（preview。読むだけ）の受け付けと、ファイルの監視、「取り込み待ちを開く」の受け渡し、編集（実行ファイルに読ませる `--templates --check-file`、作業中の錠の取り直し、保存。書くのは保存のときだけで、判定は実行ファイルの答えのまま）。vscode に依存する
  handoff-entry.ts    各画面（ルール・リスク・フェーズ・テンプレート管理）の「取り込み待ちを開く」の拡張ホスト側の共通部分（開けるかの確認と、見ている対象に合わせたツリーで開く）。実行ファイルを呼ぶので単体テストの対象外
  handoff-panel.ts    取り込み待ち画面の Webview パネル（1 枚。見るツリー（ワークスペース・プロジェクト・ワークツリー）を切り替える）。user handoff --json の一覧の受け渡しと、取り込みの受け付け（手順は core/handoff-import.ts に分けてあり、ここは実行ファイルの呼び出しを渡すだけ）、ターミナルの案内、下書きと取り込み先のファイルの監視、作業中の錠の取り直し。ボードのカードの下書きの通知からも開く（vscode に依存する）
  flow-panel.ts       フロー編集画面の Webview パネル（子ごとに 1 枚。ボードのカードから開く）。置き場と錠を実行ファイルに聞き、保存を受け付ける。エージェントの下書きの「提案あり」・取り込み・保存のあとの削除と、依頼の文もここ（vscode に依存する）
  workflow-panel.ts   計画編集タブの Webview パネル（親の提案ごとに 1 枚。識別子を渡すコマンドと、ボードの親の提案のカードの「計画編集」、承認のオーバーレイの「計画を編集」から開く）。線を変えるたびの --plan-order --json --order と、保存の --plan-order --write --expect を実行ファイルに打たせる（拡張はファイルを書かない）。提案の監視（外で変わった・承認待ちから無くなった）、未保存の「●」と閉じた後の聞き方、初回の案内もここ。未保存と、保存したあとのハッシュ（source_sha）を帳面（core/workflow-tabs.ts）に書く（vscode に依存する）
  prompt-handover.ts  Claude Code に文を渡す経路（コピー / 新しいセッションで開く / 専用ターミナルで claude -p を実行）。ボードとフロー編集画面が使う（vscode に依存する）
  projects-panel.ts   プロジェクト管理画面の Webview パネル。clone の送信、.gitignore への追記（vscode に依存する）
  terminal.ts         「ccnavi」ターミナルの用意とコマンドの送信（vscode に依存する）
  tour.ts             画面ごとの初回の案内を見たかどうか（拡張の globalState に画面の名前ごとに持つ）（vscode に依存する）。左パネルの案内（package.json の walkthroughs。本文は media/tour/）を、拡張の版が変わった最初の起動と、パネルのタイトルバーの ? で開き、プロジェクト管理 → チケット管理の基本の案内を続けて動かす（vscode に依存する）
  ccnavi.ts           実行ファイルの探索と --explain --json / --test --json / --test-expectations --json / --lint（--rules / --project-rules-file / --risk / --project-risk-file / --project-phases-file の差し替え）/ --lint --json（--flow でフロー 1 本を確かめる）/ --agree --preview --json / user agree --yes … --json / --templates --json / --templates --show <名前> --json / user handoff [<パス>…] --json（取り込み先行の一覧。見るだけ）と user handoff <パス…> --yes --expect --via board --json（取り込み）/ --templates --draft <親> --json（ボードの親のカードの「テンプレートの下書きを作る」）/ --suggest --json / --plan-order <親> --json（--order）と --plan-order --write の実行（Node の子プロセス）
  git.ts              ローカルの git を読み取り専用で起動する（origin を読む。Node の子プロセス）
  webview-asset.ts    バンドルした画面（out/webview/<名前>.js）と CSS（同 .css）を読む。渡すのは画面の名前で、拡張が <script nonce> と <style nonce> に流し込む
  core/
    model.ts          ボードの JSON の形（実行ファイルとの契約）と読み取り
    approvemodel.ts   承認の JSON の形（--agree --preview --json / user agree --yes … --json）と読み取り。計画を持つ親の図の中身（plans）も読む。一覧のうち未保存のタブがある親と、保存したあとに提案が変わった親を引く関数と、その文
    plan-graph.ts     計画の図の点・線・置き場所を、実行ファイルの plans の 1 件から組む（start / end の仮の点と線、loose の印、引けない線・消せる線、振り直す前の番号で覚えた置き場所）。判定はしない。VS Code に触れないので単体で試せる
    planordermodel.ts --plan-order <親> --json の答えの形（実行ファイルとの契約）と読み取り。plans は承認の preview と同じ読み方。保存の答えをパネルが読む形（書いた・変わらない・外で変わった・書かなかった）
    testmodel.ts      試験の JSON の形（--test --json / --test-expectations --json）と読み取り
    suggestmodel.ts   候補の JSON の形（--suggest --json）と読み取り。allow の候補は並べない
    lintmodel.ts      lint の JSON の形（--lint --json）と読み取り、プロジェクトごとの苦情とフローの苦情（(flow)）の抜き出し
    board.ts          列とカードへの組み立て、操作の有無（親の提案のカードの「計画編集」は提案の has_plan から）。状態の札とフェーズ行の一言を JSON の欄から付ける。マージ状況の帯の題を引く archivedTitleOf
    status.ts         カードの状態の札（statusOf）とフェーズ行の状態の一言（phaseStatusOf）。上から最初にヒットしたものを採る純粋な関数で、JSON の判定済みの欄を表示の言葉に置き換えるだけ
    mrstate.ts        マージ状況の確認が読む 2 つの答え（ccnavi-review.sh mr-state <番号>、ccnavi sync retired）の厳しい検証と、リポジトリのディレクトリの引き方
    merge.ts          マージ状況の確認の遷移（いまの状態 ＋ 入力 → 次の状態 ＋ やること）と、画面に渡す形 MergeView。世代（token）で遅れた答えを破棄し、確認中の再押下・片付けの再入を受けない。外へ出る仕事は返すだけで、行うのは board-panel
    board-moved.ts    前の読み直しから列が変わったカードと、新しく現れたカード（いまの状態 ＋ 読めたボード → 次の状態）。持ち直すのは board-panel。VS Code に触れないので単体で試せる
    board-view.ts     ボードの拡張ホストと画面の契約（見せる中身 BoardData、押した操作 BoardMessage、承認のオーバーレイの状態）
    approval-machine.ts 承認のオーバーレイの遷移（いまの状態 ＋ 入力 → 次の状態 ＋ やること）。外へ出る仕事は返すだけで、行うのは board-panel。未保存の計画編集タブがある親を含む一覧は承認を打たない（承認を打つ直前の確かめ blockedBeforeApprove も）、「計画を編集」は閉じてからタブを開く、保存したあとに提案が変わった親を覚える、もここ。VS Code に触れないので単体で試せる
    workflow-tabs.ts  計画編集タブの帳面（未保存か線の答えを待っているタブがある親と、タブが最後に保存したときの source_sha。ボードを読み直したとき承認待ちに無い親のぶんを忘れる staleSaved）。タブが書き、ボードが読む（画面どうしは import しないので、ここだけを通す）。拡張ホストの中だけに持ち、残さない
    screen-host.ts    画面に中身を渡す段取り（送る / 入れ物ごと / 作り直し中で持ち越し）。VS Code に触れないので単体で試せる
    render.ts         ボードの入れ物の HTML（外部資源なし、テーマ変数だけ）。中身は画面（React）が作る
    rules-view.ts     ルール管理の拡張ホストと画面の契約（ルールの形 SECTIONS / RuleForm / RulesModel、見せる形 RulesPage / RulesData、押した操作 RulesMessage）
    rules-render.ts   ルール管理の入れ物の HTML（外部資源なし）。中身は画面（React）が作る
    rules-doc.ts      rules.yml の読み書き（yaml の Document でコメントを残す。組み合わせ条件 when は元のノードを使い回して書き直す）
    rules-when.ts     組み合わせ条件（when）の形 CondNode と、組み合わせ条件の操作・書式の誤り・1 行の要約・図の置き方（yaml も DOM も入れない。画面と読み書きで分け合う）
    risk-view.ts      リスク管理の拡張ホストと画面の契約（配点の形 RiskForm、見せる形 RiskPage / RiskData、押した操作 RiskMessage）
    risk-render.ts    リスク管理の入れ物の HTML（外部資源なし）。中身は画面（React）が作る
    risk-doc.ts       risks.yml の読み書き（同じくコメントを残す）と、組み込みの配点の本文
    phases-view.ts    フェーズ管理の拡張ホストと画面の契約（定義の形 PhasesForm、見せる形 PhasesPage / PhasesData、押した操作 PhasesMessage）
    phases-render.ts  フェーズ管理の入れ物の HTML（外部資源なし）。中身は画面（React）が作る
    phases-doc.ts     phases.yml の読み書き（同じくコメントを残す。読まない順序の欄は名指しするだけで、書き戻しで手を付けない）
    templates-view.ts テンプレート管理の拡張ホストと画面の契約（見せる形 TemplatesPage / TemplatesData、答えの形 TemplatesJson、押した操作 TemplatesMessage、編集の答え・図の中身の答え TemplateCheck / EditOpened）
    templates-render.ts テンプレート管理の入れ物の HTML（外部資源なし）。中身は画面（React）が作る
    templatesmodel.ts `--templates` の答え（一覧・層ごとの中身・統合結果・下書き・本文・編集中の 1 本の検査）の読み取り。判定はしない。答えの版が違えば解釈せず伝える
    template-edit.ts  テンプレートの編集の手元のコピー（EditTemplate）と編集の関数（ステップの追加・削除・並べ替え・線・フロー。先行が小さい添字になるよう振り直す）、書き出しと見比べ。vscode も DOM も node も入れない
    handoff-entry.ts  各画面の「取り込み待ちを開く」の契約（開けるか HandoffEntry・メッセージ openHandoff・開くツリーのキー）。VS Code にも node にも触れない
    handoff-view.ts   取り込み待ちの拡張ホストと画面の契約（答えの形 HandoffList / HandoffEntry / HandoffResult、見せる形 HandoffPage / HandoffData、押した操作 HandoffMessage）
    handoffmodel.ts   user handoff --json の答え（見るだけ・取り込み）の読み取りと、画面の判断（取り込めるか・取り込めない理由・作業中の錠の掛かり方（template と layer-config）・ターミナルのコマンド・読み直しのあとの選択・見るツリー）。VS Code にも node にも触れない
    handoff-import.ts 取り込みの手順（錠を取り直す → 選んだパスを指して見るだけをもう一度呼び expect を取る → entry_sha を見せたものと比べる → 取り込む）と defense のターミナルの案内。実行ファイルの呼び出しは HandoffDeps として受け取る（試験で差し替える）
    handoff-render.ts 取り込み待ちの入れ物の HTML（外部資源なし）。中身は画面（React）が作る
    flow-doc.ts       子のフロー（YAML）の読み書きと編集（知らない欄・種類を失わない）、雛形、入れ子の段の数え方と注意。正しいかは決めない（描けないときだけ断る）
    flow-view.ts      フロー編集の拡張ホストと画面の契約（見せる形 FlowPage / FlowData、押した操作 FlowMessage とその形の確認、錠を実行ファイルの答えから引く flowTargetOf、カードのボタンの言葉、エージェントへの依頼のボタンの言葉と依頼の文）
    workflow-view.ts  計画編集の拡張ホストと画面の契約（見せる形 WorkflowPage / WorkflowData、押した操作 WorkflowMessage とその形の確認、--order の形の確認）と、答えを画面の状態に整える純関数（線を元の番号の先行に戻す、未保存、番号の変わるステップ、外で変わった差分、保存できない理由）。パネルの決めごとのうち VS Code に触れないもの（無くなった理由、閉じた後に戻すかを聞くか、保存で覚えるハッシュ）も。番号は振り直さない
    workflow-render.ts 計画編集の入れ物の HTML（外部資源なし）。中身は画面（React）が作る
    ticket-id.ts      チケットの識別子の形の見分け（フロー編集と計画編集が画面から届いた識別子を受けるか）
    flow-render.ts    フロー編集の入れ物の HTML（外部資源なし）。中身は画面（React）が作る
    flow-lint.ts      フローの本文を呼ぶたびに別の名前の一時ファイルに書いて実行ファイル（--lint --json --flow）に確かめさせ、(flow) の error を理由にする。通れば実行ファイルが読んだ中身（flow.data）と、(flow) の warn（パスを直したもの）・渡る手順（rendered）・候補（candidates）を返す
    flow-match.ts     画面の中身と実行ファイルが読んだ中身（flow.data）の見比べ（整数と浮動小数の揃え方、食い違った場所と両者の値の言い方）
    flow-history.ts   フロー編集（とテンプレートの編集）の元に戻す・やり直すの履歴（直す前の中身の複製を積む。同じ欄への打ち込みをまとめる。上限 100）
    flow-diff.ts      読み込んだフローと編集中のフローの見比べ（未保存の判定と、保存前に見せる足した・消した・変えたノードと線）。下書きの取り込みの前に見せる、値の前後まで並べた差分（textDiff）
    tour-sidebar.ts   左パネルの案内（ウォークスルー）の ID と、続けて動かす基本の案内の画面。DOM にも vscode にも触れないので、単体で試せる
    tour-place.ts     吹き出しの案内の置き場所（画面の外に出さない）。DOM に触れないので単体で試せる
    tour-def.ts       案内の定義（YAML）の形 TourDef と検査 parseTour（画面ごとの screen / sample / courses。手順は target・題・本文・scene・image と alt）。見本の目印の文 SAMPLE_NOTE もここ。DOM に触れないので単体で試せる
    tour-rules.ts     ルール管理の案内の見本（tours/rules.yml の sample:）を RuleForm と実行ファイルの JSON の形にする。rules.yml と同じ書き方のルールを読む小さな読み取り（実際の readRules と同じ結果になることをテストが見る）
    tour-board.ts     ボード画面の案内の見本（tours/board.yml の sample:）を parseBoardJson / buildBoard と承認・残った指摘の読み取りに通して、画面が描く形にする。実行ファイルの JSON と同じ形で書く
    tour-risk.ts      リスク管理の案内の見本（tours/risk.yml の sample:）を、risks.yml と同じ書き方から RiskPage（配点あり・ファイル無し・統合結果）にする小さな読み取り。yaml を持たず、readRisk と同じ結果になることをテストが見る
    tour-flow.ts      フロー編集の案内の見本（tours/flow.yml の sample）を FlowDoc と FlowChecks にし、手順ごとの scene を読む。yaml を持たず、実際の読み取り・実行ファイルとの一致はテストで検査する
    tour-workflow.ts  計画編集の案内の見本（名前付きの --plan-order の答え）を parsePlanOrder に通して、画面が描く形にする
    tour-templates.ts テンプレート管理の案内の見本（tours/templates.yml の sample:）を、小さい書き方から TemplatesPage（自身の層のテンプレート・下書き・統合結果）と本文の見本にする小さな読み取り。判定はせず、見本に書いた値をそのまま使う
    tour-phases.ts    フェーズ管理の案内の見本（tours/phases.yml の sample:）を、phases.yml と同じ書き方から PhasesPage にする小さな読み取り。yaml を持たず、readPhases と同じ結果になることをテストが見る
    yaml11.ts         実行ファイル（PyYAML、YAML 1.1）が文字列以外に読む語の見分け。risk-doc と phases-doc が引用符を足す判断に使う
    targets.ts        ルール管理・リスク管理・フェーズ管理・テンプレート管理が切り替える設定の対象（共通・ワークスペース・プロジェクト。4 画面とも同じ 3 種）の一覧と、欄の値
    sums.ts           ルール管理とリスク管理の統合結果（共通の設定 + 1 つの設定）の表示用の形。ボードの JSON の sums[] を並べ直すだけで、足し直さない
    projects.ts       プロジェクト管理の判断。URL と名前の検査、origin のキー、clone の行、プロジェクトになっていない .git の探索、.gitignore の行の有無と追記、画面の中身の組み立て
    projects-view.ts  プロジェクト管理の拡張ホストと画面の契約（見せる形 ProjectsPage / ProjectsData、押した操作 ProjectsMessage）
    projects-render.ts プロジェクト管理の入れ物の HTML（外部資源なし）。中身は画面（React）が作る
    hooks.ts          settings.json の hooks の読み取りと、ツール名で走る hook の絞り込み
    lock.ts           保存できるか（doing のチケットの有無。プロジェクトのルールならそのプロジェクトの分だけ）。取り込み待ち画面は template と layer-config の取り込みに、テンプレート管理画面（templates-panel.ts）は編集の保存に、この錠をそのまま掛ける（handoffmodel.ts の lockForTree が見るツリーに合わせる）
    commands.ts       ターミナルに送るコマンド行と、承認・残った指摘の対応方針を子プロセスで打つ引数の配列
    decidemodel.ts    残った指摘の JSON（実行ファイルとの契約）の読み取りと、選んだ対応方針の確かめ
    screens.ts        画面の入口の帳面（Root）。どの画面をどう開くかを 1 か所に集める。画面どうしは互いを import せず、ここへ要求を出す
    watch.ts          8 つの画面（ボード・ルール管理・リスク管理・フェーズ管理・テンプレート管理・取り込み待ち・フロー編集・計画編集）が見張る場所（提案・承認済みチケットとマーカー・ワークツリーの登録）の glob
    locate.ts         実行ファイルの探索順
    ticket-control.ts CCNAVI_TICKET_CONTROL の読み取り（settings.json と settings.local.json）と、実行ファイルが返す値（ボードの JSON の `settings.ticket_control`）との突き合わせ
  webview/            画面（React）。DOM を触る側で、vscode も node も import しない。tsconfig.webview.json で型を見る
    vscode.ts         acquireVsCodeApi の窓口。画面ごとの契約に依存しない（送り口は poster<M>() で作る）
    initial.ts        埋め込みの JSON（最初の 1 枚）を読む。画面ごとの契約に依存しない
    appearance.ts     見た目の切り替え（body のクラスの書き換え）。9 画面で 1 本
    useHostMessages.ts 拡張ホストからのメッセージの受信（見た目はここで受ける）と、組み上がったときの ready。9 画面で 1 本
    useEscape.ts      Esc で閉じる面の入口。重なっているときは最も手前の面（ESCAPE_LAYER。CSS の z-index と同じ順）だけが受ける
    tours/<画面>.yml   案内の定義（sample: 案内の間だけ描く見本、courses: 基本の案内と使い方ごとの案内。手順は target=data-tour の値・題・本文・scene・画像の image と alt・時系列の図の diagram と focus。図は diagrams: に登場者と矢印で書く）。ビルドのとき esbuild のプラグインが JSON にしてバンドルへ入れる（画面に YAML の解析器は入らない）。ボード・ルール管理・リスク管理・フェーズ管理・テンプレート管理・フロー編集・計画編集・プロジェクト管理の 8 画面（プロジェクト管理は sample: を持たない）。list.css の `.field` / `.sum` は `display: contents` で箱を持たないので、指す先がそれでも `Tour.tsx` の `boxOf` が子の枠を合わせて強調する
    tours/images.ts   手順の image（名前）から画像の data: URL を引く表。画像は tours/images/<名前>.png に置き、esbuild の dataurl の読み込みでバンドルへ入れる（Webview はファイルを読まず、ボードの CSP だけ img-src data: を許す）。表と置き場と YAML の突き合わせはテストが見る
    Tour.tsx          吹き出しの案内と、その出し入れ（useTour）。案内のある 8 画面で 1 本。画面ごとの初回（拡張ホストの tour）と、ヘッダ右上の ?（TourButton。ツールバーは ScreenHeader が組み、? を最後の子に置くのでどの画面も同じ位置）で案内の一覧を開き、選んだ 1 本が出る（coursesOf が YAML の定義から案内の一覧を作る）。画面全体を覆う `role="dialog"` は Dialog.tsx を使わず、指す先の枠の影で暗くする作りを別に持つ。画像のある手順は、本文の下に img を出し、吹き出しを広げる（指す先は空にして中ほどに出す）
    TargetSelect.tsx  ルール管理・リスク管理・フェーズ管理・テンプレート管理の、設定の対象を切り替える欄
    workflow/tour-marks.ts 案内の間だけ、計画の図の中のステップ・線に data-tour の印を付ける（共通の図の部品 shared/PlanGraph.tsx には口が無いため）
    flow/post.ts の muteForTour は、案内の間に tourDone 以外を拡張ホストへ送らない。board/post.ts の mutePosts も同じ
    Tour.css          Tour.tsx の CSS（吹き出しの案内と見本の帯）。各画面の style.css が @import する
    styles/appearance.css Claude の配色（body のクラスの下でテーマ変数を上書きする）。共通の骨組み（page.css）・ボタン（button.css）・設定 3 画面の一覧（list.css）は Chrome 拡張と共有する extensions/shared/styles/ にあり、画面の style.css が button.css（@ccnavi/shared/styles/）・appearance.css（この置き場）・page.css（@ccnavi/shared/styles/）の順に @import する。欄の幅の名前（--w-field・--w-field-wide・--w-grow）は page.css の :root にあり、画面の CSS は 320px・360px を直書きせずそれを参照する（CB-T439 が検査する）
    （CSS の値）重なり順（--z-*）・角丸（--radius-*）・文字サイズ（--font-*）・すき間（--gap-*）・影（--shadow-popup）は page.css の :root で定め、画面の CSS には数値を直書きしない。直書きは test/shared/style.test.ts の CB-T585 が見つける（Chrome 拡張の CSS も対象。見つけるのは z-index・px の角丸・em の文字サイズ・gap の数値・影の rgba で、`font:` の省略形、rem、TSX のインライン style は見ない）
    board/style.css   ボード画面の CSS の入口。部品の CSS を @import で並べるだけ（計画の図のために React Flow の CSS と shared/PlanGraph.css も読む）
    board/App.css     App.tsx の CSS（列・取っ手・畳んだ列）
    board/Card.css    Card.tsx の CSS（カード 1 枚。操作ボタン card-button の寸法もここ）
    board/MergeBand.tsx マージ状況の帯（親ごとの 1 行、［MR を開く］［片付ける］、コマンドのコピー）
    board/MergeBand.css MergeBand.tsx の CSS
    board/Approval.css Approval.tsx の CSS（承認のオーバーレイ）
    board/post.ts     ボードの送り口。契約に無いものは型で止まる
    board/main.tsx    ボード画面の入口。埋め込みの JSON を読んでマウントする
    board/App.tsx     ツールバー・絞り込み・列・フェーズ・脚注と、拡張ホストからのメッセージの受け
    board/Card.tsx    カード 1 枚（見出し・位置・ユーザ待ち・注意・フェーズ行・補助の操作・詳細）
    board/Approval.tsx 承認のオーバーレイ（一覧・計画の図（読むだけ）・本文・対象外・渡す文）
    board/Decide.tsx   レビュー完了のオーバーレイの中身（0 件は「レビュー済みにする」、1 件以上は指摘ごとの選択と相談のプロンプトのコピー）
    board/state.ts    画面が覚えるもの（絞り込み・畳んだ列・列の幅・詳細を開いているカード）の読み書き
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
    risk/state.ts     編集中の配点（行ごとのキー）と、開いている行（id で保持する）の読み書き
    risk/text.ts      要約の文・欄の名前・絞り込みが適用する文字列
    phases/style.css  フェーズ管理画面の CSS の入口
    phases/App.css    App.tsx の CSS（枠と見出し）
    phases/Phase.css  Phase.tsx の CSS（定義 1 件の行）
    phases/post.ts    フェーズ管理の送り口。契約に無いものは型で止まる
    phases/main.tsx   フェーズ管理画面の入口。埋め込みの JSON を読んでマウントする
    phases/App.tsx    注意の帯・ツールバー・定義の一覧と、拡張ホストからのメッセージの受け
    phases/Phase.tsx  定義 1 件の行（要約と、開いたときの欄。編集可能な範囲と必須成果物は , 区切り。順序の欄は持たない）
    phases/state.ts   編集中の定義（行ごとのキー）・開いている行（id で保持する）・id の重なり
    phases/text.ts    要約の文・絞り込みが適用する文字列・空のときの言葉
    templates/style.css テンプレート管理画面の CSS の入口
    templates/App.css   App.tsx の CSS（枠・札・「取り込み待ちを開く」の段落の余白）
    templates/Template.css Template.tsx の CSS（テンプレート 1 件の行）
    templates/Draft.css Draft.tsx の CSS（下書き 1 件）
    templates/Sum.css   Sum.tsx の CSS（統合結果の枠と表）
    templates/Plan.css  Plan.tsx の CSS（計画の表）
    templates/Edit.css  Edit.tsx・PlanEdit.tsx・FlowEdit.tsx の CSS（編集欄）。図の見た目は shared/PlanGraph.css と flow/ の CSS を取り込む
    templates/Problems.css Problems.tsx の CSS（苦情の一覧）
    templates/post.ts   テンプレート管理の送り口。契約に無いものは型で止まる
    templates/main.tsx  テンプレート管理画面の入口。埋め込みの JSON を読んでマウントする
    templates/App.tsx   帯・ツールバー・開いている対象の層の一覧と下書き・統合結果の枠、「取り込み待ちを開く」（共通部品 shared/HandoffLink.tsx）、拡張ホストからのメッセージの受け。案内の間は見本を描く
    templates/Sum.tsx   統合結果の枠（読み取り専用。実行ファイルの索引を、統合結果を選ぶ欄と表で出す。ルール管理・リスク管理の Sum.tsx と同じ作り）
    templates/Template.tsx テンプレート 1 件（札・図・本文・「図を編集」。行は最初から開く）
    templates/Diagrams.tsx 一覧の 1 行の図（計画の図と子のフローの図。読むだけ。実行ファイルの答え TemplateCheck を描く）
    templates/Edit.tsx  編集欄（説明・使う場面・計画・フロー、元に戻す、保存、作業中の錠の帯、未保存の確認。直すたびに実行ファイルに読ませる）
    templates/PlanEdit.tsx 編集欄の計画 1 つ（共有の計画の図で線を引く。種類・レビューの選択、並べ替え、削除、追加）
    templates/FlowEdit.tsx 編集欄の子のフロー 1 本（フロー編集画面の図と右の欄を使い、部品箱でノードを足す）
    templates/Draft.tsx 下書き 1 件（読むだけ。札と指摘・計画。取り込みは取り込み待ち画面）
    templates/Plan.tsx  計画の表（番号・種類・レビュー・先行ステップ）
    templates/Problems.tsx 苦情の一覧（深刻度は色と文字の両方で出す）
    templates/text.ts   答えの値を読みやすい言葉に直す（判定はしない）
    handoff/style.css   取り込み待ち画面の CSS の入口
    handoff/App.css     App.tsx の CSS（札・一括の操作・確認の帯）
    handoff/Entry.css   Entry.tsx の CSS（対象 1 件の行・差分・指摘）
    handoff/Result.css  Result.tsx の CSS（取り込みの結果）
    handoff/post.ts     取り込み待ちの送り口。契約に無いものは型で止まる
    handoff/main.tsx    取り込み待ち画面の入口。埋め込みの JSON を読んでマウントする
    handoff/App.tsx     帯・ツールバー（ツリーの欄）・対象の一覧・選択・確認の帯・結果。選んだ対象と開いた行だけを画面が持つ。無効（古い実行ファイル）・エラー・読み込み中の表示
    handoff/Entry.tsx   対象 1 件（選択の欄・種類と状態の札・取り込めない理由・差分と指摘・defense のターミナルの案内）
    handoff/Result.tsx  取り込みの結果（取り込めた対象・消した下書き・残した下書きと理由・失敗の理由）
    handoff/text.ts     種類・状態の値を読みやすい言葉に直す（判定はしない）
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
    shared/PlanGraph.tsx 計画の図の部品（React Flow）。承認のオーバーレイが読むだけで、計画編集タブが編集で使う。editable が真ならステップからステップへ線を引け（引けない線は引いている最中に断る）、onDisconnect を渡せば消せる線に × を出す。置き場所（layout）を渡せば、番号が振り直っても点を動かさない
    workflow/style.css 計画編集タブの CSS の入口（先頭で React Flow の CSS を @import し、shared/PlanGraph.css と Tour.css も読む）
    workflow/App.css   App.tsx の CSS（図の枠の高さ・外で変わった差分の表）
    workflow/Review.css Review.tsx の CSS（保存の前の確かめ）
    workflow/post.ts  計画編集の送り口。契約に無いものは型で止まる
    workflow/main.tsx 計画編集タブの入口。埋め込みの JSON を読んでマウントする
    workflow/App.tsx  無くなった・外で変わったの帯と差分・ツールバー（並べ直す・案に戻す・再読み込み・この順序に決める・?）・番号の変わるステップ・理由の一覧・計画ごとの図・初回の案内と、拡張ホストからのメッセージの受け
    workflow/Review.tsx 保存の前の確かめ（番号の変わるステップ・付け替える子の提案・計画と食い違う子）
    shared/Dialog.tsx  画面を覆うオーバーレイの部品（背景と本体と操作の列）。承認・保存前の確かめ・エージェントの提案の確認が使う。幅は旧クラス側の `--overlay-w` で決める
    shared/Dialog.css  Dialog.tsx の CSS（背景・本体・見出し・操作の列）
    shared/HandoffLink.tsx 「取り込み待ちを開く」のボタン（ルール・リスク・フェーズ・テンプレート管理が置く。開けない理由も出す）
    shared/Maximize.tsx 図を全画面で出す部品（useMaximize・MaximizeButton・RefitOnChange）
    shared/Maximize.css Maximize.tsx の CSS。図の枠は isolation で内側の z-index を外へ及ぼさない
    shared/SequenceDiagram.tsx 案内の中の時系列の図（登場者の列と、時間順の矢印。矢印の値は等幅。focus の矢印だけ光らせ、ほかは薄くする）。DOM と CSS だけで描き、VS Code のテーマに従う
    shared/SequenceDiagram.css SequenceDiagram.tsx の CSS。Tour.css が @import する
    shared/PlanGraph.css PlanGraph.tsx の CSS（図・点・仮の線・注意の色。React Flow の色を --vscode-* に置き換える）
    flow/Inspector.tsx 右の欄（ノード・線・フローの中身）
    flow/SaveReview.tsx 保存前の差分の一覧（保存する・やめる・次から確かめない）
    flow/Proposal.tsx   エージェントの下書きの差分（値の前後まで）と取り込み、エージェントへの依頼の文（コピー・新しいセッションで開く）
    flow/Preview.tsx  担当に渡る手順のプレビュー（実行ファイルが並べた行をそのまま出す）
    flow/text.ts      ノードの目印（メインに戻る・入れ子）と 1 行の要約
    rules/style.css   ルール管理画面の CSS の入口
    rules/App.css     App.tsx の CSS（タブ・節・判定の欄・判定を試す・hook の 2 つのタブが共有する表）
    rules/Rule.css    Rule.tsx の CSS（ルール 1 件の行・ツールの選択肢）
    rules/When.css    When.tsx の CSS（組み合わせ条件の図。React Flow の色を --vscode-* に置き換える）
    rules/Judge.css   Judge.tsx の CSS（判定の結果）
    rules/post.ts     ルール管理の送り口。契約に無いものは型で止まる
    rules/main.tsx    ルール管理画面の入口。埋め込みの JSON を読んでマウントする
    rules/App.tsx     帯・ツールバー・3 つのタブ（ルール・判定を試す・hook）と、拡張ホストからのメッセージの受け
    rules/Rule.tsx    ルール 1 件の行（要約と、開いたときの欄。ツールの選択肢と、ファイルを選ぶ欄。「組み合わせ条件に変換」）
    rules/When.tsx    組み合わせ条件（when）の図（React Flow）と、選んだノードの操作の欄・誤りの一覧
    rules/Judge.tsx   判定の結果とサンプルの一括判定の表。実行ファイルが返した判定を読むだけ
    rules/Hooks.tsx   hook の一覧（表示するだけ）
    rules/state.ts    編集中のルール（行ごとのキー）・開いている行（id で保持する）・開いているタブ
    rules/text.ts     要約の文・絞り込みが適用する文字列・件数の出し方
media/
  icon.svg            アクティビティバーのアイコン
  tour/*.md           左パネルの案内（ウォークスルー）の各段の本文（panel・projects・board・basics・more）
test/
  fixtures/board.json 実行ファイルの出力の実例。Python 側の tests/ticket/test_board.py が書き出す
  fixtures/test.json, expectations.json  --test --json / --test-expectations --json の実例。tests/core/test_test_json.py が書き出す
  fixtures/suggest.json             --suggest --json の実例。tests/guard/test_suggest.py がキーの集合を突き合わせる
  fixtures/approve-preview.json, approve-yes.json, approve-mismatch.json  承認の JSON の実例。tests/ticket/test_approve_json.py が書き出す
  fixtures/plan-order.json          --plan-order <親> --json の答えの実例（開いたとき・逆向きの線・循環・終端の検査・書いたとき・改版）。tests/ticket/test_plan_order.py が書き出す
  helpers/fixture.ts  board.json を読む
  helpers/dom.ts      画面の HTML を happy-dom に読み込み、スクリプトを走らせて postMessage と state を記録する
  helpers/board.ts    ボード画面（React）をバンドルしたものごと happy-dom で開く
  helpers/cards.ts    カードの組み立てを欄ごとに指定して作る部品（状態の札・レビュー・マージの画面のテストが使う）
  helpers/projects.ts プロジェクト管理画面（React）をバンドルしたものごと happy-dom で開く
  helpers/risk.ts     リスク管理画面（React）をバンドルしたものごと happy-dom で開く
  helpers/phases.ts   フェーズ管理画面（React）をバンドルしたものごと happy-dom で開く
  helpers/templates.ts テンプレート管理画面（React）をバンドルしたものごと happy-dom で開く
  helpers/handoff-link.ts 「取り込み待ちを開く」の確かめ（3 画面のテストが共有する）
  helpers/handoff.ts  取り込み待ち画面（React）をバンドルしたものごと happy-dom で開く。種類と状態を 1 本ずつ並べた対象の見本も持つ
  helpers/rules.ts    ルール管理画面（React）をバンドルしたものごと happy-dom で開く
  helpers/flow.ts     フロー編集画面（React）をバンドルしたものごと happy-dom で開く（図を描くので大きさの偽物を入れる）。フローの見本も持つ
  helpers/workflow.ts 計画編集タブ（React）をバンドルしたものごと happy-dom で開く（図を描くので大きさの偽物を入れる）
  helpers/plan-order.ts --plan-order の答えの実例（fixtures/plan-order.json）を読む
  helpers/plan-reorder.ts 振り直しの見本の表（リポジトリの tests/fixtures/plan-reorder.json。実行ファイルのテストと共有し、書き換えない）を読み、表の計画を plans の形に並べる
  helpers/jsdom.ts    happy-dom で固まる図の点のドラッグだけを jsdom で動かす（いま使うテストは無い）
  board/              ボード（board, model, approvemodel, status と、画面を動かす render.dom / board.dom / card.dom（カードの構造・操作・詳細）/ review.dom（レビュー完了の画面）/ merge.dom（マージ状況の帯）、承認のオーバーレイの計画の図を動かす approval-graph.dom、カードの「計画編集」を動かす workflow-card.dom）
  rules/              ルール管理（rules-doc, rules-when, hooks, testmodel と、画面を動かす rules.dom。入れ物は rules-render）
  risk/               リスク管理（risk-doc と、画面を動かす risk.dom。入れ物は risk-render）
  phases/             フェーズ管理（phases-doc, phases-layer と、画面を動かす phases.dom。入れ物は phases-render）
  templates/          テンプレート管理（templates-model・templates-args・templates-render・template-edit の単体テストと、画面を動かす templates.dom・templates-edit.dom（編集欄）・templates-tour.dom、案内の見本の tour-templates。下書きの取り込みは持たない）
  handoff/            取り込み待ち（handoffmodel の読み取りと判断、handoff-args、取り込みの手順 handoff-import（実行ファイルを差し替えて、expect の取り方・錠・defense の案内を見る）、handoff-render の単体テストと、画面を動かす handoff.dom）
  projects/           プロジェクト管理（projects と、画面を動かす projects.dom）
  flow/               フロー編集（flow-doc, flow-view, flow-lint, flow-match, flow-write と、書き出しを PyYAML で読み戻す flow-pyyaml（リポジトリのルートで uv run python か python3 を起動する。起動できなければ飛ばす）、画面を動かす flow.dom、線を引ける先・コピー・貼り付け・履歴・差分の flow-edit-ops、元に戻す・コピー・ミニマップ・保存前の確かめを画面で動かす flow-edit.dom、ボードのカードのボタンを動かす flow-card.dom、エージェントの下書きの取り込みと依頼のボタンを動かす flow-proposal.dom）
  workflow/           計画編集（planordermodel, workflow-view, ボードと結ぶ帳面の workflow-tabs と、画面を動かす workflow.dom）
  shared/             画面をまたぐもの（locate, commands, lock, layers, yaml11, ticket-control, screens, style, appearance, screen-host, approval-machine, plan-graph, decidemodel, mrstate, merge, read-only（読み取りから書き込みを呼ばない）)
  */*.test.ts         組み立てと HTML の文字列を見る単体テスト CB-T01〜
  */*.dom.test.ts     happy-dom で動かすテスト CB-D01〜。画面はどれも React なので、描くものも動かして見る（render.dom, projects.dom, risk.dom, phases.dom, rules.dom, flow.dom, templates.dom, templates-edit.dom, handoff.dom）
  shared/test-ids.test.ts  ID の決まり（重複しない・名前は ID から始まる）を、テストで守る
scripts/
  bundle.js           esbuild で本体を out/extension.js にバンドルする
  bundle-webview.js   esbuild で画面（React）を画面ごとに out/webview/<名前>.js へバンドルする。画面は src/webview/<名前>/main.tsx があるものを見つける（表で持たない）。`.png` は data: の URL にして JS へ入れる（案内の画像。tours/images.ts）
  package.sh          vsix の組み立て
```

**テストの ID。** 名前の頭に付ける `CB-T…` / `CB-D…` は「失敗したテストを名指しする」ためのもので、手動確認の表（[manual-checks.md](manual-checks.md)）やチケットの記録から参照する。

| 何 | 決まり |
|---|---|
| どちらを使うか | `CB-D` は happy-dom で動かすもの（`*.dom.test.ts`）、`CB-T` はそれ以外 |
| 番号の採り方 | **最後尾の次を採る。** グループごとの順序や空き番号に差し込まない。画面ごとに別のファイルで採ると、同じ番号を 2 つ付けやすい |
| 枝番 | `CB-T19b` は、既にある ID に後から足した確認。番号は同じでよく、枝番まで込みで一意にする |
| 確かめ方 | `test/shared/test-ids.test.ts`（CB-T163 / CB-T164）が、重複と「ID の無いテスト」を見つける。手で数えるなら `grep -rhoE 'test\("CB-[TD][0-9]+[a-z]*' test --include=*.ts \| sed 's/test("//' \| sort \| uniq -d`（枝番まで数える） |
| 振り直すとき | 後から付けたほうを最後尾の次へ動かす。参照（手動確認の表・`.ccnavi/approved/` の記録）も追う |
