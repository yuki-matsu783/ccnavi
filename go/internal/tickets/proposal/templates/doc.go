// Package templates は、作業・レビュー計画のテンプレートを読み、足し、検査し、使う形に組む。テンプレートは、1 つの
// 親の全体計画・フィードバック計画と、各子のフローの本文を 1 ファイルにまとめたもの。このパッケージはファイルを
// 書かない。下書きと取り込みは sync が、提案への書き出しは scaffold が行う。
//
// 置き場は共通（config/common/templates/）と各レイヤー（config/project/templates/）。使うのは、共通と 1 つのレイヤーを
// 合わせたもの。同名で全欄が同じなら、レイヤーの側を info で破棄する。中身が違えば error にして、レイヤーの側だけを
// 使わない。読み方はフローと同じで、リンク・ハードリンク・大きさ・別名・UTF-8 を検査する。
//
// 計画の検査は、合成した親の提案を ticket.Parse に通して、承認の検査を当てる。順序の食い違いは error。型 id と kind の
// 食い違いは warn にする。定義は後から変わり、使うときの承認で改めて error になるため。--templates の JSON は
// 拡張（ccnavi-board の templatesmodel.ts）との契約。
package templates
