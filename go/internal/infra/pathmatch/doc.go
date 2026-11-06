// Package pathmatch はパスを照らし合わせる。ルールの glob を正規表現にすること（Translate）と、
// 書き込み先・削除先までの道にリンク（シンボリックリンクと junction）があるかを調べること。
// 道をたどってリンクを探す判定は、必ずここを通す。
package pathmatch
