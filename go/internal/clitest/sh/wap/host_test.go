package wap

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/shkit"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
)

// ホストに触る副命令（request・confirm・ready）と、ユーザ専用の decide・close-early。

// vars は GitLab の代わりにつなぐ環境（PATH とトークン）。名前と値の組を足せる。
func (h *hostKit) vars(more ...string) map[string]string {
	out := map[string]string{env.Path: h.path, env.GitLabToken: "t0k"}
	for i := 0; i+1 < len(more); i += 2 {
		out[more[i]] = more[i+1]
	}
	return out
}

func (h *hostKit) bodyFile() string {
	return shkit.Write(h.t, filepath.Join(h.base, "body.md"), "見てほしい\n")
}

func (h *hostKit) requestArgs() []string {
	return []string{"review-request", "--phase", "1", "--body-file", h.bodyFile()}
}

func (h *hostKit) readyArgs() []string { return []string{"ready", "--parent", parentID} }

func TestRequestConfirmAndReadyAreCarried(t *testing.T) {
	h := newHostKit(t, hostHalf)
	okRun(t, h.review(opts{}, h.requestArgs()...))
	h.carried(approved + "/phases/" + parentID + "/1.requested")
	eq(t, "投稿", h.gl.noteCount(), 1)
	log := filepath.Join(h.base, "tidy.log")
	logger := h.wrapExe("logger", "case \" $* \" in\n*\" worktree \"*) printf '%s\\n' \"$*\" >>'"+shkit.Slash(log)+"' ;;\nesac\n", h.half)
	okRun(t, h.review(opts{env: map[string]string{env.EntryPath: logger}}, "confirm", "--phase", "1"))
	h.carried(approved + "/phases/" + parentID + "/1.reviewed")
	// done/ へ動いた子のワークツリーの片付けを、送った後に実行ファイルへ頼む
	has(t, shkit.Read(t, log), "worktree tidy "+parentID+" --phase 1")
	ready := okRun(t, h.review(opts{dir: h.ws, env: map[string]string{env.EntryPath: logger}}, h.readyArgs()...))
	h.carriedAfterDrop(approved + "/phases/" + parentID + "/ready.json")
	// 最後に親のワークツリーを外から消し、そのあと残っているワークツリーの一覧を頼む
	logged := shkit.Read(t, log)
	retire := strings.Index(logged, "worktree retire "+parentID)
	remaining := strings.Index(logged, "worktree remaining "+parentID)
	if retire < 0 || remaining < 0 || retire > remaining {
		t.Errorf("retire の後に remaining を頼む順になっていない:\n%s", logged)
	}
	dropped := "ワークツリー " + parentID + " を削除しました"
	has(t, ready.Stdout, dropped)
	if d, w := strings.Index(ready.Stdout, "Draft を解除した"), strings.Index(ready.Stdout, dropped); d < 0 || d > w {
		t.Errorf("Draft の解除が削除より先でない:\n%s", ready.Stdout)
	}
	if shkit.Exists(h.tree) {
		t.Error("親のワークツリーが残っている")
	}
	eq(t, "親のブランチ", h.git(h.ws, "branch", "--list", parentID), parentID)
	// 成功の終わりに、次の一手（ユーザのマージ待ち）を 1 行だけ言う
	eq(t, "次の一手の行", len(linesWith(ready.Stdout, "次: ")), 1)
}

// Draft を外し損ねたら、外から打っても親のワークツリーは消さない（ready を打ち直せるようにするため）。
func TestReadyKeepsTheParentWorktreeWhenTheDraftStays(t *testing.T) {
	h := newHostKit(t, hostHalf)
	h.gl.keepDraft = true
	r := h.review(opts{dir: h.ws}, h.readyArgs()...)
	if r.Code == 0 {
		t.Error("Draft が残ったのに 0")
	}
	has(t, r.Stderr, "Draft を解除できませんでした")
	lacks(t, r.Stdout, "を削除した")
	if !shkit.IsDir(h.tree) {
		t.Error("親のワークツリーを消した")
	}
}

// ワークスペースルートから相対パスで打っても、親のワークツリーへ移った後に write-and-push の取り込み（ccnavi-sync.sh）を見失わない。
// 親のワークツリーにはスクリプトのコピーを置かない（相対パスがそこで解かれると、見失うか、ワークツリーの古いコピーを使う）。
func TestReadyFromOutsideByARelativeScriptPath(t *testing.T) {
	h := newHostKit(t, hostHalf)
	h.git(h.tree, "rm", "-r", "-q", ".ccnavi/scripts")
	h.git(h.tree, "commit", "-q", "-m", "no scripts here")
	h.git(h.tree, "push", "-q", "origin", parentID)
	extra := map[string]string{env.Path: h.path, env.GitLabToken: "t0k", env.EntryPath: h.half, "HALF_TREE": h.tree}
	r := run.Command(t, "sh", []string{".ccnavi/scripts/ccnavi-review.sh", "ready", "--parent", parentID},
		run.Opts{Dir: h.ws, Env: h.env(extra)})
	code(t, r, 0)
	has(t, r.Stdout, "Draft を解除した")
	has(t, r.Stdout, "ワークツリー "+parentID+" を削除しました")
	if shkit.Exists(h.tree) {
		t.Error("親のワークツリーが残っている")
	}
}

func TestReadyParentRefusesWhatIsNotAParentWorktree(t *testing.T) {
	h := newHostKit(t, hostHalf)
	for _, args := range [][]string{
		{"--parent"},
		{"--parent", "../x"},
		{"--parent", "i0009"},
		{"--parent="},
		{"--parent", ""},
		{"--parent", parentID, "--parent"},
		{"--parent", parentID, "--parent=" + parentID},
	} {
		r := h.review(opts{dir: h.ws}, append([]string{"ready"}, args...)...)
		if r.Code != 2 {
			t.Errorf("%q: 終了コード %d（2 のはず）\n%s%s", args, r.Code, r.Stdout, r.Stderr)
		}
		lacks(t, r.Stdout, "Draft を解除した")
	}
	if !shkit.IsDir(h.tree) {
		t.Error("親のワークツリーを消した")
	}
}

// ready の退避（閉じたチケットの削除）と、消した wip/ の追跡済みのファイルは、write-and-push がコミットして push してから Draft を外す。
// 消した wip/ のファイルと、戻せる版（消す前の HEAD）を出す。
func TestReadyCommitsTheArchiveAndWipRemovalsAndNamesThem(t *testing.T) {
	h := newHostKit(t, hostHalf)
	old := approved + "/phases/old/closed.json"
	shkit.Write(t, filepath.Join(h.tree, parts(old)), "{}\n")
	h.git(h.tree, "add", old)
	h.git(h.tree, "commit", "-q", "-m", "old")
	h.wipNoteCommitted()
	head := h.head()
	mover := h.wrapExe("mover", h.prelude()+"case \" $* \" in\n*\" review ready \"*)\n"+
		"  prev=\"\"; for a in \"$@\"; do\n"+
		"    [ \"$prev\" = --record-writes ] && list=\"$a\"\n"+
		"    [ \"$prev\" = --record-tree ] && tree=\"$a\"\n"+
		"    [ \"$prev\" = --root ] && root=\"$a\"\n"+
		"    prev=\"$a\"; done\n"+
		"  rm \"$tree/"+old+"\" \"$tree/"+wipNote+"\"\n"+
		"  printf '"+old+"\\n"+wipNote+"\\n' >\"$list\"\n"+
		"  mkdir -p \"$root/logs/state\"; : >\"$root/logs/state/note.md\"\n"+
		"  printf '%s\\n' \"$root/logs/state/note.md\"\n"+
		"  printf 'tree %s\\n' \"$tree\"\n"+
		"  printf 'wip-from "+head+"\\nwip "+wipNote+"\\n'\n"+
		// 報告の文は実行ファイルが組み、sh は成功のあとにそのまま出す
		"  printf 'say wip/ の追跡済みのファイルを削除しました（1 件。削除する前のコミット "+head[:12]+" から戻せる）:\\n'\n"+
		"  printf 'say   "+wipNote+"\\n'\n"+
		"  exit 0 ;;\nesac\n", h.half)
	ready := okRun(t, h.review(opts{dir: h.ws, env: map[string]string{env.EntryPath: mover}}, h.readyArgs()...))
	has(t, ready.Stdout, "Draft を解除した")
	has(t, ready.Stdout, "wip/ の追跡済みのファイルを削除しました（1 件")
	has(t, ready.Stdout, head[:12])
	has(t, ready.Stdout, "  "+wipNote)
	h.carriedAfterDrop(wipNote)
	has(t, strings.Join(h.committedIn(h.ws, parentID), "\n"), old)
	for _, rel := range []string{old, wipNote} {
		eq(t, rel+" の ls-tree", h.git(h.ws, "ls-tree", parentID, "--", rel), "")
	}
}

// 実際の実行ファイルの ready（退避を始めた後の打ち直し）を write-and-push の中で通す。
// 1 回目の push をリモートが断る → write-and-push が戻す → 取り込みからやり直して残りを移し、送ってから Draft を外す。
func TestTheRealReadyMovesTheRestAgainAfterAFailedPush(t *testing.T) {
	h := newHostKit(t, hostHalf)
	archive := filepath.Join(h.ws, "logs", "archive", "self")
	// 親はもう退避にだけある（前の回が移した）。ツリーには統合先からの過去の親子が残る。
	for _, rel := range []string{"doing/" + parentID + ".md", "doing/" + childID + ".md"} {
		h.git(h.tree, "rm", "-q", "--", approved+"/"+rel)
	}
	shkit.Write(t, filepath.Join(archive, "done", parentID+".md"), h.parentText(samples.Plan(h.plan...), nil))
	shkit.Write(t, filepath.Join(archive, "phases", parentID, "ready.json"), `{"mr": 1}`)
	oldTicket := "---\nversion: 1\nticket: old\ntitle: 古い親\nrationale: r\n" +
		"human_review:\n  required: false\n  reason: t\n" +
		"allow:\n  - match: Write|Edit\n    glob: \"src/*\"\n---\n"
	shkit.Write(t, filepath.Join(h.tree, parts(approved), "done", "old.md"), oldTicket)
	shkit.Write(t, filepath.Join(h.tree, parts(approved), "phases", "old", "closed.json"), "{}\n")
	h.git(h.tree, "add", "-A")
	h.git(h.tree, "commit", "-q", "-m", "退避の途中")
	h.git(h.tree, "push", "-q", "origin", parentID)
	head := h.head()
	// 記録のツリーは実行ファイルの pathcase.Fold と同じ形（大文字小文字を畳む）で書く
	record, _ := json.Marshal(map[string]any{"parent": parentID, "tree": pathcase.Fold(ospath.Realpath(h.tree)), "head": head, "files": []string{}})
	shkit.Write(t, filepath.Join(archive, "ready", parentID+".json"), string(record))
	// リモートは 1 回目の push だけ断る
	once := filepath.Join(h.base, "refused-once")
	shkit.WriteExec(t, filepath.Join(h.remote, "hooks", "pre-receive"),
		"#!/bin/sh\n[ -e '"+shkit.Slash(once)+"' ] && exit 0\n: >'"+shkit.Slash(once)+"'\necho refused >&2\nexit 1\n")
	ready := okRun(t, h.review(opts{dir: h.ws, env: map[string]string{env.EntryPath: h.bin}}, h.readyArgs()...))
	if !shkit.Exists(once) {
		t.Error("1 回目の push を断っていない")
	}
	has(t, ready.Stderr, "push が通らなかった")
	has(t, ready.Stdout, "Draft を解除した")
	h.carriedAfterDrop(approved + "/done/old.md")
	for _, rel := range []string{"done/old.md", "phases/old/closed.json"} {
		eq(t, rel+" の ls-tree", h.git(h.ws, "ls-tree", parentID, "--", approved+"/"+rel), "")
		if !shkit.IsFile(filepath.Join(archive, parts(rel))) {
			t.Errorf("%s が退避にない", rel)
		}
	}
}

// 投稿の後に失敗した依頼を打ち直しても、同じ目印の依頼は投稿し直さない。
func TestARequestIsNotPostedTwiceAcrossRuns(t *testing.T) {
	h := newHostKit(t, hostHalf)
	once := filepath.Join(h.base, "failed-once")
	failing := opts{env: map[string]string{"HALF_FAIL": once}}
	first := h.review(failing, h.requestArgs()...)
	if first.Code == 0 {
		t.Error("1 回目は失敗するはず")
	}
	eq(t, "投稿", h.gl.noteCount(), 1)
	// 投稿の後、送れたかを書く前に落ちた形にする（段階を unknown に戻す）。打ち直しは、目印で見つけた投稿を ops mark-sent で送り済みに直す。
	pending := h.exe(opts{}, "ops", "pending")
	code(t, pending, 0)
	eq(t, "未完了", pending.Stdout, "")
	opsDir := filepath.Join(h.state, "ops")
	entries, _ := os.ReadDir(opsDir)
	var records []string
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".json") {
			records = append(records, e.Name())
		}
	}
	if len(records) != 1 {
		t.Fatalf("操作記録 %v", records)
	}
	shown := h.exe(opts{}, "ops", "show", strings.TrimSuffix(records[0], ".json"))
	var show struct {
		Stages []struct {
			ID string `json:"id"`
		} `json:"stages"`
	}
	if err := json.Unmarshal([]byte(shown.Stdout), &show); err != nil || len(show.Stages) == 0 {
		t.Fatalf("ops show: %v\n%s", err, shown.Stdout)
	}
	var record map[string]any
	dec := json.NewDecoder(bytes.NewReader([]byte(shkit.Read(t, filepath.Join(opsDir, records[0])))))
	dec.UseNumber()
	if err := dec.Decode(&record); err != nil {
		t.Fatal(err)
	}
	record["stages"].([]any)[0].(map[string]any)["state"] = "unknown"
	data, _ := json.Marshal(record)
	shkit.Write(t, filepath.Join(opsDir, records[0]), string(data))
	has(t, h.exe(opts{}, "ops", "pending").Stdout, show.Stages[0].ID)
	again := okRun(t, h.review(failing, h.requestArgs()...))
	has(t, again.Stdout, "投稿済み")
	eq(t, "投稿", h.gl.noteCount(), 1)
	eq(t, "未完了", h.exe(opts{}, "ops", "pending").Stdout, "")
	h.carried(approved + "/phases/" + parentID + "/1.requested")
}

// ターミナルの decide は、選ぶのを write-and-push の外で済ませ、write-and-push の中では --yes で書くだけ。
func TestTerminalDecideChoosesOutsideWriteAndPushAndWritesInside(t *testing.T) {
	h := newHostKit(t, hostHalf)
	chooser := h.wrapExe("chooser", h.prelude()+"case \" $* \" in\n*\" --choose-out \"*)\n"+
		"  prev=\"\"; for a in \"$@\"; do [ \"$prev\" = --choose-out ] && out=\"$a\"; prev=\"$a\"; done\n"+
		"  [ -d '"+shkit.Slash(h.lockDir())+"' ] && echo LOCKED-WHILE-CHOOSING >&2\n"+
		"  printf 'choices {\"u1\":\"keep\"}\\ndigest d0\\n' >\"$out\"\n"+
		"  exit 0 ;;\n"+
		// 事前の検査（--check）は通す。受け入れる指摘の計画（--accept-plan）は、操作記録を作るスタブ（half）に任せる
		"*\" --check \"*) exit 0 ;;\n"+
		"*\" --accept-plan \"*) exec '"+h.half+"' \"$@\" ;;\n"+
		"*\" --yes \"*)\n"+
		"  prev=\"\"; for a in \"$@\"; do\n"+
		"    [ \"$prev\" = --record-writes ] && list=\"$a\"\n"+
		"    [ \"$prev\" = --record-tree ] && tree=\"$a\"\n"+
		"    prev=\"$a\"; done\n"+
		"  mkdir -p \"$tree/.ccnavi/approved/phases/i0001\"\n"+
		"  printf '{}' >\"$tree/.ccnavi/approved/phases/i0001/1.reviewed\"\n"+
		"  printf '.ccnavi/approved/phases/i0001/1.reviewed\\n' >\"$list\"\n"+
		"  prev=\"\"; for a in \"$@\"; do\n"+
		"    [ \"$prev\" = --accept-done ] && dp=\"$a\"; [ \"$prev\" = --root ] && r=\"$a\"; prev=\"$a\"; done\n"+
		"  op=$(sed -n 's/.*\"op\": \"\\([0-9a-f]*\\)\".*/\\1/p' \"$dp\")\n"+
		"  [ -z \"$op\" ] || '"+h.exePath+"' --root \"$r\" ops applied \"$op\" local true\n"+
		"  printf '{\"version\":1,\"ok\":true}\\n'; exit 0 ;;\nesac\n", h.half)
	done := okRun(t, h.review(opts{script: "ccnavi-user-decide.sh", env: map[string]string{env.EntryPath: chooser}}, "1"))
	has(t, done.Stdout, "対応方針を置いて送った")
	lacks(t, done.Stderr, "LOCKED-WHILE-CHOOSING")
	h.carried(approved + "/phases/" + parentID + "/1.reviewed")
}

// 親は --parent で受ける。ワークスペースルート（統合先のブランチ）から打っても、その親を閉じる。
func TestCloseEarlyHandsOverToTheCarrier(t *testing.T) {
	h := newHostKit(t, hostHalf)
	okRun(t, h.review(opts{script: "ccnavi-user-close-early.sh", dir: h.ws}, "--parent", parentID, "--reason", "r", "--no-issue"))
	h.carried(approved + "/phases/" + parentID + "/2.skipped")
}

func TestCloseEarlyWithoutAParentStopsBeforeTheHost(t *testing.T) {
	h := newHostKit(t, hostHalf)
	log := filepath.Join(h.base, "calls.log")
	logging := h.wrapExe("logging", "printf '%s\\n' \"$*\" >>'"+shkit.Slash(log)+"'\n", h.half)
	before := h.committed("HEAD")
	r := h.review(opts{script: "ccnavi-user-close-early.sh", env: map[string]string{env.EntryPath: logging}}, "--reason", "r")
	code(t, r, 2)
	has(t, r.Stderr, "--parent <親の識別子> が必要です")
	// 親のワークツリーの中なら、案内に親の識別子を埋める
	has(t, r.Stderr, "--parent "+parentID+" --reason 'r'")
	calls := ""
	if shkit.Exists(log) {
		calls = shkit.Read(t, log)
	}
	lacks(t, calls, "close-early")
	eq(t, "投稿", h.gl.noteCount(), 0)
	eqList(t, "コミット", h.committed("HEAD"), before)
}

func TestCloseEarlyRefusesFirstWhenTheFamilyCannotBeWritten(t *testing.T) {
	h := newHostKit(t, hostHalf)
	log := filepath.Join(h.base, "calls.log")
	refusing := h.wrapExe("refusing", "printf '%s\\n' \"$*\" >>'"+shkit.Slash(log)+"'\ncase \" $* \" in\n*\" write-and-push family \"*) exit 1 ;;\nesac\n", h.half)
	before := h.committed("HEAD")
	r := h.review(opts{script: "ccnavi-user-close-early.sh", env: map[string]string{env.EntryPath: refusing}}, "--parent", parentID, "--reason", "r")
	code(t, r, 1)
	has(t, r.Stderr, "書かずに止めた")
	// 受け入れの計画・ホストへの返信・issue・コメントの前に止まり、何も変わらない
	lacks(t, shkit.Read(t, log), "close-early")
	eq(t, "投稿", h.gl.noteCount(), 0)
	eqList(t, "コミット", h.committed("HEAD"), before)
}

// レビューの状態を書く sh も write-and-push で回す（ccnavi-user-decide.sh を代表に見る）。

func TestDecideIsCarriedAndTheAnswerStaysJSON(t *testing.T) {
	h := newHostKit(t, half)
	r := okRun(t, h.sh("ccnavi-user-decide.sh", opts{dir: h.tree, env: h.vars(env.EntryPath, h.half)}, "1", "--choices", `{"u1":"keep"}`, "--digest", "d0"))
	lines := strings.Split(strings.TrimSpace(r.Stdout), "\n")
	var answer map[string]any
	if err := json.Unmarshal([]byte(lines[len(lines)-1]), &answer); err != nil {
		t.Fatalf("最後の行が JSON でない: %v\n%s", err, r.Stdout)
	}
	if answer["ok"] != true {
		t.Errorf("ok = %v", answer["ok"])
	}
	eq(t, "標準出力の行数", len(lines), 1)
	eqList(t, "コミット", h.committed("HEAD"), []string{approved + "/phases/" + parentID + "/1.reviewed"})
	eqList(t, "件名", h.subjects(1), []string{"ccnavi: " + parentID + " のフェーズ 1 の指摘の対応方針を決めた"})
	eq(t, "リモート", h.remoteParent(), h.head())
	// 決めた内容のコメントは、送った後に投稿する。
	eq(t, "投稿", h.gl.noteCount(), 1)
}

func TestDecidePreviewTakesNoLockAndWritesNothing(t *testing.T) {
	h := newHostKit(t, half)
	head := h.head()
	if err := os.MkdirAll(h.lockDir(), 0o755); err != nil {
		t.Fatal(err)
	}
	shkit.Write(t, filepath.Join(h.lockDir(), "owner"), "other 1 1 1-1\n")
	shown := h.sh("ccnavi-review.sh", opts{dir: h.tree, env: h.vars()}, "decide", "1", "--preview")
	lacks(t, shown.Stderr, "ロック")
	eq(t, "HEAD", h.head(), head)
}
