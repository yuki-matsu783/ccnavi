/**
 * 拡張が頼る実行ファイルの契約の版。src/ccnavi/entry/version.py の COMPAT、ccnavi-common.sh の CCNAVI_COMPAT と揃える
 * （scripts/ccnavi-package.sh が配るときに見比べる）。起動のときに実行ファイルと比べはしない。
 * 上げ方は src/ccnavi/entry/version.py の説明のとおり
 */
export const EXTENSION_COMPAT = 20;
