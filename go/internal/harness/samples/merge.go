package samples

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/ospath"
)

func git(t testing.TB, dir string, args ...string) string {
	t.Helper()
	full := append([]string{"-c", "user.email=t@example.com", "-c", "user.name=t"}, args...)
	cmd := exec.Command("git", full...)
	cmd.Dir = dir
	out, err := cmd.Output()
	if err != nil {
		stderr := ""
		if e, ok := err.(*exec.ExitError); ok {
			stderr = string(e.Stderr)
		}
		t.Fatalf("git %v: %v %s", args, err, stderr)
	}
	return strings.TrimSpace(string(out))
}

// RecordMerge は子を親のブランチへ合流したあとの記録（merged_sha）を、write-and-push の finish の代わりに書く。
//
// 本物の finish は、親のワークツリーで子を合流するのと同じコミットで、子の承認済みチケットに合流した子の先頭を書く。
// sh（write-and-push）を通らずに実行ファイルを直に起こすテストは、合流を git merge で済ませたあと、これで同じ記録を足してコミットする。
func RecordMerge(t testing.TB, parentTree, child string) {
	t.Helper()
	sha := git(t, parentTree, "rev-parse", child)
	for _, rel := range []string{
		".ccnavi/approved/done/" + child + ".md",
		".ccnavi/approved/doing/" + child + ".md",
		"wip/proposals/review/" + child + ".md",
	} {
		path := filepath.Join(parentTree, filepath.FromSlash(rel))
		raw, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		text := setField(string(raw), "merged_sha", sha)
		if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
		git(t, parentTree, "add", "--", rel)
		staged := exec.Command("git", "diff", "--cached", "--quiet")
		staged.Dir = parentTree
		if staged.Run() != nil {
			git(t, parentTree, "commit", "--quiet", "-m", "merged_sha "+child)
		}
		return
	}
}

// Field は SetFields が書き換える frontmatter の欄 1 つ。
type Field struct{ Key, Value string }

// setField は frontmatter の欄 1 つを、行単位で書き換える（無ければ閉じの `---` の前に足す）。
// 値は `"…"` で囲む（実行ファイルが書く形と同じ）。
func setField(text, key, value string) string {
	lines := ospath.Splitlines(text)
	end := -1
	for i := 1; i < len(lines); i++ {
		if lines[i] == "---" {
			end = i
			break
		}
	}
	if end < 0 {
		panic("samples.SetFields: frontmatter の閉じが無い")
	}
	line := key + `: "` + value + `"`
	set := false
	for i := 1; i < end; i++ {
		if head, _, _ := strings.Cut(lines[i], ":"); head == key {
			lines[i], set = line, true
			break
		}
	}
	if !set {
		lines = append(lines[:end], append([]string{line}, lines[end:]...)...)
	}
	out := strings.Join(lines, "\n")
	if strings.HasSuffix(text, "\n") {
		out += "\n"
	}
	return out
}

// SetFields は frontmatter の欄を、行単位で書き換える（欄ごとに）。手で動かした形を作る準備に使う。
func SetFields(text string, fields ...Field) string {
	for _, f := range fields {
		text = setField(text, f.Key, f.Value)
	}
	return text
}
