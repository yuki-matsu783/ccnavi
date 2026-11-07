package scaffold

import (
	"fmt"
	"regexp"
	"strings"
	"unicode"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/form"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/proposal/templates"
)

// allowMatch は allow に書く項の match。判定に使われるのは Write / Edit / NotebookEdit を含む項だけ。
const allowMatch = "Write|Edit"

// parsePlan は --plan design,acceptance,implement を型の並びにする。空の要素は落とす。
func parsePlan(text string) []string {
	var out []string
	for part := range strings.SplitSeq(text, ",") {
		if s := ospath.Strip(part); s != "" {
			out = append(out, s)
		}
	}
	return out
}

// lineBreaking は改行や制御文字（Cc・Zl・Zp）か。タイトルと --scope に入れさせない。
func lineBreaking(r rune) bool { return unicode.In(r, unicode.Cc, unicode.Zl, unicode.Zp) }

// collectAllow は plan の順に各型の scope を集め、重複を除いた glob の並びと、inherit の型の並び。重複は大文字
// 小文字を区別せずに数える（範囲の照合が区別しないため）。
func collectAllow(types *phases.Types, plan []string) (globs, inherited []string) {
	seen := map[string]bool{}
	for _, name := range plan {
		pt := types.Get(name)
		if pt.Inherit {
			if !contains(inherited, name) {
				inherited = append(inherited, name)
			}
			continue
		}
		for _, glob := range pt.ScopeGlobs {
			if key := pyfmt.Lower(glob); !seen[key] {
				seen[key] = true
				globs = append(globs, glob)
			}
		}
	}
	return globs, inherited
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

var drivePrefix = regexp.MustCompile(`\A[A-Za-z]:`)

// parseScope は --scope の値（繰り返し可。カンマ区切りも可）を glob の並びにする。不正なら理由。
func parseScope(values []string) ([]string, string) {
	var globs []string
	seen := map[string]bool{}
	for _, value := range values {
		for part := range strings.SplitSeq(value, ",") {
			glob := ospath.Strip(part)
			if glob == "" {
				continue
			}
			shown := pyfmt.Repr(glob)
			if strings.ContainsFunc(glob, lineBreaking) {
				return nil, "--scope " + shown + " に改行や制御文字は指定できない"
			}
			if strings.ContainsAny(glob, "<>") {
				return nil, "--scope " + shown + " に `<` `>` は指定できない。" +
					"`<触るファイルの glob>` のような案内の仮の値は、実際のパスに置き換える"
			}
			if ticket.GlobHasSpace(glob) {
				return nil, "--scope " + shown + " に" + ticket.GlobSpaceReason
			}
			norm := strings.ReplaceAll(glob, `\`, "/")
			if strings.HasPrefix(norm, "/") || drivePrefix.MatchString(norm) || contains(strings.Split(norm, "/"), "..") {
				return nil, "--scope " + shown + " はワークツリーのルートからの相対の glob で書く" +
					"（絶対パスと `..` は指定できない）"
			}
			if key := pyfmt.Lower(glob); !seen[key] {
				seen[key] = true
				globs = append(globs, glob)
			}
		}
	}
	return globs, ""
}

// plainName は引用符なしで書いてよい型の名前（YAML の真偽・null の語は除く）。
var plainName = regexp.MustCompile(`\A[A-Za-z][A-Za-z0-9_-]*\z`)

var yamlWords = map[string]bool{"y": true, "n": true, "yes": true, "no": true, "on": true, "off": true,
	"true": true, "false": true, "null": true}

func scalar(text string) string {
	if plainName.MatchString(text) && !yamlWords[pyfmt.Lower(text)] {
		return text
	}
	return quote(text)
}

// quote は YAML の二重引用符の文字列にする（JSON の文字列は YAML として読める）。
func quote(text string) string {
	out, _ := pyjson.Dumps(text, pyjson.Options{RawUnicode: true})
	return out
}

// itemLine は計画の 1 ステップ（ticket.Parse が読む形。PlanItem.Raw と同じ書き分け）。
func itemLine(item templates.Item) string {
	if item.Plain() {
		return "  - " + scalar(item.Type)
	}
	parts := []string{"type: " + scalar(item.Type)}
	if item.Review.Said() {
		parts = append(parts, fmt.Sprintf("review: %t", item.Review == ticket.Needed))
	}
	if item.Deferred {
		parts = append(parts, "deferred: true")
	}
	if len(item.After) > 0 {
		nums := make([]string, len(item.After))
		for i, n := range item.After {
			nums[i] = fmt.Sprint(n)
		}
		parts = append(parts, "after: ["+strings.Join(nums, ", ")+"]")
	}
	return "  - {" + strings.Join(parts, ", ") + "}"
}

// linear は型の列を一直線の計画にする（前のステップが各ステップに先行する）。
func linear(plan []string) []templates.Item {
	out := make([]templates.Item, len(plan))
	for i, name := range plan {
		out[i] = templates.Item{Type: name}
		if i > 0 {
			out[i].After = []int{i}
		}
	}
	return out
}

// render は雛形の全文。phases: は書かない（書いたあとに planedit.Fill が差し込む）。after は提案の絶対番号
// （フィードバック計画は全体計画の続き）。issue は 12 か owner/repo#12。
func render(ident, title string, plan, feedback []templates.Item, allow []string, issue string, mr int) string {
	lines := []string{ticket.Fence, fmt.Sprintf("version: %d", ticket.Version), "ticket: " + ident,
		"issue: " + issue, fmt.Sprintf("mr: %d", mr), "title: " + quote(title), "plan:"}
	for _, item := range plan {
		lines = append(lines, itemLine(item))
	}
	if len(feedback) > 0 {
		lines = append(lines, "feedback:")
		for _, item := range feedback {
			lines = append(lines, itemLine(item))
		}
	}
	lines = append(lines, "rationale: |", "  "+form.RationaleTodo, "allow:")
	for _, glob := range allow {
		lines = append(lines, "  - match: "+allowMatch, "    glob: "+quote(glob))
	}
	lines = append(lines, ticket.Fence)
	for _, heading := range form.ParentHeadings {
		lines = append(lines, "## "+heading, "", form.HeadingTodo[heading], "")
	}
	return strings.Join(lines, "\n")
}
