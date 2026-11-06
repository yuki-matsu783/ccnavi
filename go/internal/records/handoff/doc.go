// Package handoff は、会話が大きく育ったまま放置されたセッションに、次のセッションへの引き継ぎを促す。
//
// 放置されてプロンプトキャッシュが時間切れで消えたあとに同じ会話を続けると、会話全体を送り直すので料金が
// 数倍になる。消える前に引き継ぎプロンプトを書かせれば、新しいセッションを小さな会話から始められる。
// 働きは 2 つで、どちらも会話の大きさが「基準 + 閾値」以上のときだけ動く。知らせ（Notice）はターンの
// 終わりにユーザへ事情を出し、待機（Start → Check）は待ち時間が過ぎても入力が無ければモデルを起こす文を返す。
// 基準は前回書かせたときの大きさで、会話がそれより小さくなれば（/compact・/clear）0 に戻る。
//
// 置き場は `<state>/idle/<セッション>.json` と `<セッション>.notice`。待つのは sh（ccnavi-idle.sh）の sleep で、
// ここは判定と状態だけを持つ。ネットワークには出ない。
//
// Python からの対応: records.idlehandoff。threshold・unattended・usage_of・start・check・reset・notice・prune →
// Threshold・Unattended・UsageOf・Start・Check・Reset・Notice・Prune。秒の値は time.Duration にした。
package handoff
