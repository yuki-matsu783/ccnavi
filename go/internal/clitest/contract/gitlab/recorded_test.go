package gitlab

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"testing"

	cs "ccnavi/internal/harness/contractshape"
)

// loadRecorded は本物から録った recorded.json。録るのは手動なので、無ければ飛ばす（gitlab-contract.md）。
func loadRecorded(t *testing.T) cs.Document {
	t.Helper()
	if _, err := os.Stat(filepath.Join(cs.DataDir(), "recorded.json")); err != nil {
		t.Skip("recorded.json が無い（本物の GitLab で録るのは手動。gitlab-contract.md）")
	}
	return load[cs.Document](t, "recorded.json")
}

func TestRecordedHeaderSaysItWasRecordedFromGitLab(t *testing.T) {
	t.Parallel()
	doc := loadRecorded(t)
	if doc.Version != 1 || doc.Source != "gitlab" {
		t.Errorf("version = %d, source = %q", doc.Version, doc.Source)
	}
	if doc.GitlabImage == nil || !strings.HasPrefix(*doc.GitlabImage, "gitlab/gitlab-ce@sha256:") {
		t.Errorf("gitlab_image = %v", doc.GitlabImage)
	}
	if doc.GitlabVersion == nil || !regexp.MustCompile(`^\d+\.\d+\.\d+`).MatchString(*doc.GitlabVersion) {
		t.Errorf("gitlab_version = %v", doc.GitlabVersion)
	}
	if doc.RecordedAt == nil || !regexp.MustCompile(`^\d{4}-\d\d-\d\d$`).MatchString(*doc.RecordedAt) {
		t.Errorf("recorded_at = %v", doc.RecordedAt)
	}
	allow := load[struct {
		NotRecorded []struct {
			What string `json:"what"`
		} `json:"not_recorded"`
	}](t, "allow.json")
	var wanted []string
	for _, n := range allow.NotRecorded {
		wanted = append(wanted, n.What)
	}
	if !slices.Equal(wanted, doc.NotRecorded) || len(wanted) == 0 {
		t.Errorf("not_recorded = %v, want %v", doc.NotRecorded, wanted)
	}
}

func TestEveryRequestIsRecordedWithOnlyStatusAndShapeAndTheExpectedStatus(t *testing.T) {
	t.Parallel()
	doc := loadRecorded(t)
	all := loadAll(t)
	var names []string
	for _, r := range all {
		names = append(names, r.Name)
		got, ok := doc.Requests[r.Name]
		if !ok {
			continue
		}
		if got.Status != r.ExpectStatus {
			t.Errorf("%s: 記録のステータス %d, 期待 %d", r.Name, got.Status, r.ExpectStatus)
		}
	}
	var recorded []string
	for n := range doc.Requests {
		recorded = append(recorded, n)
	}
	slices.Sort(names)
	slices.Sort(recorded)
	if !slices.Equal(names, recorded) {
		t.Errorf("記録の要求 = %v, want %v", recorded, names)
	}
	// 記録には status と shape だけを置く（本文の値を持ち込まない）。
	data, err := os.ReadFile(filepath.Join(cs.DataDir(), "recorded.json"))
	if err != nil {
		t.Fatal(err)
	}
	var raw struct {
		Requests map[string]map[string]json.RawMessage `json:"requests"`
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatal(err)
	}
	for name, entry := range raw.Requests {
		var keys []string
		for k := range entry {
			keys = append(keys, k)
		}
		slices.Sort(keys)
		if !slices.Equal(keys, []string{"shape", "status"}) {
			t.Errorf("%s: 記録の欄 = %v", name, keys)
		}
	}
}

func TestRecordedReadKeysExistAndNonEmptyArraysHaveElements(t *testing.T) {
	t.Parallel()
	doc := loadRecorded(t)
	if problems := cs.ReadsProblems(loadAll(t), doc.Requests); len(problems) != 0 {
		t.Errorf("reads のキーが記録に無い: %v", problems)
	}
	for _, name := range cs.NonEmpty {
		if doc.Requests[name].Shape == nil || doc.Requests[name].Shape.Items == nil {
			t.Errorf("%s: 空にできない配列の要素の形が無い", name)
		}
	}
}

// 模擬が本物と合っていることを示すテスト。本物の記録のうち、模擬が答える（sh の）要求だけを reads の範囲で比べる。
// 拡張だけの要求は TS 側（pnpm test）で MockGitLab と比べる。
func TestRecordedAgreesWithTheFakeExceptForTheAllowList(t *testing.T) {
	t.Parallel()
	doc := loadRecorded(t)
	fake := loadFake(t)
	want := map[string]cs.Entry{}
	for _, r := range fake {
		want[r.Name] = doc.Requests[r.Name]
	}
	found := cs.CompareAll(want, run(t, fake, nil), cs.ReadsOf(fake))
	allow := load[struct {
		Entries []cs.AllowEntry `json:"entries"`
	}](t, "allow.json")
	left, problems := cs.ApplyAllow(found, allow.Entries)
	if len(problems) != 0 || len(left) != 0 {
		t.Errorf("許可リストの問題 = %v, 残った違い = %v", problems, left)
	}
}

func TestNoForbiddenStringInTheRecording(t *testing.T) {
	t.Parallel()
	loadRecorded(t)
	data, err := os.ReadFile(filepath.Join(cs.DataDir(), "recorded.json"))
	if err != nil {
		t.Fatal(err)
	}
	if hits := cs.ScanForbidden(string(data), load[[]cs.Forbidden](t, "forbidden.json")); len(hits) != 0 {
		t.Errorf("禁止文字列に当たった: %v", hits)
	}
}
