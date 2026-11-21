package self

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/infra/env"
)

// ---- 実行ファイル ----

func TestTheBinaryIsStoppedFromShellWritesAndNamedToolsOnlyWhenTheSettingPointsAtIt(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	path := s.binary()

	mustHave(t, s.run("PreToolUse", call{Bin: path, Command: "cp /tmp/other.exe dist/ccnavi/ccnavi.exe"}).Stdout, "deny", settingFilesRule)
	mustHave(t, s.run("PreToolUse", call{Bin: path, Tool: "Write", Path: path}).Stdout, "deny", binaryRule)

	// CCNAVI_ENTRY_PATH が空なら、そこは守る対象ではない。パスを推測して守ると、そこに在る別のファイルを実体として扱うことになる。
	mustNot(t, s.run("PreToolUse", call{Command: "cp /tmp/other.exe dist/ccnavi/ccnavi.exe"}).Stdout, "deny")
}

// 組み立ての置き場（bin/<os>-<arch>/）の書き込み用の表記は空白をまたぐので、コピーする側には行き先だけに適用する表記を別に足す。
// 振り分けの実体を外へコピーするだけなら止まらず、行き先なら止まる。置き場をディレクトリで行き先にしても止まる
// （sh の入っているディレクトリ、組み立ての置き場の親 bin/、その親）。
func TestCopyingTheLauncherBundleOutIsNotStoppedButCopyingIntoItIs(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	spelled, _, _ := s.scriptsLayout("tools")
	target := exe.HostTarget()
	build := "tools/bin/" + target
	check := func(command string, stops bool) {
		t.Helper()
		r := s.run("PreToolUse", call{Bin: spelled, Command: command})
		if got := strings.Contains(r.Stdout, "deny") && strings.Contains(r.Stdout, settingFilesRule); got != stops {
			t.Errorf("%q: 止める=%v のはず: %s", command, stops, r.Stdout)
		}
	}
	for _, command := range []string{
		"cp " + build + "/ccnavi /tmp/keep",
		"cp -r " + build + " /tmp/keep",
		"cp tools/scripts/" + launcherName + " /tmp/keep.sh",
		"cp /tmp/other.sh tools/scripts/",
		"cp -r /tmp/docs tools/",
	} {
		check(command, false)
	}
	for _, command := range []string{
		"cp /tmp/other " + build + "/ccnavi",
		"cp /tmp/other " + build,
		"cp /tmp/other " + build + "/ccnavi && echo done",
		"cp -t " + build + " /tmp/ccnavi",
		"cp /tmp/other.sh tools/scripts/" + launcherName,
		"mv " + build + "/ccnavi /tmp/keep",
		"cp /tmp/" + launcherName + " tools/scripts/",
		"cp -r /tmp/" + target + " tools/bin/",
		"cp -r /tmp/bin tools/",
		"cp -r /tmp/scripts tools",
	} {
		check(command, true)
	}
}

// ---- バックアップを取って戻す（実行ファイル） ----

func TestTheBinaryIsBackedUpAtSessionStartAndRestoredAfterTheRun(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	path := s.binary()
	s.run("SessionStart", call{Bin: path})
	write(t, path, "MZ replaced\n")

	r := s.run("PostToolUse", call{Bin: path})

	if got := read(t, path); got != "MZ fake executable\n" {
		t.Errorf("戻っていない: %q", got)
	}
	mustHave(t, r.Stdout, "restored", "ccnavi.exe")
}

// ツール呼び出しの中で変わった実行ファイルは直前の版へ戻す。戻す前に書かれていた版は aside へ退避して、この呼び出しで書かれた版を消さない。
func TestAChangeInsideAToolCallGoesBackAndTheWrittenVersionIsKeptAside(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	path := s.binary()
	s.run("SessionStart", call{Bin: path})
	s.run("PreToolUse", call{Bin: path})
	write(t, path, "MZ replaced\n")

	r := s.run("PostToolUse", call{Bin: path})

	if got := read(t, path); got != "MZ fake executable\n" {
		t.Errorf("直前の版へ戻るはず: %q", got)
	}
	mustHave(t, r.Stdout, "restored")
	found := false
	_ = filepath.WalkDir(filepath.Join(s.state, "aside"), func(p string, d os.DirEntry, err error) error {
		if err == nil && !d.IsDir() && read(t, p) == "MZ replaced\n" {
			found = true
		}
		return nil
	})
	if !found {
		t.Error("この呼び出しで書かれた版を aside に残すはず")
	}
}

// 外で変わった版を基準にし、この呼び出しの変更はその版へ戻す。
func TestARebuiltBinaryBecomesTheBaseAndTheCallsChangeGoesBackToIt(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	path := s.binary()
	s.run("SessionStart", call{Bin: path})
	write(t, path, "MZ rebuilt\n")
	s.run("PreToolUse", call{Bin: path})
	write(t, path, "MZ tampered\n")

	r := s.run("PostToolUse", call{Bin: path})

	if got := read(t, path); got != "MZ rebuilt\n" {
		t.Errorf("戻す先は、この呼び出しの直前の版: %q", got)
	}
	mustHave(t, r.Stdout, "restored")
}

// 再ビルドや別プロセスの書き込みは、次の実行前の照合で新しい版として基準に入り、戻さない。
func TestABinaryRebuiltOutsideAToolCallIsAcceptedAsTheNewVersion(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	path := s.binary()
	s.run("SessionStart", call{Bin: path})
	write(t, path, "MZ rebuilt\n")

	s.run("PreToolUse", call{Bin: path})
	r := s.run("PostToolUse", call{Bin: path})

	if got := read(t, path); got != "MZ rebuilt\n" {
		t.Errorf("外の変更は戻さない: %q", got)
	}
	if !contains(s.store(), "MZ rebuilt\n") {
		t.Errorf("基準の store に新しい版が要る: %q", s.store())
	}
	mustNot(t, r.Stdout, "restored")
}

// ツールを呼ぶ前に外で作り直された実行ファイルは新しい版として取り直す。大きさで変化を見るので、差し替えの内容は元と大きさを変える
// （同じ大きさは見分けられない）。実行前の知らせは判定が拒否したときにしか stdout に出ないので、取り直したことは記録（guarded）と
// 基準の store に残る。
func TestARebuiltBinaryIsRebasedBeforeTheNextToolCall(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	path := s.binary()
	s.run("SessionStart", call{Bin: path})
	write(t, path, "MZ rebuilt\n")

	s.run("PreToolUse", call{Bin: path})

	if got := read(t, path); got != "MZ rebuilt\n" {
		t.Errorf("外の変更は戻さない: %q", got)
	}
	if !contains(s.store(), "MZ rebuilt\n") {
		t.Errorf("基準の store に新しい版が要る: %q", s.store())
	}
	if !contains(strs(s.lastRecord()["guarded"]), "bin:rebased") {
		t.Errorf("記録に bin:rebased が要る: %v", s.lastRecord())
	}
}

// バックアップが無い状態（セッション開始のイベントに登録していないか、実行ファイルを指していない設定）は異常ではない。
func TestWithoutSessionStartTheBinaryPassesSilently(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	path := s.binary()
	write(t, path, "MZ replaced\n")

	r := s.run("PostToolUse", call{Bin: path})

	if read(t, path) != "MZ replaced\n" || r.Stdout != "" {
		t.Errorf("何も言わずに通すはず: %q", r.Stdout)
	}
}

func TestAMissingBinaryIsReportedAtSessionStart(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	r := s.run("SessionStart", call{Bin: s.at("dist/ccnavi/ccnavi.exe")})
	mustHave(t, r.Stdout, env.EntryPath)
}

// hook の登録は 3 つの環境で同じ 1 行を使う。実行ファイルは Windows でだけ .exe を付けるので、設定に書いたパスと在るファイルの
// パスが食い違う。書いた側を直させるのではなく、在るほうを選ぶ。在るパスならそれをそのまま使う（継ぎ足して探すのは書いたパスが無いときだけ）。
func TestABinaryPathWithoutTheExtensionStillHits(t *testing.T) {
	t.Parallel()
	for name, c := range map[string]struct{ file, spelled, content string }{
		"拡張子を書かないパス": {"dist/ccnavi/ccnavi.exe", "dist/ccnavi/ccnavi", "MZ fake executable\n"},
		"拡張子なしのファイル": {"dist/ccnavi/ccnavi", "dist/ccnavi/ccnavi", "ELF fake executable\n"},
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			s := newSG(t)
			path := write(t, s.at(c.file), c.content)
			spelled := s.at(c.spelled)

			s.run("SessionStart", call{Bin: spelled})
			write(t, path, "replaced\n")
			s.run("PostToolUse", call{Bin: spelled})

			if got := read(t, path); got != c.content {
				t.Errorf("戻っていない: %q", got)
			}
		})
	}
}

// ---- 振り分けの sh を <置き場>/scripts/ に、実体を <置き場>/bin/<os>-<arch>/ に分けた形 ----
//
// 指す先の名前が振り分けの sh なら、実体は sh の隣ではなく ../bin/ に在る。守るパスとバックアップする先は同じ条件で切り替わらなければ
// ならない。片方だけ切り替わると、守っている場所とバックアップしている場所が食い違う。

// scriptsLayout は sh を <place>/scripts/、実体を <place>/bin/<この機械>/ に置く。place を空にすると、ワークスペースルートの直下に
// scripts/ と bin/ が並ぶ。返すのは（CCNAVI_ENTRY_PATH に書く相対パス, sh の絶対パス, 実体の絶対パス）。
func (s *sg) scriptsLayout(place ...string) (spelled, launcher, bin string) {
	s.t.Helper()
	if len(place) == 0 {
		place = []string{".ccnavi"}
	}
	if len(place) == 1 && place[0] == "" {
		place = nil
	}
	parts := append(append([]string{}, place...), "scripts", launcherName)
	spelled = strings.Join(parts, "/")
	launcher = write(s.t, s.at(spelled), "#!/bin/sh\n")
	bin = write(s.t, s.at(strings.Join(append(append([]string{}, place...), "bin", exe.HostTarget(), "ccnavi"), "/")), "ELF fake executable\n")
	return spelled, launcher, bin
}

// hook が実際に走らせるのは .ccnavi/bin/<この機械>/ccnavi。sh の隣（.ccnavi/scripts/<この機械>/）を探していると、バックアップが
// 無いまま差し替えを見逃す。階層が 2 つしかない浅いパス（scripts/ccnavi-launcher.sh）でも名前だけで切り替わる。
// バックアップする先の解き方そのものは infra/platformtag の TestLaunchedExecutable が見る。
func TestALauncherBundleIsBackedUpAtSessionStartAndRestoredAfterTheRun(t *testing.T) {
	t.Parallel()
	for name, place := range map[string][]string{"既定の置き場": {".ccnavi"}, "浅いパス": {""}} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			s := newSG(t)
			spelled, _, bin := s.scriptsLayout(place...)

			s.run("SessionStart", call{Bin: spelled})
			write(t, bin, "ELF replaced\n")
			r := s.run("PostToolUse", call{Bin: spelled})

			if got := read(t, bin); got != "ELF fake executable\n" {
				t.Errorf("戻っていない: %q", got)
			}
			mustHave(t, r.Stdout, "restored")
		})
	}
}

// 既定の置き場では ccnavi ディレクトリの保護（*/.ccnavi/*）と二重に止まり、先に名指しされるのはそちら。ccnavi ディレクトリの名前は
// 動かせないので、実行ファイル由来の保護が止めていることは、.ccnavi/ の外に置いた振り分け（tools/）で確かめる。sh の隣は配る場所ではない。
// そこに適用すると守る必要の無い場所を止め、本当に実体が在る ../bin/ を外す。
func TestTheLauncherBundleGuardHitsUnderBinNotBesideTheLauncher(t *testing.T) {
	t.Parallel()
	target := exe.HostTarget()
	cases := []struct {
		name    string
		place   []string
		rel     string
		guarded bool
		rule    bool // guard-binary の名前が出るか
	}{
		{"既定の置き場の bin の下", []string{".ccnavi"}, ".ccnavi/bin/" + target + "/_internal/x", true, false},
		{"tools の bin の下", []string{"tools"}, "tools/bin/" + target + "/x", true, true},
		{"tools の bin の下の実体の隣", []string{"tools"}, "tools/bin/" + target + "/_internal/x", true, true},
		{"sh の隣は守らない", []string{"tools"}, "tools/scripts/" + target + "/x", false, false},
		{"浅いパス", []string{""}, "bin/" + target + "/_internal/x", true, true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			s := newSG(t)
			spelled, _, _ := s.scriptsLayout(c.place...)
			r := s.run("PreToolUse", call{Bin: spelled, Tool: "Write", Path: s.at(c.rel)})
			if got := strings.Contains(r.Stdout, "deny"); c.guarded && !got {
				t.Errorf("止めるはず: %s", r.Stdout)
			}
			if c.rule {
				mustHave(t, r.Stdout, binaryRule)
			}
			if !c.guarded {
				mustNot(t, r.Stdout, binaryRule)
			}
		})
	}
}

// 実体の置き場（bin/<os>-<arch>/）は、シェルからの書き込みでも止まる。ccnavi ディレクトリの外（tools/）では、止めるのは実行ファイルの
// パスだけ。
func TestTheLauncherBundlePlaceIsStoppedFromShellWrites(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	spelled, _, bin := s.scriptsLayout()
	target := exe.HostTarget()
	absolute := strings.ReplaceAll(filepath.Dir(bin), `\`, "/")
	for _, command := range []string{"rm -rf .ccnavi/bin/" + target, "rm -rf " + absolute} {
		mustHave(t, s.run("PreToolUse", call{Bin: spelled, Command: command}).Stdout, "deny", settingFilesRule)
	}
	s2 := newSG(t)
	spelled2, _, _ := s2.scriptsLayout("tools")
	mustHave(t, s2.run("PreToolUse", call{Bin: spelled2, Command: "rm -rf tools/bin/" + target}).Stdout, "deny", settingFilesRule)
}

// ---- bin/ に実体が無く、PATH の ccnavi が起動される形 ----
//
// PATH の置き場はワークスペースの外に在るものとして、使い捨ての別ディレクトリに置く。

// pathLayout は振り分けの sh だけを置き、実体は PATH の置き場に置く。返すのは（sh の相対パス, 実体, PATH を足す env）。
func (s *sg) pathLayout() (spelled, bin string, pathEnv map[string]string) {
	s.t.Helper()
	spelled = ".ccnavi/scripts/" + launcherName
	write(s.t, s.at(spelled), "#!/bin/sh\n")
	outside := base.TempDir(s.t)
	bin = write(s.t, filepath.Join(outside, "local", "bin", "ccnavi"), "ELF fake on path\n")
	if err := os.Chmod(bin, 0o755); err != nil {
		s.t.Fatal(err)
	}
	return spelled, bin, map[string]string{"PATH": filepath.Dir(bin) + string(os.PathListSeparator) + os.Getenv("PATH")}
}

func TestTheBinaryOnPathIsBackedUpAndRestoredWhenNothingIsUnderBin(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	spelled, bin, pathEnv := s.pathLayout()

	s.run("SessionStart", call{Bin: spelled, Env: pathEnv})
	write(t, bin, "ELF replaced on path\n")
	r := s.run("PostToolUse", call{Bin: spelled, Env: pathEnv})

	if got := read(t, bin); got != "ELF fake on path\n" {
		t.Errorf("戻っていない: %q", got)
	}
	mustHave(t, r.Stdout, "restored")
}

// sh は自分と同じファイルを飛ばして次の項目の実体を起動する。守る対象も同じ実体にする。
func TestAPathEntryLinkedToTheLauncherIsSkippedAndTheRealBinaryIsGuarded(t *testing.T) {
	t.Parallel()
	if !base.CanSymlink() {
		t.Skip("シンボリックリンクを作れない")
	}
	s := newSG(t)
	spelled, bin, pathEnv := s.pathLayout()
	if err := os.Chmod(s.at(spelled), 0o755); err != nil { // 実行できなければ候補に入らない
		t.Fatal(err)
	}
	linked := base.TempDir(t)
	if err := os.Symlink(s.at(spelled), filepath.Join(linked, "ccnavi")); err != nil {
		t.Fatal(err)
	}
	pathEnv = map[string]string{"PATH": linked + string(os.PathListSeparator) + pathEnv["PATH"]}

	s.run("SessionStart", call{Bin: spelled, Env: pathEnv})
	write(t, bin, "ELF replaced on path\n")
	s.run("PostToolUse", call{Bin: spelled, Env: pathEnv})

	if got := read(t, bin); got != "ELF fake on path\n" {
		t.Errorf("戻っていない: %q", got)
	}
}

func TestABinaryOnPathThatAppearsAfterSessionStartIsGuardedFromTheFirstVersionSeen(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	spelled, bin, pathEnv := s.pathLayout()

	s.run("SessionStart", call{Bin: spelled})             // 開始時は PATH に無かった
	s.run("PreToolUse", call{Bin: spelled, Env: pathEnv}) // 基準を取る
	write(t, bin, "ELF replaced on path\n")
	s.run("PostToolUse", call{Bin: spelled, Env: pathEnv})

	if got := read(t, bin); got != "ELF fake on path\n" {
		t.Errorf("戻っていない: %q", got)
	}
}

// ---- 取り直す・消す・予行 ----

// 消された実行ファイルは取り直す版が無いので、セッション開始の版に戻す。
func TestARemovedBinaryGoesBackToTheSessionStartVersion(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	path := s.binary()
	s.run("SessionStart", call{Bin: path})
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}

	s.run("PostToolUse", call{Bin: path})

	if got := read(t, path); got != "MZ fake executable\n" {
		t.Errorf("戻っていない: %q", got)
	}
}

func TestDryRunDoesNotRebaseAndSaysItChanged(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	path := s.binary()
	s.run("SessionStart", call{Bin: path})
	write(t, path, "MZ replaced\n")

	r := s.run("PostToolUse", call{Bin: path, Setting: "dry-run"})

	if got := read(t, path); got != "MZ replaced\n" {
		t.Errorf("予行では基準に触らない: %q", got)
	}
	mustHave(t, r.Stdout, "would-restore")
}

// バックアップすることは誰の書きかけも消さない。ここを enable に限ると、切り替えた最初のセッションが戻す先を持たないまま走る。
func TestDryRunStillBacksUpAtSessionStart(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	path := s.binary()

	s.run("SessionStart", call{Bin: path, Mode: "dry-run"})

	if store := s.store(); len(store) != 1 || store[0] != "MZ fake executable\n" {
		t.Errorf("store = %q", store)
	}
}

// ---- ユーザの発言までの変更は受け入れる（実行ファイル） ----

func TestABinaryTheUserRemovedBetweenTurnsIsNotRestored(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	path := s.binary()
	s.run("SessionStart", call{Bin: path})
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}

	s.run("UserPromptSubmit", call{Bin: path, Prompt: "お願いします"})
	s.run("PostToolUse", call{Bin: path})

	if exists(path) {
		t.Error("ユーザが消したものを戻してはいけない")
	}
}

func TestABinaryTheUserRemovedAndPlacedAgainIsGuardedAsANewVersion(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	path := s.binary()
	s.run("SessionStart", call{Bin: path})
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	s.run("UserPromptSubmit", call{Bin: path, Prompt: "お願いします"})
	write(t, path, "MZ rebuilt\n")

	s.run("PreToolUse", call{Bin: path})
	write(t, path, "MZ tampered, longer\n")
	r := s.run("PostToolUse", call{Bin: path})

	if got := read(t, path); got != "MZ rebuilt\n" {
		t.Errorf("戻っていない: %q", got)
	}
	mustHave(t, r.Stdout, "restored")
}

// 設定ファイルの「無かったところに現れた」と同じ扱い。差し替えを見逃さないように知らせ、次の実行前に新しい版として基準に入る。
func TestABinaryTheUserRemovedThatAppearsInAToolCallIsReportedButNotRemoved(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	path := s.binary()
	s.run("SessionStart", call{Bin: path})
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	s.run("UserPromptSubmit", call{Bin: path, Prompt: "お願いします"})
	write(t, path, "MZ built by a tool\n")

	r := s.run("PostToolUse", call{Bin: path})

	if got := read(t, path); got != "MZ built by a tool\n" {
		t.Errorf("消さない: %q", got)
	}
	mustHave(t, r.Stdout, "実行ファイルが現れた")
}

func TestAnUnchangedBinaryReferenceGetsANewTimestamp(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	path := s.binary()
	s.run("SessionStart", call{Bin: path})
	ref := filepath.Join(s.state, "selfguard", "s1", "bin.ref")
	s.setAge(2*86400, ref)

	s.run("PreToolUse", call{Bin: path})

	if s.age(ref) > time.Hour {
		t.Error("参照の更新時刻が新しくなっていない")
	}
}

func TestABackgroundNotificationDoesNotAcceptARemovedBinary(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	path := s.binary()
	s.run("SessionStart", call{Bin: path})
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}

	s.run("UserPromptSubmit", call{Bin: path, Prompt: "<task-notification>\n</task-notification>"})
	s.run("PostToolUse", call{Bin: path})

	if got := read(t, path); got != "MZ fake executable\n" {
		t.Errorf("戻っていない: %q", got)
	}
}

func TestARebuiltBinaryAtThePromptBecomesTheBaseEvenInDryRun(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	path := s.binary()
	s.run("SessionStart", call{Bin: path})
	write(t, path, "MZ rebuilt\n")

	s.run("UserPromptSubmit", call{Bin: path, Prompt: "お願いします", Setting: "dry-run"})
	r := s.run("PostToolUse", call{Bin: path, Setting: "dry-run"})

	found := false
	for _, c := range s.store() {
		found = found || c == "MZ rebuilt\n"
	}
	if !found {
		t.Errorf("store = %q", s.store())
	}
	mustNot(t, r.Stdout, "would-restore") // 取り直したので、変わったと言わない
}

// ---- バックアップを溜めない ----

// 同じビルドのまま何セッション走っても、コピーは 1 本で済むこと。
func TestBinaryBackupsAreOneCopyAcrossSessions(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	path := s.binary()
	s.run("SessionStart", call{Bin: path, Session: "s1"})
	s.run("SessionStart", call{Bin: path, Session: "s2"})

	if store := s.store(); len(store) != 1 || store[0] != "MZ fake executable\n" {
		t.Errorf("store = %q", store)
	}
	if exists(filepath.Join(s.state, "selfguard", "s1", "bin")) {
		t.Error("セッションごとのコピーを持たないはず")
	}
}
