// Package docindex は、1 つの git のツリーにある md の frontmatter の索引（ディレクトリごとの `ccnavi-index.jsonl`）
// を組む。git が挙げる md を読み、concept_id と mtime が前と同じ行は使い回す。変わったものだけを読み直し、git が
// 無視している索引だけを書く。ccnavi が書いた形でない ccnavi-index.jsonl は上書きも削除もしない。複数のツリーを
// 合わせて引くのは catalog/docs の役目。
//
// 一時ファイルは git のディレクトリに排他で作る。こうすると、ワークツリーの `git status` に出ない。`.git` が別の
// ファイルシステムにあって置き換えが失敗したときだけ、索引の隣の `.ccnavi-tmp-*/` に作る。その前に、git が無視する
// ことを確かめる。
//
// Python からの対応: docsearch_index。build → Build（引数は Options）、Built → Built、NotARepository → ErrNotRepository、
// GitFailed → *GitError、front_matter → FrontMatter、_read_head → ReadHead、_ignored → Ignored、_excluded → Excluded、
// 索引の 1 行（dict）→ Row。_add_ignore の ADDED・PRESENT・NOT_ADDED → ignoreResult。
package docindex
