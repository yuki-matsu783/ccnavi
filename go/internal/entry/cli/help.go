package cli

// helpTopics は `--help` の話題を決めるオプション。先頭から見て、最初に当たったものを使う。
var helpTopics = []struct {
	name  string
	given func(a opts) bool
}{
	{"version", func(a opts) bool { return a.Truthy("version") }},
	{"lint", func(a opts) bool { return a.Truthy("lint") }},
	{"test", func(a opts) bool { return !a.IsNone("test") || a.Str("test_expectations") != "" }},
	{"explain", func(a opts) bool { return a.Truthy("explain") }},
	{"docs", func(a opts) bool { return a.Truthy("docs") }},
	{"templates", func(a opts) bool { return a.Truthy("templates") }},
	{"learnings", func(a opts) bool { return a.Truthy("learnings") }},
	{"logs", func(a opts) bool { return a.Truthy("prune") || a.Truthy("suggest") }},
	{"agree", func(a opts) bool { return a.Truthy("agree") || !a.IsNone("plan_order") || a.Truthy("fill_phases") }},
	{scriptsTopic, func(a opts) bool {
		if !a.IsNone("reviewed") || a.Truthy("close_early") {
			return true
		}
		for _, dest := range []string{"record_writes", "placed_out", "choose_out", "accept_plan", "accept_check",
			"accept_done", "no_issue", "via"} {
			if a.Truthy(dest) {
				return true
			}
		}
		return false
	}},
}

// helpText は `--help` の本文。`--all` なら全文、話題のオプションか語（`--help docs`・`ticket --help`）があれば
// その節、なければ話題の一覧。
func helpText(a opts) string {
	if a.Truthy("all") {
		return fullUsage()
	}
	for _, t := range helpTopics {
		if t.given(a) {
			return topic(t.name)
		}
	}
	cmd := a.command()
	if len(cmd) == 0 {
		return overview()
	}
	if word := topicWord(cmd[0]); word != "" {
		return topic(word)
	}
	return "ccnavi: 話題 '" + cmd[0] + "' はありません。話題は次のとおりです。\n\n" + overview()
}

// topicOrOverview は語が選ぶ話題の節。語が話題でなければ一覧。
func topicOrOverview(word string) string {
	if t := topicWord(word); t != "" {
		return topic(t)
	}
	return overview()
}
