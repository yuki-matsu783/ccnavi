package turngate

import (
	"ccnavi/internal/policy/texts"
)

// where は文に出す宣言とキーの位置（`（宣言 <id>、キー <key>）`）。
func where(g *Gate, key string) string {
	out := "（宣言 " + g.ID
	if key != "" {
		out += "、キー " + key
	}
	return out + "）"
}

func keyOrName(stop *Stop) string {
	if stop.Key != "" {
		return stop.Key
	}
	if stop.Gate.KeyName != "" {
		return stop.Gate.KeyName
	}
	return defaultKeyName
}

// AskMessage は確認ダイアログに添える文。文は Gate.AskMessage の識別子が持つ。
func AskMessage(stop *Stop, root string) string {
	g := stop.Gate
	return texts.Say(root, g.AskMessage, texts.Values{
		"second_name": g.SecondName,
		"where":       where(g, stop.Key),
		"key":         keyOrName(stop),
	}, nil)
}

// Message は止めた文（何が起きたか・なぜ・次の一手・迂回しない、の順に 1 行ずつ）。宣言の文面（Gate.Message）は
// 止めた理由が「同じコマンド」か「発言が無い」のときに使い、記録を読み書きできないときは共通の文を使う。
func Message(stop *Stop, root string) string {
	g := stop.Gate
	values := texts.Values{
		"first_name":  g.FirstName,
		"second_name": g.SecondName,
		"where":       where(g, stop.Key),
	}
	switch stop.Reason {
	case Unreadable, Unwritable, Broken:
		values["detail"] = stop.Detail
		next := "place"
		if stop.Reason == Broken {
			next = "broken"
		}
		return texts.Say(root, stateMessage, values, texts.Variants{"what": string(stop.Reason), "next": next})
	}
	values["key"] = keyOrName(stop)
	variant := "no_user_turn"
	if stop.Reason == SameCommand {
		variant = "same_command"
	}
	return texts.Say(root, g.Message, values, texts.Variants{"what": variant, "next": variant})
}
