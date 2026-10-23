# XAUUSD Confluence Bot

A deployable gold bot that runs on a schedule with **zero uploads**. Each cycle it reads
the six timeframes (1W, 1D, 4H, 30m, 15m, 3m), derives the institutional posture from the
RSI structure, confirms it against COT, checks the news calendar, builds the trade
(entry / stop / TP1–TP3), and sends it to Telegram or WhatsApp — only when something
actionable changes.

It uses the exact indicator spec from the Confluence Desk: **RSI 9 (close) · SMA 14 of RSI
· Bollinger 2σ on the RSI · Liquidity Swings (length 14, wick extremity) · ATR 14**.

> **Not financial advice.** This is a rules-based decision aid. It cannot predict price and
> can be wrong. Keep a human approving every order — never let it auto-execute. Gold is
> leveraged and volatile; risk only what you can lose.

---

## How it works

```
                 ┌── data path:  Twelve Data candles ──► indicators ──┐
 six timeframes ─┤                                                    ├─► TFRead ×6 ─┐
                 └── vision path: chart screenshots ──► Claude vision ─┘             │
                                                                                     ▼
   COT (CFTC) ─┐                                                            confluence engine
   calendar  ──┼──► MacroRead ──────────────────────────────────────────►  (posture + trade)
               │                                                                     │
                                                                                     ▼
                                                              Telegram / WhatsApp alert
```

- **Posture** (ACCUMULATION / DISTRIBUTION / ROTATION) comes from 1W + 1D RSI trend and
  divergence, confirmed by COT. The weekly/daily decide where the banks sit.
- **Trigger** comes only from the execution timeframes (15m, 3m): a liquidity sweep or
  RSI reclaim **in the direction of posture**. No aligned trigger → it waits.
- **Stop** sits beyond the swept wick + an ATR-style buffer; **targets** are 1R / structural / 3R.

## Two data modes

| Mode | Set `SOURCE=` | Needs | Notes |
|------|---------------|-------|-------|
| **Data** (recommended) | `data` | `TWELVEDATA_API_KEY` | Pulls real OHLCV and computes everything precisely. True zero-touch. |
| **Vision** | `vision` | `ANTHROPIC_API_KEY` + images in `charts/` | Claude reads screenshots named `1W.png … 3m.png`. Use when you want it to read actual TradingView snapshots. You supply the images (drop them in `charts/`, or wire your own capture). |

The data mode is the one that runs with nothing to upload. Vision mode still needs the six
images to exist each run, so for hands-off use, prefer data mode.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env     # then fill it in
python selftest.py       # offline sanity check, no keys needed
python run_once.py       # one real cycle
```

**Keys you'll want:**
- **Twelve Data** (free): https://twelvedata.com → `TWELVEDATA_API_KEY`
- **Telegram**: message `@BotFather` → `/newbot` → `TELEGRAM_BOT_TOKEN`; then message your
  bot once and get your chat id from `https://api.telegram.org/bot<TOKEN>/getUpdates` → `TELEGRAM_CHAT_ID`
- **Anthropic** (only for vision mode): https://console.anthropic.com → `ANTHROPIC_API_KEY`

## Deploy (you host it — pick one)

**GitHub Actions cron (free, no server).** Push this repo to GitHub, add your keys under
*Settings → Secrets and variables → Actions*, and the workflow in
`.github/workflows/schedule.yml` runs every 15 min during London+NY hours. Easiest hands-off option.

**A small VPS / Render / Railway / Fly.** Run the long loop:
```bash
python run_loop.py       # cycles every RUN_EVERY_MIN minutes
```
On systemd, point a service at `run_loop.py`. On Render, use a Background Worker with that start command.

## Verifying the macro sources

- **COT** (`bot/cot.py`) downloads the CFTC Disaggregated Futures-Only history file and filters
  gold (contract `088691`) — the same market the Tradingster/A1 links track. It derives a
  stance from the week-over-week change in commercial (bank) net position. First run of a new
  year may fall back to the prior year's file until the current one is published.
- **Calendar** (`bot/calendar_check.py`) uses the weekly ForexFactory JSON mirror to flag a
  high-impact USD event inside `NEWS_WINDOW_MIN`. ForexFactory has no official API; if the
  mirror is down, the bot just proceeds without the brake and notes it.
- **USD stance** is left neutral by default. If you want it, wire a DXY or EUR/USD read into
  `gather_macro()` in `run_once.py`.

## Tuning

Everything is in `.env` / `config.py`: indicator lengths, `STOP_BUFFER_PCT`,
`MIN_CONVICTION`, the news window, and `ALERT_ON` (`actionable` vs `always`). The
confluence weights live at the top of `bot/engine.py`.

## Limits worth knowing

- No live feed is guaranteed accurate; verify levels on your own chart before acting.
- Spot-gold tick volume is often missing from data APIs, so the Liquidity-Swings volume tag
  may be zero — sweep detection itself doesn't need volume.
- LuxAlgo's exact code is closed-source; `find_pivots` / `detect_sweep` reproduce the
  actionable behaviour, not the pixels.
- The vision reader is as good as the screenshot; a clean chart with the RSI pane and
  Liquidity Swings visible reads best.

## Files

```
config.py              settings from env
run_once.py            one cycle (cron / Actions entrypoint)
run_loop.py            long-running scheduler (VPS)
selftest.py            offline check, no keys
bot/indicators.py      RSI/BB/ATR + liquidity swings + sweep + divergence
bot/engine.py          candles -> TFRead, and the confluence -> Signal
bot/data_price.py      Twelve Data OHLCV (data mode)
bot/vision.py          Claude reads chart screenshots (vision mode)
bot/cot.py             CFTC COT parser (gold 088691)
bot/calendar_check.py  high-impact USD news check
bot/alerts.py          Telegram + optional Twilio WhatsApp
bot/models.py          TFRead / MacroRead / Signal
```
