package ids

import (
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyfmt"
)

const (
	// JAChars は識別子に使える日本語の字（ひらがな・カタカナ・長音記号・CJK 統合漢字・々）。
	JAChars = "々ぁ-ゖァ-ヺー一-鿿"
	// IDChars は識別子の 2 文字目以降の文字クラスの中身（`[...]` に入れて使う）。拡張と sh が写している。
	IDChars = `A-Za-z0-9._\-` + JAChars

	// MaxChildNumber は子の識別子のフェーズ番号と連番の上限（どちらも 2 桁）。
	MaxChildNumber = 99
	// MaxIDLength は識別子の長さの上限（文字数）。識別子はパスに 2 回出ることがあり、Windows の MAX_PATH に収める。
	MaxIDLength = 64
	// SuggestedIDLength は issue から作る識別子と新規の提案に勧める長さ（lint の warn）。
	SuggestedIDLength = 48
	// maxAdoptIDLength は既にあるブランチを親に採用するときの上限（子は親 + 6 文字で 64 文字に収める）。
	maxAdoptIDLength = MaxIDLength - 6

	// DefaultIssuePrefix は issue から始めるときの既定の先頭の語。
	DefaultIssuePrefix = "feature"
	// EmptySlug はタイトルから何も残らなかったときの slug。
	EmptySlug = "issue"

	// BranchKey は使えない `branch:` の欄の名前。あればチケットの読み込みが error にする。
	BranchKey = "branch"
	// MaxBranchLength はブランチ名の長さの上限（文字数）。ref はパスにもなる。
	MaxBranchLength = 200
)

// ReservedBranchIDs は統合先や保護されたブランチの名前（ccnavi-git.sh の push の拒否と同じリスト）。
// infra/config の先頭の語の予約も同じリストを読むので、名前は config に置く。
var ReservedBranchIDs = config.ReservedBranchIDs

// refPrefixes は先頭の階層に置けない語（git の ref の名前とリモートの名前）。
var refPrefixes = []string{"refs", "heads", "remotes", "tags", "origin", "upstream", "head"}

var (
	idPattern     = regexp.MustCompile(`\A[A-Za-z0-9][` + IDChars + `]*\z`)
	childPattern  = regexp.MustCompile(`\A([A-Za-z0-9][` + IDChars + `]*)-([0-9]{2})-([0-9]{2})\z`)
	childTail     = regexp.MustCompile(`-[0-9]{2}\z`)
	namePattern   = regexp.MustCompile(`\A[A-Za-z0-9][A-Za-z0-9._-]*\z`)
	formPattern   = regexp.MustCompile(`\A([a-z][a-z0-9]*)-([1-9][0-9]*)-([A-Za-z0-9` + JAChars + `][` + IDChars + `]*)\z`)
	slugDrop      = regexp.MustCompile(`[^A-Za-z0-9` + JAChars + `]+`)
	branchPattern = regexp.MustCompile(`\A[A-Za-z0-9][` + IDChars + `/]*\z`)
)

func runes(s string) int { return utf8.RuneCountInString(s) }

// IDProblem は識別子として読めない理由。読めれば空。文字・NFC・長さを見る（形は見ない）。
func IDProblem(text string) string {
	if text == "" {
		return "識別子がありません。"
	}
	if pyfmt.NFC(text) != text {
		return "識別子が NFC でない（macOS で濁点が分かれた NFD の表記など）。" +
			"見た目が同じでも別の名前になるので NFC で書く"
	}
	if !idPattern.MatchString(text) {
		return "識別子に使えない文字がある（先頭は ASCII の英数字、続きは英数字・`.`・`_`・`-`・" +
			"ひらがな・カタカナ・漢字）"
	}
	if n := runes(text); n > MaxIDLength {
		return fmt.Sprintf("識別子が長すぎる（%d 文字。上限は %d 文字）", n, MaxIDLength)
	}
	return ""
}

// IsValidID は識別子として読めるか。区切り文字と先頭の "." を持たないので、ファイル名を組む側と sh から
// 引数を受ける側が自分でも同じ検査を当てるのに使う。
func IsValidID(text string) bool { return IDProblem(text) == "" }

// IsValidName は ASCII の 1 語の名前の形か（プロジェクト・リスクとフェーズの定義の識別子・テンプレートの名前）。
// 全文で見る。Python の `$` で書いた呼び手は、末尾の改行 1 つを外してから渡す。
func IsValidName(text string) bool { return namePattern.MatchString(text) }

// IsValidFamily は sh から引数で渡る親の識別子の形か。識別子として読めて、パスとして読まれる ".." を含まない。
func IsValidFamily(text string) bool { return IsValidID(text) && !strings.Contains(text, "..") }

// Child は子の識別子（`<親>-<2 桁のフェーズ番号>-<2 桁の連番>`）を読んだもの。
type Child struct {
	Parent string
	Phase  int
	Seq    int
}

// ParseChild は子の識別子の形なら、その親・フェーズ番号・連番と真。識別子だけから親を割り出すときは
// 右から 2 段を剥がすので、親が "-" や数字を含んでも 1 通りに決まる。
func ParseChild(id string) (Child, bool) {
	m := childPattern.FindStringSubmatch(id)
	if m == nil {
		return Child{}, false
	}
	phase, _ := strconv.Atoi(m[2])
	seq, _ := strconv.Atoi(m[3])
	return Child{Parent: m[1], Phase: phase, Seq: seq}, true
}

// ChildID は子の識別子を組む。フェーズ番号も連番も 2 桁の 0 埋め。
func ChildID(parent string, phase, seq int) string {
	return fmt.Sprintf("%s-%02d-%02d", parent, phase, seq)
}

// HasChildTail は識別子の末尾が `-<2 桁>` か。親の識別子に置くと子の識別子の途中と紛れる。
func HasChildTail(id string) bool { return childTail.MatchString(id) }

// Form は新しい親の識別子の形 `<先頭の語>-<番号>-<slug>` を読んだもの。
type Form struct {
	Prefix string
	Number int
	Slug   string
}

// FormOf は name が `<先頭の語>-<番号>-<slug>` の形で、先頭の語が prefixes にあれば、その読みと真。
// 番号が int に収まらないものは形に合わないとする。
func FormOf(name string, prefixes []string) (Form, bool) {
	m := formPattern.FindStringSubmatch(name)
	if m == nil || !slices.Contains(prefixes, m[1]) {
		return Form{}, false
	}
	n, err := strconv.Atoi(m[2])
	if err != nil {
		return Form{}, false
	}
	return Form{Prefix: m[1], Number: n, Slug: m[3]}, true
}

// DirectNameWarning は直接作業のワークツリーの名前が形に合わないときの案内（1 行）。合えば空。
// root があれば切り方を sh の完全な打ち方で出す。
func DirectNameWarning(name string, prefixes []string, root string) string {
	if _, ok := FormOf(name, prefixes); ok {
		return ""
	}
	cut := "`ccnavi-worktree.sh <名前>`"
	if root != "" {
		cut = "`" + config.ScriptCommand(root, config.WorktreeWrapper) + " <名前>`"
	}
	return "直接作業のワークツリー名 " + name + " が `<先頭の語>-<番号>-<slug>` の形ではありません。" +
		"次から、先頭の語（CCNAVI_BRANCH_PREFIXES）と番号（issue の番号、" +
		"無ければ `ccnavi-ticket.sh next-id` の通し番号）を付けて " +
		cut + " で切ってください。既にある名前は変えなくてよい"
}

// IdentifierNumber は親の識別子に含まれる番号。読むのは `<先頭の語>-<番号>-<slug>` だけ。無ければ偽。
func IdentifierNumber(name string, prefixes []string) (int, bool) {
	f, ok := FormOf(name, prefixes)
	return f.Number, ok
}

// NextSerial は直接作業のワークツリー名に付ける次の通し番号（番号を持つ識別子の最大 + 1）。先頭の語が違っても
// 同じ通し番号を使う。skipChildren が偽なら子の形の名前も数える（手元のブランチ名には親の居ない子が混ざる）。
func NextSerial(names []string, prefixes []string, skipChildren bool) int {
	most := 0
	for _, name := range names {
		if _, child := ParseChild(name); skipChildren && child {
			continue
		}
		if n, ok := IdentifierNumber(name, prefixes); ok && n > most {
			most = n
		}
	}
	return most + 1
}

// issueSlug は issue のタイトルから作る slug。英数字と日本語の字だけを残し、ほかは "-" にまとめる。
// NFKC で全角英数を半角に、ASCII の英字を小文字にそろえる。room 文字を超えるときは語の区切りで切り、
// 最初の 1 語が room を超えるときだけその語を room 文字で切る。何も残らなければ空。
func issueSlug(title string, room int) string {
	var b strings.Builder
	for _, c := range pyfmt.NFKC(title) {
		if c < utf8.RuneSelf && 'A' <= c && c <= 'Z' {
			c += 'a' - 'A'
		}
		b.WriteRune(c)
	}
	slug := strings.Trim(slugDrop.ReplaceAllString(b.String(), "-"), "-")
	room = max(room, 0)
	if runes(slug) <= room {
		return slug
	}
	kept := ""
	for _, word := range strings.Split(slug, "-") {
		joined := word
		if kept != "" {
			joined = kept + "-" + word
		}
		if runes(joined) > room {
			break
		}
		kept = joined
	}
	if kept == "" {
		kept = string([]rune(slug)[:room])
	}
	return strings.TrimRight(kept, "-")
}

// IssueError は issue から識別子を作れない（番号・プロジェクト名・先頭の語が形に合わない）理由。
// Error() は Python の ValueError の文と同じ。
type IssueError struct{ Detail string }

func (e *IssueError) Error() string { return e.Detail }

// IssueIdentifier は issue から親の識別子 `<prefix>-<番号>-<slug>` を決め、タイトルが長くて slug を短くしたかを
// 添える。プロジェクトの issue なら slug の頭に `<プロジェクト名>-` を付ける（issue の番号はリポジトリごと）。
// 全体は SuggestedIDLength 文字に収め、末尾が `-<2 桁>` になるときはその部分を除く。
// 「issue → 識別子」はこの 1 つだけ。作れなければ *IssueError。
func IssueIdentifier(number int, title, project, prefix string) (string, bool, error) {
	fail := func(detail string) (string, bool, error) {
		return "", false, &IssueError{Detail: detail}
	}
	if number <= 0 {
		return fail(fmt.Sprintf("issue の番号は正の整数: %d", number))
	}
	if project != "" && !IsValidName(project) {
		return fail("プロジェクト名が識別子の形でない: " + pyfmt.Repr(project))
	}
	if !config.IsBranchPrefix(prefix) {
		return fail("先頭の語が形に合わない: " + pyfmt.Repr(prefix))
	}
	head := fmt.Sprintf("%s-%d-", prefix, number)
	if project != "" {
		head += project + "-"
	}
	slug := issueSlug(title, SuggestedIDLength-runes(head))
	// NFKC で字数が増えるタイトルでも切られないよう、十分大きい上限で全体の slug を作る。
	shortened := runes(slug) < runes(issueSlug(title, 1_000_000))
	ident := head + slug
	for slug != "" && HasChildTail(ident) {
		r := []rune(slug)
		slug = strings.TrimRight(string(r[:max(len(r)-3, 0)]), "-")
		ident = head + slug
	}
	if slug == "" {
		ident = head + EmptySlug
	}
	if problem := IDProblem(ident); problem != "" {
		return fail("issue から識別子を作れない: " + problem)
	}
	return ident, shortened, nil
}

// BranchToID は親のブランチ名から親の識別子を作る（"/" を "-" に置き換える）。sh（ccnavi_branch_to_id）と
// TS（branchToId）にも同じものがあり、tests/fixtures/branch-to-id.json の表で一致を確かめる。
func BranchToID(branch string) string { return strings.ReplaceAll(branch, "/", "-") }

// AdoptionIDProblem は既にあるブランチを親に採用するとき、識別子が使えない理由。使えれば空。
func AdoptionIDProblem(branch string) string {
	ident := BranchToID(branch)
	if problem := IDProblem(ident); problem != "" {
		return problem
	}
	if n := runes(ident); n > maxAdoptIDLength {
		return fmt.Sprintf("識別子が長すぎる（%d 文字。子の識別子を収めるため採用できるのは %d 文字まで）",
			n, maxAdoptIDLength)
	}
	if HasChildTail(ident) {
		return "識別子の末尾が `-<2 桁>` になる（子の識別子の途中と紛れる）"
	}
	return ""
}

// IDAnswer は sh の `worktree id <ブランチ>` への答え（1 行 1 項目。id・problem・direct・child）。
// sh は字の種類と NFC と文字数を自分で確かめない（ロケールでバイト数と字数が変わる）ので、ここに聞く。
func IDAnswer(branch string) []string {
	ident := BranchToID(branch)
	lines := []string{"id " + ident}
	shape := BranchProblem(branch)
	problem := shape
	if problem == "" {
		problem = AdoptionIDProblem(branch)
	}
	if problem != "" {
		lines = append(lines, "problem "+problem)
	}
	direct := shape
	if direct == "" {
		direct = IDProblem(ident)
	}
	if direct != "" {
		lines = append(lines, "direct "+direct)
	}
	if _, child := ParseChild(ident); !strings.Contains(branch, "/") && child {
		lines = append(lines, "child yes")
	}
	return lines
}

// BranchProblem は親のブランチ名として使えない理由。使えれば空。字は識別子の字に "/" を足したものだけで、
// git check-ref-format の形の規則、統合先や保護されたブランチの名前、NFC でない表記を断る。
func BranchProblem(name string) string {
	if name == "" {
		return "ブランチ名が空"
	}
	if pyfmt.NFC(name) != name {
		return "ブランチ名が NFC でない（見た目が同じでも別の名前になるので NFC で書く）"
	}
	if n := runes(name); n > MaxBranchLength {
		return fmt.Sprintf("ブランチ名が長すぎる（%d 文字。上限は %d 文字）", n, MaxBranchLength)
	}
	if !branchPattern.MatchString(name) {
		return "ブランチ名に使えない字がある（先頭は ASCII の英数字、続きは英数字・" +
			"`.`・`_`・`-`・`/`・ひらがな・カタカナ・漢字。空白・制御文字・記号は使わない）"
	}
	if strings.Contains(name, "..") || strings.Contains(name, "//") || strings.HasPrefix(name, "/") ||
		strings.HasSuffix(name, "/") || strings.HasSuffix(name, ".") {
		return "git のブランチ名に使えない形（`..`・`//`・先頭や末尾の `/`・末尾の `.`）"
	}
	for _, part := range strings.Split(name, "/") {
		if strings.HasPrefix(part, ".") || strings.HasSuffix(part, ".lock") {
			return "git のブランチ名に使えない形（`.` で始まる階層・`.lock` で終わる階層）"
		}
	}
	folded := pyfmt.Casefold(name)
	parts := strings.Split(folded, "/")
	if slices.Contains(refPrefixes, parts[0]) {
		return "先頭の階層 `" + strings.Split(name, "/")[0] + "` は git の ref の名前" +
			"（refs・heads・remotes・tags）か" +
			"リモートの名前（origin・upstream）と紛らわしい。" +
			"`origin/main` のような名前は親のブランチにしない"
	}
	for _, p := range parts {
		if p == "head" || strings.HasSuffix(p, "_head") {
			return "`HEAD`・`FETCH_HEAD` のような階層は git の特別な名前と紛らわしい"
		}
	}
	if slices.Contains(ReservedBranchIDs, folded) || slices.Contains(ReservedBranchIDs, parts[0]) ||
		strings.HasPrefix(folded, "release-") {
		return "統合先や保護されたブランチの名前（main・master・develop・release と、" +
			"それを先頭の階層に持つ main/*・release/* など、release-*）は親のブランチにしない"
	}
	return ""
}

// SameID は大文字小文字だけが違う識別子を同じと読む（区別しないファイルシステムではぶつかるため）。
func SameID(a, b string) bool { return pyfmt.Casefold(a) == pyfmt.Casefold(b) }
