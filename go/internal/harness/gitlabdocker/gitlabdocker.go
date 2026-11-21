package gitlabdocker

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/harness/gitlabapi"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/records/diaglog"
)

// Image は GitLab CE のイメージ（ダイジェストで固定。タグの 19.4.1 は Docker Hub に無い）。版の正はここ 1 か所で、ほかの文書に版の数字を写さない。
const Image = "gitlab/gitlab-ce@sha256:9b33b45b9f42d176bada85ee5ecb81ddab7e506c435f44cd582206e284b2809c"

const (
	// DefaultPort は公開ポートの既定。
	DefaultPort = 8929
	// MemLimit はコンテナの上限。下限（MemoryMin）は GitLab の要求（4GB）より大きくとる。足りないと OOM で engine ごと落ちる。
	MemLimit  = "6g"
	MemoryMin = 6 * 1024 * 1024 * 1024
	// Container は compose の container_name。DataVolume は compose の project 名（compose.yml の name）と、そのボリューム名。
	Container  = "gitlab"
	DataVolume = "ccnavi-gitlab_gitlab_data"
	// LogName は診断ログの出どころ。
	LogName = "gitlab-manual"
)

const (
	waitSeconds  = 15 * 60
	pollSeconds  = 15
	queryTimeout = 30 * time.Second
	railsTimeout = 90 * time.Second
	tokenTimeout = 300 * time.Second
	// MissingExit は docker などを起こせなかったときの終了コード。timeoutExit は時間切れ（GitLab が重いと docker exec は返らない）。
	missingExit = 127
	timeoutExit = 124
)

// TokenKeys はトークンのファイルに必ず要るキー。TokenOutputKeys は make_gitlab_tokens.rb が出すキー。
var (
	TokenKeys       = []string{gitlabapi.TokenKey, gitlabapi.ReviewerTokenKey}
	TokenOutputKeys = []string{gitlabapi.TokenKey, gitlabapi.ReviewerTokenKey, gitlabapi.ReviewerIDKey, env.ProbeRootID}
)

// Port は公開ポート（CCNAVI_GITLAB_PORT。読めなければ既定）。
func Port(getenv func(string) string) int {
	if n, err := strconv.Atoi(getenv(env.GitLabPort)); err == nil && n > 0 {
		return n
	}
	return DefaultPort
}

// Base は GitLab の根の URL。
func Base(port int) string { return fmt.Sprintf("http://127.0.0.1:%d", port) }

// StateDir は状態のファイル（root.env・tokens.env）の置き場。リポジトリの外（XDG_CACHE_HOME があればその下）。
func StateDir(getenv func(string) string, home string) string {
	cache := getenv(env.XdgCacheHome)
	if cache == "" {
		cache = filepath.Join(home, ".cache")
	}
	return filepath.Join(cache, "ccnavi", "gitlab")
}

// FindRoot は dir から上って、go/go.mod を持つ所（リポジトリの根）を返す。
func FindRoot(dir string) (string, error) {
	for {
		if _, err := os.Stat(filepath.Join(dir, "go", "go.mod")); err == nil {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", errors.New("リポジトリの根（go/go.mod の親）が見つからない。リポジトリの中で打つ")
		}
		dir = parent
	}
}

// Result は外のコマンドの結果。
type Result struct {
	Code           int
	Stdout, Stderr string
}

// RunOpts は Run の入力。Timeout が 0 なら待ち続ける。Stream なら出力を取らず、そのまま端末に出す。
type RunOpts struct {
	Stdin   string
	Env     []string
	Timeout time.Duration
	Stream  bool
}

// RunFunc は外のコマンドを起こす（docker）。時間切れは終了コード 124、起こせなければ 127。
type RunFunc func(args []string, o RunOpts) Result

// Exec は RunFunc の既定。
func Exec(args []string, o RunOpts) Result {
	ctx := context.Background()
	if o.Timeout > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, o.Timeout)
		defer cancel()
	}
	cmd := exec.CommandContext(ctx, args[0], args[1:]...)
	cmd.Env = o.Env
	if o.Stdin != "" {
		cmd.Stdin = strings.NewReader(o.Stdin)
	}
	var out, errb bytes.Buffer
	if o.Stream {
		cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
	} else {
		cmd.Stdout, cmd.Stderr = &out, &errb
	}
	err := cmd.Run()
	res := Result{Stdout: out.String(), Stderr: errb.String()}
	var exit *exec.ExitError
	switch {
	case ctx.Err() != nil:
		return Result{Code: timeoutExit, Stderr: "時間切れ"}
	case errors.As(err, &exit):
		res.Code = exit.ExitCode()
	case err != nil:
		res.Code, res.Stderr = missingExit, err.Error()
	}
	return res
}

// Client は GitLab の API の入口のうち、起動と確認が使う分（gitlabapi.Client が満たす）。
type Client interface {
	User(token string) (int, any)
	Version(token string) (int, any)
}

// Tool は起動・停止・確認の 1 組。欄のうち Root・Home・Getenv 以外は、空なら既定。
type Tool struct {
	// Root はリポジトリの根（compose.yml と make_gitlab_tokens.rb の場所、診断ログの置き場）。
	Root string
	// Home は状態のファイルの既定の置き場の親。Getenv は環境変数の読み方（nil なら os.Getenv）。Environ は docker compose に渡す環境（nil なら os.Environ）。
	Home    string
	Getenv  func(string) string
	Environ []string
	Run     RunFunc
	// NewClient は GitLab への入口（テストは偽の相手を返す）。
	NewClient func(base string) Client
	Out, Err  io.Writer
	Sleep     func(time.Duration)
	// Wait・Poll は起動を待つ上限と間隔（秒）。0 なら既定。
	Wait, Poll int
}

func (t *Tool) getenv(name string) string {
	if t.Getenv != nil {
		return t.Getenv(name)
	}
	return os.Getenv(name)
}

func (t *Tool) run(args []string, o RunOpts) Result {
	if t.Run != nil {
		return t.Run(args, o)
	}
	return Exec(args, o)
}

func (t *Tool) query(args ...string) Result { return t.run(args, RunOpts{Timeout: queryTimeout}) }

func (t *Tool) out() io.Writer {
	if t.Out != nil {
		return t.Out
	}
	return os.Stdout
}

func (t *Tool) errw() io.Writer {
	if t.Err != nil {
		return t.Err
	}
	return os.Stderr
}

func (t *Tool) say(format string, a ...any)  { fmt.Fprintf(t.out(), format+"\n", a...) }
func (t *Tool) warn(format string, a ...any) { fmt.Fprintf(t.errw(), format+"\n", a...) }

func (t *Tool) log() *diaglog.Logger { return diaglog.Get(LogName, t.Root) }

func (t *Tool) sleep(d time.Duration) {
	if t.Sleep != nil {
		t.Sleep(d)
		return
	}
	time.Sleep(d)
}

// Port・Base はこの道具の公開ポートと GitLab の根。
func (t *Tool) Port() int    { return Port(t.getenv) }
func (t *Tool) Base() string { return Base(t.Port()) }

func (t *Tool) stateDir() string { return StateDir(t.getenv, t.Home) }

// PasswordPath・TokensPath は状態のファイル。
func (t *Tool) PasswordPath() string { return filepath.Join(t.stateDir(), "root.env") }
func (t *Tool) TokensPath() string   { return filepath.Join(t.stateDir(), "tokens.env") }

func (t *Tool) client() Client {
	if t.NewClient != nil {
		return t.NewClient(t.Base())
	}
	return gitlabapi.NewClient(t.Base())
}

// WriteEnvFile は 0600 で書く。既にあるファイルも 0600 に直す。
func WriteEnvFile(path string, values map[string]string, order []string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	keys := order
	if keys == nil {
		for k := range values {
			keys = append(keys, k)
		}
		sort.Strings(keys)
	}
	var b strings.Builder
	for _, k := range keys {
		if v, ok := values[k]; ok {
			b.WriteString(k + "=" + v + "\n")
		}
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	defer f.Close()
	if err := os.Chmod(path, 0o600); err != nil {
		return err
	}
	_, err = f.WriteString(b.String())
	return err
}

func (t *Tool) dockerOK() bool {
	return t.query("docker", "version", "--format", "{{.Server.Version}}").Code == 0
}

func (t *Tool) memoryBytes() int64 {
	n, _ := strconv.ParseInt(strings.TrimSpace(t.query("docker", "info", "--format", "{{.MemTotal}}").Stdout), 10, 64)
	return n
}

func (t *Tool) imagePresent() bool { return t.query("docker", "image", "inspect", Image).Code == 0 }
func (t *Tool) volumeExists() bool {
	return t.query("docker", "volume", "inspect", DataVolume).Code == 0
}

// containerState は (状態, ヘルス)。コンテナが無いときは空。
func (t *Tool) containerState() (string, string) {
	done := t.query("docker", "inspect", "--format", "{{.State.Status}} {{.State.Health.Status}}", Container)
	if done.Code != 0 {
		return "", ""
	}
	parts := strings.Fields(done.Stdout)
	switch len(parts) {
	case 0:
		return "", ""
	case 1:
		return parts[0], ""
	}
	return parts[0], parts[1]
}

// RunningImage は動いているコンテナのイメージ（契約の録りが、版の正と合うかを見る）。コンテナが無ければ空。
func (t *Tool) RunningImage() string {
	done := t.query("docker", "inspect", "--format", "{{.Config.Image}}", Container)
	if done.Code != 0 {
		return ""
	}
	return strings.TrimSpace(done.Stdout)
}

func (t *Tool) compose(password string, args ...string) Result {
	environ := t.Environ
	if environ == nil {
		environ = os.Environ()
	}
	if password == "" {
		password = "unused"
	}
	environ = append(append([]string{}, environ...),
		"GITLAB_IMAGE="+Image,
		"GITLAB_PORT="+strconv.Itoa(t.Port()),
		"GITLAB_MEM_LIMIT="+MemLimit,
		"GITLAB_ROOT_PASSWORD="+password,
	)
	file := filepath.Join(t.Root, "tests", "manual", "gitlab", "compose.yml")
	// 起動と停止は時間がかかるので、時間切れで打ち切らない
	return t.run(append([]string{"docker", "compose", "-f", file}, args...), RunOpts{Env: environ})
}

func (t *Tool) railsOK() bool {
	done := t.run([]string{"docker", "exec", Container, "gitlab-rails", "runner", "puts :ok"}, RunOpts{Timeout: railsTimeout})
	return done.Code == 0 && strings.Contains(done.Stdout, "ok")
}

// ensureTokens は、有効なトークンが既にあればそのまま返す。無いときか rotate のときだけ作る（作ると既存は失効する）。
// 401（失効）のときだけ無効とみなす。0・5xx は一時的な不通かもしれず、作り直すと有効な既存を失効させる。
func (t *Tool) ensureTokens(client Client, rotate bool) map[string]string {
	current := gitlabapi.ReadEnvFile(t.TokensPath())
	present := true
	for _, k := range TokenKeys {
		present = present && current[k] != ""
	}
	if !rotate && present {
		valid := true
		for _, k := range TokenKeys {
			if code, _ := client.User(current[k]); code == 401 {
				valid = false
			}
		}
		if valid {
			t.log().Info("トークンを再利用した")
			return current
		}
	}
	script, err := os.ReadFile(filepath.Join(t.Root, "tests", "manual", "gitlab", "make_gitlab_tokens.rb"))
	if err != nil {
		t.warn("次: make_gitlab_tokens.rb を読めない: %v", err)
		return nil
	}
	done := t.run([]string{"docker", "exec", "-i", Container, "gitlab-rails", "runner", "-"}, RunOpts{Stdin: string(script), Timeout: tokenTimeout})
	if done.Code != 0 {
		t.log().Error("トークンを作れなかった", "rc", done.Code)
		t.warn("次: トークンを作れなかった。docker logs gitlab で見る")
		return nil
	}
	found := map[string]string{}
	for _, line := range strings.Split(done.Stdout, "\n") {
		if k, v, ok := strings.Cut(strings.TrimRight(line, "\r"), "="); ok {
			for _, want := range TokenOutputKeys {
				if k == want {
					found[k] = v
				}
			}
		}
	}
	for _, k := range TokenKeys {
		if found[k] == "" {
			t.log().Error("トークンの出力が読めなかった")
			t.warn("次: トークンの出力が読めなかった。make_gitlab_tokens.rb を確かめる")
			return nil
		}
	}
	if err := WriteEnvFile(t.TokensPath(), found, TokenOutputKeys); err != nil {
		t.warn("次: トークンのファイルを書けない: %v", err)
		return nil
	}
	t.log().Info("トークンを作った", "count", len(found))
	return found
}

// waitReady は、健康・rails・/api/v4/version が揃うまで待つ。(トークン, version の応答) を返す。
func (t *Tool) waitReady(client Client, rotate bool) (map[string]string, any) {
	wait, poll := t.Wait, t.Poll
	if wait == 0 {
		wait = waitSeconds
	}
	if poll == 0 {
		poll = pollSeconds
	}
	elapsed, shown := 0, -1
	var tokens map[string]string
	for elapsed < wait {
		_, health := t.containerState()
		if health == "healthy" && t.railsOK() {
			if tokens == nil {
				tokens = t.ensureTokens(client, rotate)
			}
			if tokens != nil {
				if status, body := client.Version(tokens[gitlabapi.TokenKey]); status == 200 {
					t.log().Info("応答した", "elapsed", elapsed)
					return tokens, body
				}
			}
		}
		if elapsed/60 > shown {
			shown = elapsed / 60
			if health == "" {
				health = "-"
			}
			t.say("待っている（%d 秒。健康: %s）", elapsed, health)
		}
		t.sleep(time.Duration(poll) * time.Second)
		elapsed += poll
	}
	return nil, nil
}

// rootPassword はコンテナを作るときのパスワード。コンテナかボリュームが既にあれば作らず、今の値を返す。
func (t *Tool) rootPassword() (string, error) {
	if state, _ := t.containerState(); state != "" || t.volumeExists() {
		return gitlabapi.ReadEnvFile(t.PasswordPath())["GITLAB_ROOT_PASSWORD"], nil
	}
	raw := make([]byte, 24)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	password := hex.EncodeToString(raw)
	if err := WriteEnvFile(t.PasswordPath(), map[string]string{"GITLAB_ROOT_PASSWORD": password}, nil); err != nil {
		return "", err
	}
	t.log().Info("root のパスワードを作った")
	return password, nil
}

// Up は起動して、応答するまで待つ。終了コードを返す。
func (t *Tool) Up(pull, rotate bool) int {
	if !t.dockerOK() {
		t.say("次: Docker が動いていない（docker version が通らない）。Docker を起動してから打ち直す")
		return 2
	}
	if memory := t.memoryBytes(); memory < MemoryMin {
		gb := float64(memory) / (1 << 30)
		t.say("次: Docker に割り当てたメモリが %.1fGB。GitLab CE は 6GB 以上が要る。Docker の設定で増やしてから打ち直す（足りないと engine ごと落ちる）", gb)
		t.log().Warn("メモリが少ない", "gb", fmt.Sprintf("%.1f", gb))
	}
	if !t.imagePresent() {
		if !pull {
			t.say("次: イメージ %s がローカルに無い（約 4GB）。取るなら up --pull を打つ", Image)
			return 2
		}
		t.log().Info("イメージを取る")
		if t.run([]string{"docker", "pull", Image}, RunOpts{Stream: true}).Code != 0 {
			t.say("次: pull が失敗した。ネットワークとプロキシを確かめる")
			return 1
		}
	}
	state, _ := t.containerState()
	password, err := t.rootPassword()
	if err != nil {
		t.warn("次: root のパスワードを書けない: %v", err)
		return 1
	}
	if state != "running" {
		t.log().Info("起動する", "port", t.Port())
		if done := t.compose(password, "up", "-d", "--pull", "never"); done.Code != 0 {
			t.log().Error("起動できなかった", "rc", done.Code)
			t.warn("次: コンテナを起動できなかった\n%s", strings.TrimSpace(done.Stderr))
			return 1
		}
	}
	t.say("起動を待っている。再起動は約 2 分かかる（実測）")
	tokens, body := t.waitReady(t.client(), rotate)
	if tokens == nil {
		t.log().Error("15 分待っても応答しない")
		t.say("次: 15 分待っても応答しない。docker logs gitlab で見る")
		return 1
	}
	version := "?"
	if m := gitlabapi.AsMap(body); m != nil {
		version = gitlabapi.Text(m["version"])
	}
	t.say("起動した。GitLab %s（%s）", version, t.Base())
	t.say("トークン: %s（set -a; . <このパス>; set +a で読む。端末には出さない）", t.TokensPath())
	return 0
}

// Status は状態を出す。トークンの値は出さない。
func (t *Tool) Status() int {
	if !t.dockerOK() {
		t.say("次: Docker が動いていない")
		return 2
	}
	state, health := t.containerState()
	if state == "" {
		state = "無し"
	}
	line := "コンテナ: " + state
	if health != "" {
		line += "（健康: " + health + "）"
	}
	t.say("%s", line)
	tokens := gitlabapi.ReadEnvFile(t.TokensPath())
	t.say("トークン: %s", t.TokensPath())
	if state != "running" {
		return 0
	}
	for _, k := range TokenKeys {
		if tokens[k] == "" {
			t.say("トークン: 無し（up で作る）")
			return 0
		}
	}
	client := t.client()
	status, body := client.Version(tokens[gitlabapi.TokenKey])
	version := fmt.Sprint(body)
	if m := gitlabapi.AsMap(body); m != nil {
		version = gitlabapi.Text(m["version"])
	}
	t.say("API: HTTP %d、GitLab %s", status, version)
	codes := make([]int, len(TokenKeys))
	allOK, revoked := true, false
	for i, k := range TokenKeys {
		codes[i], _ = client.User(tokens[k])
		allOK = allOK && codes[i] == 200
		revoked = revoked || codes[i] == 401
	}
	// up と同じく、401（失効）だけを無効とみなす。0・5xx は一時的な不通かもしれないので「不明」
	verdict := "有効"
	switch {
	case allOK:
	case revoked:
		verdict = "無効（up で作り直す）"
	default:
		verdict = fmt.Sprintf("不明（HTTP %v。時間をおいて再実行）", codes)
	}
	t.say("トークンの有効性: %s", verdict)
	return 0
}

// Down はコンテナを止めて消す。ボリュームは残す。purge ならボリュームと状態のファイルも消す。
func (t *Tool) Down(purge bool) int {
	if !t.dockerOK() {
		t.say("次: Docker が動いていない")
		return 2
	}
	password := gitlabapi.ReadEnvFile(t.PasswordPath())["GITLAB_ROOT_PASSWORD"]
	args := []string{"down"}
	if purge {
		args = append(args, "-v")
	}
	if done := t.compose(password, args...); done.Code != 0 {
		t.log().Error("停止できなかった", "rc", done.Code)
		t.warn("次: 停止できなかった\n%s", strings.TrimSpace(done.Stderr))
		return 1
	}
	if purge {
		for _, p := range []string{t.PasswordPath(), t.TokensPath()} {
			_ = os.Remove(p)
		}
		t.log().Info("止めて、ボリュームと状態を消した")
		t.say("止めて、ボリュームと状態のファイルを消した")
		return 0
	}
	t.log().Info("止めた", "purge", false)
	t.say("止めた（ボリュームは残す。--purge で消す）")
	return 0
}
