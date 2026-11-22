package core

import (
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
)

// 配布する文書・スキルが、PATH に無い `ccnavi` を直に打たせていないか（#786）。
// 配布先では `ccnavi` が PATH に無く、起動の sh 経由でしか動かない。手順として書く `ccnavi --…` と `ccnavi ticket …` は、この形にする。

// distributed は scripts/ccnavi-package.sh が配る、エージェントが読む場所。
var distributed = []string{".claude/skills", ".ccnavi/docs", ".ccnavi/messages", ".ccnavi/CCNAVI.md"}

// bareCall はコード片（`…`・'…'）か行頭に置いた、PATH 前提の呼び出し。
var bareCall = regexp.MustCompile("(?:^|[\\s`'（(])ccnavi (?:--[a-z]|ticket )")

func TestDistributedFilesHaveNoBareCCNaviCall(t *testing.T) {
	t.Parallel()
	var found []string
	scan := func(path string) {
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		rel, _ := filepath.Rel(base.Root(), path)
		for i, line := range strings.Split(string(data), "\n") {
			if bareCall.MatchString(line) {
				found = append(found, filepath.ToSlash(rel)+":"+strconv.Itoa(i+1))
			}
		}
	}
	for _, rel := range distributed {
		root := filepath.Join(base.Root(), filepath.FromSlash(rel))
		info, err := os.Stat(root)
		if err != nil {
			t.Fatal(err)
		}
		if !info.IsDir() {
			scan(root)
			continue
		}
		err = filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
			if err == nil && !d.IsDir() && (strings.HasSuffix(path, ".md") || strings.HasSuffix(path, ".yml")) {
				scan(path)
			}
			return err
		})
		if err != nil {
			t.Fatal(err)
		}
	}
	if len(found) != 0 {
		t.Errorf("PATH 前提の `ccnavi …` は `sh .ccnavi/scripts/ccnavi-launcher.sh …` に直す: %v", found)
	}
}
