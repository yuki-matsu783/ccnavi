package post

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/testkit"
)

// 実行後チェックの一連の呼び出しを、Python の post と同じ手順で両方に打ち、返す文・記録・state のファイル・退避・
// ワークツリーの状態を突き合わせる。止められないイベントの唯一の手立て（差し戻しの文と戻し）が Python と同じであること。

// step は 1 つの手順。op が write・remove・git ならワークスペースを動かし、call なら実行後チェックを呼ぶ。
type step struct {
	Op   string   `json:"op"`
	Path string   `json:"path,omitempty"`
	Text string   `json:"text,omitempty"`
	Args []string `json:"args,omitempty"`
	// call の中身。
	Kind      string         `json:"kind,omitempty"` // check・at_stop・at_prompt・baseline
	Tool      string         `json:"tool,omitempty"`
	Input     map[string]any `json:"input,omitempty"`
	Restore   string         `json:"restore,omitempty"`
	Enforcing bool           `json:"enforcing,omitempty"`
	Accept    bool           `json:"accept,omitempty"`
}

type watchSpec struct {
	Path string `json:"path"` // 空ならワークスペースルート
	Side bool   `json:"side"`
}

type scenario struct {
	Deny    []map[string]string `json:"deny"`
	Ask     []map[string]string `json:"ask"`
	Watched []watchSpec         `json:"watched"`
	Scope   bool                `json:"scope"`
	Steps   []step              `json:"steps"`
}

const scenarioOracle = `
import io, json, os, re, subprocess, sys
from ccnavi.hook import events, post
from ccnavi.infra import hookio, settings, tree
from ccnavi.policy import rules
from ccnavi.records import audit
c = json.load(sys.stdin)
root, s = c["root"], c["scenario"]
env = dict(os.environ, **dict(e.split("=", 1) for e in c["gitenv"]))
def rule(r, decision):
    x = rules.Rule(id=r["id"], bare_id=r["id"], match="Write|Edit", decision=decision, message=r["message"], root=root,
                   from_file=True, file="rules.yml")
    x.compiled = re.compile(r["regex"])
    return x
rs = rules.RuleSet(deny=[rule(r, rules.DENY) for r in s["deny"]], ask=[rule(r, rules.ASK) for r in s["ask"]])
state = os.path.join(root, "logs", "state")
mine = (state, os.path.join(root, "logs", "decisions.jsonl"))
out = []
for st in s["steps"]:
    op = st["op"]
    if op == "write":
        p = os.path.join(root, st["path"])
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(st["text"])
        continue
    if op == "remove":
        os.remove(os.path.join(root, st["path"]))
        continue
    if op == "git":
        subprocess.run(["git", *st["args"][1:]], cwd=os.path.join(root, st["args"][0]), env=env, check=True, capture_output=True)
        continue
    conf = settings.load(root)[0]
    watched = []
    for w in s["watched"]:
        t = tree.main_tree(root) if not w["path"] else tree.tree_of(root, os.path.join(root, w["path"]), conf.projects)
        watched.append(post.Watched(t, rs, "rules.yml", w["side"]))
    scope = events.scope_guard(conf, root) if s["scope"] else None
    places = (conf.tickets, conf.approved)
    payload = hookio.Input(event="PostToolUse", session_id="s1", tool_name=st.get("tool", ""),
                           tool_input=st.get("input") or {}, cwd=root)
    record, err = audit.Record(), io.StringIO()
    text, tried, kind = "", False, st["kind"]
    if kind == "check":
        text, tried = post.check(err, st.get("enforcing", False), st.get("restore", ""), state, mine, watched, scope,
                                 payload, record, places, root)
    elif kind == "at_stop":
        text = post.at_stop(err, state, mine, watched, scope, payload, record, places, root)
    elif kind == "at_prompt":
        post.at_prompt(err, state, mine, watched, scope, payload, record, places, root, accept=st.get("accept", False))
    else:
        post.capture_baseline(err, state, "s1", watched, scope, record, mine, places, root)
    files, aside = {}, []
    if os.path.isdir(state):
        for name in sorted(os.listdir(state)):
            p = os.path.join(state, name)
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as f:
                    files[name] = f.read()
        for d, _, names in os.walk(os.path.join(state, "aside")):
            aside += [os.path.relpath(os.path.join(d, n), state).replace(os.sep, "/") for n in names]
    status = [subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=os.path.join(root, w["path"]), env=env,
                             capture_output=True, text=True).stdout for w in s["watched"]]
    out.append({"text": text, "tried": tried, "stderr": err.getvalue(), "decision": record.decision,
                "reason": record.reason, "enforced": record.enforced, "code": record.code, "paths": list(record.paths),
                "rules": list(record.rules), "detail": record.detail, "files": files, "aside": sorted(aside), "status": status})
json.dump(out, sys.stdout, ensure_ascii=False)
`

type result struct {
	Text     string            `json:"text"`
	Tried    bool              `json:"tried"`
	Stderr   string            `json:"stderr"`
	Decision string            `json:"decision"`
	Reason   string            `json:"reason"`
	Enforced bool              `json:"enforced"`
	Code     string            `json:"code"`
	Paths    []string          `json:"paths"`
	Rules    []string          `json:"rules"`
	Detail   string            `json:"detail"`
	Files    map[string]string `json:"files"`
	Aside    []string          `json:"aside"`
	Status   []string          `json:"status"`
}

func buildRules(s scenario, root string) *rules.RuleSet {
	make := func(r map[string]string, d rules.Decision) *rules.Rule {
		return &rules.Rule{ID: r["id"], BareID: r["id"], Match: "Write|Edit", Decision: d, Message: r["message"], Root: root,
			FromFile: true, File: "rules.yml", Compiled: pyre.MustCompile(r["regex"], 0)}
	}
	rs := &rules.RuleSet{}
	for _, r := range s.Deny {
		rs.Deny = append(rs.Deny, make(r, rules.Deny))
	}
	for _, r := range s.Ask {
		rs.Ask = append(rs.Ask, make(r, rules.Ask))
	}
	return rs
}

func gitIn(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), testkit.GitEnv()...)
	out, _ := cmd.Output()
	return string(out)
}

func runScenario(t *testing.T, s scenario, root string) []result {
	t.Helper()
	rs := buildRules(s, root)
	state := filepath.Join(root, "logs", "state")
	var out []result
	for _, st := range s.Steps {
		switch st.Op {
		case "write":
			put(t, root, st.Path, st.Text)
			continue
		case "remove":
			os.Remove(filepath.Join(root, filepath.FromSlash(st.Path)))
			continue
		case "git":
			testkit.Git(t, filepath.Join(root, st.Args[0]), st.Args[1:]...)
			continue
		}
		conf := testkit.Settings(t, root)
		var watched []Watched
		for _, w := range s.Watched {
			tr := tree.MainTree(root)
			if w.Path != "" {
				tr, _ = tree.TreeOf(root, filepath.Join(root, w.Path), conf.Projects)
			}
			watched = append(watched, Watched{Tree: tr, Rules: rs, Source: "rules.yml", SideBySide: w.Side})
		}
		var guard *ScopeGuard
		if s.Scope && conf.TicketsEnabled() {
			copies, _ := approval.Scan(conf, root, false, nil)
			guard = &ScopeGuard{Root: root, Copies: approval.ByID(copies), Projects: conf.Projects,
				Places: Places{conf.Tickets, conf.Approved}}
		}
		var stderr bytes.Buffer
		c := &Checker{Stderr: &stderr, State: state, Mine: []string{state, filepath.Join(root, "logs", "decisions.jsonl")},
			Watched: watched, Scope: guard, Places: Places{conf.Tickets, conf.Approved}, Root: root}
		input := pyjson.NewObject()
		for k, v := range st.Input {
			input.Set(k, v)
		}
		in := hookio.Input{Event: hookio.PostToolUse, SessionID: "s1", ToolName: st.Tool, ToolInput: input, Cwd: root}
		record := &audit.Record{}
		r := result{}
		switch st.Kind {
		case "check":
			r.Text, r.Tried = c.Check(st.Enforcing, config.Mode(st.Restore), in, record)
		case "at_stop":
			r.Text = c.AtStop(in, record)
		case "at_prompt":
			c.AtPrompt(in, record, st.Accept)
		default:
			c.CaptureBaseline("s1", record)
		}
		r.Stderr, r.Decision, r.Reason, r.Enforced, r.Code = stderr.String(), string(record.Decision), string(record.Reason), record.Enforced, record.Code
		r.Paths, r.Rules, r.Detail = record.Paths, record.Rules, record.Detail
		r.Files = map[string]string{}
		if names, err := os.ReadDir(state); err == nil {
			for _, e := range names {
				if !e.IsDir() {
					data, _ := os.ReadFile(filepath.Join(state, e.Name()))
					r.Files[e.Name()] = string(data)
				}
			}
		}
		filepath.WalkDir(filepath.Join(state, asideDir), func(p string, d os.DirEntry, err error) error {
			if err == nil && !d.IsDir() {
				rel, _ := filepath.Rel(state, p)
				r.Aside = append(r.Aside, filepath.ToSlash(rel))
			}
			return nil
		})
		sort.Strings(r.Aside)
		for _, w := range s.Watched {
			r.Status = append(r.Status, gitIn(t, filepath.Join(root, w.Path), "status", "--porcelain", "-uall"))
		}
		out = append(out, r)
	}
	return out
}

var stamp = regexp.MustCompile(`aside/\d{8}-\d{6}`)

// normalized はルートと退避の日時を置き換えた JSON。nil と空の列は同じに読む。
func normalized(t *testing.T, r result, root string) string {
	t.Helper()
	if r.Paths == nil {
		r.Paths = []string{}
	}
	if r.Rules == nil {
		r.Rules = []string{}
	}
	if r.Aside == nil {
		r.Aside = []string{}
	}
	data, err := json.MarshalIndent(r, "", " ")
	if err != nil {
		t.Fatal(err)
	}
	quoted, _ := json.Marshal(root)
	text := strings.ReplaceAll(string(data), strings.Trim(string(quoted), `"`), "<root>")
	// 記録のキーはツリーのルートを畳んで持つ。
	folded, _ := json.Marshal(pathcase.Fold(root))
	text = strings.ReplaceAll(text, strings.Trim(string(folded), `"`), "<root>")
	return stamp.ReplaceAllString(text, "aside/<stamp>")
}

func compareScenario(t *testing.T, s scenario, setup func(t *testing.T) string) {
	t.Helper()
	pyRoot, goRoot := setup(t), setup(t)
	want := testkit.Answers[[]result](t, "", scenarioOracle,
		map[string]any{"root": pyRoot, "scenario": s, "gitenv": testkit.GitEnv()})
	got := runScenario(t, s, goRoot)
	calls := 0
	for i, st := range s.Steps {
		if st.Op != "call" {
			continue
		}
		g, w := normalized(t, got[calls], goRoot), normalized(t, want[calls], pyRoot)
		if g != w {
			t.Errorf("手順 %d（%s %s）:\nGo:\n%s\nPython:\n%s", i, st.Kind, st.Restore, g, w)
		}
		calls++
	}
	if calls == 0 {
		t.Fatal("呼び出しが無い")
	}
}

func write(path, text string) step { return step{Op: "write", Path: path, Text: text} }
func gitStep(dir string, args ...string) step {
	return step{Op: "git", Args: append([]string{dir}, args...)}
}
func bash(command, restore string, enforcing bool) step {
	return step{Op: "call", Kind: "check", Tool: config.ToolBash, Input: map[string]any{"command": command}, Restore: restore, Enforcing: enforcing}
}
func call(kind string) step { return step{Op: "call", Kind: kind} }

var protectRules = scenario{
	Deny: []map[string]string{
		{"id": "protected", "regex": `[\\/]protected[\\/]`, "message": "protected/ は書かない。"},
		{"id": "approved", "regex": `[\\/]\.ccnavi[\\/]approved[\\/]`, "message": "承認済みチケットはユーザが動かす。"},
		{"id": "proposals", "regex": `[\\/]wip[\\/]proposals[\\/]`, "message": "提案はスクリプトが動かす。"},
	},
	Ask: []map[string]string{{"id": "watched", "regex": `[\\/]watched[\\/]`, "message": ""}},
}

func mainWorkspace(wrapper bool) func(t *testing.T) string {
	return func(t *testing.T) string {
		root := testkit.Workspace(t)
		put(t, root, "protected/keep.txt", "committed\n")
		put(t, root, "watched/deps.txt", "committed\n")
		put(t, root, "src/app.py", "print(1)\n")
		put(t, root, ".gitignore", "logs/\n")
		if wrapper {
			put(t, root, ".ccnavi/scripts/"+config.GitWrapper, "exit 0\n")
		}
		put(t, root, ".ccnavi/approved/doing/t1.md", testkit.ParentText("t1", nil))
		put(t, root, ".ccnavi/approved/doing/t2.md", testkit.ParentText("t2", nil))
		testkit.Commit(t, root, "init")
		return root
	}
}

func TestMainTreeMatchesPython(t *testing.T) {
	s := protectRules
	s.Watched = []watchSpec{{}}
	many := []step{}
	for i := range 13 {
		many = append(many, write("protected/many/"+string(rune('a'+i))+".txt", "x\n"))
	}
	s.Steps = append([]step{
		write("protected/keep.txt", "pre\n"),
		call("baseline"),
		bash("ls", "disable", true),
		bash("ls", "disable", true),
		call("at_prompt"),
		write("protected/new.txt", "x\n"),
		write("watched/deps.txt", "changed\n"),
		bash("python build.py", "dry-run", false),
		write("protected/b.txt", "b\n"),
		write("src/app.py", "print(2)\n"),
		bash("python build.py", "enable", true),
		// スクリプトだけが書く欄の書き足しは外す。範囲の書き換えは報告して戻す。
		write(".ccnavi/approved/doing/t1.md", strings.Replace(testkit.ParentText("t1", nil), `started_at: ""`, `started_at: "2026-01-01T00:00:00+0900"`, 1)),
		bash("ccnavi ticket start t1", "enable", true),
		write(".ccnavi/approved/doing/t1.md", strings.Replace(testkit.ParentText("t1", nil), `glob: "src/*"`, `glob: "**"`, 1)),
		bash("sed -i s/src/x/ t1.md", "enable", true),
		// finish の移動（doing から review へ、着手の時刻と完了の時刻を足しただけ）は外す。
		{Op: "remove", Path: ".ccnavi/approved/doing/t2.md"},
		write("wip/proposals/review/t2.md", strings.Replace(testkit.ParentText("t2", nil), `completed_at: ""`, `completed_at: "2026-01-02T00:00:00+0900"`, 1)),
		bash("ccnavi ticket finish t2", "enable", true),
		write("protected/committed.txt", "c\n"),
		gitStep("", "add", "protected/committed.txt"),
		gitStep("", "commit", "-q", "-m", "agent"),
		call("at_stop"),
		{Op: "call", Kind: "at_prompt", Accept: true},
		write("protected/new.txt", "agent wrote again\n"),
		bash("python build.py", "enable", true),
		bash("python build.py", "enable", true),
		{Op: "call", Kind: "check", Tool: config.ToolRead, Input: map[string]any{"file_path": "x"}, Restore: "enable", Enforcing: true},
	}, many...)
	s.Steps = append(s.Steps, bash("python gen.py", "disable", true), call("at_stop"))
	compareScenario(t, s, mainWorkspace(true))
}

func TestWithoutBaselineOrWrapperMatchesPython(t *testing.T) {
	s := protectRules
	s.Watched = []watchSpec{{}}
	s.Steps = []step{
		call("at_stop"),
		write("protected/keep.txt", "pre\n"),
		bash("ls", "enable", true),
		write("protected/x.txt", "x\n"),
		write("protected/keep.txt", "again\n"),
		bash("python build.py", "enable", true),
		write("protected/y.txt", "y\n"),
		{Op: "call", Kind: "check", Tool: config.ToolWrite, Input: map[string]any{"file_path": "protected/y.txt"}, Restore: "dry-run", Enforcing: false},
	}
	compareScenario(t, s, mainWorkspace(false))
}

func TestSideBySideMatchesPython(t *testing.T) {
	s := protectRules
	s.Watched = []watchSpec{{Side: true}}
	s.Steps = []step{
		call("baseline"),
		call("at_prompt"),
		write("protected/x.txt", "x\n"),
		bash("ls", "enable", true),
		bash("ls", "enable", true),
		write("protected/x.txt", "x2\n"),
		bash("ls", "enable", true),
		call("at_stop"),
	}
	compareScenario(t, s, mainWorkspace(true))
}

func TestTicketScopeMatchesPython(t *testing.T) {
	s := protectRules
	s.Watched = []watchSpec{{}, {Path: ".claude/worktrees/feat"}}
	s.Scope = true
	wt := ".claude/worktrees/feat/"
	s.Steps = []step{
		call("baseline"),
		call("at_prompt"),
		write(wt+"outside.txt", "x\n"),
		write(wt+"src/ok.txt", "ok\n"),
		write(wt+"wip/handoff/draft.md", "d\n"),
		{Op: "call", Kind: "check", Tool: config.ToolWrite, Input: map[string]any{"file_path": wt + "outside.txt"}, Restore: "enable", Enforcing: true},
		write(wt+"protected/p.txt", "p\n"),
		bash("cp a protected/p.txt", "enable", true),
		call("at_stop"),
	}
	compareScenario(t, s, func(t *testing.T) string {
		root := testkit.Workspace(t, "feat")
		put(t, root, ".gitignore", "logs/\n")
		testkit.Commit(t, root, "init")
		wtRoot := testkit.Worktree(root, "feat")
		testkit.Git(t, wtRoot, "merge", "-q", "main")
		testkit.Put(t, testkit.Approved(root, "feat", "doing", "feat"), testkit.ParentText("feat", nil))
		testkit.Commit(t, wtRoot, "approve")
		return root
	})
}
