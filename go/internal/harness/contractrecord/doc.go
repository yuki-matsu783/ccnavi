// Package contractrecord は、動いている本物の GitLab に requests.json の列を送り、応答の形を recorded.json に録る（契約テストの録る側。
// 消した tests/manual/gitlab/record_contract.py の写し）。仕様は `.agents/docs/gitlab-contract.md`。手動で回す。自動テストは本物を立てない。
//
//	gitlabmanual record-contract            録って tests/contract/gitlab/recorded.json に置く
//	gitlabmanual record-contract --dry-run  置かずに標準出力へ（理由も出す）
//
// 前提: `gitlabmanual status` で GitLab が動いていて、トークンが有効なこと（この道具は起動も停止もしない）。トークンは
// `gitlabmanual up` が書く tokens.env から読む。同名の環境変数があればそちらを使う。
//
// やること
//  1. 使い捨てのプロジェクト `p551-<乱数>` を作り、contractshape.SceneState と同じ場面を組み、拡張だけの要求のために feature にもう 1 コミットを
//     足す（sha.*・blob.sha を積む）。merge_status が確定し diff_refs が入るまで待つ
//  2. requests.json の列を順に送る（読み取り → 書き込み）。応答の形を正規化する
//  3. 2 回やる（2 回目は別のプロジェクト）。reads の各キーの形が 2 回で一致しなければ、置かない
//  4. 置く前の検査（contractshape.PlacementProblems。期待のステータス・配列が非空・reads のキーが記録にある・機密・イメージの一致）に 1 つでも
//     当たれば、理由を出して置かない。通れば一時ファイルから rename で置く（contractshape.WriteAtomic）
//  5. 作ったプロジェクトを消す（自分のものだけ。途中で失敗しても消す）。CCNAVI_PROBE_KEEP=1 で残す
//
// 出力の JSON は、キーを名前順に、字下げ 1 字で書く（Python の json.dumps(indent=1, sort_keys=True) と同じ。requests も名前順になる）。
package contractrecord
