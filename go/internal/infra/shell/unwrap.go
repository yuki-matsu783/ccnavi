package shell

import "strings"

// 実行役のコマンド（`env rm x` の env）を 1 枚ずつ外して、中で実行されるコマンドを層として並べる。
// ルールの多くはコマンドの頭に固定して書かれるので、実行役を前に置くだけで外れるのを防ぐ。
// 層は止める側のルールにだけ適用する。

// unwrapWords は 1 回の読みで作る層の語数の上限。`eval` や `sh -c` は読み直すと語が増えるので、
// 深さだけでは抑えきれない。
const unwrapWords = 2000

type layer struct {
	runner string
	words  []string
	quoted bool
	// reread は `eval` か `sh -c` の文字列を読み直した層（とその内側）か。
	reread bool
}

// unwrap は全コマンドの層を、コマンドの順・外側から内側の順に並べる。読み直した文字列の中で見つけた
// 書き直しを求める形は rewrites に足す。
func unwrap(commands []runnable, rewrites *[]Rewrite) []layer {
	var out []layer
	budget := unwrapWords
	for _, c := range commands {
		peelInto(c.words, 0, &out, &budget, c.quoted, rewrites, false)
	}
	return out
}

// peelInto は 1 本から層を足す。途中の層も残す（`env sh …agree.sh` の `sh …` の層に承認のルールが
// ヒットする）。語数の上限を越えた層は足さない。上限を越えたあとも外す処理は回るので、読み直した
// 文字列の中の書き直しを求める形は並ぶ。
func peelInto(command []string, depth int, out *[]layer, budget *int, quoted bool, rewrites *[]Rewrite, reread bool) {
	if depth >= UnwrapDepth {
		return
	}
	runner, inners, further := peel(command, rewrites)
	// シェルの層をさらに外してよいのは `-c` の文字列を読み直したときだけ。
	reread = reread || runner == "eval" || (shells[runner] && further)
	for _, inner := range inners {
		*budget -= len(inner)
		if *budget < 0 {
			return
		}
		*out = append(*out, layer{runner, inner, quoted, reread})
		if further {
			peelInto(inner, depth+1, out, budget, quoted, rewrites, reread)
		}
	}
}

// looked はコマンド名を見る層。読み直した層は、外側が縮退していれば見ない。
func looked(layers []layer, degraded bool) [][]string {
	var out [][]string
	for _, l := range layers {
		if !(l.reread && degraded) {
			out = append(out, l.words)
		}
	}
	return out
}

func single(rest []string) [][]string {
	if len(rest) == 0 {
		return nil
	}
	return [][]string{rest}
}

// peel は実行役を 1 枚だけ外し、その名前・中で実行されるコマンド・さらに外してよいかを返す。
// シェルと `.` に渡したファイルはスクリプトでコマンド名ではないので、その先は外さない。
func peel(command []string, rewrites *[]Rewrite) (string, [][]string, bool) {
	if len(command) == 0 {
		return "", nil, false
	}
	head := command[0]
	// 前に置いたリダイレクト（`>/dev/null env rm x`）。外さないと実行役が先頭に来ない。
	k := 0
	for k < len(command) {
		width := RedirectWidth(command, k)
		if width == 0 {
			break
		}
		k += width
	}
	if k > 0 {
		return strings.Join(command[:k], " "), single(command[k:]), true
	}
	if IsAssignment(head) {
		i := 0
		for i < len(command) && IsAssignment(command[i]) {
			i++
		}
		return head, single(command[i:]), true
	}
	// `/bin/sh x`・`git.exe x` は基底の名前で読み直す。
	name := Base(head)
	if name != head && name != "" {
		return head, [][]string{append([]string{name}, command[1:]...)}, true
	}
	switch {
	// find の述語の `\;` は shlex が区切りの `;` と同じ表記で返すので、2 つめの `-exec` からは
	// 別のコマンドの頭に来る。そこも find の続きとして読む。
	case name == "find" || isFindExec(name):
		return "find", findCommands(command), true
	case IsRunner(name):
		return name, single(RunnerCommand(name, command[1:])), true
	case shells[name]:
		inners, further := shellCommands(command[1:], rewrites)
		return name, inners, further
	case sources[name]:
		return name, single(command[1:]), false
	case name == "eval":
		return name, reread(strings.Join(command[1:], " "), rewrites), true
	case name == "xargs":
		return name, single(skipOptions(command[1:], xargsValueOptions)), true
	}
	return "", nil, false
}

func isFindExec(word string) bool {
	for _, e := range findExec {
		if word == e {
			return true
		}
	}
	return false
}

// shellCommands は `sh <ファイル> <引数>` ならファイルと引数、`sh -c '<文字列>'` なら読み直したコマンド。
// `sh -s a b` は標準入力を読み、後ろは引数。
func shellCommands(args []string, rewrites *[]Rewrite) ([][]string, bool) {
	takesCode, fromStdin := false, false
	i := 0
	for i < len(args) {
		token := args[i]
		if token == "--" {
			i++
			break
		}
		if shellValueOptions[token] {
			i += 2
			continue
		}
		if (strings.HasPrefix(token, "-") || strings.HasPrefix(token, "+")) && len(token) > 1 {
			if !strings.HasPrefix(token, "--") {
				takesCode = takesCode || strings.Contains(token, "c")
				fromStdin = fromStdin || strings.Contains(token, "s")
			}
			i++
			continue
		}
		break
	}
	if i >= len(args) {
		return nil, false
	}
	rest := args[i:]
	switch {
	case takesCode:
		return reread(rest[0], rewrites), true
	case fromStdin:
		return nil, false
	}
	return [][]string{rest}, false
}

// findCommands は `-exec` の類の後ろの、`;` か `+` までのコマンド。
func findCommands(command []string) [][]string {
	var out [][]string
	for i := 0; i < len(command); {
		if !isFindExec(command[i]) {
			i++
			continue
		}
		j := i + 1
		for j < len(command) && command[j] != ";" && command[j] != "+" {
			j++
		}
		if j > i+1 {
			out = append(out, command[i+1:j])
		}
		i = j + 1
	}
	return out
}

// reread は `sh -c` と `eval` に渡った文字列を、外側と同じ走査で読み直す。
func reread(src string, rewrites *[]Rewrite) [][]string {
	reading, commands, _, _ := readAt(src, 0)
	*rewrites = append(*rewrites, reading.Rewrites...)
	return wordsOf(commands)
}
