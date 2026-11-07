package rules

import (
	"slices"
	"testing"

	"ccnavi/internal/infra/hookio"
)

// 判定が文字列として読む欄は、どれも payload を読むときに型を確かめる欄の一覧に入っている
// （tests/guard/test_internal_error.py から移した）。漏れると {"command": ["git", "push"]} が空として通る。
func TestEverySubjectFieldIsCheckedForItsType(t *testing.T) {
	for _, f := range SubjectFields {
		if !slices.Contains(hookio.ToolInputTextFields, f.Field) {
			t.Errorf("%s の %s を型の検査の一覧が持たない", f.Tool, f.Field)
		}
	}
}
