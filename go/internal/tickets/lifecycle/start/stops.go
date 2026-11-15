package start

import (
	"os"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/policy/texts"
)

// stopText は、止めるときの 1 文（診断ログの識別子・終了コード・文）。
type stopText struct {
	ID   string
	Code int
	Text string
}

// asks は、文を標準出力（long-slug は標準エラー）に出し、ユーザに聞くために止める道（終了コード 3、ホストに届かない道は 4）。
var asks = map[string]func(root string, kv map[string]string, o *outcome){
	"list-open-host": func(root string, kv map[string]string, o *outcome) {
		o.Out("ccnavi-start: ホストの API に届かず、open の issue・MR の一覧を取れなかった（" + orQ(kv["why"]) + "）。")
		if kv["kind"] == "github" {
			o.Out(texts.Say(root, "NEXT_START_LIST_OPEN__GITHUB", nil, nil))
		} else {
			o.Out(texts.Say(root, "NEXT_START_LIST_OPEN__GITLAB", nil, nil))
		}
		o.Exit(exitHost)
	},
	"new-issue-host": func(root string, kv map[string]string, o *outcome) {
		o.Out("ccnavi-start: ホストの API に届かず、issue を作れなかった（" + kv["why"] + "）。何も作成せずに処理を中断しました。")
		if kv["kind"] == "github" {
			o.Out(texts.Say(root, "NEXT_START_NEW_ISSUE__GITHUB", nil, nil))
		} else {
			o.Out(texts.Say(root, "NEXT_START_NEW_ISSUE__GITLAB", texts.Values{"host": orQ(kv["host"])}, nil))
		}
		o.Exit(exitHost)
	},
	"similar-found": func(root string, kv map[string]string, o *outcome) {
		if kv["checked"] != "" {
			o.Out("ccnavi-start: 同じタイトルの open の issue・MR があるため、--similar-checked を付けても issue を作らずに処理を中断しました（タイトル: " + kv["title"] + "）。")
		} else {
			o.Out("ccnavi-start: タイトルの似た open の issue・MR があるため、issue を作らずに処理を中断しました（タイトル: " + kv["title"] + "）。")
		}
		o.Out(kv["found"])
		o.Out(texts.Say(root, "NEXT_START_SIMILAR_FOUND", nil, nil))
		if kv["checked"] != "" {
			o.Out(texts.Say(root, "NOTE_START_SIMILAR_CHECKED", nil, nil))
		} else {
			o.Out(texts.Say(root, "NOTE_START_SIMILAR_UNCHECKED", texts.Values{"orig_args_q": kv["orig"]}, nil))
		}
		o.Exit(exitAsk)
	},
	"similar-confirm": func(root string, kv map[string]string, o *outcome) {
		o.Out("ccnavi-start: issue「" + kv["title"] + "」はまだ作っていません。タイトルの機械的な確認では似た open の issue・MR は見つかりませんでしたが、言い換えは拾えないので、意味が近いかの確認を挟みます。")
		o.Out(texts.Say(root, "NEXT_START_SIMILAR_CONFIRM", texts.Values{"title": kv["title"], "orig_args_q": kv["orig"]}, nil))
		o.Exit(exitAsk)
	},
	"multi": func(root string, kv map[string]string, o *outcome) {
		o.Out("ccnavi-start: 候補が " + kv["total"] + " 件見つかったため、何も作成せずに処理を中断しました。ユーザに見せ、既存のブランチで続けるか・新しく切るか・やめるかを聞いて、返事を待ってください。")
		o.Exit(exitAsk)
	},
	"long-slug": func(root string, kv map[string]string, o *outcome) {
		o.Err("ccnavi-start: issue #" + kv["number"] + " のタイトルが長く、タイトルから作る名前 " + kv["branch"] + " は途中で切れます。ブランチは作らずに止めました（--new-issue から来たときの issue は作成済み）。")
		o.Err(texts.Say(root, "NOTE_START_SLUG_SHORT", nil, nil))
		if kv["project"] != "" {
			o.Err(texts.Say(root, "NOTE_START_SLUG_PROJECT", texts.Values{"project": kv["project"]}, nil))
		}
		o.Err(texts.Say(root, "NEXT_START_LONG_SLUG", texts.Values{"real_args_q": kv["real"]}, nil))
		o.Exit(exitUsage)
	},
	"proposal-only": func(root string, kv map[string]string, o *outcome) {
		o.Out("ccnavi-start: 候補 " + kv["branch"] + " は、手元にも origin にも MR にも実体がありません（チケットの提案だけ）。何も作成せずに処理を中断しました。ユーザに確かめてください。")
		o.Exit(exitAsk)
	},
	"mr-exists": func(root string, kv map[string]string, o *outcome) {
		o.Out("ccnavi-start: issue #" + kv["number"] + " に紐づく MR !" + kv["mr"] + "（" + kv["url"] + "）がすでにあります。この PC にそのブランチ " + kv["branch"] + " のワークツリーが無いので、他の PC や他のセッションの作業かもしれません。何も作成せずに処理を中断しました。")
		o.Out(texts.Say(root, "NEXT_START_MR_EXISTS", texts.Values{"issue": kv["number"], "mr": kv["mr"]}, nil))
		o.Exit(exitAsk)
	},
	"stale": func(root string, kv map[string]string, o *outcome) {
		o.Out("ccnavi-start: 候補 " + kv["branch"] + " は、統合先 " + kv["integration"] + " より進んでおらず、開いた MR もありません（取り込み済みで消し忘れた古いブランチかもしれない）。何も作成せずに処理を中断しました。ユーザに見せ、このブランチを使うか・新しく切るか・やめるかを聞いて、返事を待ってください。")
		o.Exit(exitAsk)
	},
	"no-mr": func(root string, kv map[string]string, o *outcome) {
		number := kv["number"]
		if number == "" {
			number = "<番号>"
		}
		o.Out("ccnavi-start: 候補 " + kv["branch"] + " には開いた MR がありません。issue と MR のセットが確定しないため、何も作成せずに処理を中断しました。このブランチで続けるなら、ユーザに MR（説明欄に Closes #" + number + "）を作ってもらい、--mr <番号> を付けて打ち直します。新しく切るか・やめるかも含め、ユーザに聞いて返事を待ってください。")
		o.Exit(exitAsk)
	},
}

// exitHost は、ホストに届かず代行してほしいときの終了コード。
const exitHost = 4

// orQ は、空なら ?。
func orQ(s string) string {
	if s == "" {
		return "?"
	}
	return s
}

// Stop は、ccnavi-start.sh が止めるときの文を返す（`ccnavi start stop <名前> <名前>=<値>…`）。fail の表（stops）の名前なら
// `fail` の行で、聞くために止める道（asks）なら出力の行と `exit` で答える。知らない名前なら偽。
func Stop(root, key string, kv map[string]string) (string, bool) {
	var o outcome
	if ask, found := asks[key]; found {
		o.Val("reason", key)
		ask(root, kv, &o)
		return o.String(), true
	}
	s, found := stops[key]
	if !found {
		return "", false
	}
	// 差し込みは 1 回の走査で置き換える（値の中の `{名前}` を、もう一度差し込まない）。
	var pairs []string
	for k, v := range kv {
		pairs = append(pairs, "{"+k+"}", v)
	}
	o.Fail(s.ID, s.Code, strings.NewReplacer(pairs...).Replace(s.Text))
	return o.String(), true
}

// CheckReport は --check の出力（対象リポジトリ・項目ごとの行・結論・次の一手）を組み立てる。pf は事前確認の答え
// （`ccnavi preflight check` の行）のファイル、repo は対象リポジトリ（ホストのパスか git のトップ）、project はその名前。
func CheckReport(root, pf, repo, project string) (string, int) {
	data, _ := os.ReadFile(pf)
	answer := map[string][]string{}
	for _, line := range ospath.Splitlines(string(data)) {
		k, v, _ := strings.Cut(line, " ")
		answer[k] = append(answer[k], v)
	}
	var o outcome
	if project == "" {
		project = "ワークスペース自身"
	}
	o.Out("対象リポジトリ: " + repo + "（" + project + "）")
	for _, line := range answer["line"] {
		_, text, _ := strings.Cut(line, " ")
		o.Out("確認: " + text)
	}
	result := strings.Join(answer["result"], "")
	switch result {
	case "ok":
		o.Out("結論: チケット作業を始められる状態です。")
		o.Out(texts.Say(root, "NEXT_START_CHECK_OK", nil, nil))
	case "ng":
		o.Out("結論: 不備があり、チケット作業を始められません。")
	default:
		o.Out("結論: 確かめられなかった項目があります（省略は前提の項目が OK でないため確かめていません）。")
	}
	o.Out(strings.Join(answer["next"], "\n"))
	switch result {
	case "ok":
		o.Exit(0)
	case "ng":
		o.Exit(exitUnmet)
	default:
		o.Exit(exitHost)
	}
	return o.String(), 0
}
