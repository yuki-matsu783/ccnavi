package handoff

import (
	"regexp"
	"strings"

	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/tickets/model/place"
)

// JSONVersion は user handoff の JSON の版。
const JSONVersion = 1

// KindID は取り込み先の種類（JSON の kind）。
type KindID string

const (
	Template    KindID = "template"
	Messages    KindID = "messages"
	LayerConfig KindID = "layer-config"
	Docs        KindID = "docs"
	Defense     KindID = "defense"
)

// Kind は取り込み先の種類の表の 1 行。
type Kind struct {
	ID KindID
	// shown は設計書（docs/21_design/tickets/handoff.md）に載せる形。表と文書の食い違いをテストが見る。
	shown    []string
	patterns []*regexp.Regexp
	// creatable は取り込み先に無いファイルを新しく作れるか。onBoard はボードの経路（--via board）で取り込めるか。
	creatable bool
	onBoard   bool
}

func (k *Kind) matches(key string) bool {
	for _, p := range k.patterns {
		if p.MatchString(key) {
			return true
		}
	}
	return false
}

const (
	reName  = `[^/]+`
	reDeep  = `(?:[^/]+/)*[^/]+`
	reLayer = `(?:rules|phases|risks)\.yml`
)

func full(patterns ...string) []*regexp.Regexp {
	out := make([]*regexp.Regexp, len(patterns))
	for i, p := range patterns {
		out[i] = regexp.MustCompile(`\A(?:` + p + `)\z`)
	}
	return out
}

// kinds は取り込み先の表（許可リスト）。直すときは設計書の表と、sh（ccnavi-stage.sh の is_importable）も直す。
var kinds = []*Kind{
	{
		ID: Template,
		shown: []string{
			".ccnavi/config/common/templates/<名前>.yml",
			".ccnavi/config/project/templates/<名前>.yml",
			"projects/<名前>/.ccnavi/config/project/templates/<名前>.yml",
		},
		patterns: full(
			`\.ccnavi/config/common/templates/`+reName+`\.yml`,
			`\.ccnavi/config/project/templates/`+reName+`\.yml`,
			`projects/`+reName+`/\.ccnavi/config/project/templates/`+reName+`\.yml`,
		),
		creatable: true, onBoard: true,
	},
	{
		ID:        Messages,
		shown:     []string{".ccnavi/messages/<名前>.yml"},
		patterns:  full(`\.ccnavi/messages/` + reName + `\.yml`),
		creatable: true,
	},
	{
		ID: LayerConfig,
		shown: []string{
			".ccnavi/config/{common,project}/{rules,phases,risks}.yml",
			"projects/<名前>/.ccnavi/config/project/{rules,phases,risks}.yml",
		},
		patterns: full(
			`\.ccnavi/config/(?:common|project)/`+reLayer,
			`projects/`+reName+`/\.ccnavi/config/project/`+reLayer,
		),
		onBoard: true,
	},
	{
		ID:       Docs,
		shown:    []string{".ccnavi/docs/**/*.md", ".ccnavi/CCNAVI.md"},
		patterns: full(`\.ccnavi/docs/`+reDeep+`\.md`, `\.ccnavi/ccnavi\.md`),
		onBoard:  true,
	},
	{
		ID: Defense,
		shown: []string{
			".ccnavi/scripts/**",
			".claude/hooks/**",
			".claude/settings.json",
			".claude/settings.local.json",
			".claude/skills/**/*.md",
		},
		// スキルの frontmatter は使えるツールや hook を持てる（権限を広げうる）ので、docs ではなく防御にする。
		patterns: full(
			`\.ccnavi/scripts/`+reDeep,
			`\.claude/hooks/`+reDeep,
			`\.claude/settings\.json`,
			`\.claude/settings\.local\.json`,
			`\.claude/skills/`+reDeep+`\.md`,
		),
	},
}

// FoldPart はパスの 1 要素を比べるための形（NFC と fold）。開く表記には使わない。
func FoldPart(part string) string {
	return pyfmt.NFC(pathcase.Fold(pyfmt.NFC(part)))
}

// Key は相対パス（"/" 区切り）を、表に当てて比べるための形にする。
func Key(rel string) string {
	parts := strings.Split(rel, "/")
	for i, p := range parts {
		parts[i] = FoldPart(p)
	}
	return strings.Join(parts, "/")
}

// KindOf はそろえた相対パスが当たる種類。どれにも当たらなければ nil（対象外）。
func KindOf(key string) *Kind {
	for _, k := range kinds {
		if k.matches(key) {
			return k
		}
	}
	return nil
}

// DraftRel は取り込み先の相対パスに対応する、下書きのツリーからの相対パス（階層名は読み替える）。
func DraftRel(targetRel string) string {
	return place.Handoff + "/" + names.ToDraft(targetRel)
}
