package base

import (
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"ccnavi/internal/infra/ospath"
)

// CommonSh は保護済み sh が起動して最初に `.` で読む共通部のファイル名（入口の `ccnavi-common.sh` と、入口が同じ
// ディレクトリから読む部品 `ccnavi-common-*.sh`）。sh を使い捨ての木へコピーするテストは、名前を並べずにこれで全部を
// 一緒にコピーする。部品は入口が `$0` のディレクトリから読むので、1 本でも欠けると sh は起動の段階で失敗する。
func CommonSh(t testing.TB, scriptsDir string) []string {
	t.Helper()
	paths, err := filepath.Glob(filepath.Join(scriptsDir, "ccnavi-common*.sh"))
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	hasEntry := false
	for _, p := range paths {
		name := filepath.Base(p)
		hasEntry = hasEntry || name == "ccnavi-common.sh"
		names = append(names, name)
	}
	if !hasEntry {
		t.Fatalf("%s が無い", filepath.Join(scriptsDir, "ccnavi-common.sh"))
	}
	return names // Glob は辞書順
}

// PutCCNaviMD は作業ディレクトリに、このリポジトリの `.ccnavi/CCNAVI.md` をコピーする（セッション開始で渡す作業の決まりの
// 原本）。保護対象の下書き（wip/handoff/）があれば、取り込んだ後の姿として下書きを読む。
func PutCCNaviMD(t testing.TB, root string) string {
	t.Helper()
	target := filepath.Join(root, projectHome, "CCNAVI.md")
	data, err := os.ReadFile(Final(".ccnavi/CCNAVI.md"))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(target, data, 0o644); err != nil {
		t.Fatal(err)
	}
	return target
}

// PutMessages は作業ディレクトリに、このリポジトリの文面ファイル（`.ccnavi/messages/`）をコピーする。
// 文面は文面ファイルが持つので、文を見るテストの使い捨てのワークスペースには要る。無くても判定は変わらず、文が短い既定の文に落ちる。
func PutMessages(t testing.TB, root string) string {
	t.Helper()
	source := filepath.Join(Root(), projectHome, "messages")
	target := filepath.Join(root, projectHome, "messages")
	err := filepath.WalkDir(source, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(source, path)
		if err != nil {
			return err
		}
		dest := filepath.Join(target, rel)
		if d.IsDir() {
			return os.MkdirAll(dest, 0o755)
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(dest, data, 0o644)
	})
	if err != nil {
		t.Fatal(err)
	}
	return target
}

// 文面に置くパスを引用せずに書ける字（実行ファイルの案内と同じ決め方）。
var (
	barePath         = regexp.MustCompile("^[^\\s'\"\\\\$`!*?\\[\\]{}()<>|&;#~]+$")
	specialInDoubles = regexp.MustCompile("[\"\\\\$`!]")
)

// quoted は要るときだけ引用する。まず `"…"`、`"` の中でも意味を持つ字があるときは `'…'`。
func quoted(path string) string {
	switch {
	case barePath.MatchString(path):
		return path
	case !specialInDoubles.MatchString(path):
		return `"` + path + `"`
	}
	return "'" + strings.ReplaceAll(path, "'", `'"'"'`) + "'"
}

// ScriptCommand は文面が案内する `.ccnavi/scripts/` の sh の打ち方（`sh <ルートの実パス>/.ccnavi/scripts/<name>`）。
// 区切りは `/`。空白やシェルの記号を含むときだけ引用する。実行ファイルの書き方と食い違えば、案内の文を比べるテストが落ちる。
// 実行ファイルの書き方を呼ばずに、独立に組む。
func ScriptCommand(root, name string) string {
	base := strings.TrimRight(strings.ReplaceAll(realpath(root), `\`, "/"), "/")
	return "sh " + quoted(base+"/"+projectHome+"/scripts/"+name)
}

var windowsDrive = regexp.MustCompile(`^[A-Za-z]:\\`)

// ShellPath は文面が `cd <パス> && …` の形で案内するパス。区切りは Windows のパスだけ `/` に揃える。
func ShellPath(path string) string {
	if ospath.Windows || windowsDrive.MatchString(path) {
		path = strings.ReplaceAll(path, `\`, "/")
	}
	return quoted(path)
}
