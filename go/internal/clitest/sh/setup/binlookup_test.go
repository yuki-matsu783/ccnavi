package setup

import (
	"fmt"
	"path/filepath"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

// 保護済み sh が起動する実行ファイルを探す順（.ccnavi/scripts/ccnavi-common.sh の ccnavi_bin）。
//
// ユーザがターミナルから `ccnavi-ticket.sh` などを打つ場面では settings.json の env が反映されず、CCNAVI_ENTRY_PATH は無いのが普通。
// 配布先には `dist/` もソースも無く、実行ファイルは `.ccnavi/bin/<os>-<arch>/` にしか無い。そこを見ないと、配布先では必ず
// 「実行ファイルが見つかりません」で止まる。ワークスペースを使い捨てで組み、実行ファイルの代わりに受け取った引数を書くだけの sh を置く。

// stubExe は呼ばれたら、どれが呼ばれたかと引数を 1 行で出す。
func stubExe(name string) string {
	return fmt.Sprintf("#!/bin/sh\nprintf \"%%s %%s\\n\" \"%s\" \"$*\"\n", name)
}

type binLookup struct {
	t  *testing.T
	ws string
}

func newBinLookup(t *testing.T) *binLookup {
	t.Helper()
	notWindows(t)
	haveCommands(t, "sh")
	b := &binLookup{t: t, ws: base.TempDir(t)}
	copyTree(t, base.ScriptsDir(), filepath.Join(b.ws, ".ccnavi", "scripts"), false, nil)
	chmod(t, filepath.Join(b.ws, ".ccnavi", "scripts", "ccnavi-launcher.sh"), 0o755)
	return b
}

// ticket は `sh .ccnavi/scripts/ccnavi-ticket.sh status x` を ENTRY_PATH なしで起こす（extra は足す環境）。
func (b *binLookup) ticket(extra map[string]string) run.Result {
	b.t.Helper()
	return run.Command(b.t, "sh", []string{filepath.Join(".ccnavi", "scripts", "ccnavi-ticket.sh"), "status", "x"},
		run.Opts{Dir: b.ws, Env: extra})
}

func (b *binLookup) machineBuild() {
	writeExec(b.t, filepath.Join(b.ws, ".ccnavi", "bin", exe.HostTarget(), "ccnavi"), stubExe("bin"))
}

func (b *binLookup) pathBuild() string {
	place := filepath.Join(b.ws, "on-path")
	writeExec(b.t, filepath.Join(place, "ccnavi"), stubExe("path"))
	return place
}

func TestBinLookupOrder(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name  string
		setup func(b *binLookup) map[string]string
		want  string
	}{
		{"配布先は env なしで機械の組み立てを起動する", func(b *binLookup) map[string]string {
			b.machineBuild()
			return nil
		}, "bin "},
		{"ローカルの組み立てが機械の組み立てより先", func(b *binLookup) map[string]string {
			b.machineBuild()
			writeExec(b.t, filepath.Join(b.ws, "dist", "ccnavi", "ccnavi"), stubExe("dist"))
			return nil
		}, "dist "},
		{"env があれば env が先", func(b *binLookup) map[string]string {
			b.machineBuild()
			writeExec(b.t, filepath.Join(b.ws, "other", "ccnavi"), stubExe("env"))
			return map[string]string{env.EntryPath: "other/ccnavi"}
		}, "env "},
		{"bin もソースも無ければ PATH の ccnavi を使う", func(b *binLookup) map[string]string {
			return map[string]string{env.Path: pathWith(b.pathBuild())}
		}, "path "},
		{"機械の組み立てが PATH の ccnavi より先", func(b *binLookup) map[string]string {
			b.machineBuild()
			return map[string]string{env.Path: pathWith(b.pathBuild())}
		}, "bin "},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			b := newBinLookup(t)
			r := b.ticket(c.setup(b))
			code(t, r, 0)
			startsWith(t, r.Stdout, c.want)
			in(t, r.Stdout, "ticket status x")
		})
	}
}

// 見つからないときは 2 で、探した場所を言う。ccnavi という名前で張った launcher へのリンクは、起動すると自分に戻るので無いものとして扱う。
// PATH の相対の項目は使わない。
func TestBinLookupSaysWhereItLookedWhenThereIsNothingToRun(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name  string
		setup func(b *binLookup) map[string]string
		in    string
	}{
		{"何も無い", func(b *binLookup) map[string]string { return nil }, ".ccnavi/bin/"},
		{"launcher へのリンクは実行ファイルではない", func(b *binLookup) map[string]string {
			if !base.CanSymlink() {
				b.t.Skip("シンボリックリンクを作れない")
			}
			place := filepath.Join(b.ws, "on-path")
			symlink(b.t, filepath.Join(b.ws, ".ccnavi", "scripts", "ccnavi-launcher.sh"), filepath.Join(place, "ccnavi"))
			return map[string]string{env.Path: pathWith(place)}
		}, ""},
		{"PATH の相対の項目は使わない", func(b *binLookup) map[string]string {
			b.pathBuild()
			return map[string]string{env.Path: pathWith("on-path")}
		}, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			b := newBinLookup(t)
			r := b.ticket(c.setup(b))
			code(t, r, 2)
			in(t, r.Stderr, "実行ファイルが見つかりません", c.in)
		})
	}
}
