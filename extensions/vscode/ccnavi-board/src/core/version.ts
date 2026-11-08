/**
 * 拡張が頼る実行ファイルの契約の版。go/internal/entry/versionのCompat、ccnavi-common.shのCCNAVI_COMPATと揃える
 * （scripts/ccnavi-package.shが配るときに見比べる）。起動のときに実行ファイルと比べはしない。
 * 上げ方はgo/internal/entry/versionのCompatの説明のとおり
 */
export const EXTENSION_COMPAT = 20;
