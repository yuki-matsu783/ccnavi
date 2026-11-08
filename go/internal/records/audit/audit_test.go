package audit

import (
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/pyjson"
)

const long = "s3cr3t-value-that-is-long-enough"

var ghp = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"

func writeOne(t *testing.T, r *Record) *pyjson.Object {
	t.Helper()
	path := filepath.Join(t.TempDir(), "logs", "decisions.jsonl")
	if err := NewLog(path).Write(r); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSuffix(string(data), "\n"), "\n")
	if len(lines) != 1 {
		t.Fatalf("lines = %q", lines)
	}
	v, err := pyjson.Loads(lines[0])
	if err != nil {
		t.Fatal(err)
	}
	return v.(*pyjson.Object)
}

// 欄の順と省き方は記録を読む側との約束。空の欄は省き、decision と enforced は空でも入れる。
func TestLineShape(t *testing.T) {
	line := writeOne(t, &Record{
		Mode: "enable", Event: "PreToolUse", Tool: "Bash", Subject: "ls", Decision: Allow,
		Rules: []string{"r1"}, Session: "s1", Agent: "a1", Hinted: true, Hits: []any{1},
	})
	want := []string{"ts", "mode", "event", "tool", "subject", "decision", "enforced", "hinted", "rules", "session", "ms"}
	if got := line.Keys(); strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("keys = %v, want %v", got, want)
	}
	if !regexp.MustCompile(`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{6})?[+-]\d{2}:\d{2}$`).MatchString(line.String("ts")) {
		t.Errorf("ts = %q", line.String("ts"))
	}
	if line.Value("enforced") != false {
		t.Errorf("enforced = %v", line.Value("enforced"))
	}
	if _, ok := line.Value("ms").(float64); !ok {
		t.Errorf("ms = %#v", line.Value("ms"))
	}
}

func TestEmptyPathWritesNothing(t *testing.T) {
	if err := NewLog("").Write(&Record{Decision: Allow}); err != nil {
		t.Fatal(err)
	}
}

// 伏せるのは記録の 1 行だけで、Record そのものは書き換えない。
func TestFieldsAreRedacted(t *testing.T) {
	r := &Record{
		Decision:  Allow,
		Subject:   "curl -H 'Authorization: Bearer " + long + "' x",
		Unwrapped: "GITHUB_TOKEN=" + ghp + " gh api",
		Detail:    "password=hunter2",
	}
	line := writeOne(t, r)
	text, _ := pyjson.Dumps(line, pyjson.Options{})
	for _, secret := range []string{long, ghp, "hunter2"} {
		if strings.Contains(text, secret) {
			t.Errorf("line has %q: %s", secret, text)
		}
	}
	if !strings.Contains(line.String("subject"), "Authorization: Bearer") || !strings.Contains(r.Subject, long) {
		t.Errorf("subject = %q, record = %q", line.String("subject"), r.Subject)
	}
}

// 上限の手前で始まり、上限をまたぐ値も伏せる（切ってから伏せると形が途中で切れて残る）。
func TestRedactRunsBeforeTheLimit(t *testing.T) {
	pad := strings.Repeat("x", recordedSubjectLimit-20)
	line := writeOne(t, &Record{Decision: Allow, Subject: pad + " --token " + long + long})
	if strings.Contains(line.String("subject")[len(pad)+9:], long[:10]) {
		t.Errorf("subject = %q", line.String("subject"))
	}
}

// 伏せる前に上限の数倍で切るので、100 万字でも期限の中で書ける（回帰）。
func TestLongSubjectIsCutFirst(t *testing.T) {
	subject := strings.Repeat("-", 1_000_000)
	start := time.Now()
	line := writeOne(t, &Record{Decision: Allow, Subject: subject})
	if d := time.Since(start); d > 500*time.Millisecond {
		t.Errorf("took %v", d)
	}
	if want := "…(+" + strconv.Itoa(len(subject)-recordedSubjectLimit) + ")"; !strings.HasSuffix(line.String("subject"), want) {
		t.Errorf("subject ends %q", line.String("subject")[recordedSubjectLimit:])
	}
}

func TestLimitedCountsCodePoints(t *testing.T) {
	text := strings.Repeat("あ", recordedSubjectLimit+2)
	if got := limited(text, 0); got != strings.Repeat("あ", recordedSubjectLimit)+"…(+2)" {
		t.Errorf("limited = %q", got[len(got)-10:])
	}
	if got := limited("abc", 0); got != "abc" {
		t.Errorf("limited = %q", got)
	}
}
