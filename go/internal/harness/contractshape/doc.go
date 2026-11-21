// Package contractshape は、GitLab の応答の「形」を作り、比べる（tests/contract/shape.py・fakeside.py の写し）。仕様は
// `.agents/docs/gitlab-contract.md`。
//
// 形（Node）は JSON で表せる木で、Chrome 拡張の TS（`test/helpers/shape.ts`）も同じ表（`tests/contract/gitlab/normalize-cases.json`）で読む。
//
//	{"t": ["integer", "null"],       型の和集合（並べ替え済み）
//	 "keys": {名前: node},            object のとき。キーの順は持たない
//	 "opt": [名前, ...],              keys のうち任意（一部の要素にだけある）のもの
//	 "items": node}                   array のとき。要素の形の和集合。空の配列は持たない
//
// 比べる側の部品は shape.go（Normalize・Compare・CompareAll・ApplyAllow・ScanForbidden・PlacementProblems・WriteAtomic・Substitute）、
// 模擬の側の場面と送り手は fakeside.go（SceneState・Run）。本物の側の場面を組むのは手動の道具（cmd/gitlabmanual）で、
// 置くか置かないかの判定（PlacementProblems）と原子的な書き込み（WriteAtomic）はここに置き、Docker なしで試す。
//
// 応答の本文は encoding/json で、数を json.Number に読んだもの（整数と小数を区別するため。LoadData と Run がそう読む）。
package contractshape
