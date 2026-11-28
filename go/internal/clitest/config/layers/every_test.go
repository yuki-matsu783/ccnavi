package layers

import (
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/configws"
	"ccnavi/internal/harness/run"
)

// ルールの `every`（渡す回の刻み）の受入テスト。
//
// `every: N` が刻むのは「渡す回」で、ヒットした回数が N の倍数になった回だけが渡す回になる。`every` が無ければヒットするたびが渡す回。
//
//	| 欄                      | いつ渡るか           | `every: 5` のとき |
//	| `additionalContext`     | 渡す回のたび         | 5・10・15…回目    |
//	| `additionalContextOnce` | 渡す回の最初の 1 回  | 5 回目だけ        |
//
// 数えの置き場は `additionalContextOnce` の記録と同じで、文脈の分け方も同じ（セッションと、サブエージェントならその 1 回の起動）。
// 刻みと数えの置き場の細部は go/internal/policy/ctxfile の単体テスト（TestEveryAndOnce）が見る。ここは実行ファイルを通した答えだけを見る。

const (
	everyText = "ここまでに 5 件の編集があった。ルールと突き合わせること。"
	onceText  = "突き合わせの観点は docs/30_adr/ を見る。"
)

// srcRule は狭い allow。広い allow の warn（既存の検査）に引っかからない形で書く。
func srcRule(extra ...any) configws.Obj {
	return configws.With(configws.Obj{"id": "src", "match": "Write", "glob": "*/src/*"}, extra...)
}

// hitter はルールを共通設定の既定の場所に置いた使い捨てのルート。`--rules` では渡さない（診断でだけ有効で、hook の判定には届かない）。
type hitter struct {
	t    *testing.T
	root string
}

func newHitter(t *testing.T, allow configws.Obj) *hitter {
	t.Helper()
	h := &hitter{t: t, root: base.TempDir(t)}
	h.setRules(allow)
	return h
}

func (h *hitter) setRules(allow configws.Obj) {
	h.t.Helper()
	body := configws.Obj{
		"version": 1,
		"deny":    []configws.Obj{{"id": "push", "match": "Bash", "glob": "*git push*", "message": "push はユーザが行う"}},
		"allow":   []configws.Obj{allow},
	}
	configws.Write(h.t, base.CommonPath(h.root, "rules"), configws.Body(body))
}

// hit はルールに 1 回適用して、モデルへ渡った文。渡らなければ空。
func (h *hitter) hit(session, agent string) string {
	h.t.Helper()
	payload := configws.Body(configws.Obj{
		"hook_event_name": "PreToolUse", "tool_name": "Write", "session_id": session, "agent_id": agent, "cwd": h.root,
		"tool_input": configws.Obj{"file_path": filepath.Join(h.root, "src", "a.py")},
	})
	r := run.Ccnavi(h.t, []string{"--root", h.root, "--mode", "enable"}, run.Opts{Input: payload, Dir: base.Root()})
	if r.Code != 0 {
		h.t.Fatalf("code = %d\n%s", r.Code, r.Stderr)
	}
	if strings.TrimSpace(r.Stdout) == "" {
		return ""
	}
	return hookContext(h.t, r.Stdout)
}

func (h *hitter) hits(times int, session, agent string) []string {
	h.t.Helper()
	var out []string
	for range times {
		out = append(out, h.hit(session, agent))
	}
	return out
}

func blanks(n int) []string { return make([]string, n) }

func TestEveryCountsTheDeliveringHits(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name  string
		extra []any
		want  []string
	}{
		{"once comes on the first delivering hit and not again",
			[]any{"additionalContext", everyText, "additionalContextOnce", onceText, "every", 5},
			slices.Concat(blanks(4), []string{everyText + "\n\n" + onceText}, blanks(4), []string{everyText})},
		{"once alone speaks once at the nth",
			[]any{"additionalContextOnce", onceText, "every", 5},
			slices.Concat(blanks(4), []string{onceText}, blanks(5))},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			h := newHitter(t, srcRule(tc.extra...))
			if got := h.hits(10, "s1", ""); !slices.Equal(got, tc.want) {
				t.Errorf("got %q\nwant %q", got, tc.want)
			}
		})
	}
}

func TestEveryOneIsTheSameAsNoEvery(t *testing.T) {
	t.Parallel()
	// 2 本を同じ場所に置くので、先に片方を回してから置き換える。
	h := newHitter(t, srcRule("additionalContext", everyText, "additionalContextOnce", onceText, "every", 1))
	one := h.hits(3, "one", "")
	h.setRules(srcRule("additionalContext", everyText, "additionalContextOnce", onceText))
	plain := h.hits(3, "plain", "")
	want := []string{everyText + "\n\n" + onceText, everyText, everyText}
	if !slices.Equal(one, want) || !slices.Equal(plain, want) {
		t.Errorf("every:1 = %q, no every = %q, want %q", one, plain, want)
	}
}

func TestContextFileBodiesFollowTheFieldTheyBelongTo(t *testing.T) {
	t.Parallel()
	h := newHitter(t, srcRule())
	configws.Write(t, filepath.Join(h.root, "docs", "every.md"), "渡す回のたびの本文")
	configws.Write(t, filepath.Join(h.root, "docs", "once.md"), "最初の渡す回だけの本文")
	h.setRules(srcRule("additionalContext", everyText, "additionalContextFile", "docs/every.md",
		"additionalContextOnce", onceText, "additionalContextOnceFile", "docs/once.md", "every", 5))
	got := h.hits(10, "s1", "")
	if want := everyText + "\n\n渡す回のたびの本文\n\n" + onceText + "\n\n最初の渡す回だけの本文"; got[4] != want {
		t.Errorf("5 回目 = %q, want %q", got[4], want)
	}
	if want := everyText + "\n\n渡す回のたびの本文"; got[9] != want {
		t.Errorf("10 回目 = %q, want %q", got[9], want)
	}
	for i, g := range got {
		if i != 4 && i != 9 && g != "" {
			t.Errorf("%d 回目に渡った: %q", i+1, g)
		}
	}
}

func TestTheCountIsSplitBySessionAndAgent(t *testing.T) {
	t.Parallel()
	h := newHitter(t, srcRule("additionalContextOnce", onceText, "every", 3))
	for _, step := range []struct {
		session, agent string
		times          int
		want           []string
	}{
		{"s1", "", 3, []string{"", "", onceText}},
		// 別のセッションは 0 から数える。
		{"s2", "", 2, []string{"", ""}},
		// 同じセッションのサブエージェントも自分の数えを持つ。
		{"s1", "a1", 3, []string{"", "", onceText}},
		// 子の 3 回は親の数えを進めていない（親の 4 回目は渡す回ではない）。
		{"s1", "", 1, []string{""}},
	} {
		if got := h.hits(step.times, step.session, step.agent); !slices.Equal(got, step.want) {
			t.Errorf("%s/%s: got %q, want %q", step.session, step.agent, got, step.want)
		}
	}
}
