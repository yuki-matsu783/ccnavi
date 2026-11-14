package gitwrap

import (
	"os"
	"regexp"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
)

// isAbsSlashed は `/` 区切りのパスが絶対パス（`/x` か `C:/x`）か。
func isAbsSlashed(p string) bool {
	return strings.HasPrefix(p, "/") || ospath.HasDrive(p, true) && strings.HasPrefix(p[2:], "/")
}

// clean は `.` と `..` を取り除いた名前の並び。トップより上に出たら above が真。
func clean(p string) (parts []string, above bool) {
	for _, part := range strings.Split(p, "/") {
		switch part {
		case "", ".":
		case "..":
			if len(parts) == 0 {
				above = true
			} else {
				parts = parts[:len(parts)-1]
			}
		default:
			parts = append(parts, part)
		}
	}
	return parts, above
}

// stores は承認済みチケットの置き場とレビュー待ち（git のトップからの相対）。
var stores = []string{".ccnavi/approved", "wip/proposals/review"}

// StoreHit は、承認済みチケットの置き場（`.ccnavi/approved/`）かレビュー待ち（`wip/proposals/review/`）にヒットするパスか。
//
// `checkout <ref> <パス>`・`restore --source <ref>` で置き場を過去の中身に戻す形と、`restore --ours / --theirs` で
// 置き場の衝突を片側の中身で解く形を止めるために使う。置き場を動かすのはユーザと ccnavi のスクリプトで、エージェントが
// git で戻すと、承認が無かったことにも、承認が戻ったことにもなる。
//
// 比べるのは git のトップからのパス（cwd からの相対は `..` を取り除いてから比べる）。置き場の親（`.ccnavi`・`wip`・`.`）も
// 置き場ごと戻すのでヒットする。`*` `?` `[` と `:` で始まる pathspec は、どこにヒットするかを決められないのでヒットとみなす。
// 絶対パスは、トップの表記が OS で揃わない（`C:/x` と `/c/x`）ので、置き場のパスを含むかだけを見る。
func (e *Env) StoreHit(arg string) bool {
	arg = ospath.Slashed(arg)
	if strings.HasPrefix(arg, ":") || strings.ContainsAny(arg, "*?[") {
		return true
	}
	abs := isAbsSlashed(arg)
	path := arg
	if !abs {
		path = e.Prefix() + arg
	}
	parts, above := clean(path)
	if above {
		return false
	}
	norm := pathcase.Fold(strings.Join(parts, "/"))
	for _, store := range stores {
		store = pathcase.Fold(store)
		if abs {
			if strings.Contains("/"+norm+"/", "/"+store+"/") {
				return true
			}
			continue
		}
		if norm == "" || norm == store || strings.HasPrefix(norm, store+"/") || strings.HasPrefix(store+"/", norm+"/") {
			return true
		}
	}
	return false
}

// coreSettings・coreDecisions は、守る場所のうち名前に `*` を含むもの（`.claude/settings*.json`・`logs/decisions*.jsonl`）。
var (
	coreSettings  = regexp.MustCompile(`/\.claude/settings.*\.json/`)
	coreDecisions = regexp.MustCompile(`^/logs/decisions.*\.jsonl/`)
)

// coreMarks は、中に持つと ccnavi のツリーとみなす名前。
var coreMarks = []string{".ccnavi", ".claude", ".git"}

// CoreHit は、ccnavi 自身を守る場所（自己防衛の対象）にヒットするパスか。
//
// `rm`・`restore --source`・`checkout <ref> <パス>` で、hook の登録・設定・保護済みのスクリプト・実行ファイル・記録を
// 消す・別のコミットの中身に戻す形を止めるために使う。止めるのは hook が先（DENY_WRAPPER_CORE_PATH）で、ここは 2 つ目。
// 見るのは既定の置き場の名前だけ（動かした置き場は hook が見る）。
//
// ヒットとみなすのは、`.ccnavi/` の下（承認済みの置き場は除く。過去の中身に戻す形は StoreHit が止める）・
// `.claude/settings*.json`・`.claude/hooks/`・ワークスペースルートの `logs/` の記録と state と退避・`ccnavi-git.sh`、
// それらを中に含むディレクトリ（ツリーの頂上と、`.ccnavi`・`.claude`・`.git` を直に、または 1 つ下に持つディレクトリ）。
// `:` で始まる pathspec と、グロブの前に固定のディレクトリが無い指定は、どこにヒットするかを決められないのでヒットとみなす。
func (e *Env) CoreHit(arg string) bool {
	arg = ospath.Slashed(arg)
	if strings.HasPrefix(arg, ":") {
		return true
	}
	if i := strings.IndexAny(arg, "*?["); i >= 0 {
		// グロブは / もまたぐので、グロブの前の固定のディレクトリより下のどこにでもヒットする。
		arg = arg[:i]
		if j := strings.LastIndex(arg, "/"); j >= 0 {
			arg = arg[:j]
		} else {
			arg = ""
		}
		if arg == "" || arg == "." || arg == "./" || arg == "/" {
			return true
		}
	}
	top := ospath.Slashed(e.Top())
	path := arg
	if !isAbsSlashed(arg) {
		path = e.Prefix() + arg
		if top != "" {
			path = top + "/" + path
		}
	}
	parts, _ := clean(path)
	norm := ""
	for _, part := range parts {
		norm += "/" + part
	}
	low := pathcase.Fold(norm + "/")
	ws := pathcase.Fold("/" + strings.TrimPrefix(phys(e.Root), "/"))
	if strings.Contains(low, "/.ccnavi/approved/") {
		return false
	}
	if strings.Contains(low, "/.ccnavi/") || strings.HasSuffix(low, "/.claude/") || strings.Contains(low, "/.claude/hooks/") ||
		coreSettings.MatchString(low) || strings.Contains(low, "/ccnavi-git.sh/") {
		return true
	}
	if rest, ok := strings.CutPrefix(low, ws); ok &&
		(rest == "/logs/" || strings.HasPrefix(rest, "/logs/state/") || strings.HasPrefix(rest, "/logs/archive/") ||
			coreDecisions.MatchString(rest)) {
		return true
	}
	// ツリーの頂上か、その上。
	if t := pathcase.Fold("/" + strings.TrimPrefix(top, "/")); t == pathcase.Fold(norm) || strings.HasPrefix(t, pathcase.Fold(norm)+"/") {
		return true
	}
	// Windows の `C:/x` は上で `/C:/x` になる。ファイルシステムに聞くときは頭の / を外す。
	dir := norm
	if len(dir) > 3 && ospath.HasDrive(dir[1:], true) {
		dir = dir[1:]
	}
	if !ospath.IsDir(dir) {
		return false
	}
	dirs := []string{dir}
	if names, err := os.ReadDir(dir); err == nil {
		for _, n := range names {
			if sub := dir + "/" + n.Name(); ospath.IsDir(sub) {
				dirs = append(dirs, sub)
			}
		}
	}
	for _, d := range dirs {
		for _, mark := range coreMarks {
			if ospath.DiskLexists(d + "/" + mark) {
				return true
			}
		}
	}
	return false
}
