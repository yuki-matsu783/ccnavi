// Package directcand は、Stop で `integrate` を促すための、直接作業の「候補」を記録する。
//
// 直接作業のワークツリー（`.claude/worktrees/<名前>`）に Write・Edit・MultiEdit・NotebookEdit で書き込んだら、
// そのツリーをセッションごとの記録に加える。Stop は cwd のツリーに加えて候補も調べる。そのため、cwd がワークスペース
// ルートのままでも、サブエージェントが書いたツリーでも、促しが届く。候補は促す対象を絞るための材料であり、担当の証明
// ではない。登録では git を呼ばない。確かめるのは Stop の役目である。
//
// 置き場は `<state>/direct-<セッション>.json` で、形は `{"session", "entries": [{"name", "path", "seen", "checked"}]}`
// である。seen は、最後に書き込み先として見た時刻。checked は、Stop で最後に調べた時刻である（0 は未調査。どちらも
// UNIX 秒）。上限は MaxEntries 件で、seen の古いものから捨てる。読むときは、session が完全に一致する記録だけを使う。
// 名前が IntegrableName を通らない項目と、path がその名前のワークツリーと一致しない項目は捨てる。
//
// 保存できなくても何も言わない。登録は補助だからである。並行して書かれると 1 件落ちることがあるが、補わない。
//
// Python からの対応: direct_candidates をこのパッケージにした。integrable_name → IntegrableName、load → Load、
// touch → Touch、update → Update。Touch の同じ候補の判定は、名前を realpath で解かずに畳んで比べる。
package directcand
