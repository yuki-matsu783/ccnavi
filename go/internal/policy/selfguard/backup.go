package selfguard

import (
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
)

const (
	// BackupDir はバックアップの置き場（state の下）。セッションごとに分ける。別のセッションの断面で戻すと、
	// こちらが一度も見ていない内容へ書き換える。
	BackupDir = "selfguard"
	// StoreDir は大きい対象の実体の置き場（BackupDir の直下）。中身のハッシュで名前を付け、同じ中身を何本も持たない。
	StoreDir = "store"
	// refSuffix はセッションの側に置く参照（実体のハッシュ・大きさ・更新時刻）の拡張子。
	refSuffix = ".ref"
	// absentSuffix は「置かれていない」印（無いことを毎回 git に確かめに行かない）。
	absentSuffix = ".absent"
	// partSuffix はコピーの途中の名前（中断したコピーを揃ったバックアップとして拾わない）。
	partSuffix = ".part"
	// asideDir は戻す前に書かれていた版を退避する置き場（state の下）。
	asideDir = "aside"
	// noSession はセッションの識別子が無いときの置き場の名前。
	noSession = "no-session"

	// keepFor より長く触られていないセッションのバックアップはセッション開始で消す（中断して翌日に開き直した形まで拾う）。
	keepFor = 3 * 24 * time.Hour
	// refreshAfter は変わっていないバックアップの更新時刻を新しくする間隔（掃除に古いと読まれないよう keepFor より短く）。
	refreshAfter = 24 * time.Hour
	// asideStamp は退避した版の名前の頭の時刻。
	asideStamp = "20060102-150405"

	// commonRulesKey は共通レイヤーのルールファイルの key。
	commonRulesKey = "rules"
	// commonRulesDraft は共通レイヤーのルールファイルの下書きの置き場（ワークスペースルートからの相対）。
	commonRulesDraft = "wip/handoff/.ccnavi/config/common/rules.yml"
)

// Choices は戻す働きの設定が取る値。GateChoices は戻す働きを持たない切り替え（チケットの承認の経路など）の値で、
// dry-run が無い（通せば済んでしまい、報告では戻らない）。
var (
	Choices     = []config.Mode{config.Enable, config.DryRun, config.Disable}
	GateChoices = []config.Mode{config.Enable, config.Disable}
)

// Resolve は設定の値を解く。読めない値（allowed に無い語も）は enable にし、stderr に言う。書き損じた 1 語で保護が
// 消えるより、残るほうがよい。
func Resolve(stderr io.Writer, flag, declared, name string, allowed []config.Mode) config.Mode {
	written := flag
	if written == "" {
		written = declared
	}
	value := strings.ToLower(ospath.Strip(written))
	if value == "" {
		return config.Enable
	}
	names := make([]string, len(allowed))
	for i, a := range allowed {
		if string(a) == value {
			return a
		}
		names[i] = string(a)
	}
	fmt.Fprintf(stderr, "ccnavi: %s=%s は設定ではありません。%s を使います。有効な値は %s です\n",
		name, pyfmt.Quote(value), config.Enable, strings.Join(names, ", "))
	return config.Enable
}

// Action は対象 1 つについて何をしたか。報告と記録（`<key>:<action>`）に出る。
type Action string

const (
	ActionKept        Action = "kept"              // 変わっていない（報告には出さない）
	ActionRestored    Action = "restored"          // バックアップから戻した
	ActionRestoredGit Action = "restored-from-git" // バックアップが無く、git から戻した
	ActionMissing     Action = "missing"           // 対象が無く、戻す先も無い
	ActionFailed      Action = "failed"            // 戻そうとして駄目だった
	ActionWould       Action = "would-restore"     // dry-run。戻す代わりに言うだけ
	ActionRebased     Action = "rebased"           // 大きい対象が変わったので、新しい版を基準に取り直した
	ActionAccepted    Action = "accepted"          // ユーザの発言で、消えたことを受け入れた
)

// Outcome は対象 1 つについて、この 1 回で何をしたか。
type Outcome struct {
	Target Target
	Action Action
	Detail string
}

// Step は Before・After・Accept・AtStart の形（呼び手が契機ごとに選ぶ）。
type Step func(setting config.Mode, stateDir, session, root string, found []Target) []Outcome

// Before は実行前。在る対象の断面をバックアップし、消されていれば戻してからバックアップする。内容の照合はしない
// （ユーザがエディタで直している最中の 1 文字を消さない）。最初から無い対象（settings.local.json を置かないプロジェクト）は
// 印を残し、以後は何も言わない。大きい対象は大きさと更新時刻が変わったときだけ、新しい版を基準に取り直す（enable のとき）。
func Before(setting config.Mode, stateDir, session, root string, found []Target) []Outcome {
	if setting == config.Disable || stateDir == "" {
		return nil
	}
	var outcomes []Outcome
	for _, t := range found {
		b := backup{stateDir, session, t}
		if t.Heavy {
			// 参照は書き直さないので、掃除に古いと読まれないよう更新時刻だけを新しくする。
			refresh(b.ref())
			if setting == config.Enable {
				if o, ok := b.rebaseIfChanged(); ok {
					outcomes = append(outcomes, o)
				}
			}
			continue
		}
		if content, ok := fsio.ReadBytes(t.Path); ok {
			b.clearAbsent()
			if failed := b.write(content); failed != "" {
				outcomes = append(outcomes, Outcome{t, ActionFailed, "バックアップを保存できませんでした: " + failed})
			}
			// 取れた回は報告に出さない（毎回 3 行増えると、言うべき 1 行が埋もれる）。
			continue
		}
		if b.absentNoted() {
			refresh(b.absent())
			continue
		}
		saved, hasSaved := b.read()
		outcome := recoverMissing(setting, root, t, saved, hasSaved)
		content, ok := fsio.ReadBytes(t.Path)
		if !ok {
			// 戻せず、バックアップも無いなら、置かれていないファイル。印を残して次から何も言わない。
			if !hasSaved {
				b.noteAbsent()
				continue
			}
			outcomes = append(outcomes, outcome)
			continue
		}
		outcomes = append(outcomes, outcome)
		b.write(content)
	}
	return outcomes
}

// After は実行後。バックアップと違えば戻す。バックアップが読めなければ git のコミット済みの内容を使い、そう報告する
// （ユーザの書きかけが残っているかが変わる）。
func After(setting config.Mode, stateDir, session, root string, found []Target) []Outcome {
	if setting == config.Disable || stateDir == "" {
		return nil
	}
	var outcomes []Outcome
	for _, t := range found {
		b := backup{stateDir, session, t}
		if t.Heavy {
			if o, ok := b.checkHeavy(setting); ok {
				outcomes = append(outcomes, o)
			}
			continue
		}
		saved, hasSaved := b.read()
		now, hasNow := fsio.ReadBytes(t.Path)
		if hasSaved && hasNow && bytes.Equal(now, saved) {
			continue
		}
		if !hasSaved {
			if !hasNow {
				continue
			}
			if b.absentNoted() {
				// 無かったところに現れた。ユーザが置くこともあり、消すほうが取り返しが付かないので言うだけ。
				outcomes = append(outcomes, Outcome{t, ActionMissing,
					"無かったところに設定ファイルが現れた。hook の登録が増えているかもしれないので、中身をユーザに見てもらってください"})
				continue
			}
			outcomes = append(outcomes, fallBackToGit(setting, root, t, hasNow))
			continue
		}
		if setting != config.Enable {
			outcomes = append(outcomes, Outcome{t, ActionWould, "本来ならバックアップから戻す（今回は触っていない）"})
			continue
		}
		if failed := fsio.WriteFile(t.Path, saved, fsio.Plain); failed != "" {
			outcomes = append(outcomes, Outcome{t, ActionFailed, "戻せない: " + failed})
		} else {
			outcomes = append(outcomes, Outcome{t, ActionRestored, "このツール呼び出しの直前の内容に戻した"})
		}
	}
	var reported []Outcome
	for _, o := range outcomes {
		if o.Action != ActionKept {
			reported = append(reported, o)
		}
	}
	return reported
}

// Accept はユーザの発言で、前のターンの終わりからの変更をユーザの手によるものとして受け入れる（基準を取り直すだけで戻さない。
// dry-run でも取り直す）。消えたものを受け入れるのはここだけ。在るのに読めないもの（権限・壊れたリンク）は消えたことにしない。
// 実行ファイルは開始で基準を取ったものだけ取り直す。バックグラウンド作業の完了通知では呼ばない（呼び手）。
func Accept(setting config.Mode, stateDir, session, root string, found []Target) []Outcome {
	if setting == config.Disable || stateDir == "" {
		return nil
	}
	var outcomes []Outcome
	for _, t := range found {
		b := backup{stateDir, session, t}
		if t.Heavy {
			if ospath.DiskLexists(t.Path) {
				if o, ok := b.rebaseIfChanged(); ok {
					outcomes = append(outcomes, o)
				}
			} else if _, ok := b.readRef(); ok {
				fsio.Remove(b.ref())
				b.noteAbsent()
				outcomes = append(outcomes, Outcome{t, ActionAccepted, "消えたことを受け入れた"})
			}
			continue
		}
		content, ok := fsio.ReadBytes(t.Path)
		if !ok {
			if ospath.DiskLexists(t.Path) {
				continue
			}
			if _, had := b.read(); had {
				outcomes = append(outcomes, Outcome{t, ActionAccepted, "消えたことを受け入れた"})
			}
			fsio.Remove(b.path())
			b.noteAbsent()
			continue
		}
		b.clearAbsent()
		if failed := b.write(content); failed != "" {
			outcomes = append(outcomes, Outcome{t, ActionFailed, "バックアップを保存できませんでした: " + failed})
		}
	}
	return outcomes
}

// AtStart はセッション開始。古いバックアップを消し、大きい対象をバックアップする（戻さない。dry-run でも取る）。
// 小さい対象は最初の発言で Accept が取る（開始から最初の発言までの変更はユーザの手によるもので、戻す根拠が無い）。
func AtStart(setting config.Mode, stateDir, session, root string, found []Target) []Outcome {
	if setting == config.Disable || stateDir == "" {
		return nil
	}
	sweep(stateDir, session)
	var outcomes []Outcome
	for _, t := range found {
		if !t.Heavy {
			continue
		}
		if !ospath.DiskExists(t.Path) {
			outcomes = append(outcomes, Outcome{t, ActionMissing,
				"実行ファイルが見つからない。" + env.EntryPath + " のパスを確かめてください"})
			continue
		}
		if failed := (backup{stateDir, session, t}).saveHeavy(); failed != "" {
			outcomes = append(outcomes, Outcome{t, ActionFailed, "バックアップを取れない: " + failed})
		}
	}
	return outcomes
}

// sweep は keepFor より長く触られていないセッションの置き場と、どこからも参照されない古い実体を消す。件数ではなく日付で
// 切る（長く返事を待っているだけの生きたセッションを消さない）。自分の置き場は日付を見ずに残す。失敗しても開始は続ける。
func sweep(stateDir, session string) {
	if stateDir == "" {
		return
	}
	root := ospath.Join(stateDir, BackupDir)
	names, err := ospath.DiskListdir(root)
	if err != nil {
		return
	}
	cutoff := time.Now().Add(-keepFor)
	mine := safeSession(session)
	var kept []string
	for _, name := range names {
		path := ospath.Join(root, name)
		if name == mine || name == StoreDir || !ospath.IsDir(path) || !touched(path).Before(cutoff) {
			kept = append(kept, name)
			continue
		}
		// リンクの先は消さない（Python の shutil.rmtree はリンクを消さずに失敗する）。
		if !ospath.IsLink(path) {
			os.RemoveAll(path)
		}
	}
	sweepStore(root, kept, cutoff)
}

// touched は置き場がいちばん最後に触られた時刻（中身を書き換えても親の時刻が動かないファイルシステムがある）。
func touched(path string) time.Time {
	var newest time.Time
	names, err := ospath.DiskListdir(path)
	if err != nil {
		return newest
	}
	for _, entry := range append([]string{path}, joinAll(path, names)...) {
		if mtime, _, ok := ospath.Stat(entry); ok && mtime.After(newest) {
			newest = mtime
		}
	}
	return newest
}

func joinAll(dir string, names []string) []string {
	out := make([]string, len(names))
	for i, n := range names {
		out[i] = ospath.Join(dir, n)
	}
	return out
}

// sweepStore は残るセッションの参照に出てこず、cutoff より古い実体を消す（参照だけ・時刻だけのどちらでも足りない）。
func sweepStore(root string, kept []string, cutoff time.Time) {
	store := ospath.Join(root, StoreDir)
	entries, err := ospath.DiskListdir(store)
	if err != nil {
		return
	}
	alive := map[string]bool{}
	for _, name := range kept {
		directory := ospath.Join(root, name)
		files, err := ospath.DiskListdir(directory)
		if err != nil {
			continue
		}
		for _, f := range files {
			if !strings.HasSuffix(f, refSuffix) {
				continue
			}
			if r, ok := parseRef(fsio.ReadBytes(ospath.Join(directory, f))); ok {
				alive[r.digest] = true
			}
		}
	}
	for _, name := range entries {
		if alive[name] {
			continue
		}
		path := ospath.Join(store, name)
		if mtime, _, ok := ospath.Stat(path); !ok || !mtime.Before(cutoff) {
			continue
		}
		os.Remove(path)
	}
}

// backup はセッション 1 つの、対象 1 つのバックアップの置き場。
type backup struct {
	stateDir, session string
	target            Target
}

// path はバックアップの置き場。key はレイヤーで `:` を含む（Windows では代替データストリームになる）ので、使える字に置き換える。
// 切り詰めない（ワークツリー側の key の末尾の digest を削ると、別のワークツリーと同じ名前になる）。
func (b backup) path() string {
	return ospath.Join(b.stateDir, BackupDir, safeSession(b.session), fsio.SafeName(b.target.Key, fsio.NoLimit))
}

func (b backup) ref() string    { return b.path() + refSuffix }
func (b backup) absent() string { return b.path() + absentSuffix }

func (b backup) read() ([]byte, bool) { return fsio.ReadBytes(b.path()) }

// write はバックアップを取る。同じ中身なら書かず、古くなったときだけ更新時刻を新しくする。書けたら空、駄目なら理由。
func (b backup) write(content []byte) string {
	if saved, ok := b.read(); ok && bytes.Equal(saved, content) {
		refresh(b.path())
		return ""
	}
	return fsio.WriteFile(b.path(), content, fsio.Plain)
}

func (b backup) absentNoted() bool { return ospath.DiskExists(b.absent()) }
func (b backup) noteAbsent()       { fsio.WriteFile(b.absent(), nil, fsio.Plain) }
func (b backup) clearAbsent()      { os.Remove(b.absent()) }

func (b backup) readRef() (heavyRef, bool) { return parseRef(fsio.ReadBytes(b.ref())) }

// saveHeavy は大きい対象をバックアップする。先にコピーしてからその中身のハッシュで名前を付ける（ハッシュを読んでから
// コピーすると、その間に書き換えられた版が読んだハッシュの名前で入る）。参照にはバックアップした時点の大きさと更新時刻を書く。
func (b backup) saveHeavy() string {
	mtime, size, err := statTimes(b.target.Path)
	if err != nil {
		return fsio.ErrorText(err)
	}
	scratch := b.storePath("scratch-"+randomHex()) + partSuffix
	if failed := copyFile(b.target.Path, scratch); failed != "" {
		return failed
	}
	digest := fileDigest(scratch)
	if digest == "" {
		os.Remove(scratch)
		return "読めない: " + b.target.Path
	}
	entry := b.storePath(digest)
	if ospath.DiskExists(entry) {
		os.Remove(scratch)
	} else if err := os.Rename(scratch, entry); err != nil {
		os.Remove(scratch)
		return fsio.ErrorText(err)
	}
	// 最後に使った時刻にする（掃除の基準）。
	now := time.Now()
	os.Chtimes(entry, now, now)
	ref := fmt.Sprintf("%s %d %d\n", digest, size, mtime.Unix())
	if failed := fsio.WriteFile(b.ref(), []byte(ref), fsio.Plain); failed != "" {
		return failed
	}
	return ""
}

func (b backup) storePath(name string) string {
	return ospath.Join(b.stateDir, BackupDir, StoreDir, name)
}

// checkHeavy は実行後、大きい対象を大きさと更新時刻で照合し、変わっていれば直前の版に戻す（中身は読まない）。
// 参照が無いのは、開始のイベントを登録していないか実行ファイルを指していない設定で、異常ではない。
func (b backup) checkHeavy(setting config.Mode) (Outcome, bool) {
	t := b.target
	r, ok := b.readRef()
	if !ok {
		// ユーザが消したもの（Accept）がこの呼び出しの中で現れた。判定器が差し替わっていないか見る機会を残し、消さない。
		if b.absentNoted() && ospath.DiskExists(t.Path) {
			return Outcome{t, ActionMissing,
				"ユーザが消したあとに、実行ファイルが現れた。判定器が差し替わっているかもしれないので、中身をユーザに見てもらってください"}, true
		}
		return Outcome{}, false
	}
	if sameShape(t.Path, r) {
		return Outcome{}, false
	}
	if setting != config.Enable {
		return Outcome{t, ActionWould, "このツール呼び出しで変わった。enable なら直前の版に戻す（今回は触っていない）"}, true
	}
	return b.restoreHeavy(r), true
}

// restoreHeavy は実行ファイルを参照の版に戻す。今の版は消す前に aside/ へ退避し、退避できなければ戻さない。
// 実体はコピーなので、更新時刻を参照の時刻に戻す（戻さないと次の照合で毎回一致しない）。
func (b backup) restoreHeavy(r heavyRef) Outcome {
	t := b.target
	entry := b.storePath(r.digest)
	if !ospath.DiskExists(entry) {
		return Outcome{t, ActionFailed, "バックアップの実体が見つからないので戻せない。ccnavi を作り直して、セッションを開き直してください"}
	}
	existed := ospath.DiskExists(t.Path)
	kept := ""
	if existed {
		kept = ospath.Join(b.stateDir, asideDir, time.Now().Format(asideStamp)+"-"+ospath.Basename(t.Path))
		if failed := copyFile(t.Path, kept); failed != "" {
			return Outcome{t, ActionFailed, "退避できないので戻さない: " + failed}
		}
	}
	if failed := replaceWith(entry, t.Path); failed != "" {
		return Outcome{t, ActionFailed, "戻せない: " + failed}
	}
	stamp := time.Unix(r.mtime, 0)
	os.Chtimes(t.Path, stamp, stamp)
	if existed {
		return Outcome{t, ActionRestored, "このツール呼び出しで変わったので、直前の版に戻した（書かれていた版は " + kept + "）"}
	}
	return Outcome{t, ActionRestored, "このツール呼び出しで消えたので、直前の版に戻した"}
}

// rebaseIfChanged は大きさか更新時刻が変わっていれば、ツール呼び出しの外の変更として今の版を基準に取り直す。
func (b backup) rebaseIfChanged() (Outcome, bool) {
	t := b.target
	r, ok := b.readRef()
	if !ok {
		// ユーザが消したあとまた置かれた。新しい版を基準にする（参照が無いままだと残りのセッションで守れない）。
		if b.absentNoted() && ospath.DiskExists(t.Path) {
			b.clearAbsent()
			return b.rebaseHeavy(), true
		}
		// PATH の実体は開始のあとに現れることがある。最初に見た版を基準にする。
		if strings.HasPrefix(t.Key, PathKey) && ospath.DiskExists(t.Path) {
			return b.rebaseHeavy(), true
		}
		return Outcome{}, false
	}
	if sameShape(t.Path, r) || !ospath.DiskExists(t.Path) {
		return Outcome{}, false
	}
	return b.rebaseHeavy(), true
}

func (b backup) rebaseHeavy() Outcome {
	if failed := b.saveHeavy(); failed != "" {
		return Outcome{b.target, ActionFailed, "新しい版を基準に取れない: " + failed}
	}
	return Outcome{b.target, ActionRebased, "ツール呼び出しの外で変わったので、その版を基準にし直した"}
}

// heavyRef は大きい対象の参照（実体のハッシュと、バックアップした時点の大きさと更新時刻の秒）。
type heavyRef struct {
	digest      string
	size, mtime int64
}

// parseRef は参照を読む。読めない形はバックアップが無いとして扱う。
func parseRef(raw []byte, ok bool) (heavyRef, bool) {
	if !ok || len(raw) == 0 {
		return heavyRef{}, false
	}
	parts := strings.FieldsFunc(strings.ToValidUTF8(string(raw), "�"), ospath.IsSpace)
	if len(parts) != 3 {
		return heavyRef{}, false
	}
	size, err1 := strconv.ParseInt(parts[1], 10, 64)
	mtime, err2 := strconv.ParseInt(parts[2], 10, 64)
	if err1 != nil || err2 != nil {
		return heavyRef{}, false
	}
	return heavyRef{parts[0], size, mtime}, true
}

// sameShape は現物がバックアップした時点の大きさと更新時刻（秒未満は切り捨て）に一致するか。
func sameShape(path string, r heavyRef) bool {
	mtime, size, err := statTimes(path)
	return err == nil && size == r.size && mtime.Unix() == r.mtime
}

func statTimes(path string) (time.Time, int64, error) {
	info, err := os.Stat(path)
	if err != nil {
		return time.Time{}, 0, err
	}
	return info.ModTime(), info.Size(), nil
}

// fileDigest は中身の sha256。読めなければ空。
func fileDigest(path string) string {
	f, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return ""
	}
	return hex.EncodeToString(h.Sum(nil))
}

// replaceWith は別の名前へコピーしてから置き換える（中断したコピーを揃ったものとして拾わせない）。
func replaceWith(source, destination string) string {
	part := destination + partSuffix
	if failed := copyFile(source, part); failed != "" {
		os.Remove(part)
		return failed
	}
	if err := os.Rename(part, destination); err != nil {
		os.Remove(part)
		return fsio.ErrorText(err)
	}
	return ""
}

// copyFile は中身と権限と更新時刻ごとコピーする（shutil.copy2。実行ファイルの実行の権限を落とさない）。
// TODO(infra): 汎用の部品なので fsio に置くのがよい。
func copyFile(source, target string) string {
	if err := os.MkdirAll(ospath.Dirname(target), 0o777); err != nil {
		return fsio.ErrorText(err)
	}
	in, err := os.Open(source)
	if err != nil {
		return fsio.ErrorText(err)
	}
	defer in.Close()
	info, err := in.Stat()
	if err != nil {
		return fsio.ErrorText(err)
	}
	out, err := os.OpenFile(target, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, info.Mode().Perm())
	if err != nil {
		return fsio.ErrorText(err)
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return fsio.ErrorText(err)
	}
	if err := out.Close(); err != nil {
		return fsio.ErrorText(err)
	}
	if err := os.Chmod(target, info.Mode().Perm()); err != nil {
		return fsio.ErrorText(err)
	}
	if err := os.Chtimes(target, time.Now(), info.ModTime()); err != nil {
		return fsio.ErrorText(err)
	}
	return ""
}

func randomHex() string {
	var b [16]byte
	rand.Read(b[:])
	return hex.EncodeToString(b[:])
}

// refresh は更新時刻が refreshAfter より古ければ新しくする（中身には触らない）。
func refresh(path string) {
	mtime, _, ok := ospath.Stat(path)
	if ok && time.Since(mtime) > refreshAfter {
		now := time.Now()
		os.Chtimes(path, now, now)
	}
}

// safeSession はセッション識別子から作る置き場の名前（区切り文字で別の場所へ書かせない）。
func safeSession(session string) string {
	if session == "" {
		session = noSession
	}
	return fsio.SafeName(session, fsio.NoLimit)
}

// recoverMissing は実行前に対象が無かったとき、バックアップ（このセッションで最後に見た内容）か、無ければ git から戻す。
func recoverMissing(setting config.Mode, root string, t Target, saved []byte, hasSaved bool) Outcome {
	if setting != config.Enable {
		return Outcome{t, ActionWould, "対象がありません。本来なら戻す"}
	}
	if hasSaved {
		if failed := fsio.WriteFile(t.Path, saved, fsio.Plain); failed != "" {
			return Outcome{t, ActionFailed, "消えた対象を戻せない: " + failed}
		}
		return Outcome{t, ActionRestored, "消えていたので、直前の内容に戻した"}
	}
	if failed := restoreCommitted(root, t); failed != "" {
		return Outcome{t, ActionMissing, "対象が無く、git からも戻せない: " + failed}
	}
	return Outcome{t, ActionRestoredGit, "対象が無かったので、コミット済みの内容から戻した"}
}

// fallBackToGit はバックアップが無いまま実行後に来たとき。git が変わっていないと言うなら何もしない（毎回 restore すると、
// 何も起きていない呼び出しがファイルを書き換える）。
func fallBackToGit(setting config.Mode, root string, t Target, hasNow bool) Outcome {
	top := gitTop(root, t)
	changes, unreadable := git.Read(top, 0)
	if unreadable != "" {
		if !hasNow {
			return Outcome{t, ActionMissing, "対象もバックアップも無く、git も読めない: " + string(unreadable)}
		}
		return Outcome{t, ActionFailed, "バックアップが無く、git も読めない: " + string(unreadable)}
	}
	dirty := false
	for _, c := range changes {
		dirty = dirty || ospath.Realpath(c.Full) == t.Path
	}
	if !dirty && hasNow {
		return Outcome{t, ActionKept, "バックアップは無いが、コミット済みの内容と同じ"}
	}
	if setting != config.Enable {
		return Outcome{t, ActionWould, "バックアップがありません。本来なら git から戻す"}
	}
	if failed := restoreCommitted(root, t); failed != "" {
		return Outcome{t, ActionFailed, "バックアップが無く、git からも戻せない: " + failed}
	}
	return Outcome{t, ActionRestoredGit,
		"バックアップが無いのでコミット済みの内容へ戻した。コミットしていない編集があったなら、それは残っていない"}
}

func restoreCommitted(root string, t Target) string {
	top := gitTop(root, t)
	return git.RestoreCommitted(top, strings.ReplaceAll(relative(top, t.Path), ospath.Sep, "/"))
}
