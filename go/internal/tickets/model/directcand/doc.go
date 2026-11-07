// Package directcand は、Stop で `integrate` を促すための直接作業の「候補」の記録。直接作業のワークツリー
// （`.claude/worktrees/<名前>`）に Write・Edit・MultiEdit・NotebookEdit で書き込んだら、そのツリーをセッションごとの記録に
// 加える。Stop は cwd のツリーに加えて候補も調べるので、cwd がワークスペースルートのままでも、サブエージェントが
// 書いたツリーでも促しが届く。候補は促す対象を絞る材料で、担当の証明ではない。登録では git を呼ばない
// （確かめるのは Stop）。
//
// 置き場は `<state>/direct-<セッション>.json`、形は `{"session", "entries": [{"name", "path", "seen", "checked"}]}`。
// seen は最後に書き込み先として見た時刻、checked は Stop で最後に調べた時刻（0 は未調査。どちらも UNIX 秒）。
// 上限は MaxEntries 件で seen の古いものから捨てる。読むときは session が完全に一致する記録だけを使い、
// 名前が IntegrableName を通らない項目と、path がその名前のワークツリーと一致しない項目は捨てる。
// 保存できなくても何も言わない（登録は補助）。並行して書かれて 1 件落ちることがあるが、補わない。
//
// Python からの対応: direct_candidates をこのパッケージにした。integrable_name → IntegrableName、load → Load、
// touch → Touch、update → Update。Touch の同じ候補の判定は、名前を realpath で解かずに畳んで比べる。
package directcand
