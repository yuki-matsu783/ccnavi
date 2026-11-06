package mirror

import (
	"path/filepath"
	"slices"
	"strings"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathmatch"
	"ccnavi/internal/infra/yaml"
)

// 工程スキル（ワークスペースの `.claude/skills/<工程>/` で、頭の frontmatter に `process: true` を持つもの）から
// プロジェクトの `skills/<工程>/` へ写すのは、SKILL.md と references/common/ の下の .md だけ。references/ の
// それ以外はプロジェクトが育てるものなので、上書きも削除もしない（.ccnavi/docs/skill-processes.md）。

const (
	processKey = "process"
	skillFile  = "SKILL.md"
	// skillsDir は写し先の、プロジェクトのルートからの相対。目録（catalog の projskills）の読み先と同じ。
	skillsDir = "skills"
	// headLimit は frontmatter を探す頭の長さ（バイト）。
	headLimit = 8 * 1024
	shareExt  = ".md"
)

// sourceDir は工程スキルの置き場の、ワークスペースルートからの相対。
var sourceDir = []string{".claude", "skills"}

// commonRefs は references/ の最上段で、共通として写すディレクトリ。
var commonRefs = []string{"references", "common"}

func skillSource(root string) string { return ospath.Join(root, sourceDir...) }

// processes は共通の工程スキルの名前（名前の順）。リンクのディレクトリと SKILL.md は数えない。
func processes(root string) []string {
	base := skillSource(root)
	names, err := ospath.DiskListdir(base)
	if err != nil {
		return nil
	}
	var found []string
	for _, name := range names {
		path := ospath.Join(base, name, skillFile)
		if pathmatch.IsLink(ospath.Join(base, name)) || pathmatch.IsLink(path) || !ospath.IsFile(path) {
			continue
		}
		if raw, existed, _ := readStrict(path); existed && processFlag(raw) {
			found = append(found, name)
		}
	}
	return found
}

// Installed はフェーズ定義の `skill` が指す工程スキルの名前。この機械に無ければ空（案内に出さない）。
func Installed(root, name string) string {
	if name != "" && slices.Contains(processes(root), name) {
		return name
	}
	return ""
}

// processFlag は頭の frontmatter に `process: true`（真偽の真だけ）があるか。別名は読まない。
func processFlag(raw []byte) bool {
	text, _ := fsio.Decode(raw[:min(len(raw), headLimit)], fsio.Replace)
	lines := ospath.Splitlines(text)
	if len(lines) == 0 || ospath.Strip(lines[0]) != "---" {
		return false
	}
	end := slices.IndexFunc(lines[1:], func(line string) bool { return ospath.Strip(line) == "---" })
	if end < 0 {
		return false
	}
	data, err := yaml.LoadRefusingAliases(strings.Join(lines[1:1+end], "\n"))
	if err != nil {
		return false
	}
	top, ok := data.(*yaml.Map)
	return ok && top.Get(processKey) == true
}

// isShared は工程スキルのディレクトリからの相対（"/" で割ったもの）が、写す形か。
func isShared(parts []string) bool {
	if len(parts) == 1 && parts[0] == skillFile {
		return true
	}
	n := len(commonRefs)
	return len(parts) > n && slices.Equal(parts[:n], commonRefs) && strings.HasSuffix(parts[len(parts)-1], shareExt) &&
		!slices.ContainsFunc(parts, func(p string) bool { return p == "" || p == "." || p == ".." })
}

// skillSources は写すもの（プロジェクトの skills/ からの相対 → 中身）。写せなければ理由。
func skillSources(root string) (map[string][]byte, string) {
	out := map[string][]byte{}
	base := skillSource(root)
	for _, name := range processes(root) {
		top := ospath.Join(base, name)
		shared := ospath.Join(top, commonRefs...)
		// references や common そのものがリンクなら、外を指して写せてしまう。
		for _, chain := range []string{ospath.Join(top, commonRefs[0]), shared} {
			if pathmatch.IsLink(chain) {
				return nil, relPlain(root, chain) + linkedEntry
			}
		}
		found, why := scan(shared, root)
		if why != "" {
			return nil, why
		}
		files := []string{ospath.Join(top, skillFile)}
		for _, rel := range found {
			files = append(files, ospath.Join(shared, filepath.FromSlash(rel)))
		}
		for _, path := range files {
			rel := relPlain(top, path)
			if !isShared(strings.Split(rel, "/")) {
				continue
			}
			raw, existed, why := readStrict(path)
			if why != "" || !existed {
				if why == "" {
					why = missing
				}
				return nil, unreadable(relPlain(root, path), why)
			}
			out[name+"/"+rel] = raw
		}
	}
	return out, ""
}

// skillPlan はプロジェクトの skills/ に加える変更。共通側から無くなった references/common/ の .md は消す。
func skillPlan(root, treeRoot string) ([]Change, string) {
	wanted, why := skillSources(root)
	if why != "" {
		return nil, why
	}
	base := ospath.Join(treeRoot, skillsDir)
	changes, why := updates(treeRoot, base, wanted)
	if why != "" {
		return nil, why
	}
	for _, name := range processes(root) {
		common := ospath.Join(base, append([]string{name}, commonRefs...)...)
		if !ospath.DiskLexists(common) {
			continue
		}
		if pathmatch.HasLink(treeRoot, common) {
			return nil, relPlain(treeRoot, common) + linkedPath
		}
		present, why := scan(common, ospath.Dirname(common))
		if why != "" {
			return nil, why
		}
		key := name + "/" + strings.Join(commonRefs, "/") + "/"
		gone, why := removals(treeRoot, common, present, func(rel string) bool {
			_, kept := wanted[key+rel]
			return !kept && isShared(append(slices.Clone(commonRefs), strings.Split(rel, "/")...))
		})
		if why != "" {
			return nil, why
		}
		changes = append(changes, gone...)
	}
	return changes, ""
}

// applySkills は skills/ へ変更を書く。消して空になったディレクトリは、その references/common/ の下から片付ける。
func applySkills(changes []Change) string { return apply(changes) }
