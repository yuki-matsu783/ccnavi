// Package flow は子チケットのフロー（担当のサブエージェントが作業中に読む手順のグラフ）を読み、確かめ、
// 文に描く。置き場は承認済みの領域の `flows/<子>.yml` に固定で、着手中は実行前チェックが書き換えを止める
// （LockHit・InodeHit）。フローはユーザかエージェントの下書きから作られるデータで形は保証されないので、
// 読む・並べるのどこでも失敗を外に出さず、読めなければ 1 行の理由にする。読むのはふつうのファイルで
// 名前が 1 つのものだけ（ReadBytes）、YAML の別名は読まない。フローの文を文面に混ぜる前には Line・Clean を
// 通し、ccnavi の知らせや案内の区切りになりすませないようにする。
//
// Python からの対応: tickets/flow の graph・render・shape・text をまとめた。graph.read_bytes・load・parse・
// parse_yaml・as_json → ReadBytes・Load・Parse・ParseYAML・AsJSON、flow_file・flow_rel・draft_file・draft_rel・
// tickets_rel・resolve → FlowFile・FlowRel・DraftFile・DraftRel・TicketsRel・Resolve、info → FlowInfo、
// locate・lock_hit（(名前, プロジェクト) の組）→ Locate・LockHit（Location）、hard_linked・inode_hit・
// locked_message → HardLinked・InodeHit・LockedMessage、catalog → LoadCatalog（Catalog。JSON は Object）、
// fingerprint・record_digest・changed_notice・briefing → Fingerprint・RecordDigest・ChangedNotice・Briefing、
// _Loader（別名を拒む読み手）→ yaml.LoadRefusingAliases。render.render → Render（ノードの上限は固定）、
// shape.shape_problem・structure_problems・name_problems・positive_int → ShapeProblem・StructureProblems・
// NameProblems・PositiveInt、text.clean・_line・_text・_FENCE_PHRASES → Clean・Line・Text・FencePhrases。
package flow
