package fakegitlab

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
	"unicode/utf8"

	"ccnavi/internal/harness/base"
)

// Token は模擬が受けるトークン（PRIVATE-TOKEN か Authorization: Bearer）。
const Token = "fake-gitlab-token-0001"

// DefaultSkew は時計をずらす既定の量。
const DefaultSkew = 3 * time.Minute

// State は状態ファイルの中身（JSON の木）。
type State = map[string]any

// Config は New の入力。
type Config struct {
	// StatePath は状態ファイル。Initial が nil ならすでにあること。
	StatePath string
	// Initial があれば、StatePath に書いてから起こす。
	Initial State
	// Skew は時計を進める量。0 も負も渡せる（0 は 0 のまま。既定は DefaultSkew を入れる）。
	Skew time.Duration
	// Port は待ち受けるポート。0 なら OS が決める（空きを先に探して bind し直すと、その間に取られる）。
	Port int
	// Echo があれば、受けた要求を `fake-gitlab <METHOD> <path>` の 1 行で書く（手で使う入口が標準エラーを渡す）。
	Echo io.Writer
}

// Server は起動した模擬。状態・ロック・トークン・時計のずれはこのインスタンスが持つ。
type Server struct {
	// URL は `http://127.0.0.1:<port>`。
	URL string
	// Token は GITLAB_TOKEN に置く値（Token と同じ）。
	Token string
	// StatePath は状態ファイル。
	StatePath string

	http *httptest.Server
	skew time.Duration
	echo io.Writer

	mu       sync.Mutex // 状態ファイルの読み書き
	reqMu    sync.Mutex
	requests []string
}

// Start は模擬を起こし、終わりに止める。状態ファイルは使い捨ての置き場に作る。skew を渡さなければ DefaultSkew。
func Start(t testing.TB, state State, skew ...time.Duration) *Server {
	t.Helper()
	cfg := Config{StatePath: filepath.Join(base.TempDir(t), "state.json"), Initial: state, Skew: DefaultSkew}
	if len(skew) > 0 {
		cfg.Skew = skew[0]
	}
	srv, err := New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(srv.Close)
	return srv
}

// New は模擬を起こす。止めるのは Close。
func New(cfg Config) (*Server, error) {
	if cfg.Initial != nil {
		if err := save(cfg.StatePath, cfg.Initial); err != nil {
			return nil, err
		}
	} else if _, err := os.Stat(cfg.StatePath); err != nil {
		return nil, fmt.Errorf("状態ファイルが無い: %s", cfg.StatePath)
	}
	s := &Server{Token: Token, StatePath: cfg.StatePath, skew: cfg.Skew, echo: cfg.Echo}
	s.http = httptest.NewUnstartedServer(http.HandlerFunc(s.serve))
	if cfg.Port != 0 {
		ln, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", cfg.Port))
		if err != nil {
			return nil, err
		}
		s.http.Listener = ln
	}
	s.http.Start()
	s.URL = s.http.URL
	return s, nil
}

// Port は待ち受けているポート。
func (s *Server) Port() int { return s.http.Listener.Addr().(*net.TCPAddr).Port }

// Close は止める。2 回目以降は何もしない。開いたままの接続も切る。
func (s *Server) Close() { s.http.Close() }

// Requests は受けた要求の記録（`METHOD パス`。パスはクエリを含む）。
func (s *Server) Requests() []string {
	s.reqMu.Lock()
	defer s.reqMu.Unlock()
	return append([]string(nil), s.requests...)
}

func (s *Server) record(line string) {
	s.reqMu.Lock()
	s.requests = append(s.requests, line)
	s.reqMu.Unlock()
	if s.echo != nil {
		fmt.Fprintf(s.echo, "fake-gitlab %s\n", line)
	}
}

func (s *Server) now() string {
	return time.Now().UTC().Add(s.skew).Format("2006-01-02T15:04:05.000000Z")
}

func load(path string) (State, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	var state State
	if err := dec.Decode(&state); err != nil {
		return nil, err
	}
	return state, nil
}

func save(path string, state State) error {
	data, err := marshal(state, " ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, data, 0o644)
}

// marshal は日本語や < > & をエスケープしない JSON にする（Python の ensure_ascii=False に合わせる）。
func marshal(v any, indent string) ([]byte, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if indent != "" {
		enc.SetIndent("", indent)
	}
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	return bytes.TrimRight(buf.Bytes(), "\n"), nil
}

func (s *Server) send(w http.ResponseWriter, code int, payload any) {
	raw, err := marshal(payload, "")
	if err != nil {
		code, raw = http.StatusInternalServerError, []byte(`{"message":"500 encode"}`)
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_, _ = w.Write(raw)
}

func (s *Server) authed(r *http.Request) bool {
	return r.Header.Get("PRIVATE-TOKEN") == s.Token || r.Header.Get("Authorization") == "Bearer "+s.Token
}

// body は要求の本文（JSON のオブジェクト）。読めなければ空で、名指しで標準エラーに言う。
func body(r *http.Request) map[string]any {
	raw, _ := io.ReadAll(r.Body)
	out := map[string]any{}
	if len(raw) == 0 {
		return out
	}
	// UTF-8 でない本文はここで気づく。Windows のコンソール経由で日本語を引数に渡すと CP932 になり、気づかないうちに本文が空になる。
	if !utf8.Valid(raw) {
		fmt.Fprintf(os.Stderr, "fake-gitlab: 本文を読めない (UTF-8 でない) raw=%q\n", truncate(raw, 120))
		return out
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		fmt.Fprintf(os.Stderr, "fake-gitlab: 本文を読めない (%v) raw=%q\n", err, truncate(raw, 120))
		return map[string]any{}
	}
	return out
}

func truncate(b []byte, n int) []byte {
	if len(b) > n {
		return b[:n]
	}
	return b
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	s.record(r.Method + " " + r.RequestURI)
	data := body(r)
	name, args, query := route(r.URL.EscapedPath(), r.URL.Query())
	if name == "version" {
		s.send(w, 200, map[string]any{"version": "18.5.4-fake", "revision": "fake"})
		return
	}
	if !s.authed(r) {
		s.send(w, 401, map[string]any{"message": "401 Unauthorized"})
		return
	}
	if name == "" {
		s.send(w, 404, map[string]any{"message": "404 Not Found"})
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	state, err := load(s.StatePath)
	if err != nil {
		s.send(w, 500, map[string]any{"message": "500 state: " + err.Error()})
		return
	}
	proj, _ := url.PathUnescape(args["proj"])
	// /user はプロジェクトに属さない
	if name != "user" && proj != str(state["project"]) {
		s.send(w, 404, map[string]any{"message": "404 Project Not Found: " + proj})
		return
	}
	code, payload, err := (&call{s: s, r: r, data: data, state: state, args: args, query: query}).act(r.Method, name)
	if err != nil {
		s.send(w, 404, map[string]any{"message": "404 Not Found " + err.Error()})
		return
	}
	if err := save(s.StatePath, state); err != nil {
		s.send(w, 500, map[string]any{"message": "500 state: " + err.Error()})
		return
	}
	s.send(w, code, payload)
}

func str(v any) string {
	s, _ := v.(string)
	return s
}

// hostOf は応答に出す URL（web_url）の Host。
func hostOf(r *http.Request) string { return strings.TrimSpace(r.Host) }
