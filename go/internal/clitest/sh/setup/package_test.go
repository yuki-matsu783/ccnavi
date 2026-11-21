package setup

import (
	"archive/zip"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

// scripts/ccnavi-package.sh の受入テスト。ccnavi のソースに見立てた使い捨てのリポジトリを組み、そこで package を打つ。zip の中身は zip を読んで確かめる。
// 最後の 1 本は、zip を展開して本物の install（setup つき）で入れ、launcher から実行ファイルを起動するところまで通す。

const fakeExe = "#!/bin/sh\n" +
	"case \"$1\" in\n" +
	"--version)\n" +
	"\tprintf 'ccnavi 0.1.0\\ncommit: COMMIT\\nbuilt: yes\\ncompat: 18\\n'\n" +
	"\t;;\n" +
	"--lint)\n" +
	"\tprintf 'error 0 件、warn 0 件、info 0 件\\n'\n" +
	"\t;;\n" +
	"esac\n" +
	"exit 0\n"

var (
	chromeDir     = filepath.Join("extensions", "chrome", "ccnavi-approval")
	vscodeVersion = filepath.Join("src", "core", "version.ts")
)

type packageEnv struct {
	t          *testing.T
	base, root string
	out        string
}

func newPackage(t *testing.T, realCcnavi bool) *packageEnv {
	t.Helper()
	notWindows(t)
	haveCommands(t, "sh", "git", "jq", "zip", "unzip", "find", "cp", "sha256sum")
	p := &packageEnv{t: t, base: base.TempDir(t)}
	p.root = filepath.Join(p.base, "repo")
	p.out = filepath.Join(p.base, "out")
	p.buildRepo(p.root, realCcnavi)
	return p
}

// buildRepo は見立てのソース。
func (p *packageEnv) buildRepo(root string, realCcnavi bool) {
	t := p.t
	gitInit(t, root)
	for _, name := range []string{"package", "build", "setup", "install", "doctor"} {
		copyFile(t, scriptPath("ccnavi-"+name+".sh"), filepath.Join(root, "scripts", "ccnavi-"+name+".sh"), 0o755)
	}
	write(t, filepath.Join(root, ".gitignore"), "dist/\nbuild/\n")
	write(t, filepath.Join(root, "go", "cmd", "ccnavi", "main.go"), "package main\n")
	vsc := filepath.Join(root, "extensions", "vscode", "ccnavi-board")
	write(t, filepath.Join(vsc, "package.json"), `{"name":"ccnavi-board","version":"0.1.0"}`+"\n")
	write(t, filepath.Join(vsc, vscodeVersion), "export const EXTENSION_COMPAT = 18;\n")
	write(t, filepath.Join(root, chromeDir, "package.json"), `{"version":"0.4.0"}`+"\n")
	if realCcnavi {
		skip := map[string]bool{"bin": true, "logs": true, "wip": true, "scratchpad": true}
		copyTree(t, filepath.Join(base.Root(), ".ccnavi"), filepath.Join(root, ".ccnavi"), true, func(rel string) bool {
			return skip[filepath.Base(rel)]
		})
		remove(t, filepath.Join(root, ".ccnavi", "config", "project"))
	} else {
		write(t, filepath.Join(root, ".ccnavi", "CCNAVI.md"), "md\n")
		write(t, filepath.Join(root, ".ccnavi", "scripts", "ccnavi-x.sh"), "echo x\n")
		write(t, filepath.Join(root, ".ccnavi", "docs", "a.md"), "doc\n")
	}
	write(t, filepath.Join(root, ".ccnavi", "config", "common", "rules.yml"), "version: 1\n")
	write(t, filepath.Join(root, ".ccnavi", "config", "project", "phases.yml"), "mine\n")
	for _, junk := range []string{".ccnavi/scripts/ccnavi-index.jsonl", ".ccnavi/docs/.DS_Store", ".claude/skills/s/ccnavi-index.jsonl"} {
		write(t, filepath.Join(root, filepath.FromSlash(junk)), "x\n")
	}
	write(t, filepath.Join(root, ".claude", "skills", "s", "SKILL.md"), "skill\n")
	// zip がリンクを保つかを見る置き場。
	symlink(t, "SKILL.md", filepath.Join(root, ".claude", "skills", "s", "link.md"))
	for _, top := range []string{"logs", "wip", "tests"} {
		write(t, filepath.Join(root, top, "keep.txt"), "x\n")
	}
	git(t, root, "add", "-A")
	git(t, root, "commit", "-q", "-m", "init")
	p.buildDist(root)
}

func (p *packageEnv) buildDist(root string) {
	t := p.t
	head := git(t, root, "rev-parse", "HEAD")
	dist := filepath.Join(root, "dist")
	remove(t, dist)
	write(t, filepath.Join(dist, "ccnavi.target"), thisMachine()+"\n")
	writeExec(t, filepath.Join(dist, "ccnavi", "ccnavi"), strings.ReplaceAll(fakeExe, "COMMIT", head))
	write(t, filepath.Join(dist, "ccnavi-board-0.1.0.vsix"), "current")
	write(t, filepath.Join(dist, "ccnavi-board-0.0.9.vsix"), "old")
	cdist := filepath.Join(root, chromeDir, "dist")
	write(t, filepath.Join(cdist, "manifest.json"), `{"version":"0.4.0"}`+"\n")
	write(t, filepath.Join(cdist, "background.js"), `x({compat:18,version:"0.4.0"});`+"\n")
}

func (p *packageEnv) packageSh(o run.Opts, dir string, args ...string) run.Result {
	p.t.Helper()
	o.Dir = dir
	return runCmd(p.t, "sh", append([]string{filepath.Join(p.root, "scripts", "ccnavi-package.sh"), "--out", p.out}, args...), o)
}

func (p *packageEnv) pack(args ...string) run.Result { return p.packageSh(run.Opts{}, "", args...) }

func (p *packageEnv) zipPath() string {
	p.t.Helper()
	var found []string
	for _, n := range names(p.t, p.out) {
		if strings.HasSuffix(n, ".zip") {
			found = append(found, n)
		}
	}
	if len(found) != 1 {
		p.t.Fatalf("zip = %v", found)
	}
	return filepath.Join(p.out, found[0])
}

// members は zip の中の、先頭のディレクトリを除いた名前。
func (p *packageEnv) members() map[string]bool {
	p.t.Helper()
	z, err := zip.OpenReader(p.zipPath())
	if err != nil {
		p.t.Fatal(err)
	}
	defer z.Close()
	out := map[string]bool{}
	for _, f := range z.File {
		if _, rest, ok := strings.Cut(f.Name, "/"); ok && rest != "" {
			out[rest] = true
		}
	}
	return out
}

func (p *packageEnv) member(rel string) (top, text string) {
	p.t.Helper()
	z, err := zip.OpenReader(p.zipPath())
	if err != nil {
		p.t.Fatal(err)
	}
	defer z.Close()
	top = strings.SplitN(z.File[0].Name, "/", 2)[0]
	for _, f := range z.File {
		if f.Name == top+"/"+rel {
			rc, err := f.Open()
			if err != nil {
				p.t.Fatal(err)
			}
			defer rc.Close()
			var b strings.Builder
			buf := make([]byte, 4096)
			for {
				n, err := rc.Read(buf)
				b.Write(buf[:n])
				if err != nil {
					break
				}
			}
			return top, b.String()
		}
	}
	p.t.Fatalf("%s が zip に無い", rel)
	return
}

func (p *packageEnv) hasMember(substr string) bool {
	for m := range p.members() {
		if strings.Contains(m, substr) {
			return true
		}
	}
	return false
}

func (p *packageEnv) hasSuffixMember(suffix string) bool {
	for m := range p.members() {
		if strings.HasSuffix(m, suffix) {
			return true
		}
	}
	return false
}

// ---- 中身

func TestPackageContentsInAndOut(t *testing.T) {
	t.Parallel()
	p := newPackage(t, false)
	r := p.pack()
	code(t, r, 0)
	got := p.members()
	for _, want := range []string{
		"dist/ccnavi/ccnavi", "dist/ccnavi.target", "dist/ccnavi-board-0.1.0.vsix", "extensions/chrome/ccnavi-approval/dist/manifest.json",
		".ccnavi/CCNAVI.md", ".ccnavi/config/common/rules.yml", ".ccnavi/scripts/ccnavi-x.sh", ".ccnavi/docs/a.md", ".claude/skills/s/SKILL.md",
		"scripts/ccnavi-setup.sh", "scripts/ccnavi-install.sh", "scripts/ccnavi-doctor.sh", "VERSION", "INSTALL.md",
	} {
		if !got[want] {
			t.Errorf("%s が zip に無い", want)
		}
	}
	for _, unwanted := range []string{
		"dist/ccnavi-board-0.0.9.vsix", ".ccnavi/config/project/phases.yml", ".ccnavi/scripts/ccnavi-index.jsonl", ".ccnavi/docs/.DS_Store",
		".claude/skills/s/ccnavi-index.jsonl", "scripts/ccnavi-package.sh", "scripts/ccnavi-build.sh", "logs/keep.txt", "wip/keep.txt",
		"tests/keep.txt", "go/cmd/ccnavi/main.go",
	} {
		if got[unwanted] {
			t.Errorf("%s が zip に入っている", unwanted)
		}
	}
	eq(t, "次: の数", countNext(r.Stdout), 1)
}

func TestPackageVersionAndInstallMd(t *testing.T) {
	t.Parallel()
	p := newPackage(t, false)
	p.pack()
	top, version := p.member("VERSION")
	_, installMd := p.member("INSTALL.md")
	head := git(t, p.root, "rev-parse", "HEAD")
	in(t, version, "commit: "+head, "target: "+thisMachine(), "compat: 18")
	in(t, installMd, "shasum -a 256", "sha256sum", "certutil -hashfile", "すべて展開", "unzip", "--check", "~/.claude/skills/")
	if !strings.HasPrefix(top, "ccnavi-"+head[:7]+"-"+thisMachine()) {
		t.Errorf("先頭のディレクトリ = %s", top)
	}
}

// 拡張が無い・版が合わない・互換の版が違うときは、zip に入れず、そのことを言う。
func TestPackageMismatchedOrMissingExtensionsAreLeftOutWithANote(t *testing.T) {
	t.Parallel()
	t.Run("vsix が無く、Chrome の版が合わない", func(t *testing.T) {
		t.Parallel()
		p := newPackage(t, false)
		remove(t, filepath.Join(p.root, "dist", "ccnavi-board-0.1.0.vsix"))
		write(t, filepath.Join(p.root, chromeDir, "dist", "manifest.json"), `{"version":"0.3.0"}`+"\n")
		r := p.pack()
		code(t, r, 0)
		if p.hasSuffixMember(".vsix") || p.hasMember("ccnavi-approval") {
			t.Error("拡張を入れた")
		}
		in(t, r.Stdout, "VS Code 拡張", "Chrome 拡張")
	})
	chrome := map[string]string{
		"古い互換の版": `x({compat:17});` + "\n",
		"整数で比べる": `x({compat:180});` + "\n", // COMPAT=18 に対して compat:180 は別の版
	}
	for name, bundle := range chrome {
		t.Run("Chrome: "+name, func(t *testing.T) {
			t.Parallel()
			p := newPackage(t, false)
			write(t, filepath.Join(p.root, chromeDir, "dist", "background.js"), bundle)
			r := p.pack()
			if p.hasMember("ccnavi-approval") {
				t.Error("Chrome 拡張を入れた")
			}
			if name == "古い互換の版" {
				in(t, r.Stdout, "互換の版")
			}
		})
	}
	t.Run("vsix の互換の版が違う", func(t *testing.T) {
		t.Parallel()
		p := newPackage(t, false)
		write(t, filepath.Join(p.root, "extensions", "vscode", "ccnavi-board", vscodeVersion), "export const EXTENSION_COMPAT = 17;\n")
		r := p.pack()
		if p.hasSuffixMember(".vsix") {
			t.Error("vsix を入れた")
		}
		in(t, r.Stdout, "互換の版")
	})
}

// 出力先を使い回しても、古い zip に足さない。
func TestPackageReusingOutDoesNotAppendToTheOldZip(t *testing.T) {
	t.Parallel()
	p := newPackage(t, false)
	p.pack()
	remove(t, filepath.Join(p.root, "dist", "ccnavi-board-0.1.0.vsix"))
	r := p.pack()
	code(t, r, 0)
	if p.hasSuffixMember(".vsix") {
		t.Error("古い zip に足した")
	}
}

// コピーするディレクトリの中の秘密・実行時の残りは入れない。
func TestPackageSecretsAndRuntimeJunkInCopiedDirsAreLeftOut(t *testing.T) {
	t.Parallel()
	p := newPackage(t, false)
	junk := []string{".ccnavi/scripts/.env", ".ccnavi/docs/server.pem", ".claude/skills/s/credentials.json", ".claude/skills/s/logs/run.log", ".ccnavi/docs/wip/draft.md"}
	for _, rel := range junk {
		write(t, filepath.Join(p.root, filepath.FromSlash(rel)), "secret\n")
	}
	code(t, p.pack(), 0)
	got := p.members()
	for _, rel := range junk {
		if got[rel] {
			t.Errorf("%s が zip に入っている", rel)
		}
	}
}

func TestPackageADashNamedOutIsTakenAsAPath(t *testing.T) {
	t.Parallel()
	p := newPackage(t, false)
	r := runCmd(t, "sh", []string{filepath.Join(p.root, "scripts", "ccnavi-package.sh"), "--out", "-dd"}, run.Opts{Dir: p.base})
	code(t, r, 0)
	if !exists(filepath.Join(p.base, "-dd")) {
		t.Error("-dd を作っていない")
	}
}

// ---- 毎回作り直す・照合

func TestPackageSecondRunCarriesNothingFromTheFirst(t *testing.T) {
	t.Parallel()
	p := newPackage(t, false)
	p.pack()
	stale := write(t, filepath.Join(p.root, "build", "package", "stale-dir", "old.txt"), "x\n")
	leftover := write(t, filepath.Join(p.root, "build", "package", "ccnavi-old.zip"), "x\n")
	r := p.pack()
	code(t, r, 0)
	if exists(stale) || exists(leftover) {
		t.Error("前回の残りが消えていない")
	}
	if p.hasMember("stale") || p.hasMember("old.txt") {
		t.Error("前回の残りが zip に入った")
	}
}

// 組み立てたコミットと今のコミットが違えば 2 で止まり、--build を案内する。zip は作らない。
func TestPackageCommitMismatchIs2(t *testing.T) {
	t.Parallel()
	p := newPackage(t, false)
	write(t, filepath.Join(p.root, "newfile.txt"), "x\n")
	git(t, p.root, "add", "-A")
	git(t, p.root, "commit", "-q", "-m", "next")
	r := p.pack()
	code(t, r, 2)
	in(t, r.Stderr, "--build")
	if exists(p.out) && len(names(t, p.out)) != 0 {
		t.Error("zip を作った")
	}
}

func TestPackageMissingTargetOrExecutableIs2(t *testing.T) {
	t.Parallel()
	p := newPackage(t, false)
	remove(t, filepath.Join(p.root, "dist", "ccnavi", "ccnavi"))
	code(t, p.pack(), 2)
	p.buildDist(p.root)
	remove(t, filepath.Join(p.root, "dist", "ccnavi.target"))
	code(t, p.pack(), 2)
}

func TestPackageDirtyTreeMarksTheName(t *testing.T) {
	t.Parallel()
	p := newPackage(t, false)
	write(t, filepath.Join(p.root, ".ccnavi", "CCNAVI.md"), "changed\n")
	r := p.pack()
	code(t, r, 0)
	if !strings.HasSuffix(p.zipPath(), "-dirty.zip") {
		t.Errorf("zip の名前 = %s", p.zipPath())
	}
	in(t, r.Stdout, "-dirty")
}

func TestPackageSha256IsWrittenAndMatches(t *testing.T) {
	t.Parallel()
	p := newPackage(t, false)
	p.pack()
	zipped := p.zipPath()
	if !exists(zipped + ".sha256") {
		t.Fatal(".sha256 が無い")
	}
	r := runCmd(t, "sha256sum", []string{"-c", filepath.Base(zipped) + ".sha256"}, run.Opts{Dir: p.out})
	code(t, r, 0)
}

func TestPackageBadArgumentsExit2(t *testing.T) {
	t.Parallel()
	code(t, newPackage(t, false).pack("--nope"), 2)
}

// ---- zip の作り方（zip が無いときの予備: python3 -m zipfile）

// isolatedPathWithoutZip は zip の無い PATH。python3 は予備のために置く。
func (p *packageEnv) isolatedPathWithoutZip() run.Opts {
	p.t.Helper()
	haveCommands(p.t, "python3")
	if !base.CanSymlink() {
		p.t.Skip("シンボリックリンクを作れない")
	}
	bin := mkdir(p.t, filepath.Join(p.base, "nozip"))
	for _, name := range []string{
		"sh", "git", "jq", "find", "cp", "rm", "mkdir", "sed", "tr", "cut", "grep", "cat",
		"sha256sum", "dirname", "basename", "uname", "date", "ls", "chmod", "head", "python3",
	} {
		if real, err := exec.LookPath(name); err == nil {
			symlink(p.t, real, filepath.Join(bin, name))
		}
	}
	return run.Opts{Env: map[string]string{env.Path: bin}}
}

// zip が無いときは python3 の zipfile で作る。リンクを保てないので、リンクがあれば 2 で断り、無ければ作る。
// （この環境の rm は PATH の次の rm を呼ぶ安全確認のラッパーで、PATH を bin だけにすると rm が見つからず 127 で落ちる。）
func TestPackagePythonZipfileFallback(t *testing.T) {
	t.Parallel()
	t.Run("リンクがあれば断る", func(t *testing.T) {
		t.Parallel()
		p := newPackage(t, false)
		r := p.packageSh(p.isolatedPathWithoutZip(), "")
		code(t, r, 2)
		in(t, r.Stderr, "シンボリックリンク")
	})
	t.Run("リンクが無ければ作る", func(t *testing.T) {
		t.Parallel()
		p := newPackage(t, false)
		remove(t, filepath.Join(p.root, ".claude", "skills", "s", "link.md"))
		r := p.packageSh(p.isolatedPathWithoutZip(), "")
		code(t, r, 0)
		if !p.members()["dist/ccnavi/ccnavi"] {
			t.Error("dist/ccnavi/ccnavi が zip に無い")
		}
	})
}

// ---- zip → 展開 → 入れる → launcher

func TestPackageZipUnzipInstallAndLaunch(t *testing.T) {
	t.Parallel()
	p := newPackage(t, true)
	r := p.pack()
	code(t, r, 0)
	unpacked := mkdir(t, filepath.Join(p.base, "unpacked"))
	if u := runCmd(t, "unzip", []string{"-q", p.zipPath(), "-d", unpacked}, run.Opts{}); u.Code != 0 {
		t.Fatalf("unzip: %s", u.Stderr)
	}
	folder := filepath.Join(unpacked, names(t, unpacked)[0])
	link := filepath.Join(folder, ".claude", "skills", "s", "link.md")
	if to, err := os.Readlink(link); err != nil || to != "SKILL.md" {
		t.Errorf("リンクが保たれていない: %q, %v", to, err)
	}
	ws := filepath.Join(p.base, "ws")
	gitInit(t, ws)
	home := mkdir(t, filepath.Join(p.base, "home"))
	o := run.Opts{Env: map[string]string{env.Home: home}, Unset: []string{"CLAUDECODE"}}
	installer := filepath.Join(folder, "scripts", "ccnavi-install.sh")
	checked := runCmd(t, "sh", []string{installer, ws, "--from", folder, "--check"}, o)
	code(t, checked, 1)
	real := runCmd(t, "sh", []string{installer, ws, "--from", folder}, o)
	code(t, real, 0)
	eq(t, "次: の数", countNext(real.Stdout), 1)
	launcher := filepath.Join(ws, ".ccnavi", "scripts", "ccnavi-launcher.sh")
	version := runCmd(t, "sh", []string{launcher, "--version"}, run.Opts{})
	in(t, version.Stdout, "commit: "+git(t, p.root, "rev-parse", "HEAD"))
}
