package prune

import (
	"sort"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
)

// pruneState は終わったセッションの記録を、セッションごとにまとめて消す。置き場そのものがリンクなら、
// 何も消さずに報告に出す（辿って消すと置き場の外のファイルを消す）。見るのは置き場そのものだけで、
// ワークスペースルートからの道のリンクは見ない（Python と同じ。pruneDirectory は道も見る）。
func (r *run) pruneState(stateDir, session string, before time.Time) {
	if ospath.IsLink(stateDir) {
		r.problem("%s はリンクなので辿らない（記録を削除しません）", r.shown(stateDir))
		return
	}
	groups, loose := r.sessionEntries(stateDir)
	mine := map[string]bool{}
	if session != "" {
		mine[fsio.SafeName(session, fsio.SafeNameLimit)] = true
		mine[fsio.SafeName(session, fsio.NoLimit)] = true
	}
	tokens := make([]string, 0, len(groups))
	for token := range groups {
		tokens = append(tokens, token)
	}
	sort.Strings(tokens)
	var doomed []string
	for _, token := range tokens {
		if mine[token] {
			continue
		}
		alive := false
		for _, path := range groups[token] {
			if !touched(path).Before(before) {
				alive = true
			}
		}
		if !alive {
			doomed = append(doomed, groups[token]...)
		}
	}
	// どのセッションにも結び付けられなかったもの（名前の切れ目が決まらない）は、それ自身の日付で見る。
	for _, path := range loose {
		if touched(path).Before(before) {
			doomed = append(doomed, path)
		}
	}
	sort.Strings(doomed)
	for _, path := range doomed {
		if r.remove(path) {
			r.report.State = append(r.report.State, r.shown(path))
		}
	}
}

type keyed struct{ entry, name, prefix, suffix string }

// sessionEntries は state の置き場を、セッションの表記ごとにまとめる。2 つめはまとめられなかったもの。
// どの形にも当たらない名前は、知らないファイルとして残す（形を広げると知らないファイルまで消しうる）。
func (r *run) sessionEntries(stateDir string) (map[string][]string, []string) {
	groups := map[string][]string{}
	entries, err := ospath.DiskListdir(stateDir)
	if err != nil {
		return groups, nil
	}
	guard := ospath.Join(stateDir, config.SelfguardDir)
	if ospath.IsLink(guard) {
		r.problem("%s はリンクなので辿らない（バックアップを削除しません）", r.shown(guard))
	} else if backups, err := ospath.DiskListdir(guard); err == nil {
		for _, name := range backups {
			path := ospath.Join(guard, name)
			if pathcase.Fold(name) != pathcase.Fold(config.SelfguardStore) && ospath.IsDir(path) {
				groups[name] = append(groups[name], path)
			}
		}
	}

	var withKey []keyed
	var loose []string
	for _, entry := range entries {
		path := ospath.Join(stateDir, entry)
		if !ospath.IsFile(path) {
			continue
		}
		// 書きかけで残った一時ファイル（`.<名前>.<一意>.part.json`）は先頭の "." を外した名前で当てる。
		// `.once-…` は同じセッションの記録と一緒に、`.nudged-…` などはセッションの位置に `.<一意>.part` まで
		// 入るので別の組になり、それ自身の日付で消える。報告済みと turn の一時ファイルはどの形にも当たらず残る。
		name := fsio.TempOrigin(entry)
		if k, ok := keyOf(entry, name); ok {
			withKey = append(withKey, k)
			continue
		}
		if ownDate.MatchString(name) {
			loose = append(loose, path)
			continue
		}
		for _, pattern := range sessionOnly {
			if m := pattern.FindStringSubmatch(name); m != nil {
				token := m[pattern.SubexpIndex("s")]
				groups[token] = append(groups[token], path)
				break
			}
		}
	}

	known := make([]string, 0, len(groups))
	for token := range groups {
		known = append(known, token)
	}
	// 長い表記から当てる（同じ長さで両方が当たる表記は同じ表記なので、並びの残りは答えを変えない）。
	sort.SliceStable(known, func(i, j int) bool { return len(known[i]) > len(known[j]) })
	for _, k := range withKey {
		middle := k.name[len(k.prefix) : len(k.name)-len(k.suffix)]
		path := ospath.Join(stateDir, k.entry)
		token, found := "", false
		for _, t := range known {
			if strings.HasPrefix(middle, t+"-") {
				token, found = t, true
				break
			}
		}
		if found {
			groups[token] = append(groups[token], path)
		} else {
			loose = append(loose, path)
		}
	}
	return groups, loose
}

func keyOf(entry, name string) (keyed, bool) {
	for _, p := range sessionAndKey {
		if strings.HasPrefix(name, p.prefix) && strings.HasSuffix(name, p.suffix) {
			return keyed{entry, name, p.prefix, p.suffix}, true
		}
	}
	return keyed{}, false
}
