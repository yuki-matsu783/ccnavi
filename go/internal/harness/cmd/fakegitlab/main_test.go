package main

import (
	"bytes"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/fakegitlab"
)

func TestMain(m *testing.M) { base.Main(m) }

// lockedBuffer は、起こした側が標準エラーを読みながらサーバが書くための入れ物。
type lockedBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *lockedBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *lockedBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

func writeState(t *testing.T) string {
	t.Helper()
	path := filepath.Join(base.TempDir(t), "state.json")
	if err := os.WriteFile(path, []byte(`{"project": "demo/greeter", "seq": 100, "issues": [], "mrs": []}`), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

var listening = regexp.MustCompile(`fake-gitlab listening on 127\.0\.0\.1:(\d+) state=(.+)\n`)

// 起動（待ち受けを 1 行で知らせる）・1 往復（受けた要求を 1 行ずつ出す）・停止（0 で終わる）。
func TestStartsAnswersOneRequestAndStops(t *testing.T) {
	t.Parallel()
	state := writeState(t)
	stderr, stop, done := &lockedBuffer{}, make(chan os.Signal, 1), make(chan int, 1)
	go func() { done <- run(state, "0", stderr, stop) }()

	var port, line string
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if m := listening.FindStringSubmatch(stderr.String()); m != nil {
			port, line = m[1], m[0]
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if port == "" || port == "0" {
		t.Fatalf("待ち受けの行が出ない: %q", stderr.String())
	}
	if strings.Count(stderr.String(), "\n") != 1 || !strings.Contains(line, "state="+state) {
		t.Errorf("起動時の標準エラーは 1 行のはず: %q", stderr.String())
	}

	req, err := http.NewRequest("GET", fmt.Sprintf("http://127.0.0.1:%s/api/v4/user", port), nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("PRIVATE-TOKEN", fakegitlab.Token)
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	res.Body.Close()
	if res.StatusCode != 200 {
		t.Errorf("HTTP %d、200 のはず", res.StatusCode)
	}
	if !strings.Contains(stderr.String(), "fake-gitlab GET /api/v4/user\n") {
		t.Errorf("受けた要求が標準エラーに出ない: %q", stderr.String())
	}

	stop <- os.Interrupt
	select {
	case rc := <-done:
		if rc != 0 {
			t.Errorf("終了コード %d、0 のはず", rc)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("止まらない")
	}
	if _, err := http.Get(fmt.Sprintf("http://127.0.0.1:%s/api/v4/user", port)); err == nil {
		t.Error("止めたあとも答える")
	}
}

func TestBadArgumentsStopBeforeListening(t *testing.T) {
	t.Parallel()
	state := writeState(t)
	for name, tc := range map[string]struct {
		state, port string
		want        string
	}{
		"ポートが数でない":  {state, "abc", "ポートが数でない"},
		"ポートが範囲外":   {state, "70000", "ポートが数でない"},
		"状態ファイルが無い": {filepath.Join(filepath.Dir(state), "none.json"), "0", "状態ファイルが無い"},
	} {
		var stderr bytes.Buffer
		if rc := run(tc.state, tc.port, &stderr, make(chan os.Signal)); rc == 0 || !strings.Contains(stderr.String(), tc.want) || strings.Contains(stderr.String(), "listening") {
			t.Errorf("%s: rc = %d、%q", name, rc, stderr.String())
		}
	}
}

// 指定したポートが使われていれば、待たずにエラーで終わる。
func TestAnOccupiedPortIsAnError(t *testing.T) {
	t.Parallel()
	first, err := fakegitlab.New(fakegitlab.Config{StatePath: writeState(t), Skew: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	var stderr bytes.Buffer
	if rc := run(writeState(t), fmt.Sprint(first.Port()), &stderr, make(chan os.Signal)); rc != 1 || stderr.Len() == 0 {
		t.Errorf("rc = %d、%q", rc, stderr.String())
	}
}
