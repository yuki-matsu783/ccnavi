package life

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/harness/ticketkit"
)

// SessionStart で、終わっていない integrate の記録を知らせる。記録（取り込み先のツリーの .git/ccnavi-integrate/<名前>/marker）を、
// モデルの文脈と systemMessage の両方に出す。記録が無ければ何も足さない。記録ごとの文は本体の tickets/notice の pending_test.go が見る。

const pendingHead = "終わっていない integrate の記録があります"

func putIntegrateMarker(tr *ticketkit.Tree, name string, fields [][2]string) {
	tr.T.Helper()
	var text strings.Builder
	for _, f := range fields {
		text.WriteString(f[0] + " " + f[1] + "\n")
	}
	ticketkit.Write(tr.T, filepath.Join(tr.Root, ".git", "ccnavi-integrate", name, "marker"), text.String())
}

func sessionStartAt(tr *ticketkit.Tree, extra map[string]any) string {
	tr.T.Helper()
	payload := map[string]any{"hook_event_name": "SessionStart", "source": "startup", "session_id": "ss", "cwd": tr.Root}
	for k, v := range extra {
		payload[k] = v
	}
	return ticketkit.OK(tr.T, rawCall(tr, payload)).Stdout
}

func TestNoRecordSaysNothing(t *testing.T) {
	t.Parallel()
	ticketkit.Lacks(t, sessionStartAt(ticketkit.New(t), nil), pendingHead)
}

func TestSessionStartGivesBothContextAndSystemMessage(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	head := strings.TrimSpace(tr.Git(tr.Root, "rev-parse", "HEAD"))
	putIntegrateMarker(tr, "w7", [][2]string{{"state", "conflict"}, {"branch", "w7"}, {"branch_sha", head}, {"base", head}})
	var out struct {
		Hook struct {
			AdditionalContext string `json:"additionalContext"`
		} `json:"hookSpecificOutput"`
		SystemMessage string `json:"systemMessage"`
	}
	jsonOf(t, sessionStartAt(tr, nil), &out)
	ticketkit.Has(t, out.Hook.AdditionalContext, pendingHead)
	ticketkit.Has(t, out.SystemMessage, pendingHead)
}

func objectFiles(t *testing.T, tr *ticketkit.Tree) []string {
	t.Helper()
	var found []string
	err := filepath.Walk(filepath.Join(tr.Root, ".git", "objects"), func(path string, info os.FileInfo, err error) error {
		if err == nil && !info.IsDir() {
			found = append(found, path)
		}
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	slices.Sort(found)
	return found
}

func TestThePendingNoticeDoesNotWriteToTheObjectDatabase(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	head := strings.TrimSpace(tr.Git(tr.Root, "rev-parse", "HEAD"))
	tree := strings.TrimSpace(tr.Git(tr.Root, "rev-parse", "HEAD^{tree}"))
	putIntegrateMarker(tr, "w10", [][2]string{{"state", "staged"}, {"branch", "w10"}, {"branch_sha", head}, {"base", head}, {"tree", tree}})
	before := objectFiles(t, tr)
	ticketkit.Has(t, sessionStartAt(tr, nil), pendingHead)
	if after := objectFiles(t, tr); !slices.Equal(before, after) {
		t.Errorf("オブジェクトが増えた: %v → %v", before, after)
	}
}

func TestASubagentIsNotToldAboutPendingIntegrates(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	head := strings.TrimSpace(tr.Git(tr.Root, "rev-parse", "HEAD"))
	putIntegrateMarker(tr, "w8", [][2]string{{"state", "conflict"}, {"branch", "w8"}, {"branch_sha", head}, {"base", head}})
	ticketkit.Lacks(t, sessionStartAt(tr, map[string]any{"agent_id": "a1"}), pendingHead)
}
