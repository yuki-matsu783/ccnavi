// Package docs は md の frontmatter の索引を、ワークスペースとプロジェクトの置き場の直下の各プロジェクト（それぞれ
// 別の git）で合わせて引く（`ccnavi --docs`）。ツリーごとの索引は catalog/docindex が組み、concept_id はワークスペース
// ルートからの相対（`projects/<名前>/docs/x`）にそろえる。ccnavi ディレクトリの下はチケットとマーカーの置き場なので
// 載せない（使い方の文書の置き場 `.ccnavi/docs/` だけは載せる）。
//
// あわせて、引き方の案内（SessionStart と、失敗が続いたとき）と、frontmatter の無い md を Write で新しく作った
// ときの案内（PostToolUse）を持つ。案内は判定を変えず、何が起きても空を返して開始と実行後チェックを止めない。
//
// Python からの対応: docsearch と docsearch_query をまとめた。run → Run、at_start → AtStart、GUIDE_HEAD → GuideHead、
// fail_streak_note → FailStreakNote、missing_frontmatter → MissingFrontmatter、CODE_MISSING_FRONTMATTER →
// CodeMissingFrontmatter、Query → Query（sort・format は型付きの Sort・Format。ゼロ値は path・table）、
// problems_of → usageProblems（非公開）。collect・places・search・render・dwidth は非公開。
package docs
