package events

import (
	"io"
	"slices"

	"ccnavi/internal/hook/post"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/builtin"
	"ccnavi/internal/policy/ruleload"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/model/guard"
)

// checker は実行後チェックの持ち物。保護領域も範囲も、実行前チェックと同じ経路で解く。別に書くと、実行前に通った書き込みが
// ターンの終わりに報告されてしまうため。in が nil なら全部のツリー（ターンの区切り）、あればこの呼び出しが触ったツリーを見る。
// raw は呼び手が読んだ承認済みの置き場（nil なら読み直す）。
func checker(stderr io.Writer, conf *config.Settings, root string, record *audit.Record, in *hookio.Input,
	raw *approval.Raw) (*post.Checker, error) {
	watched, err := watchedFor(stderr, conf, root, record, in)
	if err != nil {
		return nil, err
	}
	return checkerOf(stderr, conf, root, watched, raw), nil
}

// checkerOf は見るツリーを決めたあとの持ち物。
func checkerOf(stderr io.Writer, conf *config.Settings, root string, watched []post.Watched, raw *approval.Raw) *post.Checker {
	return &post.Checker{
		Stderr:  stderr,
		State:   conf.State,
		Mine:    []string{conf.State, conf.Log},
		Watched: watched,
		Scope:   scopeGuard(conf, root, raw),
		Places:  post.Places{Tickets: conf.Tickets, Approved: conf.Approved},
		Root:    root,
		Conf:    conf,
	}
}

// watchedFor は実行後に見るツリーと、それぞれに当てるルール（共通レイヤーにそのツリーのレイヤーを足した和）。
// in があればワークスペースルートと、この呼び出しが触ったツリー（パスを持つツールは行き先、ほかは cwd）。横で作業する
// 指定が有効なユーザのチェックアウトは毎回見る（書き換えはそのチェックアウトに起きる）。
//
// ガード自身の保護は、実行前と同じ関数から足す。自己防衛が disable でなければ、名前で書くツール用の組み込み（共通の
// ルールファイルが読めない間も）と、チケット制御が有効なら承認済みの置き場の 1 本を足す。guard-project-home 全体は足さない。
// ccnavi ディレクトリの下の正規の書き込みが報告に並んでしまうため。
func watchedFor(stderr io.Writer, conf *config.Settings, root string, record *audit.Record,
	in *hookio.Input) ([]post.Watched, error) {
	ws := tree.MainTree(root)
	sbs := tree.SideBySideRoots(root, conf.Projects)
	sbsKeys := map[string]bool{}
	for _, t := range sbs {
		sbsKeys[pathcase.Fold(t.Root)] = true
	}
	var trees []tree.Tree
	if in == nil {
		trees = tree.AllTrees(root, conf.Projects)
	} else {
		trees = []tree.Tree{ws}
		where := in.Cwd
		if slices.Contains(config.PathTools(), in.ToolName) {
			where = record.Subject
		}
		if t, ok := tree.TreeOf(root, where, conf.Projects); ok && t.Root != ws.Root {
			trees = append(trees, t)
		}
		have := map[string]bool{}
		for _, t := range trees {
			have[pathcase.Fold(t.Root)] = true
		}
		for _, t := range sbs {
			if !have[pathcase.Fold(t.Root)] {
				trees = append(trees, t)
			}
		}
	}
	coreOn := conf.GuardCoreFiles != string(config.Disable)
	loaded := map[string]*rules.RuleSet{}
	var out []post.Watched
	for _, t := range trees {
		got, ok := loaded[t.Project]
		if !ok {
			// 共通レイヤーはツリーのレイヤーごとに読み直す（レイヤーを足すと集合が書き換わる）。共通レイヤーの苦情は
			// 同じなので最初の 1 回だけ書き、レイヤーの苦情はツリーごとに違うのでそのまま書く。
			said := stderr
			if len(loaded) > 0 {
				said = io.Discard
			}
			rs, source := ruleload.LoadRules(said, conf, record, root)
			if source != builtin.Source {
				ruleload.AddLayers(stderr, rs, ruleload.LayerFor(conf, root, &t), root, record)
			}
			if coreOn {
				selfguard.AddNamedRules(rs, conf.ProjectHome, root)
			}
			if conf.TicketsEnabled() {
				rule, err := guard.ApprovedWriteRule(conf.Approved, root)
				if err != nil {
					return nil, err
				}
				rs.Deny = append(rs.Deny, rule)
			}
			got = rs
			loaded[t.Project] = got
		}
		out = append(out, post.Watched{Tree: t, Rules: got, SideBySide: sbsKeys[pathcase.Fold(t.Root)]})
	}
	return out, nil
}

// scopeGuard は承認済みチケットを実行後の側から当てる持ち物。チケット制御が disable なら nil。範囲の上限は親に
// 固定したコピーから引くので、phases.yml は読まない。
func scopeGuard(conf *config.Settings, root string, raw *approval.Raw) *post.ScopeGuard {
	if !conf.TicketsEnabled() {
		return nil
	}
	copies, _ := approval.Scan(conf, root, false, raw)
	return &post.ScopeGuard{Root: root, Copies: approval.ByID(copies), Projects: conf.Projects,
		Places: post.Places{Tickets: conf.Tickets, Approved: conf.Approved}}
}

// readRaw は承認済みの置き場を 1 度だけ読む。チケット制御が disable なら読まない。
func readRaw(conf *config.Settings, root string) *approval.Raw {
	if !conf.TicketsEnabled() {
		return nil
	}
	return approval.ReadRaw(conf, root)
}
