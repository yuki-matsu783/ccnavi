package flow

// Chrome の入口（go/cmd/ccnavi-chrome）を、手元のツリーから組んだ要求で叩く道具。
// 元: tests/ticket/test_swap_points.py の CoreHarness（trees・disk・diff・chrome_request・ask_chrome）。

import (
	"encoding/json"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/samples"
)

const (
	chromeStamp = "2026-09-29T12:00:00+0900"
	zeroHead    = "0000000000000000000000000000000000000000"
)

var chromePlaces = []string{".ccnavi", "wip"}

// compatBump は互換の版を 1 つ進めた値（統合先の互換のマーカーが違う形）。
func compatBump(t testing.TB) string {
	t.Helper()
	n, err := strconv.Atoi(exe.Compat(t))
	if err != nil {
		t.Fatal(err)
	}
	return strconv.Itoa(n + 1)
}

// putCompat は統合先の互換のマーカー（Chrome は版が違えば書く操作を受けない）を置く。
func (tr *Tree) putCompat(value string) {
	tr.T.Helper()
	Write(tr.T, filepath.Join(tr.Root, filepath.FromSlash(samples.CompatFile)), "#!/bin/sh\nCCNAVI_COMPAT="+value+"\n")
}

// treeFiles はツリーの置き場のファイル（相対パス → 中身）。
func treeFiles(t testing.TB, treeRoot string, prefixes []string) map[string]string {
	t.Helper()
	found := map[string]string{}
	for _, prefix := range prefixes {
		_ = filepath.WalkDir(filepath.Join(treeRoot, prefix), func(p string, d fs.DirEntry, err error) error {
			if err != nil {
				return nil
			}
			if d.IsDir() && d.Name() == ".git" {
				return filepath.SkipDir
			}
			if !d.IsDir() {
				rel, _ := filepath.Rel(treeRoot, p)
				raw, err := os.ReadFile(p)
				if err != nil {
					t.Fatal(err)
				}
				found[filepath.ToSlash(rel)] = string(raw)
			}
			return nil
		})
	}
	return found
}

// trees はブランチの名前 → ツリーのルート。ワークスペースルートは main。
func (tr *Tree) trees() map[string]string {
	tr.T.Helper()
	found := map[string]string{"main": tr.Root}
	entries, err := os.ReadDir(filepath.Join(tr.Root, ".claude", "worktrees"))
	if err != nil {
		tr.T.Fatal(err)
	}
	for _, e := range entries {
		found[e.Name()] = filepath.Join(tr.Root, ".claude", "worktrees", e.Name())
	}
	return found
}

func (tr *Tree) disk() map[string]map[string]string {
	tr.T.Helper()
	out := map[string]map[string]string{}
	for name, path := range tr.trees() {
		out[name] = treeFiles(tr.T, path, chromePlaces)
	}
	return out
}

// diffRow は Changes の 1 行と同じ形（op・path・content）。
type diffRow struct{ Op, Path, Content string }

// diffDisk は 2 つの disk の違いを、ブランチごとの Changes と同じ形で返す（中身は本文。CRLF は LF）。
func diffDisk(before, after map[string]map[string]string, branch string) []diffRow {
	old, now := before[branch], after[branch]
	var paths []string
	for p := range old {
		paths = append(paths, p)
	}
	for p := range now {
		if _, ok := old[p]; !ok {
			paths = append(paths, p)
		}
	}
	slices.Sort(paths)
	var rows []diffRow
	for _, p := range paths {
		o, hadOld := old[p]
		n, hasNew := now[p]
		switch {
		case hadOld && !hasNew:
			rows = append(rows, diffRow{"delete", p, ""})
		case !hadOld:
			rows = append(rows, diffRow{"create", p, strings.ReplaceAll(n, "\r\n", "\n")})
		case o != n:
			rows = append(rows, diffRow{"update", p, strings.ReplaceAll(n, "\r\n", "\n")})
		}
	}
	return rows
}

// chromeRequest は手元のツリーから、拡張が組むのと同じ形の要求を作る（統合先は main）。
// ブランチの先頭は既定で 0 を並べたもの。heads で名前ごとに実際の先頭を渡せる（レビュー済みは依頼時の先頭と比べる）。
func (tr *Tree) chromeRequest(op, family string, heads map[string]string, extra map[string]any) map[string]any {
	tr.T.Helper()
	place := samples.ChromePlacement(tr.T, exe.ChromePath(tr.T))
	branches := map[string]any{}
	for name, path := range tr.trees() {
		files := map[string]string{}
		if name == "main" {
			var wanted []string
			for _, p := range jl(place, "integration_paths") {
				wanted = append(wanted, p.(string)+"/")
			}
			integrationFiles := strs(jl(place, "integration_files"))
			for rel, body := range treeFiles(tr.T, path, []string{".ccnavi", ".claude"}) {
				if hasAnyPrefix(rel, wanted) || slices.Contains(integrationFiles, rel) {
					files[rel] = body
				}
			}
		} else {
			var wanted []string
			for _, p := range jl(place, "branch_paths") {
				wanted = append(wanted, p.(string)+"/")
			}
			for rel, body := range treeFiles(tr.T, path, chromePlaces) {
				if hasAnyPrefix(rel, wanted) {
					files[rel] = body
				}
			}
		}
		head := zeroHead
		if h, ok := heads[name]; ok {
			head = h
		}
		branches[name] = map[string]any{"head": head, "files": files}
	}
	request := map[string]any{
		"schema": samples.ChromeSchema, "op": op, "family": family, "stamp": chromeStamp,
		"snapshot": map[string]any{
			"integration": map[string]any{"name": "main", "source": "setting", "head": zeroHead},
			"branches":    branches,
			"absent":      []any{},
		},
	}
	for k, v := range extra {
		request[k] = v
	}
	return request
}

func hasAnyPrefix(s string, prefixes []string) bool {
	for _, p := range prefixes {
		if strings.HasPrefix(s, p) {
			return true
		}
	}
	return false
}

// answer は Chrome の入口の答え（error も含めてそのまま）。
func (tr *Tree) answer(request map[string]any) map[string]any {
	tr.T.Helper()
	raw, err := json.Marshal(request)
	if err != nil {
		tr.T.Fatal(err)
	}
	return ParseJSON(tr.T, samples.ChromeHandle(tr.T, exe.ChromePath(tr.T), string(raw), filepath.Join(tr.Root, "memfs")))
}

// askChrome は error の無い答えを返す。
func (tr *Tree) askChrome(request map[string]any) map[string]any {
	tr.T.Helper()
	a := tr.answer(request)
	if _, bad := a["error"]; bad {
		tr.T.Fatalf("Chrome の入口が error を返した: %v", a["error"])
	}
	return a
}

// isNull は答えが key を持ち、その値が null であること（Python の assertIsNone(answer[key]) に当たる）。
func isNull(m map[string]any, key string) bool {
	v, ok := m[key]
	return ok && v == nil
}

// reviewedPhaseOne はフェーズ 1（MR で見る設計）の子を閉じて合流し、レビューを依頼したところまで進める。
func (tr *Tree) reviewedPhaseOne() Fixture { return tr.Phase1Requested() }

// applyChanges は Chrome の Changes を 1 コミットとしてツリーに書く（拡張の書き込みの代わり）。
func (tr *Tree) applyChanges(rows []any, treeRoot string) {
	tr.T.Helper()
	for _, row := range rows {
		path := filepath.Join(treeRoot, filepath.FromSlash(js(row, "path")))
		if js(row, "op") == "delete" {
			removeFile(tr.T, path)
		} else {
			Write(tr.T, path, js(row, "content"))
		}
	}
	Git(tr.T, treeRoot, "add", "-A")
	Git(tr.T, treeRoot, "commit", "--quiet", "-m", "ccnavi: 承認（Chrome 拡張）")
}

// changeRow は Changes の行から、末尾が suffix の path を探す。
func changeRow(rows []any, suffix string) map[string]any {
	for _, r := range rows {
		if strings.HasSuffix(js(r, "path"), suffix) {
			return jm(r)
		}
	}
	return nil
}
