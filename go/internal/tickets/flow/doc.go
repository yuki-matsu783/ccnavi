// Package flow は子チケットのフロー（担当のサブエージェントが作業中に読む手順のグラフ）を読み、確かめ、文に描く。
// 置き場は承認済みの領域の `flows/<子>.yml` に固定される。着手中は、実行前チェックが書き換えを止める
// （LockHit・InodeHit）。
//
// フローはユーザかエージェントの下書きから作られるデータで、形は保証されない。そのため、読む・並べるのどの段階でも
// 失敗を外に出さず、読めなければ 1 行の理由にする。読むのは通常のファイルで、名前が 1 つのものだけ（ReadBytes）。
// YAML の別名は読まない。フローの文を文面に混ぜる前には Line・Clean を通す。これは、フローの文が ccnavi の知らせや
// 案内の区切りになりすますのを防ぐためである。
//
// Python からの対応: tickets/flow の graph・render・shape・text をまとめた。graph.read_bytes・load・parse・
// parse_yaml・as_json → ReadBytes・Load・Parse・ParseYAML・AsJSON、flow_file・flow_rel・draft_file・draft_rel・
// tickets_rel・resolve → FlowFile・FlowRel・DraftFile・DraftRel・TicketsRel・Resolve、info → FlowInfo、
// locate・lock_hit（(名前, プロジェクト) の組）→ Locate・LockHit（Location）、hard_linked・inode_hit・
// locked_message → HardLinked・InodeHit・LockedMessage、catalog → LoadCatalog（Catalog。JSON は Object）、
// fingerprint・record_digest・changed_notice・briefing → fingerprint・RecordDigest・ChangedNotice・Briefing、
// _Loader（別名を拒む読み手）→ yaml.LoadRefusingAliases。render.render → Render（ノードの上限は固定）、
// shape.shape_problem・structure_problems・name_problems → shapeProblem・StructureProblems・NameProblems、
// text.clean・_line・_FENCE_PHRASES → Clean・Line・fencePhrases。
package flow
