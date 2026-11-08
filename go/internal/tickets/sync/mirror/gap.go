package mirror

import (
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
)

// プロジェクトのツリーの、写したもの（ミラーと skills/）のずれは、ccnavi が配るコピーのずれで、エージェントの
// 作業の変更ではない。コミットや取り消しを促すと承認の範囲を超えるので、ユーザに揃えてもらう案内に変える。
// ready・finish・confirm が同じ見分けと文を使う。止めるか通すかは呼び手が決める。

// shownPaths はずれのパスを並べる上限。
const shownPaths = 10

// NotWork はずれの分をどう扱うかの一文。
const NotWork = "ずれの分は作業による変更ではないため、コミットも取り消しもしない。"

// Ask はユーザに頼むことの一文。
const Ask = "ユーザには、`ccnavi-user-mirror.sh <プロジェクト名>` で差分と値を確かめ、" +
	"`--yes --expect <値>` で `config/common` も `skills/` も揃えて、" +
	"統合先にコミットしてもらうよう頼む"

// prefixes はプロジェクトのツリーで写したものを置く場所（ルートからの相対、末尾に "/"、畳んだもの）。
func prefixes(conf *config.Settings) []string {
	mirror := strings.Trim(ospath.Slashed(conf.CommonLayerDir("")), "/")
	return []string{pathcase.Fold(mirror + "/"), pathcase.Fold(config.SkillsDir + "/")}
}

// Split はパス（ツリーのルートからの相対）を、写したものの側とそれ以外に分ける。
func Split(conf *config.Settings, paths []string) (mirrored, others []string) {
	marks := prefixes(conf)
	for _, p := range paths {
		folded := pathcase.Fold(p)
		if strings.HasPrefix(folded, marks[0]) || strings.HasPrefix(folded, marks[1]) {
			mirrored = append(mirrored, p)
		} else {
			others = append(others, p)
		}
	}
	return mirrored, others
}

func names(paths []string) string {
	shown := strings.Join(paths[:min(len(paths), shownPaths)], "、")
	if len(paths) > shownPaths {
		shown += " ほか " + strconv.Itoa(len(paths)-shownPaths) + " 件"
	}
	return shown
}

// Label はずれたパスの表示（先頭の 10 件まで）。
func Label(mirrored []string) string {
	return "共通レイヤーのコピーとのずれ: " + names(mirrored)
}

// OthersNote はずれ以外の変更があるときの一文。無ければ空。
func OthersNote(others []string) string {
	if len(others) == 0 {
		return ""
	}
	return "ずれ以外の変更が " + strconv.Itoa(len(others)) + " 件ある（" + names(others) + "）。そちらは作業の変更として扱う。"
}
