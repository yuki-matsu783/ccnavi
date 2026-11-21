package core

import (
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
)

// ユーザとエージェントに届く文面が、日本語のまま書かれていることの検出（#561）。
// ASCII だけの文は、英語のまま出ている。コード・識別子・コマンド・パスは英数字だけで書かれるので、
// 文として読める長さ（英単語 4 語以上、かつ空白で区切った語に小文字の英単語が混じる）で拾う。
// 内部エラーの文は go/internal/hook/reasons の go test が文ごと見る。

var (
	englishSentence = regexp.MustCompile(`\b[A-Za-z]{2,}(?:\s+[A-Za-z']{2,}){3,}`)
	japanese        = regexp.MustCompile("[぀-ヿ一-鿿]")
)

// englishIn は日本語を含まない行のうち、英語の文に見えるもの。
func englishIn(text string) []string {
	var lines []string
	for _, line := range strings.Split(text, "\n") {
		if englishSentence.MatchString(line) && !japanese.MatchString(line) {
			lines = append(lines, line)
		}
	}
	return lines
}

// response は `ccnavi --test Bash <command>` が返す文（`response:` より後）。
func response(t *testing.T, root, command string) string {
	t.Helper()
	done := run.Ccnavi(t, []string{"--root", root, "--mode", "enable", "--test", "Bash", command}, run.Opts{Dir: root})
	_, after, _ := strings.Cut(done.Stdout, "\nresponse:\n")
	return after
}

func TestMessageLanguageDetectorFindsAnEnglishSentence(t *testing.T) {
	t.Parallel()
	if len(englishIn("note: this is the approved area.")) == 0 || len(englishIn("案内: 承認済みの置き場です。")) != 0 {
		t.Error("英語の文の検出が合わない")
	}
}

func TestMessageLanguageRefusalTextsKeepTheirHints(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	deletes := response(t, root, "rm -rf x")
	mustContain(t, "削除の拒否", deletes, "rmdir", "scratchpad/")
	if got := englishIn(deletes); len(got) != 0 {
		t.Errorf("英語の行: %v", got)
	}
	writeFile(t, filepath.Join(root, ".ccnavi", "scripts"), "ccnavi-git.sh", "")
	said := response(t, root, "git log")
	mustContain(t, "git の拒否", said, "status・log・diff・show・blame", "log -5")
	if got := englishIn(said); len(got) != 0 {
		t.Errorf("英語の行: %v", got)
	}
}
