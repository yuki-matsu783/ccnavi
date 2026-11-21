package hoststub

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/exe"
	"ccnavi/internal/infra/env"
)

const (
	// OriginURL は git のスタブが `git remote get-url origin` に答える既定の URL。
	OriginURL = "https://gitlab.com/acme/widgets.git"
	// API・Project は OriginURL の GitLab の API の根と、プロジェクトの URL。
	API     = "https://gitlab.com/api/v4"
	Project = API + "/projects/acme%2Fwidgets"
)

// git のスタブ。`git remote get-url origin` だけホストの形の URL を答え、あとは次の git へ渡す（自分の置き場は PATH から外す）。
//
// FAKE_TARGET_COMMON があれば、答えるのはその共通の .git（`git rev-parse --git-common-dir`）のリポジトリ（そのワークツリーを含む）だけ。
// ほかのリポジトリは本物の git が答える。FAKE_BARE と FAKE_REAL_ORIGIN があれば、本物へ渡すときに
// `-c url.<FAKE_BARE>.insteadOf=<FAKE_REAL_ORIGIN>` を足す。origin が本物のホストでも、fetch・push・ls-remote は FAKE_BARE へ向く
// （.git/config は書き換えない）。
//
// git を呼ぶたびに通るので、自分の置き場は dirname を起こさずに $0 から切り出す（PATH から見つかった $0 は置き場を含む）。
const gitScript = `#!/bin/sh
self=${0%/*}
rest=""
oldifs=$IFS
IFS=:
for p in $PATH; do
  [ "$p" = "$self" ] || rest="$rest:$p"
done
IFS=$oldifs
real_path="${rest#:}"
case " $* " in
*" remote get-url origin "*)
  hit=1
  if [ -n "${FAKE_TARGET_COMMON:-}" ]; then
    dir=.
    prev=""
    for a in "$@"; do
      if [ "$prev" = -C ]; then
        case "$a" in
        /* | [A-Za-z]:*) dir=$a ;;
        *) dir=$dir/$a ;;
        esac
        prev=""
      elif [ "$a" = -C ]; then
        prev=-C
      fi
    done
    got=$(cd "$dir" 2>/dev/null && PATH="$real_path" git rev-parse \
      --path-format=absolute --git-common-dir 2>/dev/null || :)
    got=$(printf '%s' "$got" | tr 'A-Z' 'a-z')
    want=$(printf '%s' "$FAKE_TARGET_COMMON" | tr 'A-Z' 'a-z')
    [ -n "$got" ] && [ "$got" = "$want" ] || hit=0
  fi
  if [ "$hit" = 1 ] && [ -n "${FAKE_ORIGIN_URL:-}" ]; then
    printf '%s\n' "$FAKE_ORIGIN_URL"
    exit 0
  fi
  ;;
esac
PATH="$real_path"
if [ -n "${FAKE_BARE:-}" ] && [ -n "${FAKE_REAL_ORIGIN:-}" ]; then
  if [ -n "${FAKE_CRED_URL:-}" ]; then
    # 利用者の credential helper（GCM・osxkeychain）に PAT が渡らないよう、先に空で打ち消す。
    # helper は環境変数 GITLAB_TOKEN を読む（値を引数に入れない）
    helper='!f() { echo username=root; echo "password=$GITLAB_TOKEN"; }; f'
    GIT_TERMINAL_PROMPT=0 exec git -c "url.${FAKE_BARE}.insteadOf=${FAKE_REAL_ORIGIN}" \
      -c "credential.${FAKE_CRED_URL}.helper=" \
      -c "credential.${FAKE_CRED_URL}.helper=$helper" "$@"
  fi
  exec git -c "url.${FAKE_BARE}.insteadOf=${FAKE_REAL_ORIGIN}" "$@"
fi
exec git "$@"
`

const failScript = "#!/bin/sh\nexit 1\n"

// Route は curl のスタブが 1 つの要求（`<METHOD> <URL>`）に返す答え。JSON では `[状態, 本文]`。
type Route struct {
	Status int
	Body   any
}

// MarshalJSON は routes.json の形（`[状態, 本文]`）にする。
func (r Route) MarshalJSON() ([]byte, error) { return json.Marshal([]any{r.Status, r.Body}) }

// UnmarshalJSON は routes.json の `[状態, 本文]` を読む。
func (r *Route) UnmarshalJSON(data []byte) error {
	var pair [2]json.RawMessage
	if err := json.Unmarshal(data, &pair); err != nil {
		return err
	}
	if err := json.Unmarshal(pair[0], &r.Status); err != nil {
		return err
	}
	return json.Unmarshal(pair[1], &r.Body)
}

// Routes は `<METHOD> <URL>` から答えへの表（routes.json）。
type Routes map[string]Route

// Stub は置いたスタブ。
type Stub struct {
	// BinDir は PATH の先頭に足す置き場。
	BinDir string
	// RoutesPath は curl のスタブが読む routes.json（最初は空）。
	RoutesPath string
	// Log は curl のスタブが書く要求の記録（`METHOD URL` の行。本文があれば続けて `BODY …` の行）。
	Log string
}

// WriteShim は PATH に置く sh のスタブを書く。`#!/bin/sh` と `exec '<teststub>' <サブコマンド…> "$@"` の 2 行（Windows の Git Bash でも動く形）。
func WriteShim(t testing.TB, path, teststub string, sub ...string) {
	t.Helper()
	text := "#!/bin/sh\nexec '" + strings.ReplaceAll(teststub, `\`, "/") + "' " + strings.Join(sub, " ") + " \"$@\"\n"
	writeExecutable(t, path, text)
}

func writeExecutable(t testing.TB, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o755); err != nil {
		t.Fatal(err)
	}
}

// Build はスタブを dir/hostbin に置く。routes.json（空）とログの場所を決める。
// curl が false なら curl のスタブを置かない（fakegitlab の模擬に届かなくなるため）。
func Build(t testing.TB, dir string, curl bool) Stub {
	t.Helper()
	binDir := filepath.Join(dir, "hostbin")
	writeExecutable(t, filepath.Join(binDir, "git"), gitScript)
	writeExecutable(t, filepath.Join(binDir, "gh"), failScript)
	writeExecutable(t, filepath.Join(binDir, "glab"), failScript)
	if curl {
		WriteShim(t, filepath.Join(binDir, "curl"), exe.TeststubPath(t), "curl")
	}
	s := Stub{BinDir: binDir, RoutesPath: filepath.Join(dir, "routes.json"), Log: filepath.Join(dir, "requests.log")}
	s.WriteRoutes(t, Routes{})
	return s
}

// WriteRoutes は routes.json を書き換える。
func (s Stub) WriteRoutes(t testing.TB, routes Routes) {
	t.Helper()
	data, err := json.Marshal(routes)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(s.RoutesPath, data, 0o644); err != nil {
		t.Fatal(err)
	}
}

// EnvOpts は Env の入力。空の項目は効かせない。
type EnvOpts struct {
	// Path は PATH の後ろ側。空ならテストのプロセスの PATH。
	Path string
	// Token は GITLAB_TOKEN。渡さなければホストの API は「確かめられなかった」になる。
	Token string
	// OriginURL は git のスタブが origin として答える URL。空なら OriginURL。
	OriginURL string
	// TargetCommon は git のスタブを効かせる対象の共通の .git。
	TargetCommon string
	// Bare・RealOrigin は、本物の origin への通信を bare へ向ける組。両方あるときだけ効く。
	Bare, RealOrigin string
	// CredURL（`http://127.0.0.1:8929`）は、その URL だけに環境変数 GITLAB_TOKEN を返す credential helper を足す（Docker の GitLab 用）。
	CredURL string
}

// Env はスタブを効かせる環境変数（run.Opts.Env に渡す差分）。PATH の先頭にスタブの置き場を足す。
func (s Stub) Env(o EnvOpts) map[string]string {
	path := o.Path
	if path == "" {
		path = os.Getenv(env.Path)
	}
	origin := o.OriginURL
	if origin == "" {
		origin = OriginURL
	}
	out := map[string]string{
		env.Path:          s.BinDir + string(os.PathListSeparator) + path,
		"FAKE_ORIGIN_URL": origin,
		"FAKE_ROUTES":     s.RoutesPath,
		"FAKE_LOG":        s.Log,
	}
	if o.Token != "" {
		out[env.GitLabToken] = o.Token
	}
	if o.TargetCommon != "" {
		out["FAKE_TARGET_COMMON"] = o.TargetCommon
	}
	if o.Bare != "" && o.RealOrigin != "" {
		out["FAKE_BARE"] = o.Bare
		out["FAKE_REAL_ORIGIN"] = o.RealOrigin
	}
	if o.CredURL != "" {
		out["FAKE_CRED_URL"] = o.CredURL
	}
	return out
}
