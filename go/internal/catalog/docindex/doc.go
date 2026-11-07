// Package docindex は 1 つの git のツリーの md の frontmatter の索引（ディレクトリごとの `ccnavi-index.jsonl`）を組む。
// git が挙げる md を読み、concept_id と mtime が前と同じ行は使い回し、変わったものだけを読み直して、git が無視して
// いる索引だけを書く。ccnavi が書いた形でない ccnavi-index.jsonl は上書きも削除もしない。複数のツリーを合わせて
// 引くのは catalog/docs。
//
// 一時ファイルは git のディレクトリに排他で作る（ワークツリーの `git status` に出さない）。`.git` が別の
// ファイルシステムで置き換えが失敗したときだけ、索引の隣の `.ccnavi-tmp-*/` に作る（git が無視することを先に確かめる）。
//
// Python からの対応: docsearch_index。build → Build（引数は Options）、Built → Built、NotARepository → ErrNotRepository、
// GitFailed → *GitError、front_matter → FrontMatter、_read_head → ReadHead、_ignored → Ignored、_excluded → Excluded、
// 索引の 1 行（dict）→ Row。_add_ignore の ADDED・PRESENT・NOT_ADDED → ignoreResult。
package docindex
