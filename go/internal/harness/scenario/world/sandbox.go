package world

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"ccnavi/internal/infra/platformtag"
)

// 使い捨てのワークスペースにコピーする物（リポジトリの根からの相対）。docs/wiki は、ccnavi がセッションの開始時に
// 学びの案内を作るときに読む。
var sandboxCopied = []string{".ccnavi", ".claude/settings.json", ".claude/hooks", ".gitignore", "docs/wiki"}

// 受入シナリオが前提とする test-repo の README の見出し（UC-01 の台本が書き換える行）。
const sandboxReadme = "# 演習: テキスト整形CLI\n\n受入シナリオの使い捨てのワークスペースの見本。\n"

// SandboxPrefix は使い捨てのワークスペースの親の名前の頭。
const SandboxPrefix = "ccnavi-stub-ws-"

// MakeSandboxBase は使い捨てのワークスペースの親を作る。
func MakeSandboxBase() (string, error) { return os.MkdirTemp("", SandboxPrefix) }

// BuildSandbox は base の下にワークスペース（base/ws）を組み、そのルートを返す。スタブで回すシナリオ用で、
// ccnavi の hook が動くのに要るものだけを置く。ユーザのワークスペースには触れない。
//
//   - repoRoot（このリポジトリの作業ツリー）から、.ccnavi・.claude/settings.json・.claude/hooks・.gitignore・docs/wiki を
//     コピーし、git のリポジトリにする。コピーするのは、追跡しているファイルと、まだ add していないファイル
//   - ccnaviExe（組んだ ccnavi の実行ファイル）を .ccnavi/bin/<os>-<arch>/ に置く
//   - settings.json の CCNAVI_MODE を enable にする（この使い捨てのワークスペースの中だけ）
//   - projects/test-repo に、README を 1 本持つ小さなリポジトリを置く
func BuildSandbox(repoRoot, base, ccnaviExe string) (string, error) {
	ws := filepath.Join(base, "ws")
	files, err := trackedFiles(repoRoot, sandboxCopied)
	if err != nil {
		return "", err
	}
	for _, rel := range files {
		if err := copyFile(filepath.Join(repoRoot, rel), filepath.Join(ws, rel)); err != nil {
			return "", err
		}
	}
	if err := enableMode(filepath.Join(ws, ".claude", "settings.json")); err != nil {
		return "", err
	}
	dst := filepath.Join(ws, ".ccnavi", "bin", platformtag.HostTarget(), filepath.Base(ccnaviExe))
	if err := copyFile(ccnaviExe, dst); err != nil {
		return "", err
	}
	if err := os.Chmod(dst, 0o755); err != nil {
		return "", err
	}
	if err := commitAll(ws); err != nil {
		return "", err
	}
	repo := filepath.Join(ws, filepath.FromSlash("projects/test-repo"))
	if err := os.MkdirAll(repo, 0o755); err != nil {
		return "", err
	}
	if err := os.WriteFile(filepath.Join(repo, "README.md"), []byte(sandboxReadme), 0o644); err != nil {
		return "", err
	}
	return ws, commitAll(repo)
}

func trackedFiles(repoRoot string, paths []string) ([]string, error) {
	args := append([]string{"-C", repoRoot, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--"}, paths...)
	r := Git(args, hostOpts(""))
	if r.Code != 0 {
		return nil, fmt.Errorf("git ls-files が終了コード %d: %s", r.Code, strings.TrimSpace(r.Stderr))
	}
	var out []string
	for _, p := range strings.Split(r.Stdout, "\x00") {
		if p == "" {
			continue
		}
		if info, err := os.Stat(filepath.Join(repoRoot, p)); err == nil && info.Mode().IsRegular() {
			out = append(out, p)
		}
	}
	return out, nil
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	info, err := in.Stat()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		return err
	}
	f, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, info.Mode().Perm()|0o200)
	if err != nil {
		return err
	}
	_, err = io.Copy(f, in)
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		return err
	}
	return os.Chmod(dst, info.Mode().Perm())
}

func enableMode(path string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var doc map[string]any
	if err := json.Unmarshal(data, &doc); err != nil {
		return err
	}
	env, _ := doc["env"].(map[string]any)
	if env == nil {
		env = map[string]any{}
	}
	env[ModeEnv] = "enable"
	doc["env"] = env
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(doc); err != nil {
		return err
	}
	return os.WriteFile(path, buf.Bytes(), 0o644)
}

func commitAll(dir string) error {
	pre := []string{"-C", dir, "-c", "user.name=ccnavi-test", "-c", "user.email=test@invalid", "-c", "commit.gpgsign=false"}
	for _, args := range [][]string{
		{"init", "-q", "-b", "main"},
		{"add", "-A"},
		{"commit", "-q", "-m", "使い捨てのワークスペース"},
	} {
		cmd := exec.Command("git", append(append([]string{}, pre...), args...)...)
		cmd.Env = DropGitVars(os.Environ())
		if raw, err := cmd.CombinedOutput(); err != nil {
			return fmt.Errorf("git %s: %v: %s", args[0], err, strings.TrimSpace(string(raw)))
		}
	}
	return nil
}
