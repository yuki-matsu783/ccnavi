package review

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"unicode"

	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
)

// ccnavi-review.sh mr-state の約束。ボードの「MR の状況を確認」が、MR の今の状態を機械で読む。出力は 2 行で、
// 1 行目は版の `mr-state 1`、2 行目は次のどれか（区切りは空白 1 つ）。
//
//   - `open <番号> <yes|no> <URL>`（3 つめは Draft か）
//   - `merged <番号> <URL>`
//   - `closed <番号> <URL>`
//   - `unknown <種類> <理由>`（種類は no-tool / auth / network / api / not-found）
//
// 終了コードは、状態が分かれば 0、unknown は 1、引数の誤りは 2（標準出力は空）。読むだけで、何も書き残さない。
// 応答の読み（4 つの状態・失敗の種類の分け方・URL の組み方）は実行ファイル（`ccnavi host mr-state`）にあり、
// Go の hostreply が表で見る。ここで見るのは sh の仕事（引数・origin・道具・トークンの有無・ホストに聞く道・
// 返事を渡して答えを出すこと・何も残さないこと）。理由には資格情報を入れない。
// ホストへの道具（curl）は PATH の先頭に置いたスタブに差し替える（gh / glab は必ず失敗させ、実際のホストに触れない）。

const mrToken = "s3cr3t-token"

// mrHost はホストの種類ごとに変わるもの（origin・トークンの名前・聞く道・答えの形）。
type mrHost struct {
	name, origin, tokenName, request, url string
	answer                                func(state string, merged, draft bool) string
}

var mrHosts = []mrHost{
	{
		name: "github", origin: "https://github.com/o/r.git", tokenName: "GITHUB_TOKEN",
		request: "/repos/o/r/pulls/9", url: "https://github.com/o/r/pull/9",
		answer: func(state string, merged, draft bool) string {
			body := map[string]any{"number": 9, "state": state, "merged": merged, "draft": draft, "html_url": "https://github.com/o/r/pull/9"}
			if merged {
				body["merged_at"] = "2026-01-01T00:00:00Z"
			} else {
				body["merged_at"] = nil
			}
			return mustJSON(body)
		},
	},
	{
		name: "gitlab", origin: "https://gitlab.com/o/r.git", tokenName: "GITLAB_TOKEN",
		request: "/projects/o%2Fr/merge_requests/9", url: "https://gitlab.com/o/r/-/merge_requests/9",
		answer: func(state string, merged, draft bool) string {
			gitlab := state
			if merged {
				gitlab = "merged"
			} else if state == "open" {
				gitlab = "opened"
			}
			return mustJSON(map[string]any{"iid": 9, "state": gitlab, "draft": draft, "web_url": "https://gitlab.com/o/r/-/merge_requests/9"})
		},
	},
}

func mustJSON(v any) string {
	data, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return string(data)
}

// curl のスタブ。受けた URL を curl.url に記録する。curl.code が 0 でなければ curl.err を標準エラーに出してその終了コードで終わる。
// 0 なら curl.body を出す（成功のときは標準エラーに何も出さない）。
const mrCurl = `#!/bin/sh
d=$(dirname "$0")
for a in "$@"; do url="$a"; done
printf '%s\n' "$url" >>"$d/curl.url"
code=0
[ ! -f "$d/curl.code" ] || code=$(cat "$d/curl.code")
if [ "$code" != 0 ]; then
  [ ! -f "$d/curl.err" ] || cat "$d/curl.err" >&2
  exit "$code"
fi
cat "$d/curl.body"
`

type mrSite struct {
	*site
	host mrHost
	bin  string
}

func newMRSite(t *testing.T, h mrHost) mrSite {
	t.Helper()
	s := newSite(t, h.origin, reviewSh)
	bin := filepath.Join(s.tmp, "bin")
	shkit.WriteExec(t, filepath.Join(bin, "gh"), "#!/bin/sh\nexit 1\n")
	shkit.WriteExec(t, filepath.Join(bin, "glab"), "#!/bin/sh\nexit 1\n")
	shkit.WriteExec(t, filepath.Join(bin, "curl"), mrCurl)
	return mrSite{s, h, bin}
}

// says は curl のスタブの答えを決める。
func (m mrSite) says(body string, code int, stderr string) {
	m.t.Helper()
	shkit.Write(m.t, filepath.Join(m.bin, "curl.body"), body)
	shkit.Write(m.t, filepath.Join(m.bin, "curl.code"), fmt.Sprint(code))
	shkit.Write(m.t, filepath.Join(m.bin, "curl.err"), stderr)
}

// mrOpts は mr-state の起こし方の差し替え。
type mrOpts struct {
	noToken bool
	path    string // 空なら PATH の先頭にスタブを置く
}

func (m mrSite) run(o mrOpts, sub string, args ...string) run.Result {
	m.t.Helper()
	env := map[string]string{"CCNAVI_ENTRY_PATH": exe.ShPath(m.t), "PATH": pathWith(m.bin)}
	if o.path != "" {
		env["PATH"] = o.path
	}
	if !o.noToken {
		env[m.host.tokenName] = mrToken
	}
	return m.sh(reviewSh, run.Opts{Env: env}, append([]string{sub}, args...)...)
}

func (m mrSite) state(o mrOpts, args ...string) run.Result { return m.run(o, "mr-state", args...) }

func (m mrSite) assertKnown(done run.Result, line string) {
	m.t.Helper()
	if done.Code != 0 || done.Stdout != "mr-state 1\n"+line+"\n" {
		m.t.Errorf("code = %d, stdout = %q, stderr = %q; want %q", done.Code, done.Stdout, done.Stderr, line)
	}
}

// assertUnknown は、unknown の行が約束の形（種類・空でない理由・制御文字なし・トークンなし）であること。
func (m mrSite) assertUnknown(done run.Result, kind, reason string) {
	m.t.Helper()
	rows := strings.Split(done.Stdout, "\n")
	if done.Code != 1 || len(rows) != 3 || rows[0] != "mr-state 1" || rows[2] != "" {
		m.t.Fatalf("code = %d, stdout = %q, stderr = %q", done.Code, done.Stdout, done.Stderr)
	}
	why, ok := strings.CutPrefix(rows[1], "unknown "+kind+" ")
	if !ok || why == "" || why != strings.TrimSpace(why) || strings.IndexFunc(why, unicode.IsControl) >= 0 {
		m.t.Errorf("行 = %q（種類 %s・理由は空でなく前後に空白も制御文字も無い）", rows[1], kind)
	}
	if strings.Contains(done.Stdout, mrToken) {
		m.t.Errorf("トークンが出た: %q", done.Stdout)
	}
	if reason != "" && !strings.Contains(why, reason) {
		m.t.Errorf("理由 %q に %q が無い", why, reason)
	}
}

// ホストの種類で sh の動きが変わるところ。

func TestMRStateAsksOnlyThatRequestOfTheHost(t *testing.T) {
	t.Parallel()
	for _, h := range mrHosts {
		t.Run(h.name, func(t *testing.T) {
			t.Parallel()
			m := newMRSite(t, h)
			m.says(h.answer("open", false, true), 0, "")
			m.assertKnown(m.state(mrOpts{}, "9"), "open 9 yes "+h.url)
			asked := strings.Fields(shkit.Read(t, filepath.Join(m.bin, "curl.url")))
			if len(asked) != 1 || !strings.HasSuffix(asked[0], h.request) {
				t.Errorf("聞いた URL = %v, want 1 つで %s で終わる", asked, h.request)
			}
		})
	}
}

func TestMRStateWithoutATokenIsAuth(t *testing.T) {
	t.Parallel()
	for _, h := range mrHosts {
		t.Run(h.name, func(t *testing.T) {
			t.Parallel()
			m := newMRSite(t, h)
			m.says(h.answer("open", false, false), 0, "")
			m.assertUnknown(m.state(mrOpts{noToken: true}, "9"), "auth", h.tokenName)
		})
	}
}

func TestMRStateSelfHostedKeepsItsSchemeAndPort(t *testing.T) {
	t.Parallel()
	m := newMRSite(t, mrHosts[1])
	m.git("remote", "set-url", "origin", "http://localhost:8929/g/p.git")
	m.says(`{"iid": 9, "state": "opened", "draft": false}`, 0, "")
	m.assertKnown(m.state(mrOpts{}, "9"), "open 9 no http://localhost:8929/g/p/-/merge_requests/9")
}

// 以下はホストの種類で変わらない sh の仕事（GitHub の形で見る）。

func TestMRStateNamesAMergedRequest(t *testing.T) {
	t.Parallel()
	// open のほかの状態も、実行ファイルの答えをそのまま出して 0 で終わる
	m := newMRSite(t, mrHosts[0])
	m.says(m.host.answer("closed", true, false), 0, "")
	m.assertKnown(m.state(mrOpts{}, "9"), "merged 9 "+m.host.url)
}

func TestMRStateWritesNothingBehind(t *testing.T) {
	t.Parallel()
	m := newMRSite(t, mrHosts[0])
	m.says(m.host.answer("open", false, false), 0, "")
	m.state(mrOpts{}, "9")
	if entries, _ := os.ReadDir(filepath.Join(m.ws, "logs", "state")); len(entries) > 0 {
		t.Errorf("state に残った: %v", entries)
	}
	if out := m.git("status", "--porcelain", "--", ".", ":!logs", ":!.ccnavi"); out != "" {
		t.Errorf("追跡する物が変わった:\n%s", out)
	}
}

// 失敗の返事は実行ファイルに渡して分けてもらう。理由に本文とトークンを載せない。
func TestMRStateReasonNeverCarriesTheHostBodyOrTheToken(t *testing.T) {
	t.Parallel()
	m := newMRSite(t, mrHosts[0])
	m.says("", 22, "curl: (22) The requested URL returned error: 500\n"+mrToken+" secret\n\x07bell")
	done := m.state(mrOpts{}, "9")
	m.assertUnknown(done, "api", "")
	if strings.Contains(done.Stdout, "secret") || strings.Contains(done.Stderr, mrToken) {
		t.Errorf("stdout = %q, stderr = %q", done.Stdout, done.Stderr)
	}
}

func TestMRStateWithoutATool(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name   string
		drop   []string
		reason string
	}{
		{"ホストへ届く道具が無い", []string{"curl", "gh", "glab"}, ""},
		{"jq が無い", []string{"jq"}, "jq"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			m := newMRSite(t, mrHosts[0])
			path := pathWithout(t, m.tmp, c.drop...)
			if c.reason != "" { // jq だけ無い。スタブの curl は使える
				path = m.bin + string(os.PathListSeparator) + path
			}
			m.assertUnknown(m.state(mrOpts{path: path}, "9"), "no-tool", c.reason)
		})
	}
}

func TestMRStateWithAnUnusableOriginIsNotFound(t *testing.T) {
	t.Parallel()
	for name, url := range map[string]string{"origin が無い": "", "読めない origin": "/some/local/dir"} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			m := newMRSite(t, mrHosts[0])
			m.git("remote", "remove", "origin")
			if url != "" {
				m.git("remote", "add", "origin", url)
			}
			m.assertUnknown(m.state(mrOpts{}, "9"), "not-found", "origin")
		})
	}
}

func TestMRStateRefusesABadArgumentWithNothingOnStdout(t *testing.T) {
	t.Parallel()
	m := newMRSite(t, mrHosts[0])
	m.says(m.host.answer("open", false, false), 0, "")
	for _, args := range [][]string{nil, {"abc"}, {"0"}, {"007"}, {"9; ls"}, {"-1"}, {"9", "10"}, {"1234567890"}, {""}} {
		done := m.state(mrOpts{}, args...)
		if done.Code != 2 || done.Stdout != "" || strings.TrimSpace(done.Stderr) == "" {
			t.Errorf("%q: code = %d, stdout = %q, stderr = %q", args, done.Code, done.Stdout, done.Stderr)
		}
	}
}

// merged 副命令（ccnavi-sync.sh が読む）の出力と終了コードは変えない。
func TestMRStateKeepsTheOutputOfMerged(t *testing.T) {
	t.Parallel()
	m := newMRSite(t, mrHosts[0])
	m.says("not json", 0, "")
	done := m.run(mrOpts{}, "merged", "9")
	if done.Code != 3 || strings.TrimSpace(done.Stdout) != "unknown" {
		t.Errorf("code = %d, stdout = %q, stderr = %q", done.Code, done.Stdout, done.Stderr)
	}
}
