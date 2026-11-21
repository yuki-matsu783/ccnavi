package layers

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/configws"
	"ccnavi/internal/harness/run"
)

// ユーザの発言のたびに、共通の工程スキルをプロジェクトの `skills/` へコピーする。
//
// ワークスペースの `.claude/skills/` に工程スキル（`process: true`）と、そうでないスキルを置く。見るのは 3 つ。
//
//  1. 発言が、工程スキルの `SKILL.md` と `references/common/` の `.md` を、着手済みの親のワークツリーの `skills/<工程>/` へコピーする
//     （`ticket start` はコピーしない）。`references/` のそれ以外（プロジェクト固有）と、`process: true` を持たないスキルはコピーしない
//  2. `common/` は共通のコピー。同じパスは共通の中身で上書きし、共通側に無くなった `.md` は消す（空になったディレクトリも片付ける）。
//     `references/` の common 以外（プロジェクト固有）は上書きも削除もしない
//  3. 中身が同じなら何も書かず、知らせない
//
// 工程スキルの数え方と、シンボリックリンクがあるときに写さないことは、Go のテスト（go/internal/tickets/sync/mirror）が確かめる。

func processSkill(name string) string {
	return "---\nname: " + name + "\nprocess: true\ndescription: " + name + " の入口\n---\n\n# " + name + "\n"
}

func plainSkill(name string) string {
	return "---\nname: " + name + "\ndescription: " + name + "\n---\n\n# " + name + "\n"
}

func libTicket(name string) string {
	return configws.TicketText(name, configws.TicketOpts{Project: "lib", Plan: []string{"build"}, Allow: []string{"src/*"}})
}

// skillWS は工程スキルの同期を見る 1 本のワークスペース。
type skillWS struct{ *configws.WS }

func newSkillWS(t *testing.T) *skillWS { return &skillWS{configws.NewWS(t, configws.KindBase)} }

func (w *skillWS) source(parts ...string) string {
	return filepath.Join(append([]string{w.Root, ".claude", "skills"}, parts...)...)
}

func (w *skillWS) startParent(name string) (tree, out string) {
	w.T.Helper()
	w.Propose(name, libTicket(name), "lib")
	if approved := w.Approve(); approved.Code != 0 {
		w.T.Fatalf("approve: %d\n%s%s", approved.Code, approved.Stdout, approved.Stderr)
	}
	tree = w.Worktree(w.Lib, name)
	code, out := w.StartTicket(name)
	if code != 0 {
		w.T.Fatalf("ticket start: %d\n%s", code, out)
	}
	return tree, out
}

func TestStartCopiesTheCommonFilesAndLeavesTheProjectFiles(t *testing.T) {
	t.Parallel()
	w := newSkillWS(t)
	configws.Write(t, w.source("process-test", "SKILL.md"), processSkill("process-test"))
	common := w.source("process-test", "references", "common")
	configws.Write(t, filepath.Join(common, "unit", "basics.md"), "# 基本\n")
	configws.Write(t, filepath.Join(common, "ccnavi-index.jsonl"), "{}\n")
	configws.Write(t, filepath.Join(common, "shared.md"), "共通の新しい中身\n")
	configws.Write(t, w.source("process-test", "references", "project", "workspace-only.md"), "固有\n")
	configws.Write(t, w.source("process-test", "references", "loose.md"), "直下\n")
	configws.Write(t, w.source("commit", "SKILL.md"), plainSkill("commit"))
	seeded := filepath.Join(w.Lib, "skills", "process-test", "references")
	configws.Write(t, filepath.Join(seeded, "common", "shared.md"), "古い中身\n")
	configws.Write(t, filepath.Join(seeded, "common", "old", "gone.md"), "消える\n")
	configws.Write(t, filepath.Join(seeded, "common", "note.txt"), "md でないものは触れない\n")
	configws.Write(t, filepath.Join(seeded, "project", "own.md"), "プロジェクト固有\n")
	run.Git(t, w.Lib, "add", "-A")
	run.Git(t, w.Lib, "commit", "-m", "seed")

	tree, out := w.startParent("i0001")

	skillDir := filepath.Join(tree, "skills", "process-test")
	dest := filepath.Join(skillDir, "references")
	for path, want := range map[string]string{
		filepath.Join(skillDir, "SKILL.md"):                processSkill("process-test"),
		filepath.Join(dest, "common", "unit", "basics.md"): "# 基本\n",
		filepath.Join(dest, "common", "shared.md"):         "共通の新しい中身\n",
		filepath.Join(seeded, "common", "shared.md"):       "古い中身\n", // 元リポジトリには触れない
		filepath.Join(dest, "project", "own.md"):           "プロジェクト固有\n",
		filepath.Join(dest, "common", "note.txt"):          "md でないものは触れない\n",
	} {
		if got := configws.Read(t, path); got != want {
			t.Errorf("%s = %q, want %q", path, got, want)
		}
	}
	for _, path := range []string{
		filepath.Join(dest, "common", "old"),                // 共通側に無くなった .md と空のディレクトリ
		filepath.Join(dest, "common", "ccnavi-index.jsonl"), // .md でないものはコピーしない
		filepath.Join(dest, "project", "workspace-only.md"), // common 以外の references はコピーしない
		filepath.Join(dest, "loose.md"),
		filepath.Join(tree, "skills", "commit"), // process: true の無いスキル
		filepath.Join(w.Lib, "skills", "commit"),
	} {
		if configws.Exists(path) {
			t.Errorf("%s がある", path)
		}
	}
	for _, want := range []string{"skills/process-test/SKILL.md", "削除した"} {
		if !strings.Contains(out, want) {
			t.Errorf("出力に %q が無い:\n%s", want, out)
		}
	}
}

func TestSameContentIsNotReported(t *testing.T) {
	t.Parallel()
	w := newSkillWS(t)
	configws.Write(t, w.source("process-test", "SKILL.md"), processSkill("process-test"))
	_, first := w.startParent("i0001")
	if !strings.Contains(first, "skills/process-test") {
		t.Errorf("1 本目で知らせない:\n%s", first)
	}
	tree := w.Worktree(w.Lib, "i0002")
	configws.Write(t, filepath.Join(tree, "skills", "process-test", "SKILL.md"), processSkill("process-test"))
	w.Propose("i0002", libTicket("i0002"), "lib")
	if done := w.Approve(); done.Code != 0 {
		t.Fatalf("approve: %s%s", done.Stdout, done.Stderr)
	}
	code, second := w.StartTicket("i0002")
	if code != 0 {
		t.Fatalf("ticket start: %d\n%s", code, second)
	}
	if strings.Contains(second, "skills/process-test") {
		t.Errorf("中身が同じなのに知らせた:\n%s", second)
	}
}

func TestTheStartDoesNotCopyTheSkills(t *testing.T) {
	t.Parallel()
	w := newSkillWS(t)
	configws.Write(t, w.source("process-test", "SKILL.md"), processSkill("process-test"))
	tree := w.Worktree(w.Lib, "i0001")
	configws.Write(t, filepath.Join(tree, "wip", "proposals", "todo", "i0001.md"), configws.InsertPhases(libTicket("i0001"), configws.LibPhasesText))
	if done := w.Approve(); done.Code != 0 {
		t.Fatalf("approve: %s%s", done.Stdout, done.Stderr)
	}
	target := filepath.Join(w.Root, "logs", "state", "write-and-push", "lib", "i0001.t.writes")

	started := w.Ccnavi("--record-writes", target, "--record-tree", tree, "ticket", "start", "i0001")

	if started.Code != 0 {
		t.Fatalf("ticket start: %d\n%s%s", started.Code, started.Stdout, started.Stderr)
	}
	if configws.Exists(filepath.Join(tree, "skills", "process-test")) {
		t.Error("ticket start がスキルをコピーした")
	}
	if configws.Exists(target) && strings.Contains(configws.Read(t, target), "skills/") {
		t.Error("ticket start の書き込みの記録にスキルがある")
	}
	w.Prompt("s1", "disable")
	if !configws.Exists(filepath.Join(tree, "skills", "process-test", "SKILL.md")) {
		t.Error("次の発言でコピーされない")
	}
}
