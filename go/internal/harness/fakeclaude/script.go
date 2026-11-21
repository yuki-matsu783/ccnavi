package fakeclaude

import (
	"encoding/json"
	"fmt"
	"strings"
)

// 台本の 1 手。tool・say・agent のどれか 1 つだけを持つ。分岐しない（止められても次の手へ進む）。
type Move struct {
	// Tool は Read・Write・Edit・Bash。Input の file_path は cwd からの相対で書く。
	Tool  *string        `json:"tool"`
	Input map[string]any `json:"input"`
	// Say は最後の文（result に入る）。
	Say *string `json:"say"`
	// Agent はサブエージェント（Agent ツールとして出す）。
	Agent *AgentSpec `json:"agent"`
}

// AgentSpec はサブエージェントの台本。
type AgentSpec struct {
	Type        string  `json:"type"`
	Description string  `json:"description"`
	Prompt      string  `json:"prompt"`
	Script      []Move  `json:"script"`
	Report      *string `json:"report"`
}

// Plan は台本のファイル（ScriptEnv が指す JSON）。
type Plan struct {
	Script         []Move `json:"script"`
	AfterStopBlock []Move `json:"after_stop_block"`
}

// toolNames は台本が呼べるツール。
var toolNames = []string{"Read", "Write", "Edit", "Bash"}

// ParsePlan は台本の JSON を読み、手の書き方を確かめる。
func ParsePlan(text string) (Plan, error) {
	var plan Plan
	if err := json.Unmarshal([]byte(text), &plan); err != nil {
		return Plan{}, fmt.Errorf("台本の JSON が読めない: %v", err)
	}
	if err := checkMoves(plan.Script, "script"); err != nil {
		return Plan{}, err
	}
	if err := checkMoves(plan.AfterStopBlock, "after_stop_block"); err != nil {
		return Plan{}, err
	}
	return plan, nil
}

func checkMoves(moves []Move, where string) error {
	for i, m := range moves {
		n := 0
		for _, set := range []bool{m.Tool != nil, m.Say != nil, m.Agent != nil} {
			if set {
				n++
			}
		}
		if n != 1 {
			return fmt.Errorf("%s の %d 手目: tool・say・agent のどれか 1 つを書く", where, i+1)
		}
		if m.Tool != nil && !known(*m.Tool) {
			return fmt.Errorf("%s の %d 手目: tool は %s のどれか", where, i+1, strings.Join(toolNames, " / "))
		}
		if m.Agent != nil {
			if err := checkMoves(m.Agent.Script, fmt.Sprintf("%s の %d 手目の agent", where, i+1)); err != nil {
				return err
			}
		}
	}
	return nil
}

func known(tool string) bool {
	for _, n := range toolNames {
		if n == tool {
			return true
		}
	}
	return false
}
