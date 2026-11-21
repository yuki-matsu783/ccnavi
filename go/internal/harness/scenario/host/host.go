package host

import (
	"fmt"
	"regexp"
	"sort"
	"strings"
	"time"

	"ccnavi/internal/harness/hoststub"
	"ccnavi/internal/infra/pathcase"
)

// Kind は setup.host の値。
type Kind string

const (
	Bare         Kind = "bare"
	FakeGitLab   Kind = "fake-gitlab"
	DockerGitLab Kind = "docker-gitlab"
	Real         Kind = "real"
	// Default は setup.host を書かなかったときの相手。
	Default = Real
)

// Kinds は setup.host に書ける値。
var Kinds = []Kind{Bare, FakeGitLab, DockerGitLab, Real}

// LogName は診断ログの出どころ（logs/diag/<名前>.log）。
const LogName = "scenario-runner"

const (
	// remoteRefs は対象の origin の remote-tracking ref の置き場。
	remoteRefs = "refs/remotes/origin"
	// developer は人間役に与えるメンバーの権限（GitLab の Developer）。
	developer = 30
	// deleteWait は Docker の GitLab のプロジェクトが消えるのを待つ回数（1 秒おき）。
	deleteWait = 60
	// credHelper は git が `credential.<URL>.helper` で読む。値は環境変数 GITLAB_TOKEN で、引数には入れない。
	credHelper = `!f() { echo username=root; echo "password=$GITLAB_TOKEN"; }; f`
)

// tokenVar は本物のホストに出る資格情報の変数。claude の環境から外す（偽のトークンだけを渡す）。
var tokenVar = regexp.MustCompile(`(?i)^(GH|GITHUB|GLAB|GITLAB|CCNAVI_PROBE)_.*TOKEN$`)

// Host は用意した相手。state.json にそのまま残るので、トークン（root の PAT）は入れない。
type Host struct {
	Kind         Kind              `json:"kind"`
	Dir          string            `json:"dir"`
	Top          string            `json:"top"`
	RealOrigin   string            `json:"real_origin"`
	RemoteRefs   map[string]string `json:"remote_refs"`
	Bare         string            `json:"bare,omitempty"`
	TargetCommon string            `json:"target_common"`
	Bin          string            `json:"bin"`
	Routes       string            `json:"routes"`
	Log          string            `json:"log"`
	// 以下は fake-gitlab。
	Project   string `json:"project,omitempty"`
	StatePath string `json:"state_path,omitempty"`
	Port      int    `json:"port,omitempty"`
	Token     string `json:"token,omitempty"`
	// 以下は docker-gitlab。
	ProjectID int    `json:"project_id,omitempty"`
	Base      string `json:"base,omitempty"`
	URL       string `json:"url,omitempty"`
	Version   string `json:"version,omitempty"`
	// Deleted は Docker のプロジェクトを消せたこと、Down は置き場を片付けたこと。
	Deleted bool `json:"deleted,omitempty"`
	Down    bool `json:"down,omitempty"`
}

// IsFake は、実物の GitLab を使わない相手か。
func IsFake(k Kind) bool { return k == Bare || k == FakeGitLab }

// UsesStubGit は、origin への通信を git のスタブで向け直す相手（IsFake に Docker の GitLab を足したもの）。
func UsesStubGit(k Kind) bool { return IsFake(k) || k == DockerGitLab }

// SnapshotKinds は step の終わりに相手側の状態を読む対象。
func SnapshotKinds() []Kind { return []Kind{FakeGitLab, DockerGitLab} }

// DefaultState は fake-gitlab の初期状態（setup.host_state が上書きする）。
func DefaultState() map[string]any {
	return map[string]any{"project": "ns/proj", "seq": 100, "issues": []any{}, "mrs": []any{}}
}

var projectPath = regexp.MustCompile(`^[^/\s]+(/[^/\s]+)+$`)

// Validate は setup.host と setup.host_state の形を確かめる。path は誤りの頭に付ける場所（シナリオのファイル）。
func Validate(path string, setup map[string]any) error {
	var kind any = string(Default)
	if v, ok := setup["host"]; ok {
		kind = v
	}
	k, isText := kind.(string)
	known := false
	for _, want := range Kinds {
		known = known || (isText && Kind(k) == want)
	}
	if !known {
		names := make([]string, len(Kinds))
		for i, want := range Kinds {
			names[i] = string(want)
		}
		return fmt.Errorf("%s: setup.host は %s のどれかです: %#v", path, strings.Join(names, " / "), kind)
	}
	if UsesStubGit(Kind(k)) && setup["issue"] != nil {
		return fmt.Errorf("%s: setup.issue と setup.host: %s は併用できません"+
			"（issue は runner が gh / glab で作るので、スタブの PATH では作れない）。"+
			"fake-gitlab で issue が要るときは setup.host_state に書く。"+
			"docker-gitlab で issue が要るときは、操作者が API で作る", path, k)
	}
	state, has := setup["host_state"]
	if !has {
		return nil
	}
	if Kind(k) != FakeGitLab {
		return fmt.Errorf("%s: setup.host_state は setup.host: fake-gitlab のときだけ書けます", path)
	}
	m, ok := state.(map[string]any)
	if !ok {
		return fmt.Errorf("%s: setup.host_state は dict です", path)
	}
	project := DefaultState()["project"]
	if v, ok := m["project"]; ok {
		project = v
	}
	if s, ok := project.(string); !ok || !projectPath.MatchString(s) {
		return fmt.Errorf("%s: setup.host_state.project は <namespace>/<project> の形です", path)
	}
	return nil
}

var nonName = regexp.MustCompile(`[^a-z0-9]+`)

// ProjectName は GitLab の使い捨てのプロジェクト名。大文字小文字で名前を分けないよう、ID は fold した綴りにする。
// 接頭辞 `sc-<ID>-` で、残ったものを一覧して消せる。rand は 4 桁の 16 進（時刻と合わせて重ならない印）。
func ProjectName(scenarioID string, now time.Time, rand string) string {
	ident := strings.Trim(nonName.ReplaceAllString(pathcase.Fold(scenarioID), "-"), "-")
	return fmt.Sprintf("sc-%s-%s-%s", ident, now.Format("20060102150405"), rand)
}

// OriginURL は、対象の origin として git のスタブが答える URL。
func (h *Host) OriginURL() string {
	switch h.Kind {
	case FakeGitLab:
		return fmt.Sprintf("http://127.0.0.1:%d/%s.git", h.Port, h.Project)
	case DockerGitLab:
		return h.Bare
	}
	return hoststub.OriginURL
}

// GitVia は setup.git（runner が実行する git）にも、origin への通信を bare へ向ける -c を付ける引数。
// docker-gitlab は、利用者の credential helper に PAT が渡らないよう、同じ key を空で打ち消してから、
// 環境変数 GITLAB_TOKEN を返す helper を足す（git のスタブと同じ）。
func (h *Host) GitVia() []string {
	via := []string{"-c", fmt.Sprintf("url.%s.insteadOf=%s", h.Bare, h.RealOrigin)}
	if h.Kind == DockerGitLab {
		key := fmt.Sprintf("credential.%s.helper", h.Base)
		via = append(via, "-c", key+"=", "-c", key+"="+credHelper)
	}
	return via
}

// StepEnv は claude に渡す環境（名前から値）。親の環境を継承し、本物のトークン系だけ除いて、スタブを足す。
// token は docker-gitlab のときのエージェント役のトークン（それ以外は host.Token）。
func (h *Host) StepEnv(parent []string, dockerToken string) map[string]string {
	out := map[string]string{}
	for _, kv := range parent {
		name, value, _ := strings.Cut(kv, "=")
		if pathcase.SameName(name, "PATH") {
			name = "PATH" // Windows は Path。綴り違いを残すと PATH が 2 本になる
		}
		if !tokenVar.MatchString(name) {
			out[name] = value
		}
	}
	token := h.Token
	if h.Kind == DockerGitLab {
		token = dockerToken
	}
	stub := hoststub.Stub{BinDir: h.Bin, RoutesPath: h.Routes, Log: h.Log}
	for k, v := range stub.Env(hoststub.EnvOpts{
		Path:         out["PATH"],
		Token:        token,
		OriginURL:    h.OriginURL(),
		TargetCommon: h.TargetCommon,
		Bare:         h.Bare,
		RealOrigin:   h.RealOrigin,
		CredURL:      h.Base,
	}) {
		out[k] = v
	}
	return out
}

// Environ は map の環境を `名前=値` の並びにする（名前順）。
func Environ(env map[string]string) []string {
	out := make([]string, 0, len(env))
	for k, v := range env {
		out = append(out, k+"="+v)
	}
	sort.Strings(out)
	return out
}
