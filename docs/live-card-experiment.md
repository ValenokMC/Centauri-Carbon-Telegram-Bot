# Live print card experiment (Bot API 10.4 widgets), November 2026

Telegram Bot API 10.4 (9 November 2026) added HTML widgets in messages
(`sendWidget`, `editMessageWidgetData`, `widget_query`). Release 1.2.0 shipped an
opt-in "live print card" built on them (`status_widget`). After two days of use
the owner decided to go back to the ordinary status message, and the card was
reverted. This page records what was tried, what went wrong and what to keep in
mind if widgets are tried again.

## What was built

- One widget per chat that redrew itself silently: rings for progress, nozzle
  and bed temperature, state, file, layer, time left.
- Every button inside the widget (`TelegramWidget.request()` → `widget_query` →
  `answerWidgetQuery` + `editMessageWidgetData`), with the same one-use
  confirmations as the inline buttons.
- Paged screens (files, macros, history, fans, speed, heating, diagnostics).
- A thin-line SVG icon set loaded once into the widget's `localStorage`.
- "Remove object": first a bed map drawn inside the widget, finally the old PNG
  bed picture sent above the card with a grid of the same numbers on the card.

## What went wrong

- **Telegram clients are not browsers.** Layouts that rendered correctly in
  Chrome broke in Telegram Desktop and on phones: parts of CSS grid
  (`grid-auto-flow`, `minmax`, `grid-column` with `nth-child`) were ignored, a
  rotated SVG ring turned around a different point, and rings stacked in a
  column instead of a row. The only reliable check is the real Telegram app,
  phone first.
- **Documented limits differ from the clients.** The guide allows up to 16
  blurred elements; the card was stopped ("widget stopped") with three. Two is
  the practical limit.
- **Arrays of numbers are corrupted in Telegram Desktop.** A page number arrived
  as `1.4` and a temperature as `245.245°`. Numbers inside lists must be sent as
  strings.
- **Data is 1 KB and code is 4 KB after zlib.** A bed with 56 objects does not
  fit; the readable bed map had to come back as a picture. The icon set did not
  fit into the code either and had to be fetched by the widget itself.
- **Widgets cannot be screenshotted.** Production widgets are protected content,
  so users cannot send a screenshot of a broken layout.
- **The library has no widget support yet.** With python-telegram-bot the
  methods go through `do_api_request`, and `widget_query` must be added to
  `allowed_updates` by hand.
- **Slow pictures.** A photo once took about 40 seconds: a keep-alive connection
  that Telegram had already closed made the upload wait for its timeout before
  a fresh connection succeeded. The fix (shorter idle reconnect, shorter upload
  timeout with an immediate retry) is in the unmerged branch
  `fix/faster-pictures`.
- **The finish photo** was taken after the bed lowered at the end of the print;
  it has to be taken before (fixed in the widget branch, never needed in the
  plain status message).

## If widgets are tried again

- Start with one small, display-only widget and look at it on a phone and in
  Telegram Desktop before adding any styling.
- Keep at most two blurred elements, use flex with fixed widths instead of
  complex grid, send numbers in lists as strings, and keep pictures as photos.
- Accept a visual change only by comparing it, in the real app, with the
  previous version on real data.
- The last widget code: tag `v1.2.0` (commit `9c8e90a`), branch
  `feature/live-print-widget`, and PR #12 (`fix/faster-pictures`).
