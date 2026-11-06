package fsio

import (
	"bytes"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"slices"
	"strings"
	"syscall"
	"testing"
	"time"

	"ccnavi/internal/infra/pyjson"
)

// noSleep は打ち直しの待ちを消す。
func noSleep(t *testing.T) {
	t.Helper()
	before := sleep
	sleep = func(time.Duration) {}
	t.Cleanup(func() { sleep = before })
}

// swap は差し替え点を t の間だけ置き換える。
func swap[T any](t *testing.T, target *T, value T) {
	t.Helper()
	before := *target
	*target = value
	t.Cleanup(func() { *target = before })
}

func names(t *testing.T, dir string) []string {
	t.Helper()
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	var out []string
	for _, e := range entries {
		out = append(out, e.Name())
	}
	return out
}

func readAll(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

// 並列に走る hook が同じ記録を置き換えている最中の失敗（EINVAL・EACCES）だけを数回打ち直す。
func TestWriteRetriesOnlyTransientFailures(t *testing.T) {
	noSleep(t)
	cases := []struct {
		name      string
		errno     syscall.Errno
		failures  int
		wantCalls int
		wantOK    bool
	}{
		{"一時的な失敗は打ち直す", syscall.EINVAL, 2, 3, true},
		{"共有違反も打ち直す", syscall.EACCES, 1, 2, true},
		{"続く失敗は上限で理由を返す", syscall.EINVAL, 99, writeRetries + 1, false},
		{"待っても変わらない失敗はすぐ返す", syscall.ENOSPC, 99, 1, false},
		{"EPERM は本物の権限エラーなので打ち直さない", syscall.EPERM, 99, 1, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if runtime.GOOS == "windows" {
				t.Skip("errno の番号は POSIX のもの")
			}
			path := filepath.Join(t.TempDir(), "nested", "backup")
			calls, left := 0, c.failures
			swap(t, &openFile, func(name string, flag int, perm fs.FileMode) (*os.File, error) {
				calls++
				if left > 0 {
					left--
					return nil, &fs.PathError{Op: "open", Path: name, Err: c.errno}
				}
				return os.OpenFile(name, flag, perm)
			})
			failed := WriteText(path, "x\n", LF, Plain)
			if (failed == "") != c.wantOK || calls != c.wantCalls {
				t.Fatalf("failed=%q calls=%d", failed, calls)
			}
			if c.wantOK && readAll(t, path) != "x\n" {
				t.Fatal("中身が違う")
			}
			if !c.wantOK && ospathExists(path) {
				t.Fatal("書けなかったのにファイルがある")
			}
		})
	}
}

func ospathExists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

// 差し替えの一瞬に重なっただけの読みは打ち直す（打ち直さないと数えが 0 に戻る）。無い・壊れているは打ち直さない。
func TestReadJSONRetriesOnlyTransientFailures(t *testing.T) {
	noSleep(t)
	cases := []struct {
		name      string
		errno     syscall.Errno
		failures  int
		wantCalls int
		wantOK    bool
	}{
		{"一時的な失敗は打ち直す", syscall.EACCES, 2, 3, true},
		{"続く失敗は上限で返す", syscall.EACCES, 99, readRetries + 1, false},
		{"EPERM は打ち直さない", syscall.EPERM, 99, 1, false},
		{"無いのは待っても現れない", syscall.ENOENT, 99, 1, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if runtime.GOOS == "windows" {
				t.Skip("errno の番号は POSIX のもの")
			}
			path := filepath.Join(t.TempDir(), "once-abc-def.json")
			os.WriteFile(path, []byte(`{"given": ["a"]}`), 0o644)
			calls, left := 0, c.failures
			swap(t, &readFile, func(name string) ([]byte, error) {
				calls++
				if left > 0 {
					left--
					return nil, &fs.PathError{Op: "open", Path: name, Err: c.errno}
				}
				return os.ReadFile(name)
			})
			data, err := ReadJSON(path)
			if (err == nil) != c.wantOK || calls != c.wantCalls {
				t.Fatalf("err=%v calls=%d", err, calls)
			}
			if c.wantOK {
				if got, _ := pyjson.Dumps(data, pyjson.Options{}); got != `{"given": ["a"]}` {
					t.Fatalf("data=%s", got)
				}
			}
		})
	}
}

func TestReadJSONSeparatesMissingFromBroken(t *testing.T) {
	dir := t.TempDir()
	_, err := ReadJSON(filepath.Join(dir, "none.json"))
	if !errors.Is(err, fs.ErrNotExist) {
		t.Fatalf("無いは fs.ErrNotExist: %v", err)
	}
	broken := filepath.Join(dir, "broken.json")
	os.WriteFile(broken, []byte("{ not json"), 0o644)
	_, err = ReadJSON(broken)
	var syntax *pyjson.DecodeError
	if !errors.As(err, &syntax) || errors.Is(err, fs.ErrNotExist) {
		t.Fatalf("壊れているは DecodeError: %v", err)
	}
	if obj, ok := ReadObject(broken); ok || obj != nil {
		t.Fatal("壊れたものは読めない扱い")
	}
	list := filepath.Join(dir, "list.json")
	os.WriteFile(list, []byte("[1]"), 0o644)
	if obj, ok := ReadObject(list); !ok || obj.Len() != 0 {
		t.Fatal("マップでなければ空のマップ")
	}
}

// 書き方ごとに、書けること・親を作ること・書けなかったときに前の中身が残り一時ファイルが残らないこと。
func TestReplacingWritesKeepThePreviousContentOnFailure(t *testing.T) {
	content := "\xef\xbb\xbf---\r\nticket: i0001\r\n---\r\n本文\n\r\n" // 改行も BOM も変えない
	cases := []struct {
		name string
		how  How
		fail func(t *testing.T)
	}{
		{"Atomic の差し替え", Atomic, failReplace},
		{"Durable の差し替え", Durable, failReplace},
		{"Durable の fsync（届いたか分からないものを本番にしない）", Durable, func(t *testing.T) {
			swap(t, &syncFile, func(*os.File) error { return &fs.PathError{Op: "sync", Err: syscall.EIO} })
		}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "deep", "i0001.md")
			if failed := WriteFile(path, []byte(content), c.how); failed != "" || readAll(t, path) != content {
				t.Fatalf("failed=%q", failed)
			}
			c.fail(t)
			if WriteFile(path, []byte("after"), c.how) == "" {
				t.Fatal("失敗が返らない")
			}
			if readAll(t, path) != content || !slices.Equal(names(t, filepath.Dir(path)), []string{"i0001.md"}) {
				t.Fatalf("前の中身か一時ファイルの片付けが違う: %v", names(t, filepath.Dir(path)))
			}
		})
	}
}

func failReplace(t *testing.T) {
	swap(t, &replace, func(string, string) error { return &os.LinkError{Op: "rename", Err: syscall.ENOSPC} })
}

// 一時ファイルは行き先の隣に、毎回違う名前で作り、運ぶ側の除外と掃除の条件にヒットする形にする。
func TestTemporaryNames(t *testing.T) {
	carried := regexp.MustCompile(`^\.[^/]*\.part(\.[^/]*)?$`) // 承認済みの置き場から運ばない形
	cases := []struct {
		how            How
		name           string
		prefix, suffix string
	}{
		{Atomic, "once-abc-def.json", ".once-abc-def.", ".part.json"},
		{Durable, "i0001.md", ".i0001.md.", ".part"},
	}
	for _, c := range cases {
		dir := t.TempDir()
		path := filepath.Join(dir, c.name)
		var seen []string
		swap(t, &replace, func(part, target string) error {
			seen = append(seen, part)
			return os.Rename(part, target)
		})
		for range 3 {
			WriteFile(path, []byte("x"), c.how)
		}
		if len(seen) != 3 || seen[0] == seen[1] || seen[1] == seen[2] {
			t.Fatalf("一時ファイルの名前が重なった: %v", seen)
		}
		for _, part := range seen {
			name := filepath.Base(part)
			if filepath.Dir(part) != dir || !strings.HasPrefix(name, c.prefix) || !strings.HasSuffix(name, c.suffix) ||
				!carried.MatchString(name) || !IsTempName(name) {
				t.Errorf("一時ファイルの形が違う: %s", part)
			}
			if c.how == Atomic {
				if origin := TempOrigin(name); !strings.HasPrefix(origin, "once-abc-def.") || !strings.HasSuffix(origin, ".json") {
					t.Errorf("TempOrigin(%s) = %s", name, origin)
				}
			}
		}
	}
}

func TestTempNameShapes(t *testing.T) {
	for _, name := range []string{".workflow.yml.abc12345.part", ".once-a-b.x1y2.part.json", ".i0001.md.k_9.part"} {
		if !IsTempName(name) {
			t.Errorf("一時ファイルの名前: %s", name)
		}
	}
	for _, name := range []string{"workflow.yml", "1.pending", ".hidden", "a.b.part", ".part", ".x.part", "a.b.part.json"} {
		if IsTempName(name) || TempOrigin(name) != name {
			t.Errorf("一時ファイルの名前ではない: %s", name)
		}
	}
	if got := TempOrigin(".a.b.c.x1y2_z.part.json"); got != "a.b.c.x1y2_z.part.json" {
		t.Errorf("TempOrigin = %s", got)
	}
}

// Durable は差し替えの前に中身を、後にディレクトリをディスクへ届ける。
func TestDurableSyncsBeforeReplacing(t *testing.T) {
	var order []string
	swap(t, &syncFile, func(f *os.File) error { order = append(order, "sync"); return f.Sync() })
	swap(t, &replace, func(a, b string) error { order = append(order, "replace"); return os.Rename(a, b) })
	swap(t, &syncDir, func(string) { order = append(order, "dir") })
	if WriteFile(filepath.Join(t.TempDir(), "i0001.md"), []byte("x"), Durable) != "" {
		t.Fatal("書けない")
	}
	if !slices.Equal(order, []string{"sync", "replace", "dir"}) {
		t.Fatalf("order = %v", order)
	}
}

// chmod を受け付けないファイルシステム（vfat など）でも Durable は書ける（権限の引き継ぎは最善努力）。
func TestDurableToleratesRefusedChmod(t *testing.T) {
	path := filepath.Join(t.TempDir(), "i0001.md")
	swap(t, &chmod, func(string, fs.FileMode) error { return &fs.PathError{Op: "chmod", Err: syscall.EPERM} })
	if failed := WriteFile(path, []byte("after"), Durable); failed != "" || readAll(t, path) != "after" {
		t.Fatalf("failed=%q", failed)
	}
}

// 書き込み権の無い行き先は、ディレクトリの権限だけで通る差し替えでも上書きしない（PutBack も同じ書き方）。
func TestDurableDoesNotReplaceReadOnlyDestination(t *testing.T) {
	noSleep(t)
	path := filepath.Join(t.TempDir(), "i0001.md")
	WriteFile(path, []byte("before"), Durable)
	swap(t, &writable, func(p string) bool { return p != path })
	if WriteFile(path, []byte("after"), Durable) == "" || WriteText(path, "after", LF, Durable) == "" {
		t.Fatal("読み取り専用を上書きした")
	}
	if got := WriteFile(path, []byte("after"), Durable); got != "[Errno 13] Permission denied: '"+path+"'" {
		t.Errorf("理由の文 = %q", got)
	}
	PutBack([]Prior{{Path: path, Content: []byte("put")}})
	if readAll(t, path) != "before" || !slices.Equal(names(t, filepath.Dir(path)), []string{"i0001.md"}) {
		t.Fatal("前の中身が残っていない")
	}
}

// 改行は open(path, "w", newline=...) と同じバイト列にする。NativeNewline は機械の改行。
func TestNewlineOnDisk(t *testing.T) {
	native := "a\nb\n"
	if runtime.GOOS == "windows" {
		native = "a\r\nb\r\n"
	}
	cases := map[Newline]string{NativeNewline: native, LF: "a\nb\n", CRLF: "a\r\nb\r\n", CR: "a\rb\r"}
	dir := t.TempDir()
	for nl, want := range cases {
		for _, how := range []How{Plain, Atomic, Durable} {
			path := filepath.Join(dir, "f.md")
			if WriteText(path, "a\nb\n", nl, how) != "" || readAll(t, path) != want {
				t.Errorf("nl=%q how=%d: %q", nl, how, readAll(t, path))
			}
		}
	}
}

func TestNewFiles(t *testing.T) {
	for _, how := range []How{New, NewDurable} {
		path := filepath.Join(t.TempDir(), "flows", "i0001-01-01.yml")
		if WriteFile(path, []byte("a: 1\r\n"), how) != "" || readAll(t, path) != "a: 1\r\n" {
			t.Fatalf("how=%d: 無いときに書けない", how)
		}
		if got := WriteFile(path, []byte("after"), how); got != "[Errno 17] File exists: '"+path+"'" || readAll(t, path) != "a: 1\r\n" {
			t.Fatalf("how=%d: 在るものを上書きした: %q", how, got)
		}
		if !slices.Equal(names(t, filepath.Dir(path)), []string{"i0001-01-01.yml"}) {
			t.Fatal("一時ファイルが残った")
		}
	}
}

// 溜める段では書かずに Op を溜め、読みは溜めた中身を先に見る。テキストの中身は機械に依らず LF。
func TestStagingQueuesInsteadOfWriting(t *testing.T) {
	dir := t.TempDir()
	held := filepath.Join(dir, "held.yml")
	os.WriteFile(held, []byte("held"), 0o644)
	stage, done, err := Staging()
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := Staging(); !errors.Is(err, ErrNestedStaging) {
		t.Fatal("入れ子を止めない")
	}
	a, b := filepath.Join(dir, "sub", "a.md"), filepath.Join(dir, "b.md")
	WriteText(a, "x\ny\n", NativeNewline, Durable)
	WriteFile(b, []byte("bin"), Durable)
	if WriteFile(held, []byte("x"), NewDurable) == "" {
		t.Error("在るものへの New を溜めた")
	}
	Unlink(held)
	text, ok := ReadText(a, Strict)
	listed, _ := Listdir(dir)
	exists := Lexists(held)
	done()

	if !ok || text != "x\ny\n" || exists || ospathExists(a) || !ospathExists(held) {
		t.Fatalf("溜める段の見え方が違う: %q %v %v", text, ok, exists)
	}
	slices.Sort(listed)
	if !slices.Equal(listed, []string{"b.md", "sub"}) && !slices.Equal(listed, []string{"b.md"}) {
		t.Errorf("Listdir = %v", listed)
	}
	if len(stage.Items) != 3 {
		t.Fatalf("items = %d", len(stage.Items))
	}
	first := stage.Items[0].(*Op)
	if first.how != Durable || string(first.Content) != "x\ny\n" {
		t.Errorf("Op = %+v", first)
	}
	touched := stage.Touched()
	if len(touched) != 3 || !touched[2].Gone || !touched[2].Existed || touched[0].Existed {
		t.Errorf("Touched = %+v", touched)
	}
	// Writer(FS) は同じ Op をディスクに書く。
	for _, item := range stage.Items {
		if failed := item.(*Op).Apply(); failed != "" {
			t.Fatal(failed)
		}
	}
	if readAll(t, b) != "bin" || ospathExists(held) || !strings.HasPrefix(readAll(t, a), "x") {
		t.Error("Apply が書いていない")
	}
}

func TestPolicyFailureText(t *testing.T) {
	restore := SetPolicy(func(p *Policy) { p.Prefix = "マーカーを置けない: "; p.Message = "{x} {reason}" })
	stage, done, _ := Staging()
	Unlink(filepath.Join(t.TempDir(), "none"))
	WriteFile(filepath.Join(t.TempDir(), "a"), nil, Plain)
	Remove(filepath.Join(t.TempDir(), "none"))
	done()
	restore()
	op := stage.Items[0].(*Op)
	if got := op.Policy.FailureText("r"); got != "マーカーを置けない: {x} r" {
		t.Errorf("FailureText = %q", got)
	}
	if DefaultPolicy().FailureText("r") != "r" || len(stage.Items) != 1 {
		t.Errorf("外側の扱いが戻らない、または無いものの Unlink を溜めた: %d", len(stage.Items))
	}
}

// 入れ子の記録で内側を抜けても、外側の記録は続く（同じ中身の記録を取り違えない）。
func TestNestedRecordingKeepsTheOuterOne(t *testing.T) {
	dir := t.TempDir()
	outer := StartRecording()
	inner := StartRecording()
	WriteText(filepath.Join(dir, "first"), "a", LF, Plain)
	innerPaths := inner.Stop()
	WriteText(filepath.Join(dir, "second"), "b", LF, Plain)
	Remove(filepath.Join(dir, "first"))
	outerPaths := outer.Stop()
	base := func(paths []string) []string {
		var out []string
		for _, p := range paths {
			out = append(out, filepath.Base(p))
		}
		return out
	}
	if !slices.Equal(base(outerPaths), []string{"first", "second"}) || !slices.Equal(base(innerPaths), []string{"first"}) {
		t.Fatalf("outer=%v inner=%v", outerPaths, innerPaths)
	}
}

func TestMoveAndUnlink(t *testing.T) {
	dir := t.TempDir()
	src, dst := filepath.Join(dir, "a"), filepath.Join(dir, "to", "b")
	os.WriteFile(src, []byte("x"), 0o644)
	if Move(src, dst) != "" || readAll(t, dst) != "x" || ospathExists(src) {
		t.Fatal("動かない")
	}
	if runtime.GOOS != "windows" {
		if got := Unlink(src); got != "[Errno 2] No such file or directory: '"+src+"'" {
			t.Errorf("Unlink の理由 = %q", got)
		}
		if got := Unlink(dir); !strings.HasPrefix(got, "[Errno ") {
			t.Errorf("ディレクトリは消さない: %q", got)
		}
	}
}

// 理由の文は Python の str(exc) と同じ。
func TestErrorTextMatchesPython(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("strerror の文は Linux のもの")
	}
	cases := map[string]error{
		"[Errno 2] No such file or directory: '/nonexist/a'":      &fs.PathError{Op: "open", Path: "/nonexist/a", Err: syscall.ENOENT},
		"[Errno 2] No such file or directory: '/n/a' -> '/tmp/b'": &os.LinkError{Op: "rename", Old: "/n/a", New: "/tmp/b", Err: syscall.ENOENT},
		"[Errno 28] No space left on device":                      &fs.PathError{Op: "write", Path: "/x", Err: syscall.ENOSPC},
		"[Errno 21] Is a directory: \"it's\"":                     &fs.PathError{Op: "unlink", Path: "it's", Err: syscall.EISDIR},
		"[Errno 17] File exists: 'a'":                             pathError("open", "a", errnoEEXIST),
		"[Errno 20] Not a directory: '/etc/passwd/x'":             MakeDirs("/etc/passwd/x"),
		"[Errno 2] No such file or directory: ''":                 MakeDirs(""),
	}
	for want, err := range cases {
		if got := ErrorText(err); got != want {
			t.Errorf("ErrorText = %q, want %q", got, want)
		}
	}
}

// UTF-8 として読めないバイトの区切りと文は CPython と同じ。改行は "\n" にそろう。
func TestDecodeMatchesPython(t *testing.T) {
	cases := []struct {
		in       string
		err      string
		replaced string
	}{
		{"ab\xff", "'utf-8' codec can't decode byte 0xff in position 2: invalid start byte", "ab�"},
		{"\xe3\x81", "'utf-8' codec can't decode bytes in position 0-1: unexpected end of data", "�"},
		{"\xe3\x81A", "'utf-8' codec can't decode bytes in position 0-1: invalid continuation byte", "�A"},
		{"\xed\xa0\x80", "'utf-8' codec can't decode byte 0xed in position 0: invalid continuation byte", "���"},
		{"a\xc0\xafb", "'utf-8' codec can't decode byte 0xc0 in position 1: invalid start byte", "a��b"},
		{"ok\r\nx\ry", "", "ok\nx\ny"},
	}
	for _, c := range cases {
		_, err := Decode([]byte(c.in), Strict)
		if (err == nil && c.err != "") || (err != nil && err.Error() != c.err) {
			t.Errorf("Decode(%q) err = %v", c.in, err)
		}
		if got, _ := Decode([]byte(c.in), Replace); got != c.replaced {
			t.Errorf("Decode(%q, Replace) = %q", c.in, got)
		}
	}
}

// 同じ場所を指すパスは同じ答えになる（相対・"."・".."・絶対）。まだ無いファイルも絶対パスにする。
func TestFullPath(t *testing.T) {
	base, _ := filepath.EvalSymlinks(t.TempDir())
	want := filepath.Join(base, "secrets", "key.pem")
	for _, spelled := range []string{"secrets/key.pem", "./secrets/key.pem", "secrets/./key.pem", "a/../secrets/key.pem", "build/../secrets/key.pem", want} {
		if got := FullPath(spelled, base); got != want {
			t.Errorf("FullPath(%q) = %q", spelled, got)
		}
	}
	if FullPath("", "/somewhere") != "" {
		t.Error("空のパスは空のまま（対象の無い呼び出しをディレクトリへの操作に見せない）")
	}
	if runtime.GOOS != "windows" {
		link := filepath.Join(base, "link")
		os.Symlink(filepath.Join(base, "secrets"), link)
		if got := FullPath("link/key.pem", base); got != want {
			t.Errorf("リンクを解かない: %q", got)
		}
		if got := SpelledPath("link/../link/key.pem", base); got != filepath.Join(link, "key.pem") {
			t.Errorf("SpelledPath = %q", got)
		}
	}
}

func TestSafeName(t *testing.T) {
	long := strings.Repeat("あ", 70)
	cases := []struct {
		in    string
		limit int
		want  string
	}{
		{"ab/c\\d:e f.g-h_i", SafeNameLimit, "ab_c_d_e_f.g-h_i"},
		{long, SafeNameLimit, strings.Repeat("_", 64)},
		{long, NoLimit, strings.Repeat("_", 70)},
	}
	for _, c := range cases {
		if got := SafeName(c.in, c.limit); got != c.want {
			t.Errorf("SafeName(%q, %d) = %q", c.in, c.limit, got)
		}
	}
}

func TestFixClock(t *testing.T) {
	restore, err := FixClock("2026-09-29T09:30:05+0900")
	if err != nil || Stamp() != "2026-09-29T09:30:05+0900" {
		t.Fatalf("固定できない: %v", err)
	}
	inner, _ := FixClock("")
	if Stamp() != "2026-09-29T09:30:05+0900" {
		t.Error("空は外側の固定を残す")
	}
	inner()
	restore()
	if _, err := time.Parse(StampLayout, Stamp()); err != nil {
		t.Errorf("手元の時計の形が違う: %s", Stamp())
	}
	for _, bad := range []string{"2026-02-30T09:30:05+0900", "2026-09-29 09:30:05+0900", "2026-09-29T09:30:05"} {
		if _, err := FixClock(bad); err == nil || !strings.Contains(err.Error(), "時刻の形が違う（%Y-%m-%dT%H:%M:%S%z で読めない）: '") {
			t.Errorf("FixClock(%q) = %v", bad, err)
		}
	}
	if r, err := FixClock("2026-9-1t09:30:05Z"); err != nil {
		t.Errorf("strptime が受ける形を拒んだ: %v", err)
	} else {
		r()
	}
}

func TestReadTextUndecodable(t *testing.T) {
	path := filepath.Join(t.TempDir(), "x")
	os.WriteFile(path, []byte("a\xffb\r\n"), 0o644)
	if _, ok := ReadText(path, Strict); ok {
		t.Error("Strict で読めてしまう")
	}
	if got, ok := ReadText(path, Replace); !ok || got != "a�b\n" {
		t.Errorf("Replace = %q", got)
	}
	if raw, _ := ReadBytes(path); !bytes.Equal(raw, []byte("a\xffb\r\n")) {
		t.Error("ReadBytes は改行を変えない")
	}
}
