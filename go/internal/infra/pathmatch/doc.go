// Package pathmatch は、書き込み先・削除先までの道にリンク（シンボリックリンクと junction）があるかを調べる。
// 道をたどってリンクを探す判定は、必ずここを通す。ルールの glob を式にするのは pyre.TranslateGlob。
//
// Python からの対応: pathsafe の is_link・has_link・scan・first_link・
// escapes → IsLink・HasLink・Scan・FirstLink・Escapes。
package pathmatch
