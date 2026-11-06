package risk

import (
	"bytes"
	"errors"
	"fmt"
	"io/fs"
	"math"
	"math/big"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/digest"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/config/layer"
)

// Version は risks.yml の版。
const Version = 1

// Level はリスクレベル。名前は 4 つで固定で、閾値は levels で動かす。
type Level string

const (
	Low      Level = "LOW"
	Medium   Level = "MEDIUM"
	High     Level = "HIGH"
	Critical Level = "CRITICAL"
)

// Escalates は、宣言（定義の review: false・子の required: false）に関わらずレビューが要る扱いにするリスク
// レベルか。実績で宣言を厳しい側にだけ上書きする。記録から読んだ文字列をそのまま渡してよい。
func Escalates(level string) bool {
	return level == string(High) || level == string(Critical)
}

// levels の欄の名前と既定の閾値。
const (
	levelMedium   = "medium"
	levelHigh     = "high"
	levelCritical = "critical"
	levelLow      = "low" // 書いてもよいが読まない名前
)

var levelNames = []string{levelMedium, levelHigh, levelCritical}

var defaultLevels = map[string]int{levelMedium: 20, levelHigh: 40, levelCritical: 70}

// Kind は加点条件の種類。
type Kind string

const (
	KindLines   Kind = "lines_over"
	KindFiles   Kind = "files_over"
	KindDeleted Kind = "deleted_over"
	KindGlob    Kind = "glob"
	KindScript  Kind = "script"
	KindJudge   Kind = "judge"
)

var factorKinds = []Kind{KindLines, KindFiles, KindDeleted, KindGlob, KindScript, KindJudge}

func (k Kind) counts() bool { return k == KindLines || k == KindFiles || k == KindDeleted }

const (
	// Builtin は組み込みの配点の出どころ（Definition.Source）。
	Builtin = "(builtin)"
	// Where は risks.yml の苦情の出どころ（ファイル全体への苦情の rule）。
	Where = "(risk)"
	// levelsWhere は合成した閾値の苦情の出どころ。
	levelsWhere = "(levels)"
	// scriptsDir は script: の置き場の名前。レイヤーをまたいで同じ項目かを、ここからの相対で比べる。
	scriptsDir = "/scripts/"
)

// ScriptHomes は共通レイヤーの script: を置いてよい場所（ワークスペースルートからの相対の先頭）。guard の内側。
// エージェントが書ける場所のスクリプトを許すと、自分のリスクを自分で決められる。
var ScriptHomes = []string{config.DefaultProjectHome + "/" + config.CommonDir + "/" + config.ScriptsDir + "/"}

var idPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]*$`)

// Factor は加点の項目 1 つ。
type Factor struct {
	ID      string
	Points  int
	Message string
	Kind    Kind
	// Limit は lines_over・files_over・deleted_over の閾値。Text は glob（整えた式の元）・script（"/" 区切りの
	// 相対パス）・judge（問いの文）。
	Limit int
	Text  string
	// Max は glob の上限（HasMax のときだけ）。ヒットするごとに加点するので、無ければ青天井。
	Max    int
	HasMax bool
	// Include・Exclude は数える対象のパス（glob。lines_over・files_over・deleted_over だけ）。Include が空なら
	// 全パス、あればヒットしたパスだけ。Exclude にヒットしたパスは常に外す。
	Include, Exclude []string
	// Source はこの項目が書いてあるレイヤー（記録の hit と judge の項目に残す）。Home は script: を解く基準
	// ディレクトリ（共通レイヤーと自身のレイヤーはワークスペースルート、プロジェクトはそのプロジェクトの git
	// プロジェクトルート）。読んだ側が埋める。
	Source, Home string

	compiled         *pyre.Regexp
	include, exclude []*pyre.Regexp
}

// matches は glob の項目がこのパスに当たるか。
func (f *Factor) matches(rel string) bool { return f.compiled != nil && f.compiled.Match(rel) }

// inScope は数える項目がこの変更を数えるか。rename・copy は元のパスも見る（新しいパスだけで見ると、対象の外へ
// 動かしたファイルの書き換えが数えから消え、軽い側へ倒れる）。Include はどちらかが、Exclude は両方が当たれば効く。
func (f *Factor) inScope(c Change) bool {
	paths := []string{c.Path}
	if c.Old != "" {
		paths = append(paths, c.Old)
	}
	if len(f.include) > 0 && !anyMatch(f.include, paths) {
		return false
	}
	for _, p := range paths {
		if !anyMatch(f.exclude, []string{p}) {
			return true
		}
	}
	return false
}

func anyMatch(patterns []*pyre.Regexp, paths []string) bool {
	for _, re := range patterns {
		for _, p := range paths {
			if re.Match(p) {
				return true
			}
		}
	}
	return false
}

// key はレイヤーをまたいで「同じ項目か」を比べる全欄。Source と Home は含めない。script: は書かれたパスでは
// なく、スクリプトの置き場からの相対と指す先の中身で比べる（同期で共通レイヤーをコピーした配点はパスが違い、
// パスで比べると同じ項目を 2 重に数える。中身まで見るのは、名前だけ同じ別のスクリプトを破棄しないため）。
func (f *Factor) key() string {
	value := fmt.Sprint(f.Limit)
	switch f.Kind {
	case KindScript:
		rel := f.Text
		if _, after, ok := strings.Cut(rel, scriptsDir); ok {
			rel = after
		}
		value = fmt.Sprintf("%q %q", rel, fileDigest(f.Home, f.Text))
	case KindGlob, KindJudge:
		value = fmt.Sprintf("%q", f.Text)
	}
	max := "-"
	if f.HasMax {
		max = fmt.Sprint(f.Max)
	}
	return fmt.Sprintf("%q %d %q %s %s %s %q %q", f.ID, f.Points, f.Message, f.Kind, value, max,
		uniqueSorted(f.Include), uniqueSorted(f.Exclude))
}

func uniqueSorted(items []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, s := range items {
		if !seen[s] {
			seen[s] = true
			out = append(out, s)
		}
	}
	sort.Strings(out)
	return out
}

// fileDigest はスクリプトの中身のハッシュ（改行は LF にそろえる）。置き場が決まっていないか読めなければ空。
func fileDigest(home, rel string) string {
	if home == "" {
		return ""
	}
	raw, err := os.ReadFile(ospath.Join(home, filepath.FromSlash(rel)))
	if err != nil {
		return ""
	}
	hex, _ := digest.HexOf(digest.SHA256, bytes.NewReader(bytes.ReplaceAll(raw, []byte("\r\n"), []byte("\n"))))
	return hex
}

// Definition は配点。
type Definition struct {
	// Levels は書かれた閾値だけ（medium・high・critical）。書かれていない名前は既定で読む。既定で埋めて持つと、
	// 合成のときに「書いていないレイヤー」が共通レイヤーの緩めた閾値を気づかないうちに戻す。
	Levels  map[string]int
	Factors []*Factor
	// Source はどこから読んだか（組み込みなら Builtin）。
	Source string
	// Fallback は読めなかった理由（組み込みに戻ったとき、レイヤーを空として扱ったとき）。
	Fallback string
	// Dropped は空として扱ったレイヤーの名前（記録の fallback にそのまま入る）。
	Dropped []string
}

// Judges は定性の項目。
func (d *Definition) Judges() []*Factor {
	var out []*Factor
	for _, f := range d.Factors {
		if f.Kind == KindJudge {
			out = append(out, f)
		}
	}
	return out
}

// Factor は id の項目。無ければ nil。
func (d *Definition) Factor(id string) *Factor {
	for _, f := range d.Factors {
		if f.ID == id {
			return f
		}
	}
	return nil
}

// LevelOf は点のリスクレベル。
func (d *Definition) LevelOf(points int) Level {
	t := EffectiveLevels(d.Levels)
	switch {
	case points >= t.Critical:
		return Critical
	case points >= t.High:
		return High
	case points >= t.Medium:
		return Medium
	}
	return Low
}

// Thresholds は実際に使われる閾値。
type Thresholds struct{ Medium, High, Critical int }

// Object は JSON に載せる形（{"medium", "high", "critical"}）。
func (t Thresholds) Object() *pyjson.Object {
	return pyjson.NewObject(levelMedium, t.Medium, levelHigh, t.High, levelCritical, t.Critical)
}

// EffectiveLevels は書かれた閾値に既定を足した、実際に使われる閾値。
func EffectiveLevels(levels map[string]int) Thresholds {
	get := func(name string) int {
		if v, ok := levels[name]; ok {
			return v
		}
		return defaultLevels[name]
	}
	return Thresholds{get(levelMedium), get(levelHigh), get(levelCritical)}
}

func ordered(levels map[string]int) bool {
	t := EffectiveLevels(levels)
	return t.Medium <= t.High && t.High <= t.Critical
}

func problem(sev rules.Severity, rule, detail string) rules.Problem {
	return rules.Problem{Severity: sev, Rule: rule, Detail: detail}
}

func hasError(problems []rules.Problem) bool {
	for _, p := range problems {
		if p.Severity == rules.Error {
			return true
		}
	}
	return false
}

// builtin は risks.yml が無いときの配点（big-diff・many-files・ci・deletes）。
func builtin() *Definition {
	spec := func(kv ...any) any {
		m := &yaml.Map{}
		for i := 0; i < len(kv); i += 2 {
			m.Put(kv[i], kv[i+1])
		}
		return m
	}
	factors, _ := readFactors([]any{
		spec("id", "big-diff", "points", 25, "lines_over", 300, "message", "行数が多い"),
		spec("id", "many-files", "points", 15, "files_over", 10, "message", "ファイルが多い"),
		spec("id", "ci", "points", 35, "glob", ".github/**", "max", 35, "message", "CI やエージェント定義に触った"),
		spec("id", "deletes", "points", 20, "deleted_over", 3, "message", "削除したファイルが多い"),
	}, Builtin, ScriptHomes)
	return &Definition{Levels: map[string]int{}, Factors: factors, Source: Builtin}
}

// readFile は設定ファイルを読む。無ければ (_, false, nil)、読めなければ理由の文。
func readFile(path string) (string, bool, string) {
	text, err := fsio.ReadDiskText(path, fsio.Strict)
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return "", false, ""
		}
		return "", true, path + " を読めない (" + err.Error() + ")"
	}
	return text, true, ""
}

// Load は共通レイヤーの配点を読む。無ければ組み込み。壊れていれば組み込みに戻り、苦情を返す
// （UTF-8 として読めないものも同じ）。
func Load(path string) (*Definition, []rules.Problem) {
	if path == "" {
		return builtin(), nil
	}
	text, present, why := readFile(path)
	if !present {
		return builtin(), nil
	}
	if why != "" {
		fallen := builtin()
		fallen.Fallback = why
		return fallen, []rules.Problem{problem(rules.Error, Where, why)}
	}
	d, problems := Parse(text, path, ScriptHomes)
	if d == nil {
		fallen := builtin()
		fallen.Fallback = path + " が不正。組み込みの配点で数える"
		return fallen, problems
	}
	return d, problems
}

// LoadLayer はレイヤーの配点を読む。無ければ nil（無いレイヤー = 空）。壊れていても組み込みには戻さない
// （共通レイヤーが在るのに組み込みに戻すと、共通レイヤーの配点が消える側になる）。
func LoadLayer(path string, homes []string) (*Definition, []rules.Problem) {
	if path == "" {
		return nil, nil
	}
	text, present, why := readFile(path)
	if !present {
		return nil, nil
	}
	if why != "" {
		return nil, []rules.Problem{problem(rules.Error, Where, why)}
	}
	return Parse(text, path, homes)
}

// Parse は配点 1 本を読む。homes はこのレイヤーで script: に書けるパスの先頭（共通レイヤーは ScriptHomes、
// 各レイヤーはその ccnavi ディレクトリの scripts/ だけ。たがいの側を指す定義は error にする）。error が 1 つでも
// あれば nil、warn だけなら定義と苦情。
func Parse(text, where string, homes []string) (*Definition, []rules.Problem) {
	data, err := yaml.Load(text)
	if err != nil {
		return nil, []rules.Problem{problem(rules.Error, where, "YAML として読めない: "+err.Error())}
	}
	top, ok := data.(*yaml.Map)
	if !ok {
		return nil, []rules.Problem{problem(rules.Error, where, "最上位がマップではない")}
	}
	if v := top.Get("version"); !pyfmt.EqualsInt(v, Version) {
		return nil, []rules.Problem{problem(rules.Error, where,
			fmt.Sprintf("版 %s は扱えない（このビルドが読むのは %d）", pyfmt.Repr(v), Version))}
	}
	var problems []rules.Problem
	levels := map[string]int{}
	if raw, present := top.Lookup("levels"); present && raw != nil {
		written, ok := raw.(*yaml.Map)
		if !ok {
			return nil, []rules.Problem{problem(rules.Error, where, "`levels` がマップではない")}
		}
		for _, name := range levelNames {
			v, present := written.Lookup(name)
			if !present {
				continue
			}
			n, ok := nonNegative(v)
			if !ok {
				problems = append(problems, problem(rules.Error, where, "`levels."+name+"` が 0 以上の整数ではない"))
				continue
			}
			levels[name] = n
		}
		var unknown []string
		for k := range written.All() {
			name := pyfmt.Str(k)
			if _, isString := k.(string); isString && (contains(levelNames, name) || name == levelLow) {
				continue
			}
			unknown = append(unknown, name)
		}
		sort.Strings(unknown)
		for _, name := range unknown {
			problems = append(problems, problem(rules.Warn, where, "`levels."+name+"` は未対応のリスクレベル。名前は固定"))
		}
	}
	if !ordered(levels) {
		problems = append(problems, problem(rules.Error, where, "`levels` は medium <= high <= critical の順で書く"))
	}
	rawFactors := []any{}
	if raw, present := top.Lookup("factors"); present && raw != nil {
		list, ok := raw.([]any)
		if !ok {
			return nil, []rules.Problem{problem(rules.Error, where, "`factors` がリストではない")}
		}
		rawFactors = list
	}
	factors, more := readFactors(rawFactors, where, homes)
	problems = append(problems, more...)
	if hasError(problems) {
		return nil, problems
	}
	return &Definition{Levels: levels, Factors: factors, Source: where}, problems
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

// nonNegative は 0 以上の整数（真偽を除く）。int に収まらない大きな数は上限に丸める（比べる向きは変わらない）。
func nonNegative(v any) (int, bool) {
	switch x := v.(type) {
	case int:
		return x, x >= 0
	case *big.Int:
		return math.MaxInt, x.Sign() >= 0
	}
	return 0, false
}

// readFactors は factors: の 1 項目ずつを Factor の列にする。外れた項目は苦情に積んで次へ進む（error の項目は
// 入れず、warn の項目は入れる）。
func readFactors(raw []any, where string, homes []string) ([]*Factor, []rules.Problem) {
	var problems []rules.Problem
	var factors []*Factor
	seen := map[string]bool{}
	for i, entry := range raw {
		item, ok := entry.(*yaml.Map)
		if !ok {
			problems = append(problems, problem(rules.Error, where, fmt.Sprintf("`factors[%d]` がマップではない", i)))
			continue
		}
		id := ospath.Strip(item.Text("id"))
		if strings.Contains(id, rules.IDSeparator) {
			// レイヤーの名前をつけた形（lib:schema）と見分けが付かず、記録を読んだユーザが直すファイルを決められない。
			problems = append(problems, problem(rules.Error, where, fmt.Sprintf(
				"`factors[%d].id` に `%s` は書けない。レイヤーの名前を添えた形（`self%sid` / `<プロジェクト名>%sid`）と見分けが付かない",
				i, rules.IDSeparator, rules.IDSeparator, rules.IDSeparator)))
			continue
		}
		if !idPattern.MatchString(id) {
			problems = append(problems, problem(rules.Error, where, fmt.Sprintf("`factors[%d].id` が無いか形が違う", i)))
			continue
		}
		if seen[id] {
			problems = append(problems, problem(rules.Error, where, "`"+id+"` が重複している"))
			continue
		}
		seen[id] = true
		f, why, warns := readFactor(id, item, homes)
		if why != "" {
			problems = append(problems, problem(rules.Error, id, why))
			continue
		}
		problems = append(problems, warns...)
		factors = append(factors, f)
	}
	return factors, problems
}

// readFactor は id を確かめた項目 1 つを読む。読めなければ error の文、読めれば warn の苦情も返す。
func readFactor(id string, item *yaml.Map, homes []string) (*Factor, string, []rules.Problem) {
	points, ok := nonNegative(item.Get("points"))
	if !ok {
		return nil, "`points` が 0 以上の整数ではない", nil
	}
	var written []Kind
	for _, k := range factorKinds {
		if item.Has(string(k)) {
			written = append(written, k)
		}
	}
	if len(written) != 1 {
		names := make([]string, len(factorKinds))
		for i, k := range factorKinds {
			names[i] = string(k)
		}
		return nil, "加点条件は（" + strings.Join(names, " / ") + "）のうち 1 つだけ書く", nil
	}
	f := &Factor{ID: id, Points: points, Kind: written[0]}
	value := item.Get(string(f.Kind))
	if f.Message = ospath.Strip(item.Text("message")); f.Message == "" {
		f.Message = id
	}
	if raw, present := item.Lookup("max"); present && raw != nil {
		if f.Max, ok = nonNegative(raw); !ok {
			return nil, "`max` が 0 以上の整数ではない", nil
		}
		f.HasMax = true
	}
	if !f.Kind.counts() {
		for _, k := range []string{"include", "exclude"} {
			if item.Has(k) {
				return nil, fmt.Sprintf("`%s` は %s / %s / %s の項目にだけ書ける", k, KindLines, KindFiles, KindDeleted), nil
			}
		}
	}
	var warns []rules.Problem
	switch f.Kind {
	case KindLines, KindFiles, KindDeleted:
		if f.Limit, ok = nonNegative(value); !ok {
			return nil, "`" + string(f.Kind) + "` が 0 以上の整数ではない", nil
		}
		var why string
		if f.Include, f.include, why = scopeGlobs(item.Get("include")); why != "" {
			return nil, "`include` " + why, nil
		}
		if f.Exclude, f.exclude, why = scopeGlobs(item.Get("exclude")); why != "" {
			return nil, "`exclude` " + why, nil
		}
		// ワイルドカードの無い include はその名前のファイルにしか当たらない。ディレクトリのつもりで src と
		// 書くと何も数えず点が 0 になる（気づかないうちに軽い側へ偏る）。
		for _, glob := range f.Include {
			if !strings.ContainsAny(glob, "*?[") {
				warns = append(warns, problem(rules.Warn, id, "`include` の `"+glob+"` はワイルドカードが無いので、その名前の"+
					"ファイルにしか一致しない。ディレクトリなら `"+glob+"/**` か `"+glob+"/`"))
			}
		}
	case KindGlob:
		s, _ := value.(string)
		if ospath.Strip(s) == "" {
			return nil, "`glob` が文字列ではない", nil
		}
		f.Text = strings.Trim(strings.ReplaceAll(ospath.Strip(s), `\`, "/"), "/")
		// ルールの glob と同じく、どの機械でも大文字小文字を区別しない。
		compiled, err := pyre.Compile("^"+pyre.TranslateGlob(f.Text), pyre.IgnoreCase)
		if err != nil {
			return nil, "`glob` を式にできない: " + err.Error(), nil
		}
		f.compiled = compiled
	case KindScript:
		s, _ := value.(string)
		if ospath.Strip(s) == "" {
			return nil, "`script` が文字列ではない", nil
		}
		rel := strings.ReplaceAll(ospath.Strip(s), `\`, "/")
		if ospath.IsAbs(rel) || strings.Contains(rel, "..") || !hasAnyPrefix(rel, homes) {
			quoted := make([]string, len(homes))
			for i, h := range homes {
				quoted[i] = "`" + h + "`"
			}
			return nil, "`script` の `" + rel + "` はこのレイヤーから指せない。" + strings.Join(quoted, " か ") +
				" の下に、そのレイヤーの git プロジェクトルートからの相対で置く" +
				"（守られた場所。レイヤーと共通レイヤーはたがいの側を指せない）", nil
		}
		f.Text = rel
	case KindJudge:
		s, _ := value.(string)
		if ospath.Strip(s) == "" {
			return nil, "`judge` は問いの文を書く", nil
		}
		f.Text = ospath.Strip(s)
	}
	return f, "", warns
}

func hasAnyPrefix(s string, prefixes []string) bool {
	for _, p := range prefixes {
		if strings.HasPrefix(s, p) {
			return true
		}
	}
	return false
}

// scopeGlobs は include・exclude の glob のリストを式にする。書かれていなければ空。3 つ目は error の理由。
// 末尾の "/" はディレクトリ（src/ を src/** として読む。src だけでは何も当たらない）。
func scopeGlobs(raw any) ([]string, []*pyre.Regexp, string) {
	if raw == nil {
		return nil, nil, ""
	}
	list, ok := yaml.Strings(raw)
	if ok {
		for _, g := range list {
			if ospath.Strip(g) == "" {
				ok = false
			}
		}
	}
	if !ok {
		return nil, nil, "は glob（文字列）のリストで書く"
	}
	var globs []string
	var compiled []*pyre.Regexp
	for _, g := range list {
		glob := strings.ReplaceAll(ospath.Strip(g), `\`, "/")
		if inner := strings.Trim(glob, "/"); strings.HasSuffix(glob, "/") && inner != "" {
			glob = inner + "/**"
		} else {
			glob = inner
		}
		re, err := pyre.Compile("^"+pyre.TranslateGlob(glob), pyre.IgnoreCase)
		if err != nil {
			return nil, nil, "の `" + g + "` を式にできない: " + err.Error()
		}
		globs = append(globs, glob)
		compiled = append(compiled, re)
	}
	return globs, compiled, ""
}

// markLayer はこの配点の項目に、レイヤーの名前と script: を解く基準ディレクトリを持たせる。
func (d *Definition) markLayer(name, home string) {
	for _, f := range d.Factors {
		f.Source, f.Home = name, home
	}
}

// Merge は共通レイヤーの配点に、行き先のレイヤーの配点を足す。factors は連結で、同じ id で全欄が一致すれば
// 後ろを捨て（info）、中身が違えば両方を数え、後ろを `<レイヤー>:<id>` にする（warn。空にすると点が小さく
// なる側になるので、衝突は点を増やす向きにだけ効かせる）。levels は書かれたキーごとに小さいほう。合成後の
// levels の順が崩れたら（error）、そのレイヤーを空として共通レイヤーだけを返す。
func Merge(common, extra *Definition, name string) (*Definition, []rules.Problem) {
	var problems []rules.Problem
	ids, keys := map[string]bool{}, map[string]bool{}
	for _, f := range common.Factors {
		ids[f.ID], keys[f.key()] = true, true
	}
	var added []*Factor
	for _, f := range extra.Factors {
		if keys[f.key()] {
			problems = append(problems, problem(rules.Info, f.ID, "`"+f.ID+"` は前のレイヤーと全欄が同じなので、"+
				name+" の側を破棄した。点は前のレイヤーの 1 本で数える"))
			continue
		}
		if ids[f.ID] {
			qualified := name + rules.IDSeparator + f.ID
			problems = append(problems, problem(rules.Warn, f.ID, "`"+f.ID+"` が前のレイヤーと同じ id で中身が違う。"+
				"両方を数え、"+name+" の側は `"+qualified+"` という名前にする（記録と record-risk もこの名前）。"+
				"同じ項目のつもりなら全欄を揃え、別の項目なら id を変えてください"))
			renamed := *f
			renamed.ID = qualified
			f = &renamed
		}
		ids[f.ID], keys[f.key()] = true, true
		added = append(added, f)
	}
	levels := copyLevels(common.Levels)
	for k, v := range extra.Levels {
		if prior, ok := levels[k]; ok {
			v = min(prior, v)
		}
		levels[k] = v
	}
	if !ordered(levels) {
		t := EffectiveLevels(levels)
		problems = append(problems, problem(rules.Error, levelsWhere, fmt.Sprintf(
			"合成後の `levels` が medium %d <= high %d <= critical %d になっていない。%s のレイヤーは空として扱う",
			t.Medium, t.High, t.Critical, name)))
	}
	out := &Definition{
		Levels: levels, Factors: append(append([]*Factor{}, common.Factors...), added...),
		Source: common.Source, Fallback: common.Fallback, Dropped: append([]string{}, common.Dropped...),
	}
	if hasError(problems) {
		out.Levels = copyLevels(common.Levels)
		out.Factors = append([]*Factor{}, common.Factors...)
		out.Dropped = append(out.Dropped, name)
	}
	return out, problems
}

func copyLevels(levels map[string]int) map[string]int {
	out := map[string]int{}
	for k, v := range levels {
		out[k] = v
	}
	return out
}

// ScriptProblems は script: が指す先が、そのレイヤーの git プロジェクトルートに在るか。name を渡すと、その
// レイヤーから来た項目だけを見る。走らせるときは「測れなかった」で重いほうになるが、--lint は先に言う。
func ScriptProblems(d *Definition, name string) []rules.Problem {
	var problems []rules.Problem
	for _, f := range d.Factors {
		if f.Kind != KindScript || name != "" && f.Source != name {
			continue
		}
		if !ospath.IsFile(ospath.Join(f.Home, filepath.FromSlash(f.Text))) {
			home := f.Home
			if home == "" {
				home = "(基準なし)"
			}
			problems = append(problems, problem(rules.Error, f.ID, "`script` の `"+f.Text+"` が "+home+" にありません。"+
				"ワークツリーの中のものは読まないので、元リポジトリに置いてください"))
		}
	}
	return problems
}

// CommonDefinition は共通レイヤーの配点を読み、項目に共通レイヤーの印を付ける（script: はワークスペースルートで解く）。
// 戻りの苦情は共通レイヤー自身のもの（--lint の共通レイヤーの項が言う）。
func CommonDefinition(conf *config.Settings, root string) (*Definition, []rules.Problem) {
	d, notes := Load(conf.Risk)
	d.markLayer(config.LayerCommon, root)
	return d, notes
}

// LayerDefinition は共通レイヤー + 親の project: が指すレイヤーの配点と、そのレイヤーの苦情。共通レイヤー自身の
// 苦情は返さない（--lint の共通レイヤーの項と二重に言わない）。共通レイヤーが壊れていれば組み込みに戻り、
// レイヤーを足さない。共通レイヤーにもレイヤーにも risks.yml が無ければ組み込み、共通レイヤーだけが無ければ
// レイヤーの項目だけを数える。
func LayerDefinition(conf *config.Settings, root, project string) (*Definition, []rules.Problem) {
	home, name := layer.Home(conf, root, project), layer.Name(project)
	path := layer.File(conf, root, project, config.KindRisk)
	present := path != "" && ospath.DiskExists(path)
	var d *Definition
	if present && !(conf.Risk != "" && ospath.DiskExists(conf.Risk)) {
		source := conf.Risk
		if source == "" {
			source = Builtin
		}
		d = &Definition{Levels: map[string]int{}, Source: source}
	} else {
		d, _ = Load(conf.Risk)
	}
	d.markLayer(config.LayerCommon, root)
	if d.Fallback != "" || !present {
		return d, nil
	}
	extra, notes := LoadLayer(path, []string{conf.LayerScriptHome()})
	if extra == nil {
		d.Dropped = append(d.Dropped, name)
		d.Fallback = name + " の配点が不正。このレイヤーは空として数える"
		return d, notes
	}
	extra.markLayer(name, home)
	merged, problems := Merge(d, extra, name)
	if len(merged.Dropped) > 0 {
		merged.Fallback = strings.Join(merged.Dropped, ", ") + " の配点の閾値が合成で逆転している。このレイヤーは空として数える"
	}
	return merged, append(notes, problems...)
}

// LoadDefinition は判定が使う配点（共通レイヤー + 親の project: が指すレイヤー）。
func LoadDefinition(conf *config.Settings, root, project string) *Definition {
	d, _ := LayerDefinition(conf, root, project)
	return d
}
