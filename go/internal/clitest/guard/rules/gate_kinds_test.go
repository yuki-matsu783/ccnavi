package rules

import (
	"path/filepath"
	"testing"
)

// 止めている箇所の分類（一般・ゲート・補助）と、サブエージェントの役割を確かめる。
//
//   - ゲート（GATE）と補助（AUX）は dry-run でも止める。一般の保護は dry-run なら報告だけにする
//   - 承認済みの置き場と操作記録の置き場への書き込みは、dry-run でも自己防衛の設定を切っても止める
//   - サブエージェントは、状態を動かす・公開する操作を打てない。読み取りは打てる
//
// 場所・モード・自己防衛の値・ツールの全部の組み合わせ（hook/judge の TestThePlacesAreStoppedInAnyMode）と、サブエージェントの
// 止める形・通す形の表（TestTheSubagentRoles）は Go の単体テストが見る。ここでは hook を通した代表を見る。

// gate は rules が空（{"version": 1}）の作業場所に PreToolUse を渡す。agent があればサブエージェントの呼び出し。
type gate struct {
	root string
}

func newGate(t testing.TB) gate {
	t.Helper()
	root := withRuleText(t, `{"version": 1}`)
	mkdirs(t, root+"/.claude")
	return gate{root}
}

// hook は判定と、理由か追加の文脈。
func (g gate) hook(t testing.TB, tool string, input map[string]any, mode, agent string, e map[string]string) (string, string) {
	t.Helper()
	body := pre(tool, input)
	body["cwd"] = g.root
	body["session_id"] = "s1"
	if agent != "" {
		body["agent_id"] = agent
	}
	r := fire(t, g.root, []string{"--mode", mode}, body, e)
	if r.Code != 0 {
		t.Fatalf("code = %d: %s", r.Code, r.Stderr)
	}
	out := parse(t, r)
	if out.Reason != "" {
		return out.Decision, out.Reason
	}
	return out.Decision, out.Context
}

func (g gate) bash(t testing.TB, command, mode, agent string, e map[string]string) (string, string) {
	t.Helper()
	return g.hook(t, "Bash", map[string]any{"command": command}, mode, agent, e)
}

func TestAGeneralProtectionOnlyReportsInDryRun(t *testing.T) {
	t.Parallel()
	g := newGate(t)
	decision, text := g.bash(t, "rm -rf /tmp/some-dir", "dry-run", "", nil)
	if decision != "" {
		t.Errorf("報告だけのはず: %q", decision)
	}
	mustHave(t, text, "[ccnavi dry-run]")
	if decision, _ := g.bash(t, "rm -rf /tmp/some-dir", "enable", "", nil); decision != "deny" {
		t.Errorf("enable では deny: %q", decision)
	}
}

func TestAGateStopsInDryRunAndSaysSo(t *testing.T) {
	t.Parallel()
	g := newGate(t)
	decision, text := g.bash(t, "sh .ccnavi/scripts/ccnavi-ticket.sh start i0001", "dry-run", "a1", nil)
	if decision != "deny" {
		t.Errorf("deny のはず: %s", text)
	}
	mustHave(t, text, "dry-run", "still applies")
}

// 分類を決める分岐ごとに、dry-run で止まるか報告だけかを固定する。
func TestAuxAndGateStopsStayInDryRun(t *testing.T) {
	t.Parallel()
	for name, c := range map[string]struct {
		command, agent, text string
		env                  map[string]string
	}{
		"raw git":                      {"git status", "", "", nil},
		"env assignment before script": {"CCNAVI_WORKSPACE=/x sh .ccnavi/scripts/ccnavi-git.sh push", "", "", nil},
		// 自己防衛を切っても、実行ファイルの置き換えの形は止める。
		"env assignment with the core guard off": {"CCNAVI_WORKSPACE=/x sh .ccnavi/scripts/ccnavi-git.sh push", "", "", map[string]string{"CCNAVI_GUARD_CORE_FILES": "disable"}},
		// 層の deny（認証情報）と同時にヒットしても、生の git の補助止めは dry-run で効く。
		"raw git with a layer rule": {"git status; cat .env", "", "still applies", nil},
		// 数えない上限を超えたリダイレクトで、置き場への書き込みを隠す形。
		"redirect cap":              {"echo hi " + repeat("> /dev/null ", 60) + "> .ccnavi/approved/phases/i0001/1.reviewed ; cat <<EOF", "", "", nil},
		"git hidden by a variable":  {"c=git; $c push origin main", "", "", nil},
		"git hidden by a brace":     {"{git,push,origin,main}", "", "", nil},
		"subagent brace expansion":  {"ls {src,tests}", "a1", "", nil},
		"touch in the places":       {"touch .ccnavi/approved/phases/i0001/1.reviewed", "", "", nil},
		"mkdir in the places":       {"mkdir -p .ccnavi/approved/phases/i0001", "", "", nil},
		"rsync into the places":     {"rsync -a /tmp/x .ccnavi/approved/", "", "", nil},
		"python into the places":    {`python3 -c "open('.ccnavi/approved/doing/x.md','w')"`, "", "", nil},
		"chmod in the state places": {"chmod 000 logs/state/ops/a1.json", "", "", nil},
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			g := newGate(t)
			decision, text := g.bash(t, c.command, "dry-run", c.agent, c.env)
			if decision != "deny" {
				t.Errorf("deny のはず: %s", text)
			}
			mustHave(t, text, c.text)
		})
	}
}

func repeat(s string, n int) string {
	out := ""
	for i := 0; i < n; i++ {
		out += s
	}
	return out
}

func TestAPathContainingCcnaviIsNotAnEntry(t *testing.T) {
	t.Parallel()
	g := newGate(t)
	decision, text := g.bash(t, "ls /work/ccnavi/{src,tests}", "dry-run", "", nil)
	if decision == "deny" {
		t.Errorf("止めない: %s", text)
	}
	// enable では書き直しを求めて止める。
	if decision, _ := g.bash(t, "ls /work/ccnavi/{src,tests}", "enable", "", nil); decision != "deny" {
		t.Errorf("enable では止めるはず: %q", decision)
	}
}

func TestTheToolsAndTheShellCannotWriteThePlacesInDryRun(t *testing.T) {
	t.Parallel()
	g := newGate(t)
	off := map[string]string{"CCNAVI_GUARD_CORE_FILES": "disable"}
	decision, text := g.hook(t, "Write", map[string]any{"file_path": filepath.Join(g.root, ".ccnavi/approved/phases/i0001/1.reviewed")}, "dry-run", "", off)
	if decision != "deny" {
		t.Errorf("Write は deny: %s", text)
	}
	if decision, text := g.bash(t, "echo x > logs/state/ops/a1.json", "dry-run", "", off); decision != "deny" {
		t.Errorf("シェルの書き込みは deny: %s", text)
	}
}

func TestTheSubagentCannotTypeATicketOpAndTheMainAgentCan(t *testing.T) {
	t.Parallel()
	g := newGate(t)
	command := "sh .ccnavi/scripts/ccnavi-ticket.sh start i0001"
	decision, text := g.bash(t, command, "dry-run", "a1", nil)
	if decision != "deny" {
		t.Errorf("deny のはず: %s", text)
	}
	mustHave(t, text, "DENY_SUBAGENT_TICKET_OP")
	_, text = g.bash(t, command, "enable", "", nil)
	mustNotHave(t, text, "DENY_SUBAGENT_TICKET_OP")
	if decision, text := g.bash(t, "sh .ccnavi/scripts/ccnavi-ticket.sh status", "enable", "a1", nil); decision == "deny" {
		t.Errorf("読み取りは打てる: %s", text)
	}
}

func TestTheExecutableCalledDirectlyStopsTheSubagentEvenIfTheGuardIsOff(t *testing.T) {
	t.Parallel()
	off := map[string]string{"CCNAVI_GUARD_TICKET_APPROVAL": "disable"}
	for _, command := range []string{"ccnavi ticket start i0001", "ccnavi review confirm --phase 1"} {
		t.Run(command, func(t *testing.T) {
			t.Parallel()
			g := newGate(t)
			if decision, text := g.bash(t, command, "enable", "a1", off); decision != "deny" {
				t.Errorf("サブエージェントは止める: %s", text)
			}
			if decision, text := g.bash(t, command, "enable", "", off); decision == "deny" {
				t.Errorf("メインは止めない: %s", text)
			}
		})
	}
}

// AT1：dry-run では、一般の保護の対象の変更は報告だけ。一般の復元は通る。

func TestAConfigChangeIsReportedNotStoppedInDryRun(t *testing.T) {
	t.Parallel()
	for _, rel := range []string{".claude/settings.json", ".ccnavi/config/common/rules.yml"} {
		for _, tool := range []string{"Write", "Edit"} {
			t.Run(rel+"/"+tool, func(t *testing.T) {
				t.Parallel()
				g := newGate(t)
				input := map[string]any{"file_path": filepath.Join(g.root, filepath.FromSlash(rel))}
				decision, text := g.hook(t, tool, input, "dry-run", "", nil)
				if decision != "" {
					t.Errorf("報告だけのはず: %q", decision)
				}
				mustHave(t, text, "[ccnavi dry-run] enable would have stopped this call")
				if decision, _ := g.hook(t, tool, input, "enable", "", nil); decision != "deny" {
					t.Errorf("enable では deny: %q", decision)
				}
			})
		}
	}
}

func TestAShellWriteToTheConfigIsReportedInDryRun(t *testing.T) {
	t.Parallel()
	g := newGate(t)
	decision, text := g.bash(t, "echo x > .claude/settings.json", "dry-run", "", nil)
	if decision != "" {
		t.Errorf("報告だけのはず: %q", decision)
	}
	mustHave(t, text, "enable would have stopped this call")
	if decision, _ := g.bash(t, "echo x > .claude/settings.json", "enable", "", nil); decision != "deny" {
		t.Errorf("enable では deny: %q", decision)
	}
}

func TestTheGeneralRestoreWorksAsBefore(t *testing.T) {
	t.Parallel()
	g := newGate(t)
	command := "sh .ccnavi/scripts/ccnavi-git.sh restore --ours -- .ccnavi/config/common/rules.yml"
	for _, mode := range []string{"enable", "dry-run"} {
		if decision, text := g.bash(t, command, mode, "", nil); decision == "deny" {
			t.Errorf("%s: 止めた: %s", mode, text)
		}
	}
}

// AT2：dry-run でも、自己承認の形はメインエージェントでも拒否される。

func TestTheMainAgentCannotSelfApproveInDryRun(t *testing.T) {
	t.Parallel()
	for _, command := range []string{
		"ccnavi user agree",
		"ccnavi user agree --yes",
		"ccnavi --agree --yes i0001",
		"uv run python -m ccnavi --agree --yes x",
		"sh .ccnavi/scripts/ccnavi-user-agree.sh",
	} {
		t.Run(command, func(t *testing.T) {
			t.Parallel()
			g := newGate(t)
			decision, text := g.bash(t, command, "dry-run", "", nil)
			if decision != "deny" {
				t.Errorf("deny のはず: %s", text)
			}
			mustHave(t, text, "DENY_TICKET_APPROVAL_CLI")
		})
	}
}
