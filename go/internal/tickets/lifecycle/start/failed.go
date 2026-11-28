package start

// APIFailed は、ホストの API が失敗したときの理由の文と、届いたうえで断られたかの分け方（`ccnavi start api-failed`）。
// what はどの呼び出しか（空ならホストに問い合わせる前の理由 why を使う）、status は HTTP の番号（届かなければ空）、
// body はホストの返事の本文（`host fail` が 1 行にしたもの）。HTTP の番号の読みは `host fail`（hostreply）がしている。
//
// 答えは `reason <文>`（画面に出す理由）と、ホストに届いて断られたとき（401・403 は権限が無い、404・410 は見つからない）
// だけ `refused <文>`。sh（issue_title_get）は refused があれば「issue が無い・権限が無い」として止め、無ければ
// 「ホストに届かない」として扱う。
func APIFailed(what, status, body, why string) string {
	var o outcome
	o.Line("reason", failedReason(what, status, body, why))
	switch status {
	case "401", "403":
		o.Line("refused", "権限がありません（HTTP "+status+"）")
	case "404", "410":
		o.Line("refused", "見つからない（HTTP "+status+"）")
	}
	return o.String()
}

// failedReason は API が失敗した理由の文。
func failedReason(what, status, body, why string) string {
	if what == "" && why != "" {
		return why
	}
	if what == "" {
		what = "?"
	}
	head := "ホストの API が失敗した（" + what + "）"
	switch {
	case status != "":
		// 届いたうえでホストが断った。接続や認証の問題とは限らない
		if body == "" {
			body = "（本文なし）"
		}
		return head + "。HTTP " + status + ": " + body
	case body != "":
		return head + "。ホストの返事: " + body
	}
	return head
}
