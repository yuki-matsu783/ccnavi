// Package small は、配る sh の小さい関数と記録の sh を、sh（と bash）を起こして確かめるテストの置き場である。
// tests/sh の test_diaglog_sh・test_ident_sh・test_family_sh・test_mask_url_sh・test_sh_quote_sh・test_branch_to_id_sh・
// test_host_kinds_sh・test_same_name_sh・test_sh_copies_sh・test_git_auth_sh を移した。
//
// 表（tests/fixtures の JSON）は、sh の答えと実行ファイルの答えを同じ表で見る。このパッケージは sh の側だけを見る。
// node と突き合わせるテストは、node が無い機械では飛ばす。
package small
