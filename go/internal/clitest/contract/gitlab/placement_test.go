package gitlab

import (
	"slices"
	"strings"
	"testing"

	cs "ccnavi/internal/harness/contractshape"
)

const image = "gitlab/gitlab-ce@sha256:test"

// placement は録った結果を置くか置かないかを、本物の代わりに模擬の応答で試す道具（Docker は使わない）。
type placement struct {
	requests []cs.Request
	answers  map[string]cs.Answer
	document cs.Document
}

func newPlacement(t *testing.T) placement {
	t.Helper()
	requests := loadFake(t)
	bodies := map[string]any{}
	shapes := run(t, requests, func(name string, body any) any { bodies[name] = body; return body })
	answers := map[string]cs.Answer{}
	for name, entry := range shapes {
		answers[name] = cs.Answer{Status: entry.Status, Body: bodies[name]}
	}
	v, at := "0.0.0", "2026-01-01"
	img := image
	return placement{requests, answers, cs.Document{
		Version: 1, Source: "gitlab", GitlabImage: &img, GitlabVersion: &v, RecordedAt: &at, NotRecorded: []string{}, Requests: shapes,
	}}
}

func (p placement) problems(t *testing.T, runningImage string) []string {
	t.Helper()
	return cs.PlacementProblems(p.requests, p.answers, p.document, runningImage, load[[]cs.Forbidden](t, "forbidden.json"))
}

func TestAGoodRecordingIsPlaced(t *testing.T) {
	t.Parallel()
	p := newPlacement(t)
	if got := p.problems(t, image); len(got) != 0 {
		t.Errorf("置けない: %v", got)
	}
	// 空にできない配列は、場面が空でない配列を返している。
	for _, name := range cs.NonEmpty {
		if list, _ := p.answers[name].Body.([]any); len(list) == 0 {
			t.Errorf("%s: 場面の応答が空", name)
		}
	}
}

func TestAStatusThatDiffersFromTheExpectationIsNotPlaced(t *testing.T) {
	t.Parallel()
	for name, wrong := range map[string]int{"error_401": 200, "error_404": 200, "mr_get": 404, "mr_create": 200} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			p := newPlacement(t)
			p.answers[name] = cs.Answer{Status: wrong, Body: p.answers[name].Body}
			got := p.problems(t, image)
			if len(got) != 1 || !strings.Contains(got[0], name) {
				t.Errorf("got %v", got)
			}
		})
	}
}

func TestAnEmptyArrayIsNotPlaced(t *testing.T) {
	t.Parallel()
	for _, name := range cs.NonEmpty {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			p := newPlacement(t)
			p.answers[name] = cs.Answer{Status: 200, Body: []any{}}
			got := p.problems(t, image)
			if !slices.ContainsFunc(got, func(s string) bool { return strings.Contains(s, name) && strings.Contains(s, "空") }) {
				t.Errorf("got %v", got)
			}
		})
	}
}

func TestAForbiddenStringIsNotPlaced(t *testing.T) {
	t.Parallel()
	p := newPlacement(t)
	p.document.Requests["user"].Shape.Keys["root/p551-ab12cd34"] = &cs.Node{T: []string{"string"}}
	got := p.problems(t, image)
	if !slices.ContainsFunc(got, func(s string) bool { return strings.Contains(s, "禁止文字列") && strings.Contains(s, "p551-") }) {
		t.Errorf("got %v", got)
	}
}

func TestAnotherImageIsNotPlaced(t *testing.T) {
	t.Parallel()
	p := newPlacement(t)
	for _, running := range []string{"gitlab/gitlab-ce@sha256:other", ""} {
		got := p.problems(t, running)
		if len(got) != 1 || !strings.Contains(got[0], "イメージ") {
			t.Errorf("running = %q: got %v", running, got)
		}
	}
}

func TestAReadKeyMissingFromTheRecordingIsNotPlaced(t *testing.T) {
	t.Parallel()
	p := newPlacement(t)
	delete(p.document.Requests["mr_get"].Shape.Keys, "title")
	if got, want := p.problems(t, image), []string{"mr_get: reads の title が記録に無い"}; !slices.Equal(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
}
