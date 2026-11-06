// Package platformtag は実行ファイルがどの機械向けか（`<os>-<arch>`）と、振り分けの sh
// （ccnavi-launcher.sh）が実際に起動する実行ファイルを答える。
//
// 語は scripts/ccnavi-setup.sh の host_target、.ccnavi/scripts/ccnavi-launcher.sh、VS Code 拡張の
// hostTarget と揃える。どれかだけ変えると、配った場所と探す場所が食い違う。探す順も sh と同じにする
// （自己防衛が守る実体と、hook が走らせる実体を同じにするため）。
package platformtag
