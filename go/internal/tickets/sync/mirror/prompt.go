package mirror

import (
	"hash"
	"os"
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/digest"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/approval"
)

// ユーザの発言（UserPromptSubmit）のたびに、`project:` を持つ着手済みの親のワークツリーへ写す。ツール呼び出しの
// 中では写さない（実行後チェックとバックアップから見て、エージェントの書き込みと区別が付かなくなる）。呼び手は
// selfguard の受け入れの前に呼ぶ。写した分は、ユーザの手による変更と同じに受け入れられる。
//
// 発言のたびに走るので、元と先の置き場の stat だけで作る指紋が、前に揃えたときと同じなら何も読まない。
// 写せなかったときは指紋を残さず、直るまで毎回言う。比べている間に元が動いたときも残さない。

// signaturePrefix は指紋の記録（state の置き場の `mirror-<親の識別子>.json`）の名前の頭。records/prune が名前で消す。
const signaturePrefix = "mirror-"

const signatureKey = "signature"

// Sync は写した結果の行を返す。写すものが無い・揃っているときは空。写せない理由はその行を返し、ほかの親と
// もう一方の写しは続ける（発言は止めないので、呼び手は行をそのままモデルに渡す）。
func Sync(conf *config.Settings, root string) []string {
	if !conf.TicketsEnabled() {
		return nil
	}
	tickets, _ := approval.Scan(conf, root, false, nil)
	var lines []string
	seen := map[string]bool{}
	for _, t := range tickets {
		if t.IsChild() || t.Project == "" || t.StartedAt == "" || seen[t.ID] {
			continue
		}
		seen[t.ID] = true
		worktree := tree.WorktreePath(root, t.ID)
		// 承認済みチケットの project が指すリポジトリのワークツリーであること。
		if !tree.IsWorktreeOf(tree.ProjectRoot(conf.Projects, t.Project), worktree) {
			continue
		}
		lines = append(lines, syncOne(conf, root, t.ID, t.Project, worktree)...)
	}
	return lines
}

// syncOne は 1 つの親のワークツリーに、ミラー、工程スキルの順に写す。
func syncOne(conf *config.Settings, root, ticket, project, worktree string) []string {
	sources := []string{conf.CommonLayerDir(root), skillSource(root)}
	places := append(slices.Clone(sources), conf.CommonLayerDir(worktree), ospath.Join(worktree, config.SkillsDir))
	sigPath := ""
	if conf.State != "" {
		sigPath = fsio.RecordPath(conf.State, signaturePrefix, ticket, fsio.JSONExt)
	}
	before := signature(places)
	sourcesBefore := signature(sources)
	if sigPath != "" {
		if saved, ok := fsio.ReadObject(sigPath); ok && saved.String(signatureKey) == before {
			return nil
		}
	}

	var lines, committable []string
	failedAny := false
	failMirror := "ccnavi: " + ticket + " の共通レイヤーをミラーできない: "
	changes, why := commonPlan(conf, root, worktree)
	if why == "" && len(changes) > 0 {
		why = applyCommon(conf, worktree, changes)
		if why == "" {
			lines = append(lines, "共通レイヤーとプロジェクト "+project+" のミラー（.ccnavi/config/common/）が"+
				"違っていたので、共通レイヤーの中身に揃えた。変えたのは次のファイル。")
			lines = append(lines, describe(changes)...)
			committable = appendRels(committable, changes)
		}
	}
	if why != "" {
		failedAny = true
		lines = append(lines, failMirror+why)
	}

	failSkills := "ccnavi: " + ticket + " の工程スキルをコピーできない: "
	changes, why = skillPlan(root, worktree)
	if why == "" && len(changes) > 0 {
		why = applySkills(changes)
		if why == "" {
			lines = append(lines, "共通の工程スキルとプロジェクト "+project+" の skills/ が違っていたので、共通の中身に揃えた:")
			lines = append(lines, describe(changes)...)
			committable = appendRels(committable, changes)
		}
	}
	if why != "" {
		failedAny = true
		lines = append(lines, failSkills+why)
	}

	if sigPath != "" && !failedAny && signature(sources) == sourcesBefore {
		// 次の発言は、元も先も動いていなければ stat だけで抜ける。
		fsio.WriteJSON(sigPath, pyjson.NewObject(signatureKey, signature(places)))
	}
	if len(committable) > 0 {
		lines = append(lines, "  作業を始める前に、"+worktree+" で "+strings.Join(committable, ", ")+
			" を '"+config.ScriptCommand(root, config.GitWrapper)+" add' してコミットしてください")
	}
	return lines
}

func appendRels(rels []string, changes []Change) []string {
	for _, c := range changes {
		rels = append(rels, c.Rel)
	}
	return rels
}

// signature は置き場の下のファイルの名前・種類・大きさ・更新時刻・inode・変更時刻の指紋。stat だけで作り、中身は読まない。
// リンクはたどらず、リンクであることを指紋に入れる。置き場が無いことも指紋になる。
func signature(places []string) string {
	sum := digest.New(digest.SHA256)
	for _, place := range places {
		sum.Write([]byte("\x00place\x00" + place))
		signDir(sum, place, place)
	}
	return digest.HexSum(sum)
}

func signDir(sum hash.Hash, place, dir string) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return
	}
	var files, dirs []os.DirEntry
	for _, e := range entries {
		if e.IsDir() {
			dirs = append(dirs, e)
		} else {
			files = append(files, e)
		}
	}
	for _, e := range append(files, dirs...) {
		path := ospath.Join(dir, e.Name())
		info, err := os.Lstat(path)
		if err != nil {
			continue
		}
		kind := "f"
		switch {
		case info.Mode()&os.ModeSymlink != 0:
			kind = "l"
		case info.IsDir():
			kind = "d"
		}
		ino, ctime, ok := ospath.Identity(path)
		if !ok {
			continue
		}
		// inode と変更時刻（ctime）も入れる。更新時刻を戻して同じ大きさで書き換えても、ctime は戻せない。
		row := relPlain(place, path) + "\x00" + kind + "\x00" + strconv.FormatInt(info.Size(), 10) + "\x00" +
			strconv.FormatInt(info.ModTime().UnixNano(), 10) + "\x00" + strconv.FormatUint(ino, 10) + "\x00" +
			strconv.FormatInt(ctime, 10) + "\n"
		sum.Write([]byte(row))
	}
	for _, e := range dirs {
		signDir(sum, place, ospath.Join(dir, e.Name()))
	}
}
