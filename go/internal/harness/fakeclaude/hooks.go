package fakeclaude

import (
	"regexp"
	"strings"
)

// settings は `<cwd>/.claude/settings.json` のうち、スタブが読む分（hooks と env）。
type settings struct {
	Env   map[string]any         `json:"env"`
	Hooks map[string][]hookGroup `json:"hooks"`
}

type hookGroup struct {
	Matcher string `json:"matcher"`
	Hooks   []hook `json:"hooks"`
}

type hook struct {
	Type        string   `json:"type"`
	Command     string   `json:"command"`
	Timeout     *float64 `json:"timeout"`
	AsyncRewake bool     `json:"asyncRewake"`
}

// matching は event の hook のうち、tool に当たるもの（matcher が無いか空か `*` なら全部。あれば tool 名に完全一致の正規表現）。
// command 以外の hook と asyncRewake の hook は起こさない。
func matching(groups []hookGroup, tool string, hasTool bool) []hook {
	var out []hook
	for _, g := range groups {
		if g.Matcher != "" && g.Matcher != "*" {
			re, err := regexp.Compile("^(?:" + g.Matcher + ")$")
			if !hasTool || err != nil || !re.MatchString(tool) {
				continue
			}
		}
		for _, h := range g.Hooks {
			if !h.AsyncRewake && (h.Type == "" || h.Type == "command") {
				out = append(out, h)
			}
		}
	}
	return out
}

// blocked は hook に止められたこと。text はモデルに渡る文。
type blocked struct{ text string }

// blocking は hook の応答のうち、止めるものを返す（止めなければ nil）。文は、本物の tool_result と差し戻しの文に合わせる。
func blocking(event, tool string, code int, stdout, stderr string) *blocked {
	label := event
	if tool != "" {
		label = event + ":" + tool
	}
	if code == 2 {
		reason := "[command hook]: " + strings.TrimSpace(stderr)
		if event == "Stop" {
			return &blocked{"Stop hook feedback:\n" + reason}
		}
		return &blocked{label + " hook error: " + reason}
	}
	if code != 0 {
		return nil
	}
	out := object{}
	if strings.TrimSpace(stdout) != "" {
		v, err := decode(stdout)
		if err != nil {
			return nil
		}
		if out = asObject(v); out == nil {
			return nil
		}
	}
	spec := asObject(out["hookSpecificOutput"])
	decision := asString(spec["permissionDecision"])
	reason := asString(spec["permissionDecisionReason"])
	switch {
	case event == "PreToolUse" && decision == "deny":
		return &blocked{label + " hook error: " + reason}
	case event == "PreToolUse" && decision == "ask": // -p では止まる（本物で録った）
		return &blocked{reason}
	case event == "Stop" && out["decision"] == "block":
		return &blocked{"Stop hook feedback:\n" + asString(out["reason"])}
	}
	return nil
}
