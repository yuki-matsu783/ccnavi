// Package settings は ccnavi の設定を解決する。
//
// 設定は環境変数で運ぶ。プロジェクトはそれを Claude Code の設定ファイルの env
// ブロックに書く。エージェント側の設定スキーマが独自キーを拒むため、そこが唯一
// 開いている場所になる。
//
// 値はプロセスの環境から読み、設定ファイルからは読まない。この違いが効く。
// hook が受け取る環境はセッション開始時に固定されるので、あとから設定ファイルを
// 直してもセッションを開き直すまで届かない。その古さは不便だが、同時に、
// エージェントが自分を見張るものを緩めるのを防いでいる唯一の仕組みでもある。
// 設定ファイルは作業ツリーの中にあってエージェントが書けるので、そこから読んだ
// 値は次のツール呼び出しから効いてしまう。
//
// 例外は ccnavi 自身を開発しているときだけ。OwnSourceTree を参照。
package settings

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

// ccnavi が読む環境変数。
const (
	ModeEnv  = "CCNAVI_MODE"
	RulesEnv = "CCNAVI_RULES"
	LogEnv   = "CCNAVI_LOG"
)

// ownModule は ccnavi 自身のソースツリーを見分ける印。OwnSourceTree を参照。
const ownModule = "github.com/yuki-matsu783/ccnavi"

// LocalFile は ccnavi 自身を開発しているときだけ読む上書き設定。
// コミットしないので、他のプロジェクトには存在せず、
// 直しても影響が及ぶのは道具を試している本人だけになる。
const LocalFile = "ccnavi.settings.local.json"

// 既定の置き場。プロジェクト根からの相対。
var (
	DefaultLog   = filepath.Join(".claude", "ccnavi", "log.jsonl")
	DefaultRules = filepath.Join(".claude", "ccnavi", "rules.json")
)

// Settings は解決済みの設定。パスはすべて絶対にしてあるので、
// これ以降の処理が作業ディレクトリに依存しない。
type Settings struct {
	Mode string
	// ModeDeclaredInFile は設定ファイルが求めたモード、
	// ModeFromEnvironment はプロセスが渡されたモード。
	// 2 つを分けて持つことで、呼び手が「作業ツリーの中に書かれた値」と
	// 「セッションを起動した人が渡した値」を見分けられる。
	ModeDeclaredInFile  string
	ModeFromEnvironment string

	// LiveFiles は上書き設定を読んだことを示す。
	// ccnavi 自身のソースツリーでしか立たない。
	LiveFiles bool

	Log   string
	Rules string
}

// local は上書き設定ファイルの中身。
type local struct {
	Mode  *string `json:"mode"`
	Rules *string `json:"rules"`
	Log   *string `json:"log"`
}

// Load は root にあるプロジェクトの設定を解決する。
//
// 設定ファイルが無い、あるいは読めないことは失敗ではない。
// 既定値だけで初回のチェックアウトが動かないと、
// ccnavi を入れるだけで設定ファイルの編集が必要になってしまう。
func Load(root string) (*Settings, []string) {
	s := &Settings{
		Log:                 filepath.Join(root, DefaultLog),
		Rules:               filepath.Join(root, DefaultRules),
		Mode:                os.Getenv(ModeEnv),
		ModeFromEnvironment: os.Getenv(ModeEnv),
	}
	if p := os.Getenv(RulesEnv); p != "" {
		s.Rules = resolve(root, p)
	}
	if p, ok := os.LookupEnv(LogEnv); ok {
		s.Log = logOrNone(root, p)
	}

	if !OwnSourceTree(root) {
		return s, nil
	}

	// ccnavi 自身を触っている場合。ここでファイルを読むと、編集が次のセッション
	// ではなく次のツール呼び出しから効く。道具を自分自身に当てて試すには
	// これが要る。開発のための便宜であって境界ではない。効くのはここだけで、
	// off には手が届かないままにしてある。
	conf, problems := readLocal(root)
	if conf == nil {
		return s, problems
	}
	s.LiveFiles = true

	if conf.Mode != nil {
		s.ModeDeclaredInFile = *conf.Mode
		s.Mode = *conf.Mode
	}
	if conf.Rules != nil && *conf.Rules != "" {
		s.Rules = resolve(root, *conf.Rules)
	}
	if conf.Log != nil {
		s.Log = logOrNone(root, *conf.Log)
	}

	return s, problems
}

// OwnSourceTree は root が ccnavi を開発しているチェックアウトかどうかを、
// そこにあるソースが宣言しているモジュール名で判断する。
//
// これは安全性の検査ではない。1 つのリポジトリを「道具を作っている場所」として
// 印を付け、ルールを試す人がセッションを開き直さずに変更を見られるようにする
// だけのもの。他のプロジェクトは環境変数だけが設定の出所のままなので、
// そこでエージェントが設定ファイルを書き換えても、人がセッションを開き直すまで
// ガードには届かない。
func OwnSourceTree(root string) bool {
	f, err := os.Open(filepath.Join(root, "go.mod"))
	if err != nil {
		return false
	}
	defer func() { _ = f.Close() }()

	scan := bufio.NewScanner(f)
	for scan.Scan() {
		line := strings.TrimSpace(scan.Text())
		if rest, ok := strings.CutPrefix(line, "module "); ok {
			return strings.TrimSpace(rest) == ownModule
		}
	}
	return false
}

// readLocal は上書き設定を読む。
//
// 壊れたファイルは報告して読み飛ばす。設定ファイルが壊れていることを理由に
// 判定を拒むと、書き損じたカンマ 1 つでガードが消えることになる。
func readLocal(root string) (*local, []string) {
	raw, err := os.ReadFile(filepath.Join(root, LocalFile))
	switch {
	case errors.Is(err, fs.ErrNotExist):
		return nil, nil
	case err != nil:
		return nil, []string{fmt.Sprintf("%s を読めないので無視した (%v)", LocalFile, err)}
	}

	var conf local
	if err := json.Unmarshal(raw, &conf); err != nil {
		return nil, []string{fmt.Sprintf("%s が JSON として読めないので無視した (%v)", LocalFile, err)}
	}
	return &conf, nil
}

// logOrNone は明示的な空文字を「記録しない」として扱う。
func logOrNone(root, path string) string {
	if path == "" {
		return ""
	}
	return resolve(root, path)
}

func resolve(root, path string) string {
	if filepath.IsAbs(path) {
		return path
	}
	return filepath.Join(root, path)
}
