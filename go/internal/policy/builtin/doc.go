// Package builtin は組み込みのルールを持つ。取り返しの付かない操作を止める deny（土台、LoadBase）は
// 共通レイヤーと config の有無・状態によらず常に効く。共通レイヤーが読めないとき（Load）は、
// 土台にシェルからガード自身の設定へ書き込む形の deny と、Read を通す allow を足した既定だけで判定する。
//
// 既定に入れるのは止めそこねたときに戻せないものだけ。ガードが機能していない間は、ルールが言及しない
// 呼び出しを権限モードに委ね、ユーザが居るモードなら 1 件ずつ確認させる。Read だけは通す（ワークツリーを
// 変えようがなく、確認が多すぎると見てほしい 1 件が埋もれる）。
//
// 止める形は glob の文字列一致ではなく regex で書く（`git -C . push`・`rm -fr` のような表記違いも止める）。
// どの式も、照合の時間が入力の長さにほぼ比例するように書く（所有的な繰り返しとアトミックグループ）。
// 期限はルールの合間でしか見ないので、1 本の照合が後戻りで長引くと止められずに通る。
//
// Python からの対応: builtin。load → Load、load_base → LoadBase、read_allow → ReadAllow、
// FALLBACK・SOURCE → Fallback・Source、*_RULE_ID → *RuleID。rule_data・base_data・_deny は Spec の組み立て
// （denySpecs）にまとめた。
package builtin
