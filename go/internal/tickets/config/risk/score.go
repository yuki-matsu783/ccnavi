package risk

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

const (
	// scriptTimeout は script: の項目に与える時間。
	scriptTimeout = 30 * time.Second
	// gitTimeout は差分を数える git に与える時間。閉じるときにしか走らないので、判定より長くてよい。
	gitTimeout = 10 * time.Second
	// shownHits は glob の項目の内訳に並べるパスの数。
	shownHits = 3
	// shownOutput はスクリプトの読めない出力を苦情に添える長さ。
	shownOutput = 60
	// shownCommit は問いに出すコミットの桁。
	shownCommit = 12
	// shell は script: を走らせるシェル。
	shell = "sh"
)

// Change は差分の 1 ファイル。Status は git の name-status の 1 文字目（A・M・D・R など）、Old は rename・copy の
// 元のパス（無ければ空）。バイナリの変更は行数 0。
type Change struct {
	Path           string
	Added, Deleted int
	Status         string
	Old            string
}

// Diff は子の仕事の始まりから先頭までの差分。Head は測った先のコミットの sha。
type Diff struct {
	Changes    []Change
	Base, Head string
}

// Lines は足した行と消した行の和。
func (d *Diff) Lines() int {
	n := 0
	for _, c := range d.Changes {
		n += c.Added + c.Deleted
	}
	return n
}

// Files は変えたファイルの数。
func (d *Diff) Files() int { return len(d.Changes) }

// DeletedFiles は消したファイルの数。
func (d *Diff) DeletedFiles() int {
	n := 0
	for _, c := range d.Changes {
		if strings.HasPrefix(c.Status, "D") {
			n++
		}
	}
	return n
}

// Summary は差分の 1 行の要約。
func (d *Diff) Summary() string {
	added, deleted := 0, 0
	for _, c := range d.Changes {
		added += c.Added
		deleted += c.Deleted
	}
	return fmt.Sprintf("%d ファイル、+%d -%d 行、削除したファイル %d", d.Files(), added, deleted, d.DeletedFiles())
}

// changeSet は path で引ける、入れた順の変更（同じ path は位置をそのままに置き換える）。
type changeSet struct {
	list []Change
	at   map[string]int
}

func (s *changeSet) put(c Change) {
	if s.at == nil {
		s.at = map[string]int{}
	}
	if i, ok := s.at[c.Path]; ok {
		s.list[i] = c
		return
	}
	s.at[c.Path] = len(s.list)
	s.list = append(s.list, c)
}

func (s *changeSet) get(path string) *Change {
	if i, ok := s.at[path]; ok {
		return &s.list[i]
	}
	return nil
}

func (s *changeSet) drop(path string) {
	i, ok := s.at[path]
	if !ok {
		return
	}
	s.list = append(s.list[:i], s.list[i+1:]...)
	delete(s.at, path)
	for p, j := range s.at {
		if j > i {
			s.at[p] = j - 1
		}
	}
}

func slashed(p string) string { return strings.ReplaceAll(p, `\`, "/") }

// count は git の numstat の数。バイナリは `-` で 0 と数える。
func count(text string) int {
	n, err := pyfmt.ParseInt(text)
	if err != nil {
		return 0
	}
	return n
}

// Measure は base..head の差分を数える。読めなければ nil と理由（0 件と数えると軽い側へ倒れるので、
// name-status や head を読めなくても止める）。統合先を取り込んだ merge が持ち込んだだけのパスは、子が書いた
// ものではないので数えない。Diff.Head は head の sha。
func Measure(worktree, base, head string) (*Diff, string) {
	if base == "" {
		return nil, "子の仕事の始まり（親との共通の祖先）がありません。"
	}
	opts := git.Options{Timeout: gitTimeout, RawPaths: true}
	span := base + ".." + head
	code, out := git.Output(worktree, []string{"diff", "--numstat", "-z", span}, opts)
	if code != 0 {
		return nil, "子の仕事の始まりからの差分を読めない"
	}
	changes := &changeSet{}
	// -z の numstat は `added\tdeleted\tpath\0`。rename は `added\tdeleted\t\0old\0new\0`。
	parts := strings.Split(out, "\x00")
	for i := 0; i < len(parts); {
		entry := parts[i]
		i++
		if entry == "" {
			continue
		}
		cols := strings.Split(entry, "\t")
		if len(cols) < 3 {
			continue
		}
		path, old := cols[2], ""
		if path == "" && i+1 < len(parts) {
			old, path = slashed(parts[i]), parts[i+1]
			i += 2
		}
		path = slashed(path)
		changes.put(Change{Path: path, Added: count(cols[0]), Deleted: count(cols[1]), Status: "M", Old: old})
	}
	code, out = git.Output(worktree, []string{"diff", "--name-status", "-z", span}, opts)
	if code != 0 {
		return nil, "子の仕事の始まりからの変更の種類を読めない"
	}
	parts = strings.Split(out, "\x00")
	for i := 0; i < len(parts); {
		status := parts[i]
		i++
		if status == "" {
			continue
		}
		var path string
		switch {
		case (strings.HasPrefix(status, "R") || strings.HasPrefix(status, "C")) && i+1 < len(parts):
			path = slashed(parts[i+1])
			i += 2
		case i < len(parts):
			path = slashed(parts[i])
			i++
		default:
			i = len(parts)
			continue
		}
		kind := pyfmt.Head(status, 1)
		if c := changes.get(path); c != nil {
			c.Status = kind
		} else {
			changes.put(Change{Path: path, Status: kind})
		}
	}
	for path := range git.TakenFromRemote(worktree, base, head, gitTimeout) {
		changes.drop(path)
	}
	code, sha := git.Output(worktree, []string{"rev-parse", head}, opts)
	if sha = ospath.Strip(sha); code != 0 || sha == "" {
		return nil, "測った先のコミットを読めない"
	}
	return &Diff{Changes: changes.list, Base: base, Head: sha}, ""
}

// Hit は加点した項目。Source はその項目が書いてあるレイヤー（記録に残す）。
type Hit struct {
	ID     string
	Points int
	Detail string
	Source string
}

// Score は差分と判定から出した点。Unmeasured は測れなかった項目（重いほうとして加点済み）、Pending は判定の
// 無い定性の項目（点にはまだ含まれない。揃うまで子は閉じられない）。
type Score struct {
	Points     int
	Level      Level
	Hits       []Hit
	Unmeasured []string
	Pending    []*Factor
}

// Lines はユーザ向けの内訳。
func (s *Score) Lines() []string {
	out := []string{fmt.Sprintf("リスク: %d (%s)", s.Points, s.Level)}
	for _, h := range s.Hits {
		out = append(out, fmt.Sprintf("  +%d %s", h.Points, h.Detail))
	}
	for _, u := range s.Unmeasured {
		out = append(out, "  ! "+u)
	}
	return out
}

// Object は記録に書く形（points・level・hits・unmeasured）。
func (s *Score) Object() *pyjson.Object {
	hits := []any{}
	for _, h := range s.Hits {
		hits = append(hits, pyjson.NewObject("id", h.ID, "points", h.Points, "detail", h.Detail, "source", h.Source))
	}
	unmeasured := []any{}
	for _, u := range s.Unmeasured {
		unmeasured = append(unmeasured, u)
	}
	return pyjson.NewObject("points", s.Points, "level", string(s.Level), "hits", hits, "unmeasured", unmeasured)
}

// scoped は項目の include・exclude で絞った差分。どちらも無ければ差分そのもの。
func scoped(d *Diff, f *Factor) *Diff {
	if len(f.include) == 0 && len(f.exclude) == 0 {
		return d
	}
	var kept []Change
	for _, c := range d.Changes {
		if f.inScope(c) {
			kept = append(kept, c)
		}
	}
	return &Diff{Changes: kept, Base: d.Base, Head: d.Head}
}

// Evaluate は差分と判定から点を出す。env は script: に足す環境変数。judgements は子の定性の判定の記録
// （項目の id → {head, hit, reason}）で、いまの差分の head と同じ判定だけを使い、無ければ Pending に積む。
// 測れなかった script: は配点の全部を加点する（0 と数えて軽い側へ倒さない）。
func Evaluate(d *Definition, diff *Diff, root, worktree string, env map[string]string, judgements *pyjson.Object) *Score {
	score := &Score{}
	for _, f := range d.Factors {
		hit := func(points int, detail string) {
			score.Hits = append(score.Hits, Hit{f.ID, points, detail, f.Source})
		}
		switch f.Kind {
		case KindLines:
			if n := scoped(diff, f).Lines(); n > f.Limit {
				hit(f.Points, fmt.Sprintf("%s（%d 行 > %d）", f.Message, n, f.Limit))
			}
		case KindFiles:
			if n := scoped(diff, f).Files(); n > f.Limit {
				hit(f.Points, fmt.Sprintf("%s（%d ファイル > %d）", f.Message, n, f.Limit))
			}
		case KindDeleted:
			if n := scoped(diff, f).DeletedFiles(); n > f.Limit {
				hit(f.Points, fmt.Sprintf("%s（削除した %d > %d）", f.Message, n, f.Limit))
			}
		case KindGlob:
			var paths []string
			for _, c := range diff.Changes {
				if f.matches(c.Path) {
					paths = append(paths, c.Path)
				}
			}
			if len(paths) == 0 {
				continue
			}
			points := f.Points * len(paths)
			if f.HasMax {
				points = min(points, f.Max)
			}
			shown := strings.Join(paths[:min(len(paths), shownHits)], ", ")
			if len(paths) > shownHits {
				shown += "…"
			}
			hit(points, f.Message+"（"+shown+"）")
		case KindScript:
			// 解く基準はそのレイヤーの git プロジェクトルート。
			home := f.Home
			if home == "" {
				home = root
			}
			points, note, ok := runScript(home, f.Text, worktree, env)
			switch {
			case !ok:
				hit(f.Points, f.Message+"（測れなかった: "+note+"）")
				score.Unmeasured = append(score.Unmeasured, f.ID+": "+note)
			case points > 0:
				if note == "" {
					note = "スクリプト"
				}
				hit(points, f.Message+"（"+note+"）")
			}
		case KindJudge:
			verdict, _ := judgements.Value(f.ID).(*pyjson.Object)
			if head, ok := verdict.Value("head").(string); !ok || head != diff.Head {
				score.Pending = append(score.Pending, f)
				continue
			}
			if pyfmt.Truthy(verdict.Value("hit")) {
				reason := ""
				if v := verdict.Value("reason"); pyfmt.Truthy(v) {
					reason = ospath.Strip(pyfmt.Str(v))
				}
				hit(f.Points, f.Message+"（判定: "+reason+"）")
			}
		}
	}
	for _, h := range score.Hits {
		score.Points += h.Points
	}
	score.Level = d.LevelOf(score.Points)
	return score
}

// runScript はスクリプトを走らせて点を受け取る。出力は 0 以上の整数か、{"points": N, "message": …} の JSON。
// 測れなければ ok が偽で、note が理由。
func runScript(root, rel, worktree string, env map[string]string) (points int, note string, ok bool) {
	path := ospath.Join(root, filepath.FromSlash(rel))
	if !ospath.IsFile(path) {
		return 0, rel + " がありません。", false
	}
	ctx, cancel := context.WithTimeout(context.Background(), scriptTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, shell, path)
	cmd.Dir = worktree
	cmd.Env = os.Environ()
	for k, v := range env {
		cmd.Env = append(cmd.Env, k+"="+v)
	}
	cmd.WaitDelay = time.Second
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	out, _ := fsio.Decode(stdout.Bytes(), fsio.Replace)
	var exit *exec.ExitError
	switch {
	case ctx.Err() != nil:
		return 0, rel + " を走らせられない (TimeoutExpired)", false
	case errors.As(err, &exit):
		said, _ := fsio.Decode(stderr.Bytes(), fsio.Replace)
		if said == "" {
			said = out
		}
		text := rel + " が " + strconv.Itoa(exit.ExitCode()) + " で終わった"
		if tail := ospath.Splitlines(ospath.Strip(said)); len(tail) > 0 {
			text += ": " + tail[len(tail)-1]
		}
		return 0, text, false
	case err != nil:
		return 0, rel + " を走らせられない (" + startFailure(err) + ")", false
	}
	text := ospath.Strip(out)
	if text == "" {
		return 0, rel + " が何も出さなかった", false
	}
	data, _ := pyjson.Loads(text)
	if obj, isObject := data.(*pyjson.Object); isObject {
		if n, ok := nonNegative(obj.Value("points")); ok {
			message := ""
			if v := obj.Value("message"); pyfmt.Truthy(v) {
				message = pyfmt.Str(v)
			}
			return n, message, true
		}
		return 0, rel + " の JSON に `points`（0 以上の整数）がありません。", false
	}
	if n, ok := nonNegative(data); ok {
		return n, "", true
	}
	return 0, rel + " の出力を点として読めない: " + pyfmt.Head(text, shownOutput), false
}

// startFailure は起動できなかった理由の、Python の例外の型の名前。
func startFailure(err error) string {
	switch {
	case errors.Is(err, exec.ErrNotFound), errors.Is(err, fs.ErrNotExist):
		return "FileNotFoundError"
	case errors.Is(err, fs.ErrPermission):
		return "PermissionError"
	}
	return "OSError"
}

// JudgePrompt は親がサブエージェントに渡す、定性の項目の問いと差分の要約。root は sh のパスに使う。
func JudgePrompt(child, parent string, diff *Diff, pending []*Factor, worktree, root string) string {
	lines := []string{
		"# " + child + " のリスク判定（定性）",
		"",
		"親: " + parent + "。ワークツリー: " + worktree + "。差分: `" + pyfmt.Head(diff.Base, shownCommit) + ".." +
			pyfmt.Head(diff.Head, shownCommit) + "`（" + diff.Summary() + "）。",
		"",
		"次の問いに、差分を読んで yes / no で答え、根拠を 1〜3 行で書いてください。",
		"判断するのはこの文書を渡されたサブエージェント。記録するのは親で、",
		"'" + config.ScriptCommand(root, config.TicketWrapper) + " record-risk " + child + " <項目> yes|no " +
			"--reason <根拠>' で 1 項目ずつ。",
		"",
	}
	for _, f := range pending {
		lines = append(lines, fmt.Sprintf("## %s（yes なら +%d）", f.ID, f.Points), "", f.Text, "")
	}
	lines = append(lines, "## 変更したファイル", "")
	changes := append([]Change(nil), diff.Changes...)
	sort.SliceStable(changes, func(i, j int) bool { return changes[i].Path < changes[j].Path })
	for _, c := range changes {
		lines = append(lines, fmt.Sprintf("- %s %s (+%d -%d)", c.Status, c.Path, c.Added, c.Deleted))
	}
	lines = append(lines, "")
	return strings.Join(lines, "\n")
}
