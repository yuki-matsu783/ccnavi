package risk

import (
	"errors"
	"fmt"
	"io/fs"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/shell"
)

const (
	// shownHits は glob の項目の内訳に並べるパスの数。
	shownHits = 3
	// shownOutput はスクリプトの読めない出力を苦情に添える長さ。
	shownOutput = 60
)

// Change は差分の 1 ファイル。status は git の name-status の 1 文字目（A・M・D・R など）、old は rename・copy の
// 元のパス（無ければ空）。バイナリの変更は行数 0。
type Change struct {
	path           string
	added, deleted int
	status         string
	old            string
}

// Diff は子の仕事の始まりから先頭までの差分。Head は測った先のコミットの sha。
type Diff struct {
	changes    []Change
	Base, Head string
}

// lines は足した行と消した行の和。
func (d *Diff) lines() int {
	n := 0
	for _, c := range d.changes {
		n += c.added + c.deleted
	}
	return n
}

// files は変えたファイルの数。
func (d *Diff) files() int { return len(d.changes) }

// deletedFiles は消したファイルの数。
func (d *Diff) deletedFiles() int {
	n := 0
	for _, c := range d.changes {
		if strings.HasPrefix(c.status, "D") {
			n++
		}
	}
	return n
}

// Summary は差分の 1 行の要約。
func (d *Diff) Summary() string {
	added, deleted := 0, 0
	for _, c := range d.changes {
		added += c.added
		deleted += c.deleted
	}
	return fmt.Sprintf("%d ファイル、+%d -%d 行、削除したファイル %d", d.files(), added, deleted, d.deletedFiles())
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
	if i, ok := s.at[c.path]; ok {
		s.list[i] = c
		return
	}
	s.at[c.path] = len(s.list)
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
	// 1. --numstat でパスごとの追加・削除の行数を読む。パスは NUL 区切りで読む（改行や引用符を含む名前でも崩れない）。
	opts := git.Options{Timeout: git.RiskTimeout, RawPaths: true}
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
			old, path = ospath.Slashed(parts[i]), parts[i+1]
			i += 2
		}
		path = ospath.Slashed(path)
		changes.put(Change{path: path, added: count(cols[0]), deleted: count(cols[1]), status: "M", old: old})
	}
	// 2. --name-status で変更の種類（追加・削除・改名など）を足す。改名とコピーは元と先の 2 つのパスが続く。
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
			path = ospath.Slashed(parts[i+1])
			i += 2
		case i < len(parts):
			path = ospath.Slashed(parts[i])
			i++
		default:
			i = len(parts)
			continue
		}
		kind := pyfmt.Head(status, 1)
		if c := changes.get(path); c != nil {
			c.status = kind
		} else {
			changes.put(Change{path: path, status: kind})
		}
	}
	// 3. 統合先を取り込んだ merge が持ち込んだだけのパスは、子が書いたものではないので数えない。
	for path := range git.TakenFromRemote(worktree, base, head, git.RiskTimeout) {
		changes.drop(path)
	}
	// 4. 測った先のコミットの sha を残す。判定の記録は、この sha と同じ差分にだけ効く。
	code, sha := git.Output(worktree, []string{"rev-parse", head}, opts)
	if sha = ospath.Strip(sha); code != 0 || sha == "" {
		return nil, "測った先のコミットを読めない"
	}
	return &Diff{changes: changes.list, Base: base, Head: sha}, ""
}

// hit は加点した項目。source はその項目が書いてあるレイヤー（記録に残す）。
type hit struct {
	id     string
	points int
	detail string
	source string
}

// Score は差分と判定から出した点。unmeasured は測れなかった項目（重いほうとして加点済み）、Pending は判定の
// 無い定性の項目（点にはまだ含まれない。揃うまで子は閉じられない）。
type Score struct {
	points     int
	Level      Level
	hits       []hit
	unmeasured []string
	Pending    []*Factor
}

// Lines はユーザ向けの内訳。
func (s *Score) Lines() []string {
	out := []string{fmt.Sprintf("リスク: %d (%s)", s.points, s.Level)}
	for _, h := range s.hits {
		out = append(out, fmt.Sprintf("  +%d %s", h.points, h.detail))
	}
	for _, u := range s.unmeasured {
		out = append(out, "  ! "+u)
	}
	return out
}

// Object は記録に書く形（points・level・hits・unmeasured）。
func (s *Score) Object() *pyjson.Object {
	hits := []any{}
	for _, h := range s.hits {
		hits = append(hits, pyjson.NewObject("id", h.id, "points", h.points, "detail", h.detail, "source", h.source))
	}
	unmeasured := []any{}
	for _, u := range s.unmeasured {
		unmeasured = append(unmeasured, u)
	}
	return pyjson.NewObject("points", s.points, "level", string(s.Level), "hits", hits, "unmeasured", unmeasured)
}

// scoped は項目の include・exclude で絞った差分。どちらも無ければ差分そのもの。
func scoped(d *Diff, f *Factor) *Diff {
	if len(f.include) == 0 && len(f.exclude) == 0 {
		return d
	}
	var kept []Change
	for _, c := range d.changes {
		if f.inScope(c) {
			kept = append(kept, c)
		}
	}
	return &Diff{changes: kept, Base: d.Base, Head: d.Head}
}

// Evaluate は差分と判定から点を出す。env は script: に足す環境変数。judgements は子の定性の判定の記録
// （項目の id → {head, hit, reason}）で、いまの差分の head と同じ判定だけを使い、無ければ Pending に積む。
// 測れなかった script: は配点の全部を加点する（0 と数えて軽い側へ倒さない）。
func Evaluate(d *Definition, diff *Diff, root, worktree string, env map[string]string, judgements *pyjson.Object) *Score {
	score := &Score{}
	// 項目を定義の順に当て、加点した項目（hits）と、測れなかった項目（unmeasured）、判定待ちの項目（Pending）を集める。
	for _, f := range d.Factors {
		hit := func(points int, detail string) {
			score.hits = append(score.hits, hit{f.ID, points, detail, f.Source})
		}
		switch f.Kind {
		case KindLines:
			if n := scoped(diff, f).lines(); n > f.Limit {
				hit(f.Points, fmt.Sprintf("%s（%d 行 > %d）", f.Message, n, f.Limit))
			}
		case KindFiles:
			if n := scoped(diff, f).files(); n > f.Limit {
				hit(f.Points, fmt.Sprintf("%s（%d ファイル > %d）", f.Message, n, f.Limit))
			}
		case KindDeleted:
			if n := scoped(diff, f).deletedFiles(); n > f.Limit {
				hit(f.Points, fmt.Sprintf("%s（削除した %d > %d）", f.Message, n, f.Limit))
			}
		// パスの一致。一致したパスの数だけ加点し、上限（max）があればそこで切る。
		case kindGlob:
			var paths []string
			for _, c := range diff.changes {
				if f.matches(c.path) {
					paths = append(paths, c.path)
				}
			}
			if len(paths) == 0 {
				continue
			}
			points := f.Points * len(paths)
			if f.hasMax {
				points = min(points, f.max)
			}
			shown := strings.Join(paths[:min(len(paths), shownHits)], ", ")
			if len(paths) > shownHits {
				shown += "…"
			}
			hit(points, f.Message+"（"+shown+"）")
		// スクリプトの出力を点にする。測れなければ、軽い側へ倒れないよう配点の全部を加点して unmeasured に残す。
		case KindScript:
			// 解く基準はそのレイヤーの git プロジェクトルート。
			home := f.home
			if home == "" {
				home = root
			}
			points, note, ok := runScript(home, f.Text, worktree, env)
			switch {
			case !ok:
				hit(f.Points, f.Message+"（測れなかった: "+note+"）")
				score.unmeasured = append(score.unmeasured, f.ID+": "+note)
			case points > 0:
				if note == "" {
					note = "スクリプト"
				}
				hit(points, f.Message+"（"+note+"）")
			}
		// 定性の項目。判定の記録の head が、いまの差分の head と違えば古い判定なので使わず、判定待ちに回す。
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
	for _, h := range score.hits {
		score.points += h.points
	}
	// 点の合計から段階（Low〜Critical）を決める。
	score.Level = d.levelOf(score.points)
	return score
}

// runScript はスクリプトを走らせて点を受け取る。出力は 0 以上の整数か、{"points": N, "message": …} の JSON。
// 測れなければ ok が偽で、note が理由。
func runScript(root, rel, worktree string, env map[string]string) (points int, note string, ok bool) {
	path := ospath.Join(root, filepath.FromSlash(rel))
	if !ospath.IsFile(path) {
		return 0, rel + " がありません。", false
	}
	pairs := []string{}
	for k, v := range env {
		pairs = append(pairs, k+"="+v)
	}
	// 終了の様子ごとに、測れなかった理由を決める（起動できない・時間切れ・0 以外の終了）。
	ran, err := shell.Script([]string{path}, worktree, pairs, shell.ScriptTimeout)
	out, _ := fsio.Decode(ran.Stdout, fsio.Replace)
	switch {
	case err != nil:
		return 0, rel + " を走らせられない (" + startFailure(err) + ")", false
	case ran.TimedOut:
		return 0, rel + " を走らせられない (TimeoutExpired)", false
	case ran.Code != 0:
		said, _ := fsio.Decode(ran.Stderr, fsio.Replace)
		if said == "" {
			said = out
		}
		text := rel + " が " + strconv.Itoa(ran.Code) + " で終わった"
		if tail := ospath.Splitlines(ospath.Strip(said)); len(tail) > 0 {
			text += ": " + tail[len(tail)-1]
		}
		return 0, text, false
	}
	// 成功した出力を点に読む。0 以上の整数そのもの、または points を含む JSON のどちらか。
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

// startFailure は起動できなかった理由を、例外名の形（FileNotFoundError など）で返す。点の判定に付ける理由の文に使う。
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
		"親: " + parent + "。ワークツリー: " + worktree + "。差分: `" + pyfmt.Head(diff.Base, git.ShortSHA) + ".." +
			pyfmt.Head(diff.Head, git.ShortSHA) + "`（" + diff.Summary() + "）。",
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
	changes := append([]Change(nil), diff.changes...)
	sort.SliceStable(changes, func(i, j int) bool { return changes[i].path < changes[j].path })
	for _, c := range changes {
		lines = append(lines, fmt.Sprintf("- %s %s (+%d -%d)", c.status, c.path, c.added, c.deleted))
	}
	lines = append(lines, "")
	return strings.Join(lines, "\n")
}
