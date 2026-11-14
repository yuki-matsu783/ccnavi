package selfguard

import (
	"slices"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/platformtag"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/infra/tree"
)

// シェルからの書き込みを止める式の部品。式はルールの regex として rules（pyre）が読み、
// 大文字小文字を区別せずに当てる。式の文字列は 1 字でも変わると止まる範囲が変わるので、写しの見本（testdata）と突き合わせる。
//
// 書き込みの形と場所は組でヒットする。場所の名前が出ただけ（cat・git add）では止めない。
// shell.Read が語を空白で、コマンドを Sep（`\x00`）で、引用がつないだ空白を WordSep（`\x01`）で区切って渡すので、
// 語の並びを式で読める。読み違えたときは、止める側に倒す。
// 照合の手数が長さに比例するよう、割り方が 1 通りに決まる形で書く（`(?>...)` で決め打ちし、語の頭の文字を並びと取り合わない）。
// 後読みは使わない。ルールの regex が受け付けないため。

// NotAWord は語に入らない目印（コマンドの切れ目と語の中の切れ目）。pyre.Escape はどちらも変えない。
const NotAWord = shell.Sep + shell.WordSep

const (
	// redirect はリダイレクトの形と行き先の語。`>` の並びは頭から読み始める。行き先の語は `>` `|` `&` で始めず、
	// 語の頭と終わりに目印を置かせない（引用の中の `> 場所` を書き込みと読まない）。
	redirect = `(?:^|[^>|&])[|&]*>[>|&]* ?` +
		`(?:[^ ` + NotAWord + `>|&](?:[^ \x00]*[^ ` + NotAWord + `])?)?`
	verbWrite = `(^|\x00)(mv|rm|tee|dd|truncate|patch|shred)\b[^\x00]*`
	// sedWrite は最初の `-i` で決め打ちする。しないと `-i` が出るたびに末尾まで読み直し、手数が長さの 2 乗に増える。
	sedWrite = `(^|\x00)sed\b(?>[^\x00]*?[ \x01](-[A-Za-z]*?i|--i))[^\x00]*`
	// WriteVerbs は場所の前に置く書き込みの形（リダイレクト・名指しを書き換えるコマンド・sed -i）。
	WriteVerbs = `(` + redirect + `|` + verbWrite + `|` + sedWrite + `)`

	copyVerbs = `(^|\x00)(cp|ln|install)\b[^\x00]*`
	copyName  = `(^|\x00)(cp|ln|install)\b`

	// 短い選択肢の文字の組（cp・ln・install を合わせたもの）。flags は値を取らない文字、dirWord は行き先を名指しする語、
	// valued は次の語を値に取りうる語、notDir は行き先を名指ししない語。dirWord と notDir は重ならないので、
	// 語の並びの割り方は 1 通りに決まる。何通りもあると、ヒットしない長い並びで手数が指数的に増えてしまう。
	flags      = `[a-fh-lnp-su-zA-RT-Z]`
	dirHead    = `(?:-(?-i:` + flags + `*t)|--(?-i:t))`
	dirWord    = dirHead + `[^ \x00]*`
	valued     = `(?:-(?-i:` + flags + `*[Sgmo])|--(?-i:[gmnos])[^ =\x00]*)`
	dashNotDir = `(?:-(?-i:` + flags + `*[Sgmo])[^ \x00]*` +
		`|-(?-i:` + flags + `+)(?:[^a-zA-Z \x00][^ \x00]*)?` +
		`|-(?:[^a-zA-Z \x00-][^ \x00]*)?` +
		`|--(?-i:[^t \x00])[^ \x00]*)`
	notDir = `(?:[^- \x00][^ \x00]*|` + dashNotDir + `)?`

	// copyTarget は `-t <dir>` の値、copyLast は選択肢でない最後の引数の手前まで（GNU の読み方）。
	copyTarget = copyVerbs + ` (?:-(?-i:` + flags + `*t)|--(?-i:t)[a-z-]*)[= ]?[^ \x00]*`
	copyLast   = copyName + `(?: (?:` + valued + ` ` + dirWord + `|` + notDir + `))*` + `(?: --(?: [^ \x00]*)*)?` + ` `
	inWord     = `[^ \x00]*`

	// term は名前がそこで終わる形、end は区切りが続くかそこで終わる形。語の中の目印も終わりに数える。
	term = `(?:[ ` + NotAWord + `]|$)`
	end  = `(?:[\\/ ` + NotAWord + `]|$)`

	// 最後の引数（行き先）だけに当てる終わり。行き先のあとに回した選択肢を飛ばし、引数の並びの終わり
	// （文字列の終わり・コマンドの切れ目・後ろのリダイレクト）に届く形に絞る。
	copyOptions = `(?: (?:` + valued + ` (?:(?:[^- \x00][^ \x00]*)?|` + dirWord + `)|` + dashNotDir + `))*(?: --)?`
	copyStop    = `(?:$|\x00| (?:[0-9]+ |\{\w+\} )?&?[<>])`
	copyTerm    = copyOptions + copyStop
	copyTail    = `[^ \x00]*` + copyTerm
	copyEnd     = `(?:[\\/]` + copyTail + `|` + copyTerm + `)`

	// 守るものが入っているディレクトリを行き先にした形（HolderRegex）の部品。
	holdName     = `(^|\x00)(cp|ln|install|mv)\b`
	arg          = `(?:` + valued + ` ` + dirWord + `|` + notDir + `)`
	targetFlag   = `(?:-(?-i:` + flags + `*t)|--(?-i:t)[a-z-]*)[= ]?`
	noTargetFlag = `(?:-(?-i:` + flags + `*T` + flags + `*)(?:[^a-zA-Z \x00][^ \x00]*)?|--(?-i:no-t)[^ \x00]*)`
	head         = `(?:[^- \x00][^ \x00]*[\\/]|[\\/])?`
	// unknown は名前が表記から決まらない元の語（グロブ・変数・置換・ブレース・`.` と `..` で終わる元）。
	unknown = `(?:[^- \x00][^ \x00]*)?[*?\[{$` + "`" + `][^ \x00]*|` + head + `\.\.?[\\/]*`
	dirTail = `(?:[\\/]\.?)*`

	// logsNames は `logs/` の中で守る名前（記録・ローテートした記録・state・閉じたチケットの退避）。
	logsNames = `decisions(?:\.[^\s\\/\x00]*)?\.jsonl|state|` + config.ArchiveName
)

// places は書き込みの形に続く既定の場所。copyPlaces は cp・ln・install の最後の引数用。
// `.claude` の下は hooks と settings に絞る（worktrees/ は守らない）。
var (
	places = []string{
		`\.claude(?:[\\/](hooks` + end + `|settings[\w.-]*\.json)|[\\/]?` + term + `)`,
		`\.ccnavi` + end,
		`logs[\\/](` + logsNames + `)` + end,
		`ccnavi-git\.sh`,
	}
	copyPlaces = []string{
		inWord + `\.claude(?:[\\/](hooks|settings)` + copyTail + `|[\\/]?` + copyTerm + `)`,
		inWord + `\.ccnavi` + copyEnd,
		inWord + `logs[\\/](` + logsNames + `)` + copyEnd,
		inWord + `ccnavi-git\.sh` + copyTail,
	}
	// holders は既定の表記の、守るものが入っているディレクトリと中の名前。
	holders = []holder{{Under(`logs`), logsNames}}
	// rootNames はワークスペースルートの直下に置く、守るものを含む名前（ルートを指す行き先と組む）。
	rootNames = []string{`\.ccnavi`, `\.claude`, `logs`}
	// buildDir は機械ごとの組み立ての置き場（`bin/` の下）。
	buildDir = `(?:` + strings.Join(platformtag.Systems(), "|") + `)-[a-z0-9_]+`
)

// holder は HolderRegex に渡す組（行き先のディレクトリの表記と、その中の守る名前）。
type holder struct{ dir, names string }

// form は場所の表記をどこに当てるか。
type form int

const (
	writeForm  form = iota // 書き込みの形のあと（空白で閉じる）
	copyForm               // cp・ln・install の最後の引数（引数の並びの終わりで閉じる）
	targetForm             // `-t` の値の形。書き込みの形から、ディレクトリになり得ないもの（PATH の実体）を除いたもの
)

// Under はディレクトリの表記を、語の頭からでも置き場の下からでもヒットする行き先の形にする
// （`logs` は `./logs`・`/abs/logs` にヒットし、`mylogs` にはヒットしない）。
func Under(directory string) string { return `(?:[^ \x00]*[\\/])?` + directory }

// HolderRegex は、守るものが入っているディレクトリを行き先にした cp・ln・install・mv にヒットする式。元の側に names の語か、
// 名前の決まらない語（namedOnly なら数えない）があるときだけヒットする。行き先は `-t` の値か、選択肢でない最後の引数。
// 元の語は最初に見つかったもので決め打ちする。ヒットしない長い並びで読み直さないため。
func HolderRegex(directory, names string, namedOnly bool) string {
	source := `(?:` + head + `(?:` + names + `)[\\/]*|` + unknown + `)`
	flag := `|` + noTargetFlag
	if namedOnly {
		source = `(?:` + head + `(?:` + names + `)[\\/]*)`
		flag = ""
	}
	dest := directory + dirTail
	before := `(?>(?: ` + arg + `)*? (?:` + source + flag + `) )` +
		`(?:` + arg + ` )*(?:--(?: [^ \x00]*)* )?` + dest + copyTerm
	after := `(?: ` + arg + `)* --(?>(?: [^ \x00]*)*? ` + source + ` )(?:[^ \x00]* )*` + dest + copyTerm
	targetAfter := `(?>[^\x00]*? ` + source + ` )(?:[^\x00]* )?` + targetFlag + dest + `(?:[ \x00]|$)`
	targetBefore := `(?>[^\x00]*? ` + targetFlag + dest + ` )(?:[^\x00]* )?` + source + `(?:[ \x00]|$)`
	forms := strings.Join([]string{before, after, targetAfter, targetBefore}, "|")
	return folded(holdName + `(?:` + forms + `)`)
}

// CopyDestinationRegex は cp・ln・install の行き先にヒットする式。lastPlace は最後の引数の語の頭から当てる表記
// （CopyLastPlace で組む）。targetPlace は `-t` の値の語のどこから当ててもよい表記。
func CopyDestinationRegex(lastPlace, targetPlace string) string {
	return copyLast + lastPlace + `|` + copyTarget + targetPlace
}

// CopyLastPlace は場所の表記を、最後の引数の語として当てる形にする（名前がそこで終わる形もその下も）。
func CopyLastPlace(clause string) string { return inWord + clause + copyEnd }

// folded は場所を大文字小文字を区別せずに当てる。区別する機械でも区別しない（ルールの glob とチケットの範囲と揃える）。
func folded(clause string) string { return "(?i:" + clause + ")" }

// triple は設定で動く場所 1 つの、書き込み用・最後の引数用・`-t` の値用の表記。閉じ方が違うので分けて持つ。
type triple struct{ write, last, target string }

// shellWriteRegex は書き込みの形を組む。実行ファイル（判定器の実体）の場所はここで足す。
func shellWriteRegex(binPath string, extra []triple, moved []holder) string {
	where := slices.Clone(places)
	copyWhere := slices.Clone(copyPlaces)
	targetWhere := slices.Clone(places)
	binary := BinaryClause(binPath)
	for _, t := range append([]triple{{binary, binaryClause(binPath, copyForm), binaryClause(binPath, targetForm)}}, extra...) {
		if t.write != "" {
			where = append(where, t.write)
		}
		if t.last != "" {
			copyWhere = append(copyWhere, t.last)
		}
		if t.target != "" {
			targetWhere = append(targetWhere, t.target)
		}
	}
	forms := []string{
		WriteVerbs + folded("("+strings.Join(where, "|")+")"),
		CopyDestinationRegex(folded("("+strings.Join(copyWhere, "|")+")"), folded("("+strings.Join(targetWhere, "|")+")")),
	}
	for _, h := range slices.Concat(holders, binaryHolders(binPath), moved) {
		forms = append(forms, HolderRegex(h.dir, h.names, false))
	}
	return strings.Join(forms, "|")
}

// ShellRegex は、この設定で組んだ、シェルから守る場所へ書き込む形を返す。実行前の保護（AddRules）と
// 組み込みの既定（builtin）が同じものを取る。片方だけ空の設定で組むと、動かした ccnavi ディレクトリや実行ファイルが、
// ルールファイルの壊れた間だけ守られなくなる。commonFiles は共通レイヤーの 3 本（CommonLayerFiles）。
func ShellRegex(root, binPath, projectHome string, commonFiles []string) string {
	home := projectHomeClause(projectHome, writeForm)
	extra := []triple{{home, projectHomeClause(projectHome, copyForm), home}}
	// 共通レイヤーのパスは `-t` の値（ディレクトリ）には足さない。前の 1 文字から当てるので、元の側にヒットしてしまう。
	for _, path := range commonFiles {
		extra = append(extra, triple{commonShellClause(root, path, writeForm), commonShellClause(root, path, copyForm), ""})
	}
	return shellWriteRegex(binPath, extra, movedHolders(root, projectHome, commonFiles))
}

// CommonLayerFiles は共通レイヤーの 3 本（rules・phases・risk の順）。
func CommonLayerFiles(conf *config.Settings) []string {
	return []string{conf.Rules, conf.Phases, conf.Risk}
}

// projectHomeClause は ccnavi ディレクトリ（動かしてある場合）の表記。既定の `.ccnavi` は places が持つ。
func projectHomeClause(projectHome string, f form) string {
	parts := escapedParts(homeName(projectHome))
	if len(parts) == 0 {
		return ""
	}
	if f == copyForm {
		return inWord + strings.Join(parts, pyre.AnySeparator) + copyEnd
	}
	return strings.Join(parts, pyre.AnySeparator) + end
}

// projectHomeGlob は ccnavi ディレクトリの下を、名指しのツールで止める glob（`*/.ccnavi/*`）。
func projectHomeGlob(projectHome string) string {
	if name := homeName(projectHome); name != "" {
		return "*/" + name + "/*"
	}
	return ""
}

// homeName は ccnavi ディレクトリの名前。前後の区切りを除き、区切りを含むパスは 1 つの節のまま。
func homeName(projectHome string) string {
	return strings.Trim(ospath.Slashed(projectHome), "/")
}

// escapedParts は "/" で割った空でない節を、pyre.Escape で式の字として読めるようにする。
func escapedParts(name string) []string {
	var out []string
	for _, p := range strings.Split(name, "/") {
		if p != "" {
			out = append(out, pyre.Escape(p))
		}
	}
	return out
}

// BinaryClause は実行ファイルのパスを、書き込みの形のあとに当てる表記にする（末尾 2 要素。区切りはどちらの表記にも当てる）。
// 振り分けの sh なら、sh と、hook が実際に走らせる実体の置き場（`<親>/bin/<os>-<arch>/`）と PATH の実体にも当てる。
// 書き込みの形は終わりを決めないので、名指しのツールに当てるときは `$` を足す。
func BinaryClause(binPath string) string { return binaryClause(binPath, writeForm) }

func binaryClause(binPath string, f form) string {
	parts := pathParts(binPath)
	if len(parts) == 0 {
		return ""
	}
	if parts[len(parts)-1] == platformtag.LauncherName {
		sh := joinEscaped(parts[max(len(parts)-2, 0):])
		home := ""
		if len(parts) >= 3 {
			home = pyre.Escape(parts[len(parts)-3]) + pyre.AnySeparator
		}
		built := home + `bin` + pyre.AnySeparator + buildDir
		if f == copyForm {
			var onPathCopy string
			for _, p := range pathSpellings(binPath, copyForm) {
				onPathCopy += "|" + p + copyTail
			}
			return `(?:` + inWord + `(?:` + sh + copyTail + `|` + built + copyEnd + `)` + onPathCopy + `)`
		}
		// PATH の実体はファイルなので `-t` の値（ディレクトリ）には足さない。
		// 浅い実体は前の 1 文字から当てるので、足すと `-t /tmp/out /bin/ccnavi` の元の側にヒットする。
		var onPath string
		if f == writeForm {
			for _, p := range pathSpellings(binPath, writeForm) {
				onPath += "|" + p
			}
		}
		return `(?:` + sh + `|` + built + `(?:[\\/][^\x00]*)?` + onPath + `)`
	}
	plain := joinEscaped(parts[max(len(parts)-2, 0):])
	if f == copyForm {
		return inWord + plain + copyTail
	}
	return plain
}

// pathSpellings は PATH の ccnavi に当てる表記（末尾 3 要素。2 要素の `bin/ccnavi` は別のプロジェクトにも当たる）。
// 書かれたパスと、リンクを解いた先の両方を含む。浅い実体（`/bin/ccnavi`）は、区切りで始まる形だけに当てる。
// ルールの式は後読みを受けないので、前の 1 文字も含めて当てる（commonShellClause と同じ作り）。最後の引数（copyForm）では
// 前の 1 文字は語の中に限り、語の頭なら要らない（copyLast が手前の空白まで読んである）。
func pathSpellings(launcher string, f form) []string {
	exe := platformtag.PathExecutable(launcher)
	if exe == "" {
		return nil
	}
	var found []string
	for _, path := range []string{exe, ospath.Realpath(exe)} {
		every := pathParts(path)
		spelled := joinEscaped(every[max(len(every)-3, 0):])
		switch {
		case len(every) <= 3 && f == copyForm:
			spelled = `(?:[^ \x00]*[^\w.~ \x00-])?` + pyre.AnySeparator + spelled
		case len(every) <= 3:
			spelled = `(?:^|[^\w.~-])` + pyre.AnySeparator + spelled
		case f == copyForm:
			spelled = inWord + spelled
		}
		if len(every) > 0 && !slices.Contains(found, spelled) {
			found = append(found, spelled)
		}
	}
	return found
}

// binaryHolders は実行ファイルが入っているディレクトリと中の名前（振り分けの sh なら組み立ての置き場も）。
// PATH の実体のディレクトリ（`~/.local/bin` 全体）は足さない。
func binaryHolders(binPath string) []holder {
	parts := pathParts(binPath)
	if len(parts) < 2 {
		return nil
	}
	n := len(parts)
	found := []holder{{Under(pyre.Escape(parts[n-2])), pyre.Escape(parts[n-1])}}
	if parts[n-1] == platformtag.LauncherName && n >= 3 {
		home := pyre.Escape(parts[n-3])
		found = append(found,
			holder{Under(home + pyre.AnySeparator + `bin`), buildDir},
			holder{Under(home), "bin|" + pyre.Escape(parts[n-2])})
	}
	return found
}

// pathParts はパスを両方の区切りで割り、空と `.`・`..` を除いた節。
func pathParts(path string) []string {
	var out []string
	for _, p := range splitSeps(path) {
		if p != "" && p != "." && p != ".." {
			out = append(out, p)
		}
	}
	return out
}

// splitSeps はパスを `/` と `\` の両方で割る（空の節も残す）。
func splitSeps(path string) []string {
	return strings.Split(ospath.Slashed(path), "/")
}

func joinEscaped(parts []string) string {
	out := make([]string, len(parts))
	for i, p := range parts {
		out[i] = pyre.Escape(p)
	}
	return strings.Join(out, pyre.AnySeparator)
}

// spelled はパスを、区切りをどちらの表記でも当てる形にする。
func spelled(path string) string { return joinEscaped(splitSeps(path)) }

// movedHolders は、設定で動く場所の、入っているディレクトリと名前の組。
//   - ccnavi ディレクトリと、ルートの下に置いた共通レイヤー: ルートからの相対の各要素の組。いちばん上の名前はルートの組に足す
//   - ルートの外に置いた共通レイヤー: 書かれた表記と行き着く先の、すぐ上のディレクトリと名前の組だけ
//   - ルート: rootNames と上で足した名前を、ルートを指す行き先（`.` とルートの絶対パス）で組む
func movedHolders(root, projectHome string, commonFiles []string) []holder {
	var found []holder
	top := slices.Clone(rootNames)
	ranked := []string{homeName(projectHome)}
	// 1. 共通レイヤー。ルートの下なら相対を ranked に足し、外なら書かれた表記と行き着く先の組を作る。
	for _, path := range commonFiles {
		if path == "" {
			continue
		}
		rel := ""
		if root != "" {
			rel = inside(root, path)
		}
		if rel != "" {
			ranked = append(ranked, rel)
			continue
		}
		for _, name := range sortedSet(path, ospath.Realpath(path)) {
			directory, base := ospath.Dirname(name), ospath.Basename(name)
			if directory != "" && base != "" {
				found = append(found, holder{Under(spelled(directory)), pyre.Escape(base)})
			}
		}
	}
	// 2. ranked の各パスを、段階ごとの組にする。ccnavi ディレクトリの下は、行き先がそこなら名前を問わずに止まる
	//（場所の表記）ので、組は作らず、いちばん上の名前だけ top に足す。
	homes := []string{}
	for _, h := range []string{pyfmt.Lower(homeName(projectHome)), config.DefaultProjectHome} {
		if h != "" && !slices.Contains(homes, h) {
			homes = append(homes, h)
		}
	}
	for _, rel := range ranked {
		var parts []string
		for _, p := range splitSeps(rel) {
			if p != "" {
				parts = append(parts, p)
			}
		}
		if len(parts) == 0 {
			continue
		}
		top = append(top, pyre.Escape(parts[0]))
		joined := pyfmt.Lower(strings.Join(parts, "/"))
		if slices.ContainsFunc(homes, func(h string) bool { return strings.HasPrefix(joined, h+"/") }) {
			continue
		}
		for k := 1; k < len(parts); k++ {
			found = append(found, holder{Under(joinEscaped(parts[:k])), pyre.Escape(parts[k])})
		}
	}
	// 3. ルートを指す行き先（`.` とルートの絶対パス）と top の名前で、ルートの組を作る。
	where := []string{`\.`}
	if root != "" {
		for _, b := range sortedSet(ospath.Realpath(root), ospath.Abspath(root)) {
			where = append(where, spelled(b))
		}
	}
	found = append(found, holder{"(?:" + strings.Join(where, "|") + ")", strings.Join(unique(top), "|")})
	// 共通レイヤーの 3 本は同じディレクトリに並ぶことが多い。同じ組は 1 つにする。
	return unique(found)
}

// commonShellClause は共通レイヤーの 1 本（`--rules` などで動かせる）を、シェルの書き込みに当てる表記にする。
// ルートの下ならその相対、外なら書かれたパスと行き着く先の両方。名前の途中（`myrules.yml`）にはヒットさせない。
// 後読みは使えないので、前の 1 文字も含めて当てる。
func commonShellClause(root, path string, f form) string {
	if path == "" {
		return ""
	}
	rel := ""
	if root != "" {
		rel = inside(root, path)
	}
	names := []string{rel}
	if rel == "" {
		names = sortedSet(path, ospath.Realpath(path))
	}
	var spelledNames []string
	for _, n := range names {
		if n != "" {
			spelledNames = append(spelledNames, spelled(n))
		}
	}
	if len(spelledNames) == 0 {
		return ""
	}
	named := "(?:" + strings.Join(spelledNames, "|") + ")"
	if f == copyForm {
		return `(?:` + inWord + `[^\w. \x00-])?` + named + copyTerm
	}
	return `(?:^|[^\w.-])` + named + term
}

// commonLayerRegex は共通レイヤーの 3 本を、名指しのツール（解決済みの絶対パス）に当てる式。ルートの下なら、ワークスペースと
// そこから切ったワークツリーの同じ相対に当てる。統合で統合先の設定になる経路があるため。
func commonLayerRegex(root string, commonFiles []string) string {
	var bases []string
	if root != "" {
		bases = sortedSet(ospath.Realpath(root), ospath.Abspath(root))
	}
	worktrees := `(?:` + pyre.AnySeparator + spelled(tree.WorktreesDir) + pyre.AnySeparator + `[^\\/]+)?`
	var alternatives []string
	for _, path := range commonFiles {
		if path == "" {
			continue
		}
		rel := ""
		if root != "" {
			rel = inside(root, path)
		}
		if rel != "" {
			for _, b := range bases {
				alternatives = append(alternatives, spelled(b)+worktrees+pyre.AnySeparator+spelled(rel))
			}
			continue
		}
		for _, p := range sortedSet(path, ospath.Realpath(path)) {
			alternatives = append(alternatives, spelled(p))
		}
	}
	if len(alternatives) == 0 {
		return ""
	}
	return folded("^(?:" + strings.Join(alternatives, "|") + ")$")
}

// recordsPlaces は記録と state の既定の置き場（config.DefaultLog・DefaultState）と、閉じたチケットの退避
// （config.ArchiveName）を、名指しのツール（解決済みの絶対パス）に当てる表記。
var recordsPlaces = func() string {
	stem, ext := ospath.Splitext(ospath.Basename(config.DefaultLog))
	return pyre.AnySeparator + pyre.Escape(ospath.Dirname(config.DefaultLog)) + pyre.AnySeparator +
		`(?:` + pyre.Escape(stem) + `(?:\.[^\\/]*)?` + pyre.Escape(ext) + `$|(?:` +
		pyre.Escape(ospath.Basename(config.DefaultState)) + `|` + pyre.Escape(config.ArchiveName) + `)(?:[\\/]|$))`
}()

// recordsRegex は記録と state の置き場を名指しのツールに当てる式。診断のフラグで動かした置き場にも当てる
// （記録はいまの 1 本と同じディレクトリのローテートした分。書かれたパスと行き着く先の両方）。
func recordsRegex(logPath, stateDir string) string {
	alternatives := []string{recordsPlaces}
	if logPath != "" {
		directory, base := ospath.Dirname(logPath), ospath.Basename(logPath)
		stem, ext := ospath.Splitext(base)
		for _, where := range sortedSet(ospath.Abspath(directory), ospath.Realpath(directory)) {
			alternatives = append(alternatives,
				"^"+spelled(where)+pyre.AnySeparator+pyre.Escape(stem)+`(?:\.[^\\/]*)?`+pyre.Escape(ext)+"$")
		}
	}
	if stateDir != "" {
		for _, where := range sortedSet(ospath.Abspath(stateDir), ospath.Realpath(stateDir)) {
			alternatives = append(alternatives, "^"+spelled(where)+`(?:[\\/]|$)`)
		}
	}
	return folded("(?:" + strings.Join(alternatives, "|") + ")")
}

// GitDirPathRegex は `.git` の中（とワークツリーの `.git` ファイル）を名指しのツールに当てる式。前は区切りで閉じる
// （`.gitignore`・`.github/` には当たらない）。名前の後ろの `.` と空白は Windows が開くときに落とすので、飛ばす。
const GitDirPathRegex = `[\\/]\.git[. ]*(?:[\\/]|$)`

const (
	// `.git` の前に来てよいのは語の頭・引用がつないだ空白・区切り・`--opt=` の `=`（`repo.git/` や URL には当たらない）。
	gitDirName   = `\.git\.*` + end
	gitDirPrefix = `(?:[^ \x00\x01]*[\\/])?`
	// gitVerbWrite は verbWrite から dd を外し、`<` の直後の語（読み）を外したもの。
	gitVerbWrite = `(^|\x00)(mv|rm|tee|truncate|patch|shred)\b(?:[^\x00]*[^<\x00])?[ \x01]` +
		`(?:[^ \x00\x01<]*[\\/=])?`
	gitDDWrite = `(^|\x00)dd\b[^\x00]*[ \x01]of=(?:[^ \x00]*[\\/])?`
	// sed -i は式（最初の選択肢でない語）を飛ばし、その後ろの語だけを書き先とみる。
	sedScript   = `(?>(?: -[^ \x00]*)* [^- \x00][^ \x00]*)`
	sedInPlace  = `[ \x01](?:-[A-Za-z]*?i|--i)`
	gitSedFile  = `[^\x00]*?[ \x01]` + gitDirPrefix + gitDirName
	gitSedWrite = `(^|\x00)sed\b(?:` +
		`(?>(?: -[^ \x00]*)*?` + sedInPlace + `[^ \x00]*(?: -[^ \x00]*)* [^- \x00][^ \x00]*)` +
		gitSedFile +
		`|` + sedScript + `(?:(?>[^\x00]*?` + sedInPlace + `)` + gitSedFile +
		`|(?>` + gitSedFile + `)[^\x00]*?` + sedInPlace + `))`
	// gitFindWrite は開始点に `.git` を置き、消す・コマンドを走らせる述語を持つ find。読み切れない形は生の文字列に
	// 当たるので、切れ目に生の `|` `;` `&` 改行も数え、`.git` の前後の引用符も許す。
	gitFindWrite = `(?:^|[\x00|;&\n]) *find(?: -[HLP])*` +
		`(?>(?: [^-!( \x00][^ \x00]*)*? (?:[^ \x00]*[\\/'\"])?\.git\.*(?:[\\/][^ \x00]*)?['\"]? )` +
		`(?:[^\x00]*[ \x01])?(?:-delete|-exec|-execdir|-ok|-okdir)\b`
	// gitXargsWrite は前のコマンドが `.git` を名指しし、xargs が書き込む動詞に渡す形（生の文字列でも当たる）。
	gitXargsWrite = `(?:^|[\x00|;\n])` +
		`(?>[^\x00|;\n]*?[ \x01\\/='\"]\.git\.*(?:[\\/ \x00\x01'\"]|$))` +
		`[^\x00|;\n]*(?:\x00|\|) *xargs\b[^\x00|;\n]*[ \x01'\"]` +
		`(?:rm|mv|tee|dd|truncate|patch|shred|cp|ln|install)\b`
	// gitRedirect は行き先の前置きに `>` をまたがせない（生の文字列で `>` ごとに尾を読み直すと長さの 2 乗になる）。
	gitRedirect = `(?:^|[^>|&])[|&]*>[>|&]* ?(?:[^ ` + NotAWord + `>|&][^ \x00>]*[\\/])?`
)

// GitDirShellRegex は `.git` へシェルから書き込む形（組み込みの土台が持つ）を返す。読むだけ（`cat .git/config`・
// `cp .git/config /tmp/x`）は通す。いま居る場所へ `.git` をコピーする形は、元の名前が `.git` のときだけ止める。
func GitDirShellRegex() string {
	writes := `(?:` + gitRedirect + `|` + gitVerbWrite + `|` + gitDDWrite + `)`
	last := `(?:[^ \x00]*[\\/])?\.git\.*` + copyEnd
	return strings.Join([]string{
		folded(writes + gitDirName),
		folded(gitSedWrite),
		folded(gitFindWrite),
		folded(gitXargsWrite),
		CopyDestinationRegex(folded(last), folded(gitDirName)),
		HolderRegex(`\.`, `\.git`, true),
	}, "|")
}

const (
	// RedirectLimit は、生の文字列（読み切れなかった Bash と PowerShell）に、シェルから書き込む形の保護を当ててよい `>` の個数の
	// 上限。生の文字列では引用の中の `>` にも当たるので、手数が `>` の個数 × 語の長さになる。超えたら照合を飛ばし、権限モードに
	// 依らず拒否にする。判定の期限を超えると、理由も案内も返せないため。`>>` も `2>&1` も文字の数で数える。
	RedirectLimit = 50
	// RawLengthLimit は `>` を含む生の文字列の長さの上限。個数の上限だけでは尾の長さを止められない（#241）。
	RawLengthLimit = 100_000
)

// TooManyRedirects は生の文字列の `>` が RedirectLimit を超えるか、`>` を含んで RawLengthLimit 文字を超えるか。
func TooManyRedirects(subject string) bool {
	count := strings.Count(subject, ">")
	return count > RedirectLimit || (count > 0 && utf8.RuneCountInString(subject) > RawLengthLimit)
}

// sortedSet は a と b を昇順に並べる（同じなら 1 つ）。式の並びが実行のたびに変わらないようにする。
func sortedSet(a, b string) []string {
	if a == b {
		return []string{a}
	}
	if b < a {
		a, b = b, a
	}
	return []string{a, b}
}

// unique は最初に出た順を保って、重なりを除く。
func unique[T comparable](items []T) []T {
	var out []T
	seen := map[T]bool{}
	for _, it := range items {
		if !seen[it] {
			seen[it] = true
			out = append(out, it)
		}
	}
	return out
}
