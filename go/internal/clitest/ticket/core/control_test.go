package core

import (
	"encoding/json"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/ticketkit"
)

// 不備の無いルールのファイル。ルールの中身はここの主題ではない。
const controlRules = `{
  "version": 1,
  "deny": [{"id": "git-push", "match": "Bash", "glob": "*git push*", "message": "git push is not run by the agent. Ask the user to push."}],
  "allow": [{"id": "anything", "match": "Read", "regex": "."}]
}`

// controlStart は SessionStart を、ルールだけの木に打つ。環境（CCNAVI_*）は Main が外してあるので、env に渡した分だけが効く。
func controlStart(t *testing.T, env map[string]string, extra ...string) (run.Result, string) {
	t.Helper()
	root := base.TempDir(t)
	rules := ticketkit.Write(t, base.CommonPath(root, "rules"), controlRules)
	base.PutCCNaviMD(t, root)
	payload, _ := json.Marshal(map[string]any{"hook_event_name": "SessionStart", "session_id": "s1"})
	r := run.Ccnavi(t, append([]string{"--root", root, "--rules", rules}, extra...), run.Opts{Input: string(payload), Env: env, Dir: base.Root()})
	if r.Code != 0 {
		t.Fatalf("終了コード %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	if strings.TrimSpace(r.Stdout) == "" {
		return r, ""
	}
	var out struct {
		Hook struct {
			Context string `json:"additionalContext"`
		} `json:"hookSpecificOutput"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		t.Fatal(err)
	}
	return r, out.Hook.Context
}

// チケット制御が有効なセッションの頭で、直接作業とチケット作業の使い分けが届く。後から届くもの（レビューの sh はフェーズの終わりと
// ready の手順で、設定ファイルのパスは承認のときの検査で名指しされる）は頭では言わない。
func TestSessionStartDeliversWhenToWorkDirectlyAndWhenToUseTickets(t *testing.T) {
	t.Parallel()
	_, text := controlStart(t, nil, "--mode", "enable")
	for _, want := range []string{"チケット制御を使っている", "直接作業", "チケット作業", "wip/proposals/", "ccnavi-ticket.sh"} {
		ticketkit.Has(t, text, want)
	}
	for _, not := range []string{"dry-run", "ccnavi-review.sh", "phases.yml", "risks.yml"} {
		ticketkit.Lacks(t, text, not)
	}
}

// disable でも、ccnavi が前提にしている作業の決まり（ワークツリー・git の入口など）はチケットに依らないので届く。
// チケットに関わる文が 1 つも載らないことを見る。フラグでも環境変数でも切れる。
func TestDisablingTicketControlDropsTheTicketText(t *testing.T) {
	t.Parallel()
	for _, row := range []struct {
		name  string
		env   map[string]string
		extra []string
	}{
		{"フラグ", nil, []string{"--ticket-control", "disable"}},
		{"環境変数", map[string]string{"CCNAVI_TICKET_CONTROL": "disable"}, nil},
	} {
		t.Run(row.name, func(t *testing.T) {
			t.Parallel()
			_, text := controlStart(t, row.env, append([]string{"--mode", "enable"}, row.extra...)...)
			for _, not := range []string{"チケット制御を使っている", "直接作業", "ccnavi-ticket.sh"} {
				ticketkit.Lacks(t, text, not)
			}
		})
	}
}

func TestAnUnreadableValueIsTreatedAsEnabled(t *testing.T) {
	t.Parallel()
	r, text := controlStart(t, map[string]string{"CCNAVI_TICKET_CONTROL": "off"}, "--mode", "enable")
	ticketkit.Has(t, text, "直接作業")
	ticketkit.Has(t, r.Stderr, "CCNAVI_TICKET_CONTROL='off' は設定ではありません")
}

// dry-run なら末尾にその旨と次からの従い方が付く（止まらないことだけで終えない。通ったことを許可と読ませない）。
func TestDryRunAddsAFinalNoteAboutHowToFollowFromNowOn(t *testing.T) {
	t.Parallel()
	_, text := controlStart(t, nil, "--mode", "dry-run")
	ticketkit.Has(t, text, "直接作業")
	lines := strings.Split(text, "\n")
	last := lines[len(lines)-1]
	if !strings.HasPrefix(last, "（現状: CCNAVI_MODE=dry-run") {
		t.Errorf("末尾の行 = %q", last)
	}
	ticketkit.Has(t, last, "許可と読まず")
	ticketkit.Has(t, last, "次から従う")
}
