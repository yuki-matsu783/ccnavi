package core

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// セッション開始で渡す、ccnavi が前提にしている作業の決まりの受入テスト。
//
//  1. 起動のたびに、ワークツリー・git の入口・下書きの置き場・サブエージェント・合意の要る変更が 1 行ずつ届く。
//     git の入口は打てる絶対パスで、詳しいことは --help と拒否の案内に聞く形
//  2. ワークツリーの行は `workspace-root` の deny がワークスペースのルール（共通レイヤーか自身のレイヤー）にあるときだけ載る。
//     ルールが読めず既定に戻ったときは載らない。git の行は、生の git を組み込みがいつも止めるので常に載る。
//     文は、ラッパーがあれば打つ形を名指しし、無ければユーザに頼む形にする
//  3. チケット制御が有効なときだけ、承認済みチケットの状態の確かめ方（`status`）が載る
//  4. projects/ にプロジェクトがあるときだけ、cd してから作業する行が載る
//  5. md の索引の案内が出る回だけ、詳しい決まりを --docs で引く行が載る
//  6. サブエージェントの起動（agent_id がある）には出さない。文は短い

const (
	conventionsHead  = "# ccnavi の作業の決まり"
	worktreeLine     = ".claude/worktrees/<名前>"
	gitLine          = "git は直接呼ばず"
	noWrapperGitLine = "git は直接呼ばない。" // ラッパーが無いワークスペースの git の行。誘導先が無いので、ユーザに頼む形になる。
)

// conventionsWorkspaceRule はワークツリーと git の行を支える deny。中身は判定に関係しないので、最小限にしてある。
var conventionsWorkspaceRule = rule{"id": "workspace-root", "match": "Write", "glob": "*/never/*", "message": "m"}

// block は additionalContext から作業の決まり（.ccnavi/CCNAVI.md の本文）だけを抜く。無ければ空。
// 本文は空行を挟んで続く。次の `[ccnavi]` で始まる段落（索引の案内など）の手前までを返す。
func block(text string) string {
	start := strings.Index(text, conventionsHead)
	if start < 0 {
		return ""
	}
	if end := strings.Index(text[start:], "\n\n[ccnavi] "); end >= 0 {
		return text[start : start+end]
	}
	return text[start:]
}

type conventionsEnv struct {
	t        *testing.T
	root     string
	rules    string
	wrapper  string
	attended *string // CLAUDE_CODE_SESSION_ATTENDED。nil は未設定
}

func newConventionsEnv(t *testing.T) *conventionsEnv {
	root := emptyRoot(t)
	e := &conventionsEnv{t: t, root: root}
	e.rules = rulesFile(t, root, sound, conventionsWorkspaceRule)
	base.PutCCNaviMD(t, root)
	e.wrapper = writeFile(t, root, ".ccnavi/scripts/ccnavi-git.sh", "exit 0\n")
	return e
}

func (e *conventionsEnv) start(extra []string, payloadExtra map[string]any) string {
	e.t.Helper()
	payload := map[string]any{"hook_event_name": "SessionStart", "session_id": "s1"}
	for k, v := range payloadExtra {
		payload[k] = v
	}
	// 非対話の判定は環境変数を読む。`claude -p` の中で回しても揺れないよう、毎回決める（Main が外した上で、要るときだけ足す）。
	env := map[string]string{}
	if e.attended != nil {
		env["CLAUDE_CODE_SESSION_ATTENDED"] = *e.attended
	}
	result := run.Ccnavi(e.t, append([]string{"--root", e.root, "--rules", e.rules}, extra...),
		run.Opts{Input: mustJSON(e.t, payload), Dir: base.Root(), Env: env})
	if result.Code != 0 {
		e.t.Fatalf("code = %d: %s%s", result.Code, result.Stdout, result.Stderr)
	}
	if strings.TrimSpace(result.Stdout) == "" {
		return ""
	}
	var out struct {
		HookSpecificOutput struct{ AdditionalContext string }
	}
	if err := json.Unmarshal([]byte(result.Stdout), &out); err != nil {
		e.t.Fatal(err)
	}
	return out.HookSpecificOutput.AdditionalContext
}

func (e *conventionsEnv) block(extra ...string) string {
	return block(e.start(append([]string{"--mode", "enable"}, extra...), nil))
}

func (e *conventionsEnv) writeOwnRules(body map[string]any) {
	writeFile(e.t, e.root, ".ccnavi/config/project/rules.yml", mustJSON(e.t, body))
}

// 言い回しの 1 語ずつは縛らない（CCNAVI.md の文面を直すたびに落ちるため）。届く行の目印と、打てる絶対パスだけを見る。
func TestConventionsAreDelivered(t *testing.T) {
	t.Parallel()
	e := newConventionsEnv(t)
	text := e.block()
	mustContain(t, "作業の決まり", text, worktreeLine,
		"sh "+e.root+"/.ccnavi/scripts/ccnavi-worktree.sh <名前>",
		"sh "+e.root+"/.ccnavi/scripts/ccnavi-git.sh",
		"sh "+e.root+"/.ccnavi/scripts/ccnavi-stage.sh <元のパス>",
		"sh "+e.root+"/.ccnavi/scripts/ccnavi-ticket.sh status [<親>]",
		// ルート直下の一時の置き場は、ワークツリーの中から書き違えないよう絶対パスで届く。
		e.root+"/scratchpad/", "wip/handoff/", "タグの作成・push はユーザに頼む")
}

func TestConventionsSayOnlyThatCCNaviMDIsMissing(t *testing.T) {
	t.Parallel()
	e := newConventionsEnv(t)
	if err := os.Remove(filepath.Join(e.root, ".ccnavi", "CCNAVI.md")); err != nil {
		t.Fatal(err)
	}
	text := e.start([]string{"--mode", "enable"}, nil)
	mustContain(t, "文脈", text, ".ccnavi/CCNAVI.md が無いか読めない")
	mustNotContain(t, "文脈", text, conventionsHead)
}

// ワークツリーの行は deny を見る。git の行は組み込みが支えるので、ルールに依らない。
func TestConventionsWorktreeLineNeedsTheWorkspaceRootDeny(t *testing.T) {
	t.Parallel()
	e := newConventionsEnv(t)
	text := e.block()
	mustContain(t, "既定", text, worktreeLine, gitLine)

	rulesFile(t, e.root, sound)
	// allow や ask にある同じ id は数えない。
	e.writeOwnRules(map[string]any{"version": 1, "ask": []rule{conventionsWorkspaceRule}, "allow": []rule{}})
	text = e.block()
	mustNotContain(t, "ask の id", text, worktreeLine)
	mustContain(t, "ask の id", text, gitLine, "wip/handoff/") // ほかの行は残る

	// ワークスペース自身のレイヤーの deny は数える。
	e.writeOwnRules(map[string]any{"version": 1, "deny": []rule{conventionsWorkspaceRule}})
	mustContain(t, "自身の deny", e.block(), worktreeLine)

	// ルールが読めず既定に戻ったときは載らない。組み込みの止めは足されるので git の行は載る。
	if err := os.Remove(filepath.Join(e.root, ".ccnavi", "config", "project", "rules.yml")); err != nil {
		t.Fatal(err)
	}
	writeFile(t, e.root, base.CommonRelpath("rules"), "version: [壊れている\n")
	text = e.block()
	mustNotContain(t, "読めないルール", text, worktreeLine)
	mustContain(t, "読めないルール", text, gitLine)
}

// 生の git は常に止まるので、`git` を勧めない。入口が無いときは頼む文にする。
func TestConventionsGitLineAsksTheUserWithoutTheWrapper(t *testing.T) {
	t.Parallel()
	e := newConventionsEnv(t)
	if err := os.Remove(e.wrapper); err != nil {
		t.Fatal(err)
	}
	text := e.block()
	mustContain(t, "決まり", text, noWrapperGitLine)
	mustNotContain(t, "決まり", text, gitLine, "sh "+e.root+"/.ccnavi/scripts/ccnavi-git.sh", "タグの作成・push")
}

func TestConventionsStillArriveWhenTicketControlIsDisabled(t *testing.T) {
	t.Parallel()
	e := newConventionsEnv(t)
	text := e.block("--ticket-control", "disable")
	mustContain(t, "決まり", text, gitLine)
	mustNotContain(t, "決まり", text, "ccnavi-ticket.sh")

	// dry-run の注記も、非対話のリモート反映の注記も出ない。
	dry := e.start([]string{"--mode", "dry-run", "--ticket-control", "disable"}, nil)
	mustContain(t, "dry-run", dry, conventionsHead)
	mustNotContain(t, "dry-run", dry, "（現状:")
	zero := "0"
	e.attended = &zero
	quiet := e.block("--ticket-control", "disable")
	mustContain(t, "非対話", quiet, "フォアグラウンドで起動し")
	mustNotContain(t, "非対話", quiet, "リモートへ反映する")
}

func TestConventionsCdAndDocsLinesNeedProjectsAndAnIndex(t *testing.T) {
	t.Parallel()
	e := newConventionsEnv(t)
	before := e.block()
	mustNotContain(t, "最初", before, "cd してから", "--docs")

	mkdir(t, filepath.Join(e.root, "projects", "lib", ".git"))
	mustContain(t, "プロジェクトあり", e.block(), "cd してから作業する")

	git(t, e.root, "init", "-q")
	writeFile(t, e.root, ".gitignore", "**/ccnavi-index.jsonl\n")
	writeFile(t, e.root, "README.md", "---\ntype: guide\n---\n# x\n")
	text := e.start([]string{"--mode", "enable"}, nil)
	mustContain(t, "索引あり", block(text), "--docs で、--keyword <語> を付けて引く")
	// 「下の案内」は索引の案内を指すので、その案内は決まりより後ろに出る。
	if strings.Index(text, conventionsHead) >= strings.Index(text, "[ccnavi] ドキュメント（*.md）") {
		t.Error("索引の案内が決まりより前に出た")
	}
}

func TestConventionsDryRunNoteStaysAtTheEndOfTheProcedure(t *testing.T) {
	t.Parallel()
	e := newConventionsEnv(t)
	text := e.start([]string{"--mode", "dry-run"}, nil)
	lines := strings.Split(strings.TrimRight(text, "\n"), "\n")
	if !strings.HasPrefix(lines[len(lines)-1], "（現状: CCNAVI_MODE=dry-run") {
		t.Errorf("最後の行 = %q", lines[len(lines)-1])
	}
	if strings.Index(text, conventionsHead) >= strings.Index(text, "チケット制御を使っている") {
		t.Error("進め方が決まりより前に出た")
	}
}

func TestConventionsNonInteractiveNoteOnlyWhenNonInteractive(t *testing.T) {
	t.Parallel()
	e := newConventionsEnv(t)
	zero := "0"
	e.attended = &zero
	mustContain(t, "非対話", e.block(), "このセッションは非対話", "リモートへ反映する")
	one := "1"
	for _, v := range []*string{&one, nil} {
		e.attended = v
		mustNotContain(t, "対話", e.block(), "このセッションは非対話")
	}
}

func TestConventionsAreNotSentToSubagents(t *testing.T) {
	t.Parallel()
	e := newConventionsEnv(t)
	if got := block(e.start([]string{"--mode", "enable"}, map[string]any{"agent_id": "a1"})); got != "" {
		t.Errorf("サブエージェントに出た: %q", got)
	}
}

// 毎回の起動・再開・compact に届く。考え方・作業の決まり・詳細の表・進め方を合わせて 4000 字未満に収める
// （詳細は .ccnavi/docs/ に分け、ここには入口だけ置く）。
func TestConventionsAreShort(t *testing.T) {
	t.Parallel()
	e := newConventionsEnv(t)
	mkdir(t, filepath.Join(e.root, "projects", "lib", ".git"))
	shorten := func(s string) string { return strings.ReplaceAll(s, e.root, "<root>") }
	if body := shorten(e.block()); len([]rune(body)) >= 4000 {
		t.Errorf("%d 字: %s", len([]rune(body)), body)
	}
	// 索引の案内と dry-run の注記が重なる、いちばん長い回。注記が何本も重なる分の余地として 4300 字未満にする。
	// 注記が重ならない回の上限は上げない。
	git(t, e.root, "init", "-q")
	writeFile(t, e.root, ".gitignore", "**/ccnavi-index.jsonl\n")
	writeFile(t, e.root, "README.md", "---\ntype: guide\n---\n# x\n")
	body := shorten(block(e.start([]string{"--mode", "dry-run"}, nil)))
	mustContain(t, "索引と dry-run の回", body, "--docs で")
	if n := len([]rune(body)); n >= 4300 {
		t.Errorf("%d 字: %s", n, body)
	}
	// 非対話の注記が重なる、いちばん長い回。
	zero := "0"
	e.attended = &zero
	body = shorten(block(e.start([]string{"--mode", "dry-run"}, nil)))
	mustContain(t, "非対話の回", body, "このセッションは非対話")
	if n := len([]rune(body)); n >= 4300 {
		t.Errorf("%d 字: %s", n, body)
	}
}
