// Package treecache は、テストの木を組む手順の結果を取っておき、次のテストでは同じ絶対パスへ戻す（tests/treecache.py の作り直し）。
//
// 親子のチケットの受入テストは、テストごとに git のリポジトリとワークツリーを組み、承認・着手・依頼まで実行ファイルを何度も
// 起こしてから、確かめたい 1 手を打つ。組む手順は同じなので、1 度目の結果の写しを取り、2 度目からは写しへ戻す
// （プロセスを起こさない）。git のワークツリー・origin・状態の置き場は絶対パスで互いを指すので、写しは同じパスへ戻す。
//
// 使い方:
//
//	dir, v := treecache.Build(t, "ticket-family", func(dir string) State {
//		// dir の下に木を組む（git init・承認など）。返す値は JSON に直せるものにする
//		return State{Parent: filepath.Join(dir, "…")}
//	})
//
// 決まり:
//   - 鍵は「組み方」を言う。同じ鍵の手順は同じ木と同じ値を作ること（鍵が同じなら 2 度目からは手順を走らせない）。
//     引数や属性で変わる手順は、変わる値を鍵に入れる（Python は呼ぶ前の木・引数・属性から鍵を作っていた。Go ではテストの
//     属性の写しが取れないので、呼び手が鍵に書く）
//   - dir は鍵ごとの固定のパス。手順は dir の下にだけ書く。dir の外に書いたものは写しに入らず、戻らない
//   - 返す値は JSON に直して取っておく。写しからは、取っておいた JSON を新しく読んで返す（呼び手が書き換えても次に響かない）
//   - 同じ鍵を同じパスへ戻すので、同じ鍵を使うテストどうしは並行に動けない。Build は鍵ごとにテストの終わりまで待たせる
//     （t.Parallel のテストでも取り合わず、順に並ぶ）。t.Parallel() は Build より前に呼ぶ。親が持つ鍵を t.Run の子で
//     使わない（落ちる。鍵は子の側で取る）。待ちの上限（lockWait）は、持ち主が替わるたびに数え直す（順番待ちの長さでは落ちない）
//   - 鍵は最初に使った呼び出し点（file:line）を覚える。同じ鍵が別の呼び出し点から来たら落ちる（別の組み立てに鍵を
//     コピペした間違いを拾う）。呼び出し点を共通の関数に包むなら、その関数が変わる引数を鍵に入れる。包みが呼び手の
//     点で拾わせたいときは BuildAt に Site(1) を渡す
//   - 戻すときは、いまの木と写しの違うファイルだけを消す・写す（木を丸ごと写し直さない）。違うファイルは写しの内容と
//     時刻に戻る。前のテストが木に残したものは、写しと同じ中身へ戻る（テストどうしで状態を持ち越さない）
//   - 写しはプロセスの終わりに消える（base.AtExit）
package treecache

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
)

// kind は木の中の 1 つの項目の種類。
type kind byte

const (
	kindDir     kind = 'd'
	kindFile    kind = 'f'
	kindLink    kind = 'l'
	kindOther   kind = 'o'
	emptyDigest      = ""
)

// item は木の項目 1 つ。相対パスで引く。比べるのは種類・権限・中身（更新時刻は比べない）。
type item struct {
	Kind   kind
	Mode   fs.FileMode
	Digest string // 通常のファイルは中身のハッシュ、リンクはリンク先
}

type listing map[string]item

// entry は鍵 1 つの作業用ディレクトリと写し。
type entry struct {
	mu    sync.Mutex // テスト 1 本が使っている間、持つ
	site  string     // 最初に使った呼び出し点
	saved bool
	work  string
	// owner は鍵を取っているテストの名前（取っていなければ空）。祖先のテストが持つ鍵を子で使う形を、待たずに落とすため。
	ownerMu sync.Mutex
	owner   string
	// handovers は鍵が手放された回数。待つ側は、手放しが進んでいる間は詰まりとみなさない。
	handovers atomic.Int64
	snap      string
	want      listing
	value     []byte
}

var (
	homeMu  sync.Mutex
	home    string
	entries = map[string]*entry{}
)

// lockWait は、別のテストが持つ鍵を待つ上限（持ち主が替わるたびに数え直す）。1 本のテストが長引く負荷の下（他の go test と重なる）でも詰まりとみなさないよう、子のプロセスの上限（base.ExecTimeout）と同じにする。超えたら詰まりとみなして落とす（永久に待たない）。
var lockWait = 120 * time.Second

func entryFor(t testing.TB, key, site string) *entry {
	homeMu.Lock()
	defer homeMu.Unlock()
	if home == "" {
		dir, err := os.MkdirTemp(base.TempBase(), "ccnavi-tree-")
		if err != nil {
			t.Fatal(err)
		}
		home = dir
		base.AtExit(home)
	}
	if e, ok := entries[key]; ok {
		if e.site != site {
			t.Fatalf("treecache: 鍵 %q は %s で使い始めた。%s は別の組み立てなら別の鍵にする", key, e.site, site)
		}
		return e
	}
	sum := sha256.Sum256([]byte(key))
	place := filepath.Join(home, hex.EncodeToString(sum[:6]))
	e := &entry{site: site, work: filepath.Join(place, "w"), snap: filepath.Join(place, "snap")}
	entries[key] = e
	return e
}

func (e *entry) currentOwner() string {
	e.ownerMu.Lock()
	defer e.ownerMu.Unlock()
	return e.owner
}

func (e *entry) setOwner(name string) {
	e.ownerMu.Lock()
	defer e.ownerMu.Unlock()
	e.owner = name
}

// Build は鍵の木を dir に用意し、手順の返した値（写しからは JSON で復元した値）と一緒に返す。1 度目は build を走らせて
// 写しを取り、2 度目からは dir を写しの中身へ戻す。
func Build[T any](t testing.TB, key string, build func(dir string) T) (string, T) {
	t.Helper()
	return BuildAt(t, key, Site(1), build)
}

// Site は呼び出し点（file:line）。skip は呼び出し元から数えて何段上か（0 なら Site を呼んだ行、1 ならその呼び手の行）。
// Build の外に包みの関数を置く側が、包みの呼び手を site に渡すのに使う。
func Site(skip int) string {
	_, file, line, _ := runtime.Caller(skip + 1)
	return fmt.Sprintf("%s:%d", file, line)
}

// BuildAt は Build の本体で、呼び出し点を呼び手が決める。同じ鍵が別の site から来たら落ちる。
func BuildAt[T any](t testing.TB, key, site string, build func(dir string) T) (string, T) {
	t.Helper()
	e := entryFor(t, key, site)
	if owner := e.currentOwner(); owner != "" && strings.HasPrefix(t.Name(), owner+"/") {
		t.Fatalf("treecache: 親のテスト %s が持つ鍵 %q を子 %s で使えない。鍵は t.Run の子の側で取る", owner, key, t.Name())
	}
	deadline, seen := time.Now().Add(lockWait), e.handovers.Load()
	for !e.mu.TryLock() {
		if now := e.handovers.Load(); now != seen {
			// 持ち主が替わっている（順番待ちが進んでいる）。待ちの長さは鍵を使うテストの数で決まるので、詰まりの上限は進みが止まってから数える。
			deadline, seen = time.Now().Add(lockWait), now
		}
		if time.Now().After(deadline) {
			t.Fatalf("treecache: 鍵 %q を使う別のテストが %v 終わらない。t.Parallel() より前に Build していないか、親テストが同じ鍵を持ったまま t.Run の中で Build していないかを見る", key, lockWait)
		}
		time.Sleep(20 * time.Millisecond)
	}
	e.setOwner(t.Name())
	t.Cleanup(func() {
		e.setOwner("")
		e.handovers.Add(1)
		e.mu.Unlock()
	})

	var zero T
	if e.saved {
		if err := restore(e); err != nil {
			t.Fatal(err)
		}
	} else {
		if err := remove(e.work); err != nil {
			t.Fatal(err)
		}
		if err := os.MkdirAll(e.work, 0o755); err != nil {
			t.Fatal(err)
		}
		built := build(e.work)
		data, err := json.Marshal(built)
		if err != nil {
			t.Fatalf("treecache: 手順の返り値を JSON にできない: %v", err)
		}
		if err := copyTree(e.work, e.snap); err != nil {
			t.Fatal(err)
		}
		if e.want, err = list(e.snap); err != nil {
			t.Fatal(err)
		}
		e.value, e.saved = data, true
	}
	got := zero
	if err := json.Unmarshal(e.value, &got); err != nil {
		t.Fatal(err)
	}
	return e.work, got
}

// restore は作業用ディレクトリを写しと同じ中身にする。違う所だけを触る。
func restore(e *entry) error {
	now, err := list(e.work)
	if err != nil {
		return err
	}
	paths := make([]string, 0, len(now))
	for rel := range now {
		paths = append(paths, rel)
	}
	// 深い所から消す（親を先に消すと子の消しが空振りになる）
	sort.Sort(sort.Reverse(sort.StringSlice(paths)))
	for _, rel := range paths {
		if w, ok := e.want[rel]; !ok || w.Kind != now[rel].Kind {
			if err := remove(filepath.Join(e.work, rel)); err != nil {
				return err
			}
		}
	}
	if err := os.MkdirAll(e.work, 0o755); err != nil {
		return err
	}
	wanted := make([]string, 0, len(e.want))
	for rel := range e.want {
		wanted = append(wanted, rel)
	}
	sort.Strings(wanted)
	for _, rel := range wanted {
		w := e.want[rel]
		target := filepath.Join(e.work, rel)
		switch {
		case w.Kind == kindDir:
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
		case now[rel] == w:
		default:
			if err := remove(target); err != nil {
				return err
			}
			if w.Kind == kindLink {
				if err := os.Symlink(w.Digest, target); err != nil {
					return err
				}
			} else if err := copyFile(filepath.Join(e.snap, rel), target, w.Mode); err != nil {
				return err
			}
		}
	}
	return nil
}

// list は木の中身。相対パス（OS の区切り）→ 項目。無ければ空。
func list(top string) (listing, error) {
	out := listing{}
	err := filepath.WalkDir(top, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			if os.IsNotExist(err) && path == top {
				return filepath.SkipAll
			}
			return err
		}
		if path == top {
			return nil
		}
		rel, _ := filepath.Rel(top, path)
		info, err := os.Lstat(path)
		if err != nil {
			return err
		}
		switch mode := info.Mode(); {
		case mode&fs.ModeSymlink != 0:
			dest, err := os.Readlink(path)
			if err != nil {
				return err
			}
			out[rel] = item{Kind: kindLink, Digest: dest}
		case mode.IsDir():
			out[rel] = item{Kind: kindDir}
		case mode.IsRegular():
			sum, err := digest(path)
			if err != nil {
				return err
			}
			out[rel] = item{Kind: kindFile, Mode: mode.Perm(), Digest: sum}
		default:
			// FIFO などは開くと待つので読まない。
			out[rel] = item{Kind: kindOther, Mode: mode}
		}
		return nil
	})
	return out, err
}

func digest(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return emptyDigest, err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return emptyDigest, err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// copyTree は from の中身を to へ写す（リンクはリンクのまま。更新時刻と権限を保つ）。to は先に消す。
func copyTree(from, to string) error {
	if err := remove(to); err != nil {
		return err
	}
	if err := os.MkdirAll(to, 0o755); err != nil {
		return err
	}
	return filepath.WalkDir(from, func(path string, d fs.DirEntry, err error) error {
		if err != nil || path == from {
			return err
		}
		rel, _ := filepath.Rel(from, path)
		target := filepath.Join(to, rel)
		info, err := os.Lstat(path)
		if err != nil {
			return err
		}
		switch mode := info.Mode(); {
		case mode&fs.ModeSymlink != 0:
			dest, err := os.Readlink(path)
			if err != nil {
				return err
			}
			return os.Symlink(dest, target)
		case mode.IsDir():
			return os.MkdirAll(target, 0o755)
		case mode.IsRegular():
			return copyFile(path, target, mode.Perm())
		}
		return nil
	})
}

// copyFile は通常のファイルを写す。更新時刻は元と同じにする（git の索引が「変わっていない」と見るため）。
func copyFile(from, to string, perm fs.FileMode) error {
	in, err := os.Open(from)
	if err != nil {
		return err
	}
	defer in.Close()
	info, err := in.Stat()
	if err != nil {
		return err
	}
	// 読み取り専用で作ると、あとで上書きも消しもできない（Windows）。作るときは書ける権限にして、終わりに戻す。
	out, err := os.OpenFile(to, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return err
	}
	if err := out.Close(); err != nil {
		return err
	}
	if err := os.Chmod(to, perm); err != nil {
		return err
	}
	return os.Chtimes(to, info.ModTime(), info.ModTime())
}

// remove は項目を消す。読み取り専用のファイル（git の objects）も消す（Windows は書き込みを許さないと消せない）。
func remove(path string) error {
	err := os.RemoveAll(path)
	if err == nil {
		return nil
	}
	_ = filepath.WalkDir(path, func(p string, d fs.DirEntry, _ error) error {
		if d != nil {
			_ = os.Chmod(p, 0o700)
		}
		return nil
	})
	return os.RemoveAll(path)
}
