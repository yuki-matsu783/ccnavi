// Package docs は、md の frontmatter の索引を引く（`ccnavi --docs`）。対象は、ワークスペースと、プロジェクトの置き場の
// 直下の各プロジェクト（それぞれ別の git）の md で、両方の索引を合わせて引く。ツリーごとの索引は catalog/docindex が
// 組み、concept_id はワークスペースルートからの相対（`projects/<名前>/docs/x`）にそろえる。ccnavi ディレクトリの下は
// チケットとマーカーの置き場なので載せない。ただし使い方の文書の置き場 `.ccnavi/docs/` は載せる。
//
// あわせて、引き方の案内（SessionStart と、失敗が続いたとき）と、frontmatter のない md を Write で新しく作ったときの
// 案内（PostToolUse）を返す。案内は判定を変えない。何が起きても空を返し、開始と実行後のチェックを止めない。
//
// Python からの対応: docsearch と docsearch_query をまとめた。run → Run、at_start → AtStart、GUIDE_HEAD → GuideHead、
// fail_streak_note → FailStreakNote、missing_frontmatter → MissingFrontmatter、CODE_MISSING_FRONTMATTER →
// codeMissingFrontmatter、Query → Query（sort・format は型付きの Sort・Format。ゼロ値は path・table）、
// problems_of → usageProblems（非公開）。collect・places・search・render・dwidth は非公開。
package docs
