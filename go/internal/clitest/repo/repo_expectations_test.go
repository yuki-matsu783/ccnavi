package repo

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// リポジトリの期待する判定（.ccnavi/config/common/rule-expectations.yml）が、いまのルール（rules.yml）で
// 期待どおりに判定されること。CI が無いので、ここで見ないと食い違いに気づけない。判定は --test-expectations に任せる。

// expectationsBody は `--test-expectations --json` の本文のうち、見る所。
type expectationsBody struct {
	Mismatches   int `json:"mismatches"`
	Expectations []struct {
		OK       bool   `json:"ok"`
		Expected string `json:"expected"`
		Verdict  string `json:"verdict"`
		Tool     string `json:"tool"`
		Subject  string `json:"subject"`
		Why      string `json:"why"`
		Rules    []struct {
			Section string `json:"section"`
			ID      string `json:"id"`
		} `json:"rules"`
	} `json:"expectations"`
}

func TestTheRepoExpectationsMatchTheRules(t *testing.T) {
	t.Parallel()
	root := base.Root()
	// 期待の `/repo` は --root に読み替わる。ルートがこのリポジトリのままだと、ワークツリー（`.claude/worktrees/<名前>` の下）で
	// 回したときに `Write /repo/wip/...` が allow:worktrees にヒットし、本流と答えが割れる。ルートだけを、ワークツリーの下に
	// ならない使い捨ての置き場に替える。ルールと期待はこのリポジトリのものを読む。
	scratch := filepath.ToSlash(base.TempDir(t))
	rules := base.CommonPath(root, "rules")
	expectations := filepath.Join(root, ".ccnavi", "config", "common", "rule-expectations.yml")
	r := run.Ccnavi(t, []string{
		"--root", scratch,
		"--rules", rules,
		// 見るのはルールだけ。承認済みチケットと state は外し、記録も残さない。
		"--approved", "",
		"--state", "",
		"--log", "",
		"--test-expectations", expectations,
		"--json",
	}, run.Opts{Dir: root})
	if r.Code != 0 {
		t.Fatalf("code = %d, stderr = %s", r.Code, r.Stderr)
	}
	var body expectationsBody
	if err := json.Unmarshal([]byte(r.Stdout), &body); err != nil {
		t.Fatalf("本文を読めない: %v\n%s", err, r.Stdout)
	}
	if len(body.Expectations) == 0 {
		t.Fatal("期待する判定が 1 件も読めていない")
	}
	var wrong []string
	for _, e := range body.Expectations {
		if e.OK {
			continue
		}
		var hits []string
		for _, h := range e.Rules {
			hits = append(hits, h.Section+":"+h.ID)
		}
		hit := "(無し)"
		if len(hits) > 0 {
			hit = strings.Join(hits, ", ")
		}
		wrong = append(wrong, fmt.Sprintf("%s のはずが %s: %s  %s\n    なぜ: %s\n    ヒットしたルール: %s",
			e.Expected, e.Verdict, e.Tool, e.Subject, e.Why, hit))
	}
	if body.Mismatches != 0 || len(wrong) > 0 {
		t.Errorf("期待する判定と判定が食い違った（期待する判定とルールのどちらを直すかはユーザが決める。/ccnavi-config）:\n%s",
			strings.Join(wrong, "\n"))
	}
}
