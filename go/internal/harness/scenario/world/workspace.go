package world

import (
	"ccnavi/internal/infra/env"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// gitSh は確認に出す git の状態を読む入口（ccnavi-git.sh）。
const gitSh = ".ccnavi/scripts/ccnavi-git.sh"

// RepoSnapshot は ccnavi 本体の HEAD・ブランチの一覧・ワークツリーの一覧。回す前と後で比べる。
func RepoSnapshot(root string) string {
	var parts []string
	for _, args := range [][]string{
		{"rev-parse", "HEAD"},
		{"for-each-ref", "--format=%(refname)", "refs/heads"},
		{"worktree", "list", "--porcelain"},
	} {
		r := Git(append([]string{"-C", root}, args...), hostOpts(""))
		if r.Code == 0 {
			parts = append(parts, r.Stdout)
		} else {
			parts = append(parts, fmt.Sprintf("エラー %d: %s", r.Code, r.Stderr))
		}
	}
	return strings.Join(parts, "\n")
}

// PreflightFacts は確認に出す、ワークスペースルートと使い捨てのリポジトリの git の状態。
func PreflightFacts(root string) string {
	script := filepath.Join(root, gitSh)
	var lines []string
	for _, w := range []struct{ label, cwd string }{
		{"ワークスペースルート", root},
		{"projects/test-repo", filepath.Join(root, "projects", "test-repo")},
	} {
		if info, err := os.Stat(w.cwd); err != nil || !info.IsDir() {
			lines = append(lines, fmt.Sprintf("## %s: 置き場が無い（%s）\n", w.label, w.cwd))
			continue
		}
		for _, sub := range []string{"status", "worktree list", "branch"} {
			cmd := exec.Command("sh", append([]string{script}, strings.Fields(sub)...)...)
			cmd.Dir = w.cwd
			raw, _ := cmd.CombinedOutput()
			lines = append(lines, fmt.Sprintf("## %s: git %s\n%s\n", w.label, sub, strings.TrimSpace(string(raw))))
		}
	}
	return strings.Join(lines, "\n")
}

// ModeEnv は判定のモードの環境変数。
const ModeEnv = env.Mode

// ModeCheck は判定のモードが enable かを確かめる。(enable か, 人間に見せる文)。
//
// dry-run では、hook は拒否を報告するだけで止めない。シナリオは「止まる」ことを確かめるので、enable 以外では回さない。
// 値は、プロジェクトの .claude/settings.json の env と、実行の環境変数（getenv）を見る。どちらかに enable 以外の値が
// あれば止める。値が無ければ enable（hook の既定と同じ）。
func ModeCheck(root string, getenv func(string) string) (bool, string) {
	settingsPath := filepath.Join(root, ".claude", "settings.json")
	var declared map[string]any
	data, err := os.ReadFile(settingsPath)
	switch {
	case os.IsNotExist(err):
	case err != nil:
		return false, fmt.Sprintf("## 判定のモード: %s を読めない（%v）。確かめられないので回さない", settingsPath, err)
	default:
		var doc struct {
			Env map[string]any `json:"env"`
		}
		if err := json.Unmarshal(data, &doc); err != nil {
			return false, fmt.Sprintf("## 判定のモード: %s を読めない（%v）。確かめられないので回さない", settingsPath, err)
		}
		declared = doc.Env
	}
	sources := []struct {
		label string
		value *string
	}{{".claude/settings.json の env", nil}, {"実行の環境変数", nil}}
	if v, ok := declared[ModeEnv]; ok {
		s := fmt.Sprint(v)
		sources[0].value = &s
	}
	if v := getenv(ModeEnv); v != "" {
		sources[1].value = &v
	}
	lines := []string{"## 判定のモード（CCNAVI_MODE）"}
	ok := true
	for _, s := range sources {
		if s.value == nil {
			lines = append(lines, fmt.Sprintf("- %s: 指定なし", s.label))
			continue
		}
		lines = append(lines, fmt.Sprintf("- %s: %s", s.label, *s.value))
		if v := strings.ToLower(strings.TrimSpace(*s.value)); v != "" && v != "enable" {
			ok = false
		}
	}
	if ok {
		lines = append(lines, "enable で判定している。回してよい")
	} else {
		lines = append(lines, "enable ではない。dry-run などでは止まらない判定が混じるので回さない。"+
			"モードを enable にしてから打ち直す（設定の変更はユーザが行う）")
	}
	return ok, strings.Join(lines, "\n")
}
