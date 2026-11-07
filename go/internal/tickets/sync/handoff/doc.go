// Package handoff は、保護対象の下書き（wip/handoff/<取り込み先の相対パス>）を、ユーザが元の場所へ取り込む
// （ccnavi user handoff）。エージェントが書けないものは、下書きを置き、ユーザがここで取り込む。対象は、テンプレート・
// レイヤーの設定・文面・docs・hook とラッパーの sh などである。相対パスの中の `.claude` の階層は、下書きでは
// handoffnames の表で読み替えてあるので、取り込むときに戻す。
//
// 取り込めるのは、種類の表（kinds）に当たるものだけである。パスは要素ごとに NFC と pathcase.Fold にそろえてから当てる。
// 表に当たらない下書きは対象外として数えるだけにする。学びの雛形や再現用のスクリプトも置かれるためである。選んだ対象に
// 1 つでも error があれば、何も書かない。--expect は、対象ごとの（そろえたパス・種類・下書きと取り込み先の SHA-256）を、
// 長さの前置きつきで連結した SHA-256 である。見せた内容と書く内容が同じことを約束する。plan と applied の JSON・
// ハッシュの値・書くバイト列は、拡張（ccnavi-board の handoffmodel.ts）との契約である。
//
// 書き方は、一時ファイル → fsync → 差し替えの順である。書く直前に、リンクと取り込み先のハッシュを確かめ直す。途中で
// 失敗したら、書く前のバイト列へ戻す。プロセスの途中終了に対しては、原子的ではない。
//
// Python からの対応: tickets/sync/handoff。HANDOFF_REL → place.Handoff、JSON_VERSION → JSONVersion、
// KIND_* → KindID の定数、STATE_* → State の定数、WHY_* → why の定数、fold_part・key_of・kind_of → FoldPart・Key・
// KindOf、draft_rel・spelled・walk → DraftRel・Spelled・Walk、prepare・apply → Prepare・Apply、
// plan_json・applied_json → Plan.JSON・Applied.JSON（失敗だけの答えは errorJSON）、pending・survey → Pending・Survey、
// board_refused・not_ok_reason は Apply の中だけで使う。difflib.unified_diff は diff.go に写した。
package handoff
