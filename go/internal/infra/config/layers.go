package config

import (
	"path/filepath"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
)

// レイヤーの置き場（ccnavi ディレクトリからの相対、"/" 区切り）。
const (
	LayerConfigDir = "config/project"
	// CommonDir は共通レイヤーとそのミラーの置き場。
	CommonDir = "config/common"
	// TemplatesDir は作業・レビュー計画のテンプレートの置き場。ディレクトリなので LayerKinds には入れない。
	TemplatesDir = "templates"
)

// LayerKind はレイヤーが持てる設定の種類。値は記録と `--explain --json` のキーの表記。
type LayerKind string

const (
	KindRules  LayerKind = "rules"
	KindPhases LayerKind = "phases"
	KindRisk   LayerKind = "risk"
)

// LayerKinds は 3 本の設定。3 本は独立に無くてよい。
var LayerKinds = []LayerKind{KindRules, KindPhases, KindRisk}

// LayerFileNames はレイヤーの設定のファイル名。
var LayerFileNames = map[LayerKind]string{KindRules: "rules.yml", KindPhases: "phases.yml", KindRisk: "risks.yml"}

// レイヤーの名前（記録の `source` と id の前置き）。
const (
	LayerCommon = "common"
	LayerSelf   = "self"
	// layerBuiltin は組み込みのルールの id（`builtin:<名前>`）と取り違えないよう予約する。
	layerBuiltin = "builtin"
)

// ReservedLayerNames はプロジェクトが名前に使えない表記。
var ReservedLayerNames = []string{LayerCommon, LayerSelf, layerBuiltin}

// バックアップの key の前置き。予約名のプロジェクト（`projects/self/`）とミラーを、名前の側と分ける。
const (
	ProjectKeyHome = "projects/"
	MirrorKeyHome  = "mirror/"
)

// Origin はレイヤーがどこから来たか。名前の表記では決められない（`projects/common/` の名前も
// `common`）ので、名前の文字列比較で共通レイヤーと同じ扱いを受けられないよう別に持つ。
type Origin string

const (
	OriginCommon  Origin = "common-layer"
	OriginSelf    Origin = "self-layer"
	OriginProject Origin = "project-layer"
	// OriginMirror はプロジェクトの `.ccnavi/config/common/`。判定には読まれないが守る対象には入る。
	OriginMirror Origin = "mirror-layer"
)

// LayerFile はレイヤー 1 つの設定ファイル（守る対象へ渡す形）。
type LayerFile struct {
	Origin Origin
	Layer  string
	Kind   LayerKind
	Path   string
}

// IsReservedLayerName はその名前がレイヤーの名前に予約してあるか。大文字小文字はどの機械でも区別しない
// （`Self:schema` を `self:schema` と取り違えないため）。予約の判断はここ 1 か所に置く。
func IsReservedLayerName(name string) bool {
	folded := pathcase.Fold(name)
	for _, reserved := range ReservedLayerNames {
		if folded == pathcase.Fold(reserved) {
			return true
		}
	}
	return false
}

func (s *Settings) projectHome() string {
	if s.ProjectHome != "" {
		return s.ProjectHome
	}
	return DefaultProjectHome
}

// ApprovedDir はこのツリーの承認済みチケットの置き場（絶対）。承認はそのツリーの git に乗るのでツリーごとに解く。
func (s *Settings) ApprovedDir(treeRoot string) string {
	approved := s.Approved
	if approved == "" {
		approved = DefaultApproved
	}
	return ospath.Join(treeRoot, filepath.FromSlash(approved))
}

// LayerScriptHome は自身とプロジェクトのレイヤーの `script:` に書ける唯一の場所（"/" 区切り、
// そのレイヤーの git プロジェクトルートから）。
func (s *Settings) LayerScriptHome() string {
	home := strings.Trim(ospath.Slashed(s.projectHome()), "/")
	return home + "/scripts/"
}

// LayerPath はレイヤーの設定ファイルの絶対パス（判定と診断が読む先）。診断の差し替えがあればそれを返す。
// layer が空ならパスの最後の名前で差し替えを引く。守る対象は差し替えを見ない LayerRealPath を使う。
func (s *Settings) LayerPath(homeRoot string, kind LayerKind, layer string) string {
	swaps := map[LayerKind]map[string]string{
		KindRules:  s.ProjectRulesFiles,
		KindPhases: s.ProjectPhasesFiles,
		KindRisk:   s.ProjectRiskFiles,
	}[kind]
	if len(swaps) > 0 {
		if layer == "" && homeRoot != "" {
			layer = ospath.Basename(ospath.Normpath(homeRoot))
		}
		if override := swaps[layer]; override != "" {
			return override
		}
	}
	return s.LayerRealPath(homeRoot, kind)
}

// LayerRealPath はレイヤーの設定ファイルが本来ある場所。
func (s *Settings) LayerRealPath(homeRoot string, kind LayerKind) string {
	return ospath.Join(homeRoot, filepath.FromSlash(s.projectHome()), LayerConfigDir, LayerFileNames[kind])
}

// LayerTemplatesDir はレイヤーのテンプレートの置き場（絶対）。
func (s *Settings) LayerTemplatesDir(homeRoot string) string {
	return ospath.Join(homeRoot, filepath.FromSlash(s.projectHome()), LayerConfigDir, TemplatesDir)
}

// CommonTemplatesDir は共通レイヤー（とミラー）のテンプレートの置き場（絶対）。
func (s *Settings) CommonTemplatesDir(root string) string {
	return ospath.Join(root, filepath.FromSlash(s.projectHome()), CommonDir, TemplatesDir)
}

// MirrorRealPath はプロジェクトの共通レイヤーのミラーの設定ファイルの場所。
func (s *Settings) MirrorRealPath(homeRoot string, kind LayerKind) string {
	return ospath.Join(homeRoot, filepath.FromSlash(s.projectHome()), CommonDir, LayerFileNames[kind])
}
