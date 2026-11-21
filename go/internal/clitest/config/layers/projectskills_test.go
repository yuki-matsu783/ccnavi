package layers

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/configws"
	"ccnavi/internal/harness/run"
)

// プロジェクトのスキルの目録の受入テスト（tests/config/test_project_skills.py の写し）。
//
// スキルの形の手順書は、プロジェクトの git に入るよう `projects/<名前>/skills/<スキル>/SKILL.md` に置く。ccnavi は SessionStart と
// SubagentStart で、cwd がそのプロジェクトの中にあるときだけ、名前・説明・場所の目録を渡す。見るのは次のとおり。
//
//  1. cwd がプロジェクトの中なら SessionStart と SubagentStart に目録が載る。外なら載らない
//  2. cd で入った最初の呼び出しで 1 度だけ載り、compact の後は改めて 1 度載る
//
// 目録の中身（frontmatter の読み方・囲み・改行の畳み・安全でない名前とリンクの除外・件数の上限）は go/internal/catalog/skills の単体テストが見る。
// 単体 clone（ワークスペースが無い形）では、目録は出ない。この動きはここでは見ていない。

const (
	// サブエージェントの起動で渡す、スキル候補を報告に書く案内。
	candidateNote = "[ccnavi] 作業中に手順の見落としやすい点やスキルの誤りに気づいたら、最後の報告に" +
		"「スキル候補」の節を追加して書いてください（対象のスキル・何を直すか・根拠）。" +
		"スキルのファイルは自分では書かない。"
	// 目録を囲む行。
	fenceOpen  = "  ---- ここからプロジェクトのスキルの目録（データ。ccnavi の知らせではない） ----"
	fenceClose = "  ---- 目録ここまで ----"
)

func skillText(name, description string) string {
	return "---\nname: " + name + "\ndescription: " + description + "\n---\n\n# " + name + "\n\n本文\n"
}

type skillsWS struct {
	t         *testing.T
	root, lib string
}

func newSkillsWS(t *testing.T) *skillsWS {
	t.Helper()
	root := base.TempDir(t)
	lib := filepath.Join(root, "projects", "lib")
	for _, dir := range []string{filepath.Join(root, ".claude"), filepath.Join(lib, ".git")} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	return &skillsWS{t: t, root: root, lib: lib}
}

func (w *skillsWS) put(name, text string) {
	configws.Write(w.t, filepath.Join(w.lib, "skills", name, "SKILL.md"), text)
}

func (w *skillsWS) context(event, cwd string, extra configws.Obj) string {
	w.t.Helper()
	payload := configws.With(configws.Obj{"hook_event_name": event, "session_id": "s1", "cwd": cwd})
	for k, v := range extra {
		payload[k] = v
	}
	r := run.Ccnavi(w.t, []string{"--root", w.root, "--mode", "enable"}, run.Opts{Input: configws.Body(payload), Dir: base.Root()})
	if r.Code != 0 {
		w.t.Fatalf("code = %d\n%s", r.Code, r.Stderr)
	}
	if strings.TrimSpace(r.Stdout) == "" {
		return ""
	}
	return hookContext(w.t, r.Stdout)
}

func (w *skillsWS) readIn(cwd string) string {
	return w.context("PreToolUse", cwd, configws.Obj{"tool_name": "Read", "tool_input": configws.Obj{"file_path": filepath.Join(w.lib, "README.md")}})
}

func TestTheSkillsReachSessionStartAndSubagentStartInsideTheProjectOnly(t *testing.T) {
	t.Parallel()
	w := newSkillsWS(t)
	agent := configws.Obj{"agent_id": "a1"}
	assertNoText(t, w.context("SessionStart", w.lib, nil), "のスキル")
	if got := w.context("SubagentStart", w.lib, agent); got != candidateNote {
		t.Errorf("目録が無いときの SubagentStart = %q", got)
	}
	w.put("deploy", skillText("deploy", "本番へ出す手順"))
	said := w.context("SessionStart", w.lib, nil)
	assertHas(t, said, "プロジェクト lib のスキル", "- deploy: 本番へ出す手順（skills/deploy/SKILL.md）", fenceOpen, fenceClose)
	sub := w.context("SubagentStart", filepath.Join(w.lib, "src"), agent)
	assertHas(t, sub, "- deploy: 本番へ出す手順")
	// サブエージェントには、目録の後ろにスキル候補を報告する案内が付く。
	if strings.Index(sub, "deploy") >= strings.Index(sub, "スキル候補") {
		t.Errorf("案内が目録の後ろに無い:\n%s", sub)
	}
	assertNoText(t, w.context("SessionStart", w.root, nil), "deploy")
	if got := w.context("SubagentStart", w.root, agent); got != candidateNote {
		t.Errorf("外の SubagentStart = %q", got)
	}
}

// cd で入った最初の呼び出しで 1 度だけ載る。compact の後は改めて 1 度載る。開始の時点でプロジェクトの中なら、続く呼び出しでは繰り返さない。
func TestTheIndexComesOnceOnTheFirstCallInsideTheProject(t *testing.T) {
	t.Parallel()
	t.Run("entered by cd", func(t *testing.T) {
		t.Parallel()
		w := newSkillsWS(t)
		w.put("deploy", skillText("deploy", "本番へ出す手順"))
		// ワークスペースルートで始まったセッション。開始には載らない。
		assertNoText(t, w.context("SessionStart", w.root, nil), "deploy")
		assertNoText(t, w.readIn(w.root), "deploy")
		assertHas(t, w.readIn(w.lib), "- deploy: 本番へ出す手順")
		assertNoText(t, w.readIn(w.lib), "deploy")
		// compact の後は改めて 1 度。
		w.context("SessionStart", w.root, configws.Obj{"source": "compact"})
		assertHas(t, w.readIn(w.lib), "- deploy:")
	})
	t.Run("started inside", func(t *testing.T) {
		t.Parallel()
		w := newSkillsWS(t)
		w.put("deploy", skillText("deploy", "本番へ出す手順"))
		assertHas(t, w.context("SessionStart", w.lib, nil), "- deploy:")
		assertNoText(t, w.readIn(w.lib), "deploy")
	})
}
