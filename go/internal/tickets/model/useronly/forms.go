package useronly

import (
	"strings"

	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/policy/selfguard"
)

// 表から組む式。どれも Unicode の `\s`・`\w`、後読み、大小の無視を使うので、Go の regexp ではなく pyre で読む。

func alt(words []string) string {
	out := make([]string, len(words))
	for i, w := range words {
		out[i] = pyre.Escape(w)
	}
	return strings.Join(out, "|")
}

// 免除の理由になるのは、単独の語として現れた `--preview` だけ。前は生の空白、後ろは空白か区切りか行末。
// 区切りに `;` `&` `|` と改行も見るのは、PowerShell の生の文字列（Sep が入らない）で、後ろのコマンドの
// `--preview` が前のコマンドの `--agree` を免除しないため。語の中の目印（引用がつないだ空白）もまたがない。
// またぐと `ccnavi --agree x "a --preview"` の `--preview` が免除の理由になる。`--reason=--preview`・
// `--preview=x`・`x--preview` も免除しない（実行ファイルは `--agree` を走らせる）。
var (
	previewEnd  = `[ \t;&|\r\n` + pyre.Escape(shell.Sep) + `]`
	previewWord = `[ \t]--preview(?=` + previewEnd + `|$)`
	notPreview  = `(?![^` + selfguard.NotAWord + `;&|\r\n]*` + previewWord + `)`
)

// cliForms は実行ファイルを直に呼ぶ形のうち、オプションとサブコマンドの部分（`launcher` の後ろに続ける）。
// notPreview は previewExempt のオプションにだけ付ける。承認を行う `--yes` は独立した枝で、必ず当てる。
func cliForms(notPreview string) string {
	var parts, exempt []string
	for _, f := range flags {
		if f.cli && f.previewExempt && f.where == "" {
			exempt = append(exempt, f.name)
		}
	}
	for _, f := range flags {
		if !f.cli || f.where != "" {
			continue
		}
		if f.previewExempt {
			if f.name == exempt[0] {
				parts = append(parts, `(?:`+alt(exempt)+`)\b`+notPreview)
			}
			continue
		}
		parts = append(parts, pyre.Escape(f.name)+`\b`)
	}
	for _, s := range subs {
		// サブコマンドの前の `--`（`ticket -- finish`）は、実行ファイルがオプションの終わりとして読み飛ばす。
		parts = append(parts, `\b(?:`+strings.Join(s.groups, "|")+`)\s+(?:--\s+)?(?:`+strings.Join(s.names, "|")+`)\b`)
	}
	return "(?:" + strings.Join(parts, "|") + ")"
}

// scriptForms はユーザ専用の sh（`ccnavi-user-*.sh`）を、引数に依らずコマンドの位置で止める式。
// 名前がコマンド行のどこにあっても止める検査は HumanPathForm が受け持つ。
func scriptForms() string {
	return `(^|\x00|[;&|]\s*)((sh|bash)(\s+-\S+)*\s+)?\S*` + pyre.Escape(UserScriptPrefix) + `[\w-]+\.sh\b`
}

func wordPattern(words []string) string { return `(?<![\w-])(?:` + alt(words) + `)(?![\w-])` }

func agentScriptForms() string { return `ccnavi-(?:` + strings.Join(agentScripts, "|") + `)\.sh` }

// holdExemptForms は止めている間も通す `ccnavi-…sh` の部分。エージェントの 3 本と、起動の sh の資料の引き。
func holdExemptForms() string {
	return `(?:` + agentScriptForms() + `|ccnavi-launcher\.sh\s+(?:` + alt(holdExemptLauncherFlags) + `))`
}

// callHead は呼び出しの前置き。`sh`・`bash`（選択肢付き）、直接実行（パス・引用したパス）、
// `exec`・`command`・`source`・`.` を含める。
const callHead = `(^|[;&|]\s*)(?:(?:exec|command|source|\.)\s+)?(?:(?:sh|bash)(?:\s+-\S+)*\s+)?[\"']?[^\s;&|]*?`

func subagentForbiddenForms() string {
	var parts []string
	for _, role := range subagentRoles {
		name := pyre.Escape(role.stem) + `\.sh[\"']?(?=\s|$|[;&|])`
		listed := strings.Join(role.subs, "|")
		switch role.kind {
		case roleAll:
			parts = append(parts, callHead+name)
		case roleAllow:
			parts = append(parts, callHead+name+`(?!\s+(?:`+listed+`)(?![\w-]))`)
		default:
			parts = append(parts, callHead+name+`\s+(?:`+listed+`)(?![\w-])`)
		}
	}
	return strings.Join(parts, "|")
}

// wrapperSkip はラッパーのオプションを読み飛ばす部分。値は付けなくても読む。空の引用符で値を空にした形（`--root ""`）は、
// hook が読む前に引用符ごと消えて `--root user agree` に見えるので、`user` を値と読んで入口を見逃さないため。
func wrapperSkip() string {
	value := `(?:"[^"]*"|'[^']*'|\S+)`
	return `(?:(?:` + alt(wrapperOptions) + `)(?:=\S*|\s+` + value + `)?\s+)*`
}

// userEntryForms は `user` の入口の部分（`launcher` の後ろに続ける）。`--` を挟んだ形も止める（実行ファイルは
// 断るが、止める側に倒す）。excluding は別の deny が止める操作（prune はコアのファイルの保護の設定に従う）。
func userEntryForms(excluding string) string {
	rest := ""
	if excluding != "" {
		rest = `(?!\s+` + pyre.Escape(excluding) + `\b)`
	}
	return `\s+` + wrapperSkip() + `(?:--\s+)?` + userWord + `\b` + rest
}

// userVerbForms は `user <verb>` の形（`--` は読み飛ばさない）。
func userVerbForms(verb string) string {
	return `\s+` + wrapperSkip() + `(?:--\s+)?` + userWord + `\s+` + pyre.Escape(verb) + `\b`
}

func userScriptPattern() string { return `(?<![\w-])` + pyre.Escape(UserScriptPrefix) + `[\w-]+\.sh` }
