package lint

import (
	"errors"
	"fmt"
	"io"
	"io/fs"
	"regexp"
	"strconv"

	"ccnavi/internal/hook/ccnavimd"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/config/check"
)

// collect は防御を無効化しうる記述を数え上げる。notes は設定の解決が出した苦情、complaints はモードの解決が
// 出した苦情で、どちらも深刻度を持たない文で届くのでここで付ける。並びは出力の順になる。
func collect(conf *config.Settings, root string, notes []string, mode config.Mode, complaints string, compat int) []rules.Problem {
	var problems []rules.Problem
	// 上書き設定を読み飛ばしても値は既定に戻りガードは弱まらないが、設定したつもりの値がどこにも使われていない。
	for _, note := range notes {
		problems = append(problems, rules.Problem{Severity: rules.Warn, Rule: whereSettings, Detail: note})
	}
	problems = append(problems, saidLines(complaints, whereMode)...)
	switch mode {
	case config.Disable:
		problems = append(problems, rules.Problem{Severity: rules.Warn, Rule: whereMode, Detail: string(config.Disable) + " なので何も判定しない"})
	case config.DryRun:
		// 導入の途中では正しい状態なので warn。ルールが揃っているのに 1 件も止まらない状態は、外から見ると
		// ガードが働いている状態と区別が付かない。
		problems = append(problems, rules.Problem{Severity: rules.Warn, Rule: whereMode,
			Detail: string(config.DryRun) + " なので一般の保護は報告だけにする（チケットのゲートと補助の保護は止める）"})
	}
	for _, found := range [][]rules.Problem{
		projectSettings(root),
		shCompat(root, compat),
		ccnaviMD(conf, root),
		afterCheck(root),
		// 共通レイヤーの 3 本。検査の中身は取り込み（user handoff）と同じ関数。
		check.CommonRules(conf, root),
		check.CommonPhases(conf),
		check.CommonRisk(conf, root),
		ticketProblems(conf, root),
		projectPlaces(conf, root),
		// 読めないレイヤーの苦情は判定と同じ経路が標準エラーにも書くので、受け皿で捨てて二重に言わない。
		check.LayerRules(io.Discard, conf, root, ""),
		check.LayerConfigs(conf, root, "", ""),
		check.UnmatchedScopes(conf, root),
		templateProblems(conf, root),
		messageProblems(root),
		worktreeLayers(conf, root),
		ticketPlaces(conf, root),
		projectlessWorktrees(conf, root),
		directProblems(conf, root),
		wikiProblems(conf, root),
		localSettings(root),
		syncProblems(conf, root),
	} {
		problems = append(problems, found...)
	}
	return problems
}

// shCompatFile は sh が互換の版を宣言する場所。`.ccnavi/scripts/` の sh はどれもこれを `.` で読む。
var shCompatFile = ospath.Join(config.DefaultProjectHome, config.ScriptsDir, "ccnavi-common.sh")

var shCompatLine = regexp.MustCompile(`(?m)^CCNAVI_COMPAT=([0-9]+)[ \t]*$`)

// compatFix は実行ファイルと sh が食い違ったときの直し方。ccnavi のリポジトリなら組み立て直し、配布先なら配り直し
// （配布先には組み立てる元が無い）。
func compatFix(root string) string {
	if ospath.IsFile(ospath.Join(root, "build.py")) && ospath.IsFile(ospath.Join(root, "src", "ccnavi", "__main__.py")) {
		return "build.py を実行して組み立て直してください（uv run --with pyinstaller python build.py）"
	}
	return "ccnavi のリポジトリで build.py を実行し、scripts/ccnavi-setup.sh <このワークスペース> " +
		"--force で実行ファイルと sh を配り直してください"
}

// shCompat は `.ccnavi/scripts/` の sh と、この実行ファイルの互換の版が揃っているか。食い違っても判定は動くので
// warn（sh の側でチケットやレビューの操作が失敗する）。sh が無いワークスペースは言わない。
func shCompat(root string, compat int) []rules.Problem {
	path := ospath.Join(root, shCompatFile)
	text, err := fsio.ReadDiskText(path, fsio.Strict)
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	warn := func(detail string) []rules.Problem {
		return []rules.Problem{{Severity: rules.Warn, Rule: whereVersion, Detail: detail}}
	}
	if err != nil {
		return warn(fmt.Sprintf("%s を読めない (%s)", path, fsio.ErrorText(err)))
	}
	found := shCompatLine.FindStringSubmatch(text)
	if found == nil {
		return warn(fmt.Sprintf("%s に互換の版（CCNAVI_COMPAT）が書かれていない。実行ファイル（互換 %d）より古い sh である。%s",
			path, compat, compatFix(root)))
	}
	written, _ := strconv.Atoi(found[1])
	if written == compat {
		return nil
	}
	return warn(fmt.Sprintf("sh（%s）は互換 %d、実行ファイルは互換 %d で食い違っている。%s", path, written, compat, compatFix(root)))
}

// ccnaviMD は作業の決まり（`.ccnavi/CCNAVI.md`）の書き方。無いのは warn（作業の決まりが渡らない）。条件の名前の形の
// 誤りはどちらでも判定できないので error、名前の誤りはその行が黙って落ちるので warn。ccnavi を入れる前の
// ワークスペース（`.ccnavi/` が無い）は言わない。
func ccnaviMD(conf *config.Settings, root string) []rules.Problem {
	path := ccnavimd.Path(conf, root)
	if !ospath.IsDir(ospath.Dirname(path)) {
		return nil
	}
	text, err := fsio.ReadDiskText(path, fsio.Strict)
	if errors.Is(err, fs.ErrNotExist) {
		return []rules.Problem{{Severity: rules.Warn, Rule: whereCcnaviMD,
			Detail: path + " がありません。セッションの開始で作業の決まりが渡らない。導入スクリプト（ccnavi-setup.sh）で配る"}}
	}
	if err != nil {
		return []rules.Problem{{Severity: rules.Warn, Rule: whereCcnaviMD, Detail: fmt.Sprintf("%s を読めない (%s)", path, fsio.ErrorText(err))}}
	}
	var out []rules.Problem
	for _, m := range ccnavimd.NameProblems(text) {
		out = append(out, rules.Problem{Severity: rules.Error, Rule: whereCcnaviMD, Detail: path + ": " + m})
	}
	for _, m := range ccnavimd.Problems(text, ccnavimd.ScriptNames(conf, root)) {
		out = append(out, rules.Problem{Severity: rules.Warn, Rule: whereCcnaviMD, Detail: path + ": " + m})
	}
	return out
}
