import { test } from "node:test";
import assert from "node:assert/strict";
import { ticketControlFrom, ticketControlMismatch } from "../src/core/ticket-control.js";

function env(value: string | undefined): string {
  return JSON.stringify({ env: value === undefined ? {} : { CCNAVI_TICKET_CONTROL: value } });
}

test("CB-T55 書いていなければ enable", () => {
  assert.equal(ticketControlFrom({ settings: undefined, local: undefined }), "enable");
  assert.equal(ticketControlFrom({ settings: env(undefined), local: undefined }), "enable");
  assert.equal(ticketControlFrom({ settings: "{}", local: undefined }), "enable");
  assert.equal(ticketControlFrom({ settings: "broken", local: undefined }), "enable");
});

test("CB-T56 settings.json の disable で切れる。大文字と前後の空白は許す", () => {
  assert.equal(ticketControlFrom({ settings: env("disable"), local: undefined }), "disable");
  assert.equal(ticketControlFrom({ settings: env(" Disable "), local: undefined }), "disable");
  assert.equal(ticketControlFrom({ settings: env("enable"), local: undefined }), "enable");
});

test("CB-T57 読めない値は enable に倒す（ccnavi の解決と同じ向き）", () => {
  assert.equal(ticketControlFrom({ settings: env("off"), local: undefined }), "enable");
  assert.equal(ticketControlFrom({ settings: env(""), local: undefined }), "enable");
});

test("CB-T58 settings.local.json が settings.json に勝つ。local が空なら settings を見る", () => {
  assert.equal(ticketControlFrom({ settings: env("disable"), local: env("enable") }), "enable");
  assert.equal(ticketControlFrom({ settings: env("enable"), local: env("disable") }), "disable");
  assert.equal(ticketControlFrom({ settings: env("disable"), local: env(undefined) }), "disable");
  assert.equal(ticketControlFrom({ settings: env("disable"), local: env("off") }), "disable");
});

test("CB-T59 実行ファイルの答えと食い違えば言う。空（古い実行ファイル）は問わない", () => {
  assert.equal(ticketControlMismatch("enable", "enable"), "");
  assert.equal(ticketControlMismatch("enable", ""), "");
  assert.match(ticketControlMismatch("enable", "disable"), /設定ファイル: enable、実行ファイル: disable/);
  assert.match(ticketControlMismatch("disable", "enable"), /CCNAVI_TICKET_CONTROL/);
});
