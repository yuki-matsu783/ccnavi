// Package launch は、サブエージェントの起動（Agent・Task の PreToolUse）で担当を決める。サブエージェントの hook の cwd は
// 起動した瞬間のメインの cwd に固定されるので、ワークスペースルートから任せた体には、子の範囲とフローの案内が届かない。
// そこで、起動の description（短い作業名）に書かれた担当の子（またはワークツリー）から担当を決める。決まらないときは止める
// 理由（Stop）を、決まったときはプロンプトに書き足す案内（Result.prompt）を返す。応答は書かない。判定との合成は呼び手
// （judge）が行う。
//
// 担当は description だけで決め、プロンプトは案内の差し替えにだけ読む。ccnavi が足した案内にはほかのワークツリー（親の
// フローのパスなど）が入るので、プロンプトで決めると、書き換えたあとの再送で担当が増えて見える。言及に数えるのは、承認済みの
// 子の識別子と、ccnavi のツリーに認識されたワークツリーの名前だけ。言及は担当（ワークツリーの実体）にまとめてから検証し、
// 止める理由の優先順位は Decide の順に従う。作業場所（tool_input.cwd）は書き換えない。実測で、Agent の引数として使われなかったため。
//
// Python からの対応: hook/agentlaunch。decide → Decide、Result（stop・plan）→ Result（Stop・Prompt。Plan.apply →
// Result.Apply）、Stop（code・rule・text・kind）→ Stop（Kind は config.HoldKind）。cwd を書き換える計画（INJECT_CWD・Plan.cwd）
// は、使わない形なので持たない。mentions・unavailable・strip_guide・guidance・Book・Target は私的にした。
package launch
