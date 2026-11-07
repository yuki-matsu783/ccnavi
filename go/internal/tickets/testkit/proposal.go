package testkit

import (
	"path/filepath"
	"strings"
	"testing"
)

// proposal のテスト（承認・計画の編集・候補・送る入口）が使うワークスペースと提案の見本。

// ProposalWorkspace は Workspace に、共通レイヤーの phases.yml（ProposalPhases と ProposalExtraType）を置く。
func ProposalWorkspace(t *testing.T, worktrees ...string) string {
	t.Helper()
	root := Workspace(t, worktrees...)
	Put(t, filepath.Join(root, ".ccnavi", "config", "common", "phases.yml"), "version: 1\n"+ProposalPhases+ProposalExtraType)
	return root
}

// ProposalPhases は提案の見本の定義のコピー（phases: の欄）。共通レイヤーの phases.yml にも同じ定義を置く。
const ProposalPhases = `phases:
  research:
    kind: work
    title: 調査
    review: false
    scope:
    - wip/research/*
  design:
    kind: work
    title: 設計
    review: true
    when: 設計の案内
    scope:
    - wip/design/*
  implement:
    kind: work
    title: 実装
    review: true
    when: 実装の案内
    scope:
    - src/*
    - wip/design/*
`

// ProposalExtraType は phases.yml にだけある定義（承認の画面の「この親では使わない定義」）。
const ProposalExtraType = `  release:
    kind: work
    title: リリース
    review: true
    scope: inherit
`

// ProposalParent は親の提案の見本。plan は計画の行で、空なら計画も定義のコピーも書かない。extra は frontmatter に足す行。
func ProposalParent(id string, plan []string, extra ...string) string {
	head := "---\nversion: 1\nticket: " + id + "\nissue: 1\nmr: 1\n"
	if len(plan) > 0 {
		head += ProposalPhases + "plan:\n" + strings.Join(plan, "\n") + "\n"
	}
	for _, e := range extra {
		head += e + "\n"
	}
	return head + "human_review:\n  required: true\n  reason: t\ntitle: 親 " + id + "\nrationale: |\n  理由の 1 行目\n  2 行目\n" +
		"allow:\n  - match: Write|Edit\n    glob: \"src/*\"\n  - match: Write|Edit\n    glob: \"wip/*\"\n" +
		"---\n\n## 目的\n\nx\n\n## 範囲\n\nx\n\n## 対象外\n\nx\n\n## 完了の確かめ方\n\nx\n\n## 計画の理由\n\nx\n"
}

// ProposalChild は子の提案の見本。extra は frontmatter に足す行。
func ProposalChild(id, parent, phase string, globs []string, extra ...string) string {
	text := "---\nversion: 1\nticket: " + id + "\nparent: " + parent + "\nphase: " + phase + "\n" +
		"human_review:\n  required: false\n  reason: t\ntitle: 子 " + id + "\nrationale: r\nallow:\n"
	for _, g := range globs {
		text += "  - match: Write|Edit\n    glob: \"" + g + "\"\n"
	}
	for _, e := range extra {
		text += e + "\n"
	}
	return text + "---\n\n本文\n"
}
