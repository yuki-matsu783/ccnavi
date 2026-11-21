package core

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
)

// `--lint` が `.ccnavi/scripts/` の sh の互換の版の食い違いを言うこと。
// sh は `ccnavi-common.sh` の `CCNAVI_COMPAT=<数>` で互換の版を宣言する。実行ファイルの互換の版と違えば `(version)` の warn で言い、
// 直し方（ccnavi のリポジトリなら組み立て直し、配布先なら配り直し）を名指しする。sh の無いワークスペースでは言わない。

type problem struct{ Severity, Where, Detail string }

func lintProblems(t *testing.T, root string) []problem {
	t.Helper()
	r := ccnavi(t, root, nil, "--lint", "--json", "--mode", "enable")
	if r.Code != 0 && r.Code != 1 {
		t.Fatalf("code = %d: %s", r.Code, r.Stderr)
	}
	var body struct{ Problems []problem }
	if err := json.Unmarshal([]byte(r.Stdout), &body); err != nil {
		t.Fatal(err)
	}
	var out []problem
	for _, p := range body.Problems {
		if p.Where == "(version)" {
			out = append(out, p)
		}
	}
	return out
}

func putCompatSh(t *testing.T, root, line string) {
	t.Helper()
	writeFile(t, root, ".ccnavi/scripts/ccnavi-common.sh", "# ccnavi-common\n"+line+"\nccnavi_abs() { :; }\n")
}

func compatOf(t *testing.T) int {
	t.Helper()
	n, err := strconv.Atoi(exe.Compat(t))
	if err != nil {
		t.Fatal(err)
	}
	return n
}

func TestLintShCompat(t *testing.T) {
	t.Parallel()
	t.Run("互換の版が合う sh は何も言わない", func(t *testing.T) {
		t.Parallel()
		root := emptyRoot(t)
		putCompatSh(t, root, fmt.Sprintf("CCNAVI_COMPAT=%d", compatOf(t)))
		if got := lintProblems(t, root); len(got) != 0 {
			t.Errorf("problems = %v", got)
		}
	})
	t.Run("sh の無いワークスペース（試しの置き場）では言わない", func(t *testing.T) {
		t.Parallel()
		if got := lintProblems(t, emptyRoot(t)); len(got) != 0 {
			t.Errorf("problems = %v", got)
		}
	})
	t.Run("違う互換の版は warn で、配布先では配り直しを名指しする", func(t *testing.T) {
		t.Parallel()
		root := emptyRoot(t)
		c := compatOf(t)
		putCompatSh(t, root, fmt.Sprintf("CCNAVI_COMPAT=%d", c+1))
		got := lintProblems(t, root)
		if len(got) != 1 || got[0].Severity != "warn" {
			t.Fatalf("problems = %v", got)
		}
		mustContain(t, "detail", got[0].Detail, fmt.Sprintf("互換 %d", c+1), fmt.Sprintf("実行ファイルは互換 %d", c), "scripts/ccnavi-setup.sh")
	})
	t.Run("互換の版を宣言しない sh は互換の版を持つ前の sh", func(t *testing.T) {
		t.Parallel()
		root := emptyRoot(t)
		putCompatSh(t, root, "# nothing")
		got := lintProblems(t, root)
		if len(got) != 1 || got[0].Severity != "warn" {
			t.Fatalf("problems = %v", got)
		}
		mustContain(t, "detail", got[0].Detail, "CCNAVI_COMPAT")
	})
	t.Run("ccnavi のリポジトリでは直し方は組み立て直し", func(t *testing.T) {
		t.Parallel()
		root := emptyRoot(t)
		putCompatSh(t, root, fmt.Sprintf("CCNAVI_COMPAT=%d", compatOf(t)+1))
		writeFile(t, root, "scripts/ccnavi-build.sh", "")
		writeFile(t, root, "go/cmd/ccnavi/main.go", "")
		got := lintProblems(t, root)
		if len(got) != 1 {
			t.Fatalf("problems = %v", got)
		}
		mustContain(t, "detail", got[0].Detail, "scripts/ccnavi-build.sh を実行して組み立て直して")
		mustNotContain(t, "detail", got[0].Detail, "ccnavi-setup.sh")
	})
	t.Run("このリポジトリの sh と実行ファイルは揃っている", func(t *testing.T) {
		t.Parallel()
		if got := lintProblems(t, base.Root()); len(got) != 0 {
			t.Errorf("problems = %v", got)
		}
		data, err := os.ReadFile(filepath.Join(base.Root(), ".ccnavi", "scripts", "ccnavi-common.sh"))
		if err != nil {
			t.Fatal(err)
		}
		want := regexp.MustCompile(fmt.Sprintf(`(?m)^CCNAVI_COMPAT=%d$`, compatOf(t)))
		if !want.Match(data) {
			t.Errorf("ccnavi-common.sh に CCNAVI_COMPAT=%d の行が無い", compatOf(t))
		}
	})
}
