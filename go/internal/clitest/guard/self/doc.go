// Package self は、ccnavi が自分の守る場所（設定ファイル・ルール・実行ファイル・下書きの置き場・ラッパーの sh）を
// 守る仕組みを、hook の入口から外へ向けて確かめるテストの置き場である。
//
// 止める・通すの形ごとの表は本体の単体テスト（hook/shellguard・hook/judge・policy/selfguard）が持つので、ここでは
// 「hook を通して、本物のファイルがどうなったか」と「拒否の文面・記録」を見る。
package self
