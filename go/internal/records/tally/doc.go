// Package tally は、同じ拒否の繰り返し（deny）と、失敗・拒否の連続（streak）をセッションごとに数える。
//
// どちらも判定は変えず、文面と報告に一文を足すだけ。数えられなかった（記録を読めない・書けない）ときは
// 何も足さず、何も言わない。数えの失敗が判定に届く経路は作らない。記録は state の置き場の JSON 1 本で、
// 実行前チェックの期限の中で走るので、1 回読んで 1 回書くだけにする。
//
// Python からの対応: repeat の threshold・normalize・rule_of・count・note・at_stop → DenyThreshold・
// Normalize・RuleOf・CountDeny・DenyNote・DenyReport、failstreak の threshold・fail・reset・FIRST・STREAK →
// StreakThreshold・Fail・Reset・First・Streak。key_of と _path は私的にした。
package tally
