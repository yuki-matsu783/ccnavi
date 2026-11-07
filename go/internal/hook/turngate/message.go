package turngate

import (
	"ccnavi/internal/policy/texts"
)

// where は文に出す宣言とキーの位置（`（宣言 <id>、キー <key>）`）。
func where(g *Gate, key string) string {
	out := "（宣言 " + g.id
	if key != "" {
		out += "、キー " + key
	}
	return out + "）"
}

func keyOrName(stop *Stop) string {
	if stop.key != "" {
		return stop.key
	}
	if stop.Gate.keyName != "" {
		return stop.Gate.keyName
	}
	return defaultKeyName
}

// AskMessage は確認ダイアログに添える文。文は Gate.askMessage の識別子が持つ。
func AskMessage(stop *Stop, root string) string {
	g := stop.Gate
	return texts.Say(root, g.askMessage, texts.Values{
		"second_name": g.secondName,
		"where":       where(g, stop.key),
		"key":         keyOrName(stop),
	}, nil)
}

// Message は止めた文（何が起きたか・なぜ・次の一手・迂回しない、の順に 1 行ずつ）。宣言の文面（Gate.message）は
// 止めた理由が「同じコマンド」か「発言が無い」のときに使い、記録を読み書きできないときは共通の文を使う。
func Message(stop *Stop, root string) string {
	g := stop.Gate
	values := texts.Values{
		"first_name":  g.firstName,
		"second_name": g.secondName,
		"where":       where(g, stop.key),
	}
	switch stop.reason {
	case unreadable, unwritable, Broken:
		values["detail"] = stop.detail
		next := "place"
		if stop.reason == Broken {
			next = "broken"
		}
		return texts.Say(root, stateMessage, values, texts.Variants{"what": string(stop.reason), "next": next})
	}
	values["key"] = keyOrName(stop)
	variant := "no_user_turn"
	if stop.reason == sameCommand {
		variant = "same_command"
	}
	return texts.Say(root, g.message, values, texts.Variants{"what": variant, "next": variant})
}
