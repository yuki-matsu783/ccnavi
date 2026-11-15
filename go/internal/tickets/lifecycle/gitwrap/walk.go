package gitwrap

import "strings"

// Deny は拒否。ID は診断ログに残す短い語、Message は標準エラーに出す文（`ccnavi-git: ` の後ろ）。
type Deny struct {
	ID, Message string
}

// spec はオプションの許可リスト。長いオプションは空白区切りの名前の並び。
//
//   - flags: 値を取らない長いオプション
//   - values: 値を取る長いオプション（`--x=v` か次の語）
//   - optvals: 値を `=` でだけ取れる長いオプション（`--x` だけでもよい）
//   - sflags・svalues・soptvals: 短いオプションの字（値を取らない・値を取る（残りの字か次の語）・値を残りの字でだけ取る）
type spec struct {
	flags, values, optvals, sflags, svalues, soptvals string
}

// listed は、空白区切りの並び list に name があるか（前後を空白で挟んで探す）。
func listed(list, name string) bool {
	return strings.Contains(" "+list+" ", " "+name+" ")
}

// event は Walk がコールバックに渡す 1 語の読み。kind は opt（オプション。name は `-x` か `--name`、value はその値）・
// pos（位置の引数。value がその語）・end（`--` を見た）。
type event struct{ kind, name, value string }

// Walk は args を許可リストで読み、1 語ずつ cb に渡す。一覧に無いオプション・値の無いまま終わったオプションは
// option-not-allowed で拒否する。cb が拒否を返したら、そこで止めて返す。
func Walk(sub string, s spec, args []string, cb func(event) *Deny) *Deny {
	notAllowed := func(arg string) *Deny {
		return &Deny{"option-not-allowed", sub + " の " + arg + " は使用できません。使用できるオプションは一覧にあるものだけで、長いオプションは略さずに書きます（略した表記を、git がここで止めているオプションとして読むことがあります）。使いたい形があれば、ユーザに伝えて一覧に追加してもらってください。"}
	}
	end, need := false, ""
	for _, arg := range args {
		if need != "" {
			if d := cb(event{"opt", need, arg}); d != nil {
				return d
			}
			need = ""
			continue
		}
		if end {
			if d := cb(event{"pos", "", arg}); d != nil {
				return d
			}
			continue
		}
		switch {
		case arg == "--":
			end = true
			if d := cb(event{"end", "", ""}); d != nil {
				return d
			}
		case strings.HasPrefix(arg, "--"):
			name, value, eq := strings.Cut(arg[2:], "=")
			switch {
			case listed(s.values, name):
				if eq {
					if d := cb(event{"opt", "--" + name, value}); d != nil {
						return d
					}
				} else {
					need = "--" + name
				}
			case listed(s.optvals, name):
				if d := cb(event{"opt", "--" + name, value}); d != nil {
					return d
				}
			case listed(s.flags, name):
				if eq {
					return notAllowed(arg)
				}
				if d := cb(event{"opt", "--" + name, ""}); d != nil {
					return d
				}
			default:
				return notAllowed(arg)
			}
		case arg == "-":
			if d := cb(event{"pos", "", "-"}); d != nil {
				return d
			}
		case strings.HasPrefix(arg, "-"):
			rest := arg[1:]
			for rest != "" {
				ch := rest[:1]
				rest = rest[1:]
				switch {
				case strings.Contains(s.svalues, ch):
					if rest != "" {
						if d := cb(event{"opt", "-" + ch, rest}); d != nil {
							return d
						}
					} else {
						need = "-" + ch
					}
					rest = ""
				case strings.Contains(s.soptvals, ch):
					if d := cb(event{"opt", "-" + ch, rest}); d != nil {
						return d
					}
					rest = ""
				case strings.Contains(s.sflags, ch):
					if d := cb(event{"opt", "-" + ch, ""}); d != nil {
						return d
					}
				default:
					return notAllowed(arg)
				}
			}
		default:
			if d := cb(event{"pos", "", arg}); d != nil {
				return d
			}
		}
	}
	// 値を取るオプションで終わった（値が無い）。git もエラーにするが、読み違えないよう断る。
	if need != "" {
		return notAllowed(need)
	}
	return nil
}
