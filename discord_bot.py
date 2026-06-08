import discord
from discord import app_commands
import requests
import asyncio
import os
import json
import xml.etree.ElementTree as ET
from discord.ext import tasks
from datetime import datetime, timezone, timedelta
from trading import (
    execute_trade, monitor_trade, init_db, resume_open_trades,
    check_consecutive_losses, save_range, get_saved_ranges,
    reset_ranges_db, set_discord_client,
    get_balance, calculate_lot, OANDA_ACCOUNT_ID, OANDA_BASE_URL, get_headers,
)

# ===== CONFIGURATION =====
TOKEN = os.environ.get("DISCORD_TOKEN")
GUILD_ID = 1504090278955319306
CRYPTO_CHANNEL_ID  = 1504091492237709442
BOURSE_CHANNEL_ID  = 1504091655769297077
IMMO_CHANNEL_ID    = 1504091825898524793
ECONOMIE_CHANNEL_ID = 1504092088877318206
ANNONCES_CHANNEL_ID = 1504091330492629105
FOREX_CHANNEL_ID   = 1504441241650204712
TWELVE_DATA_KEY    = os.environ.get("TWELVE_DATA_KEY")
LOG_CHANNEL_ID     = 1504830528837259384
TRADES_EN_COURS_ID = 1505293491108974742
TRADES_FERMES_ID   = 1505293534050127943

# ===== PARAMÈTRES STRATÉGIE XAU/USD =====
PAIR           = "XAU/USD"
RISK_PERCENT   = 1
RR_RATIO       = 2.0
MAX_TRADES_DAY = 4
DAILY_LOSS_PCT = 2.0
EMA_PERIOD     = 50
RSI_BUY_MIN    = 43
RSI_SELL_MAX   = 55
ATR_MIN        = 3
ATR_MAX        = 32.0
IMPULSE_ATR_MULTI = 0.65
IMPULSE_BODY_PCT  = 0.5
RETEST_TOLERANCE  = 1.5
SWING_PERIOD   = 10
NEWS_BLACKOUT_MIN = 30

# Sessions (UTC)
LONDON_RANGE_START_H = 7
LONDON_RANGE_START_M = 0
LONDON_RANGE_END_H   = 8
LONDON_RANGE_END_M   = 0
LONDON_OPEN          = 8
LONDON_CLOSE         = 11
NY_RANGE_START_H     = 13
NY_RANGE_START_M     = 0
NY_RANGE_END_H       = 13
NY_RANGE_END_M       = 35
NY_OPEN              = 13
NY_OPEN_MIN          = 35
NY_CLOSE             = 17
RANGE_CANDLES        = 12

# Variables globales
daily_trades    = 0
daily_loss      = 0.0
daily_reset_day = None
range_london    = {"high": None, "low": None, "built": False}
range_ny        = {"high": None, "low": None, "built": False}
retest_state    = {"waiting": False, "direction": None, "level": None, "session": None}

# ===== CRYPTOS / BOURSE =====
CRYPTOS = ["bitcoin", "ethereum", "binancecoin", "solana", "ripple", "cardano", "avalanche-2", "polkadot", "chainlink", "tether"]
STOCKS  = {
    "SMI": "^SSMI", "S&P 500": "^GSPC", "NASDAQ": "^IXIC",
    "DAX": "^GDAXI", "CAC 40": "^FCHI", "FTSE 100": "^FTSE",
    "Nestlé": "NESN.SW", "Novartis": "NOVN.SW", "UBS": "UBSG.SW",
    "Roche": "ROG.SW", "ABB": "ABBN.SW"
}
IMMO_FEEDS = [
    {"url": "https://kill-the-newsletter.com/feeds/8d441x04fdvm9yyftfl0.xml", "source": "🏠 Homegate"},
    {"url": "https://kill-the-newsletter.com/feeds/ufw1axyrund81i3qgr0s.xml", "source": "🏠 ImmoScout24"},
    {"url": "https://kill-the-newsletter.com/feeds/vowvpajj4gdz2s20n92h.xml", "source": "🏠 Newhome"},
]
ECONOMIE_FEEDS = [
    {"url": "https://www.snb.ch/public/en/rss/pressrel",          "source": "🏦 BNS",          "emoji": "🏦"},
    {"url": "https://www.lemonde.fr/economie/rss_full.xml",        "source": "📰 Le Monde",      "emoji": "📰"},
    {"url": "https://www.cash.ch/feeds/latest/news",               "source": "📊 Cash.ch",       "emoji": "📊"},
    {"url": "https://www.lefigaro.fr/rss/figaro_economie.xml",     "source": "📰 Le Figaro",     "emoji": "📰"},
    {"url": "https://feeds.bbci.co.uk/news/business/rss.xml",      "source": "📺 BBC Business",  "emoji": "📺"},
    {"url": "https://cointelegraph.com/rss",                       "source": "📰 CoinTelegraph", "emoji": "📰"},
]
SEEN_IMMO_FILE     = "seen_immo.json"
SEEN_ECONOMIE_FILE = "seen_economie.json"

intents = discord.Intents.default()
client  = discord.Client(intents=intents)
tree    = app_commands.CommandTree(client)

# ===== HELPERS =====
def now_utc():
    return datetime.now(timezone.utc)

async def log(msg):
    ch = client.get_channel(LOG_CHANNEL_ID)
    if ch:
        await ch.send(f"`{now_utc().strftime('%H:%M:%S')}` {msg}")
    print(msg)

# ===== FILTRE NEWS =====
def is_news_blackout():
    try:
        major_keywords = ["NFP", "CPI", "FOMC", "Fed Rate", "Interest Rate",
                          "Non-Farm", "Inflation", "GDP", "Federal Reserve",
                          "Unemployment", "Retail Sales", "PMI"]
        url  = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        r    = requests.get(url, timeout=5)
        if r.status_code != 200:
            return False
        events = r.json()
        now    = now_utc()
        for event in events:
            if event.get("country") != "USD":
                continue
            if event.get("impact") not in ["High", "3"]:
                continue
            title = event.get("title", "").upper()
            if not any(kw.upper() in title for kw in major_keywords):
                continue
            try:
                event_time_str = event.get("date", "")
                event_time     = datetime.fromisoformat(event_time_str.replace("Z", "+00:00"))
                diff_minutes   = (event_time - now).total_seconds() / 60
                if -NEWS_BLACKOUT_MIN <= diff_minutes <= NEWS_BLACKOUT_MIN:
                    return True
            except:
                continue
        return False
    except Exception as e:
        print(f"Erreur filtre news: {e}")
        return False

# ===== DONNÉES XAU/USD =====
def get_candles(interval="5min", outputsize=100):
    try:
        url  = f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval={interval}&outputsize={outputsize}&apikey={TWELVE_DATA_KEY}"
        r    = requests.get(url, timeout=10)
        data = r.json()
        if "values" not in data:
            return None
        candles = list(reversed(data["values"]))
        return {
            "closes":     [float(c["close"])  for c in candles],
            "highs":      [float(c["high"])   for c in candles],
            "lows":       [float(c["low"])    for c in candles],
            "opens":      [float(c["open"])   for c in candles],
            "timestamps": [c["datetime"]      for c in candles],
            "current":    float(candles[-1]["close"]),
        }
    except Exception as e:
        print(f"Erreur candles: {e}")
        return None

# ===== INDICATEURS =====
def calc_ema(closes, period):
    if len(closes) < period:
        return None
    k   = 2.0 / (period + 1)
    ema = closes[0]
    for p in closes[1:]:
        ema = p * k + ema * (1 - k)
    return ema

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_g  = sum(gains[:period]) / period
    avg_l  = sum(losses[:period]) / period
    if avg_l == 0:
        return 100
    return 100 - (100 / (1 + avg_g / avg_l))

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return 1.0
    trs = []
    for i in range(-period, 0):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    return sum(trs) / period

# ===== TENDANCE H1 =====
def get_h1_trend():
    data = get_candles(interval="1h", outputsize=60)
    if not data:
        return None
    closes  = data["closes"]
    highs   = data["highs"]
    lows    = data["lows"]
    current = data["current"]
    ema50   = calc_ema(closes, EMA_PERIOD)
    if not ema50:
        return None
    # FIX: garde contre crash max([]) quand données insuffisantes
    n = min(SWING_PERIOD, len(highs) - 1)
    if n < 2:
        return None
    recent_highs = highs[-n:]
    recent_lows  = lows[-n:]
    hh = recent_highs[-1] > max(recent_highs[:-1])
    hl = recent_lows[-1]  > min(recent_lows[:-1])
    ll = recent_lows[-1]  < min(recent_lows[:-1])
    lh = recent_highs[-1] < max(recent_highs[:-1])
    if current > ema50 and (hh or hl):
        return "BUY"
    elif current < ema50 and (ll or lh):
        return "SELL"
    return None

# ===== CONSTRUCTION RANGE =====
def build_range_from_candles(data_m5, sh, sm, eh, em):
    rh = rl = None
    for i, ts in enumerate(data_m5["timestamps"]):
        try:
            dt = datetime.fromisoformat(ts.replace(" ", "T"))
            h, m = dt.hour, dt.minute
            in_r = (h == sh and m >= sm) or (sh < h < eh) or (h == eh and m < em)
            if in_r:
                if rh is None or data_m5["highs"][i] > rh: rh = data_m5["highs"][i]
                if rl is None or data_m5["lows"][i]  < rl: rl = data_m5["lows"][i]
        except:
            continue
    return rh, rl

# ===== BOUGIE IMPULSIVE =====
def is_impulse(o, h, l, c, atr):
    body    = abs(c - o)
    range_c = h - l
    if range_c == 0:
        return False
    return body >= IMPULSE_ATR_MULTI * atr and (body / range_c) >= IMPULSE_BODY_PCT

# ===== FILTRE ANNULATION =====
def is_cancellation_signal(data_m5, direction, level, atr):
    closes  = data_m5["closes"]
    highs   = data_m5["highs"]
    lows    = data_m5["lows"]
    opens   = data_m5["opens"]
    current = data_m5["current"]
    rsi     = calc_rsi(closes)

    if direction == "BUY" and current < level:
        return True, "Fausse cassure — prix revenu sous le niveau"
    if direction == "SELL" and current > level:
        return True, "Fausse cassure — prix revenu sur le niveau"

    last_body = abs(closes[-1] - opens[-1])
    if direction == "BUY" and closes[-1] < opens[-1] and last_body >= atr * 1.8:
        return True, "Bougie baissière forte détectée"
    if direction == "SELL" and closes[-1] > opens[-1] and last_body >= atr * 1.8:
        return True, "Bougie haussière forte détectée"

    if direction == "BUY"  and rsi < 45:
        return True, f"RSI perdu ({rsi:.1f} < 45)"
    if direction == "SELL" and rsi > 55:
        return True, f"RSI perdu ({rsi:.1f} > 55)"

    current_trend = get_h1_trend()
    if current_trend and current_trend != direction:
        return True, f"Tendance H1 inversée → {current_trend} détectée, annulation {direction}"

    return False, None

# ===== RETEST =====
def detect_retest(data_m5, level, direction, atr):
    closes  = data_m5["closes"]
    highs   = data_m5["highs"]
    lows    = data_m5["lows"]
    opens   = data_m5["opens"]
    current = data_m5["current"]
    tol     = atr * RETEST_TOLERANCE

    if direction == "BUY":
        near   = abs(current - level) <= tol or lows[-1] <= level <= closes[-1]
        reject = closes[-1] > opens[-1] and lows[-1] <= level + tol
        return near and reject
    elif direction == "SELL":
        near   = abs(current - level) <= tol or closes[-1] <= level <= highs[-1]
        reject = closes[-1] < opens[-1] and highs[-1] >= level - tol
        return near and reject
    return False

# ===== SESSION =====
def get_current_session():
    now = now_utc()
    h   = now.hour
    m   = now.minute
    dow = now.weekday()
    if dow >= 5:
        return None
    if LONDON_OPEN <= h < LONDON_CLOSE:
        return "London"
    if (h == NY_OPEN and m >= NY_OPEN_MIN) or (NY_OPEN < h < NY_CLOSE):
        return "New York"
    return None

def build_range_safe(session_name, range_start_h, range_start_m, range_end_h, range_end_m):
    data = get_candles(interval="5min", outputsize=100)
    if not data or not data["highs"]:
        return None, None

    rh, rl = build_range_from_candles(data, range_start_h, range_start_m, range_end_h, range_end_m)
    if rh and rl and (rh - rl) > 5:
        return rh, rl

    num_candles = RANGE_CANDLES if session_name == "London" else 6
    highs = data["highs"][-num_candles:]
    lows  = data["lows"][-num_candles:]
    rh    = max(highs)
    rl    = min(lows)
    if (rh - rl) > 5:
        return rh, rl

    return None, None

# ===== LIMITES JOURNALIÈRES =====
def check_daily_limits():
    global daily_trades, daily_loss, daily_reset_day
    today = now_utc().date()
    if daily_reset_day != today:
        daily_trades    = 0
        daily_loss      = 0.0
        daily_reset_day = today
    if daily_trades >= MAX_TRADES_DAY:
        return False, f"Max {MAX_TRADES_DAY} trades/jour atteint"
    if daily_loss <= -DAILY_LOSS_PCT:
        return False, f"Stop loss journalier -{DAILY_LOSS_PCT}% atteint"
    return True, "OK"

# ===== ANALYSE XAU/USD =====
async def analyze_xauusd():
    global retest_state, daily_trades

    session = get_current_session()
    await log(f"🔍 DEBUG [1] Analyse démarrée - Session: {session or 'Aucune'} | Heure UTC: {now_utc().strftime('%H:%M:%S')}")

    if not session:
        await log("❌ DEBUG [2] Pas dans une session autorisée (Londres/NY)")
        return None

    ok, reason = check_daily_limits()
    if not ok:
        await log(f"❌ DEBUG [3] Limites journalières bloquées: {reason}")
        return None

    if not check_consecutive_losses():
        await log("❌ DEBUG [4] Stop à cause de pertes consécutives")
        return None

    if is_news_blackout():
        await log("❌ DEBUG [5] Blackout news actif")
        return None

    data_m5  = get_candles(interval="5min",  outputsize=100)
    data_m15 = get_candles(interval="15min", outputsize=30)
    if not data_m5 or not data_m15:
        await log("❌ DEBUG [6] Impossible de récupérer les données candles")
        return None

    atr_m15 = calc_atr(data_m15["highs"], data_m15["lows"], data_m15["closes"])
    await log(f"🔍 DEBUG [7] ATR M15 = {atr_m15:.2f} (Min:{ATR_MIN} - Max:{ATR_MAX})")

    if atr_m15 < ATR_MIN or atr_m15 > ATR_MAX:
        await log(f"❌ DEBUG [8] ATR hors limites → arrêt")
        return None

    trend = get_h1_trend()
    await log(f"🔍 DEBUG [9] Tendance H1 détectée : {trend or 'NEUTRE'}")

    if not trend:
        await log("❌ DEBUG [10] Pas de tendance H1 valide (EMA50 + structure)")
        return None

    r = range_london if session == "London" else range_ny
    if not r["built"] or r["high"] is None:
        await log(f"❌ DEBUG [11] Range {session} non disponible")
        return None

    await log(f"🔍 DEBUG [12] Range OK → High: {r['high']:.2f} | Low: {r['low']:.2f}")

    closes  = data_m5["closes"]
    highs   = data_m5["highs"]
    lows    = data_m5["lows"]
    opens   = data_m5["opens"]
    current = data_m5["current"]
    atr_m5  = calc_atr(highs, lows, closes)
    rsi     = calc_rsi(closes)

    await log(f"🔍 DEBUG [13] RSI M5 = {rsi:.1f} | Prix actuel = {current:.2f}")

    range_high = r["high"]
    range_low  = r["low"]
    lc = closes[-1]
    lo = opens[-1]
    lh = highs[-1]
    ll = lows[-1]

    direction = None
    level     = None

    if trend == "BUY" and lc > range_high and is_impulse(lo, lh, ll, lc, atr_m5) and rsi > RSI_BUY_MIN:
        direction = "BUY"
        level     = range_high
        await log(f"🔍 DEBUG [14] Conditions BUY breakout remplies")
    elif trend == "SELL" and lc < range_low and is_impulse(lo, lh, ll, lc, atr_m5) and rsi < RSI_SELL_MAX:
        direction = "SELL"
        level     = range_low
        await log(f"🔍 DEBUG [14] Conditions SELL breakout remplies")

    if direction and not retest_state["waiting"]:
        retest_state = {"waiting": True, "direction": direction, "level": level, "session": session}
        await log(f"📊 Breakout {direction} XAU/USD @ {current:.2f} | Attente retest {level:.2f}")
        await log(f"🔍 DEBUG [14b] retest_state={retest_state}")
        return None

    if retest_state["waiting"] and retest_state["session"] == session:
        direction = retest_state["direction"]
        level     = retest_state["level"]

        cancelled, reason = is_cancellation_signal(data_m5, direction, level, atr_m5)
        if cancelled:
            retest_state = {"waiting": False, "direction": None, "level": None, "session": None}
            await log(f"❌ Setup annulé — {reason}")
            return None

        tol = atr_m5 * RETEST_TOLERANCE
        await log(f"🔍 DEBUG [15a] current={current:.2f} | level={level:.2f} | tol={tol:.2f} | low[-1]={data_m5['lows'][-1]:.2f} | close[-1]={data_m5['closes'][-1]:.2f}")

        if detect_retest(data_m5, level, direction, atr_m5):
            retest_state = {"waiting": False, "direction": None, "level": None, "session": None}

            if direction == "BUY":
                sl  = round(min(lows[-5:]) - atr_m5 * 1.5, 2)
                tp1 = round(current + (current - sl),     2)
                tp2 = round(current + (current - sl) * 2, 2)
                tp3 = round(current + (current - sl) * 3, 2)
            else:
                sl  = round(max(highs[-5:]) + atr_m5 * 1.5, 2)
                tp1 = round(current - (sl - current),     2)
                tp2 = round(current - (sl - current) * 2, 2)
                tp3 = round(current - (sl - current) * 3, 2)

            sl_distance = abs(current - sl)

            await log(f"✅ DEBUG [15] Setup VALIDÉ → Entrée {direction} !")
            return {
                "pair":      PAIR,
                "direction": direction,
                "entry":     round(current, 2),
                "sl":        sl,
                "tp":        tp2,
                "tp1":       tp1,
                "tp2":       tp2,
                "tp3":       tp3,
                "sl_pips":   sl_distance,
                "score":     100,
                "session":   session,
                "rsi":       rsi,
                "atr":       atr_m5,
            }

    return None

# ===== RETEST TOUTES LES MINUTES =====
@tasks.loop(minutes=1)
async def check_retest_minutely():
    global retest_state, daily_trades

    if now_utc().weekday() >= 5:
        return
    if not get_current_session():
        return
    if not retest_state["waiting"]:
        return

    session   = get_current_session()
    direction = retest_state["direction"]
    level     = retest_state["level"]

    if retest_state["session"] != session:
        return

    try:
        url    = f"{OANDA_BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/pricing?instruments=XAU_USD"
        r      = requests.get(url, headers=get_headers(), timeout=5)
        if r.status_code != 200:
            return
        prices = r.json().get("prices", [{}])[0]
        # FIX: BUY s'exécute au prix ask, SELL au prix bid
        if direction == "BUY":
            current = float(prices.get("asks", [{"price": "0"}])[0]["price"])
        else:
            current = float(prices.get("bids", [{"price": "0"}])[0]["price"])

        if current == 0:
            return

        atr_approx = 5.0
        tol        = atr_approx * RETEST_TOLERANCE
        near       = abs(current - level) <= tol

        if near:
            await log(f"⚡ RETEST MINUTAIRE détecté | {direction} | current={current:.2f} | level={level:.2f} | tol={tol:.2f}")
            data_m5 = get_candles(interval="5min", outputsize=100)
            if not data_m5:
                return

            atr_m5 = calc_atr(data_m5["highs"], data_m5["lows"], data_m5["closes"])

            cancelled, reason = is_cancellation_signal(data_m5, direction, level, atr_m5)
            if cancelled:
                retest_state = {"waiting": False, "direction": None, "level": None, "session": None}
                await log(f"❌ Setup annulé (minutaire) — {reason}")
                return

            if detect_retest(data_m5, level, direction, atr_m5):
                retest_state = {"waiting": False, "direction": None, "level": None, "session": None}

                closes  = data_m5["closes"]
                highs   = data_m5["highs"]
                lows    = data_m5["lows"]
                rsi     = calc_rsi(closes)

                if direction == "BUY":
                    sl  = round(min(lows[-5:]) - atr_m5 * 1.5, 2)
                    tp1 = round(current + (current - sl),     2)
                    tp2 = round(current + (current - sl) * 2, 2)
                    tp3 = round(current + (current - sl) * 3, 2)
                else:
                    sl  = round(max(highs[-5:]) + atr_m5 * 1.5, 2)
                    tp1 = round(current - (sl - current),     2)
                    tp2 = round(current - (sl - current) * 2, 2)
                    tp3 = round(current - (sl - current) * 3, 2)

                sl_distance = abs(current - sl)
                analysis = {
                    "pair":      PAIR,
                    "direction": direction,
                    "entry":     round(current, 2),
                    "sl":        sl,
                    "tp":        tp2,
                    "tp1":       tp1,
                    "tp2":       tp2,
                    "tp3":       tp3,
                    "sl_pips":   sl_distance,
                    "score":     100,
                    "session":   session,
                    "rsi":       rsi,
                    "atr":       atr_m5,
                }

                await log(f"✅ RETEST MINUTAIRE VALIDÉ → Entrée {direction} @ {current:.2f}")

                forex_channel = client.get_channel(FOREX_CHANNEL_ID)
                if forex_channel:
                    emoji  = "🟢" if direction == "BUY" else "🔴"
                    action = "ACHAT" if direction == "BUY" else "VENTE"
                    balance     = get_balance()
                    lot_size, _ = calculate_lot(balance, sl_distance, "XAU_USD")
                    embed = discord.Embed(
                        title=f"{emoji} {action} — 🥇 Or / Dollar (XAU/USD)",
                        color=0x2ECC71 if direction == "BUY" else 0xE74C3C
                    )
                    embed.add_field(
                        name="​",
                        value=(
                            f"```\n"
                            f"Entry  : {analysis['entry']:.2f}\n"
                            f"SL     : {analysis['sl']:.2f}\n"
                            f"TP1    : {analysis['tp1']:.2f}\n"
                            f"TP2    : {analysis['tp2']:.2f}\n"
                            f"TP3    : {analysis['tp3']:.2f}\n"
                            f"Lot    : {lot_size}\n"
                            f"RSI    : {analysis['rsi']:.1f}\n"
                            f"```"
                        ),
                        inline=False
                    )
                    embed.set_footer(text=f"AlphaValais • {now_utc().strftime('%d.%m.%Y %H:%M')} • Session {session} ⚡ Retest 1min")
                    await forex_channel.send(embed=embed)

                channels     = {"trades_en_cours": client.get_channel(TRADES_EN_COURS_ID), "trades_fermes": client.get_channel(TRADES_FERMES_ID)}
                trade_result = await execute_trade(analysis, channels)
                if trade_result:
                    daily_trades += 1
                    await log(f"✅ Trade ouvert (minutaire) — {direction} | ID: {trade_result['contract_id']}")

    except Exception as e:
        await log(f"❌ Erreur check_retest_minutely: `{e}`")

def build_signal_bar(score, max_score=100):
    filled = int((score / max_score) * 10)
    return "█" * filled + "░" * (10 - filled)

# ===== FONCTIONS CRYPTO/BOURSE/RSS =====
def get_crypto_prices():
    try:
        ids = ",".join(CRYPTOS)
        r   = requests.get(f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=chf&include_24hr_change=true", timeout=10)
        if r.status_code == 200 and r.json():
            return r.json()
    except:
        pass
    try:
        results = {}
        bmap = {"bitcoin": "BTCUSDT", "ethereum": "ETHUSDT", "binancecoin": "BNBUSDT", "solana": "SOLUSDT", "ripple": "XRPUSDT", "cardano": "ADAUSDT", "avalanche-2": "AVAXUSDT", "polkadot": "DOTUSDT", "chainlink": "LINKUSDT", "tether": "USDTUSDT"}
        chf = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=chf", timeout=5).json().get("tether", {}).get("chf", 0.9)
        for coin, sym in bmap.items():
            if sym == "USDTUSDT":
                results[coin] = {"chf": round(chf, 4), "chf_24h_change": 0}
                continue
            r = requests.get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={sym}", timeout=5)
            if r.status_code == 200:
                d = r.json()
                results[coin] = {"chf": round(float(d.get("lastPrice", 0)) * chf, 2), "chf_24h_change": round(float(d.get("priceChangePercent", 0)), 2)}
        return results
    except:
        return {}

def get_stock_prices():
    results = {}
    for name, symbol in STOCKS.items():
        try:
            r    = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            data = r.json()
            price  = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
            prev   = data["chart"]["result"][0]["meta"]["chartPreviousClose"]
            results[name] = {"price": price, "change": ((price - prev) / prev) * 100}
        except:
            results[name] = {"price": 0, "change": 0}
    return results

def load_seen(filename):
    try:
        with open(filename, "r") as f:
            return set(json.load(f))
    except:
        return set()

def save_seen(seen, filename):
    with open(filename, "w") as f:
        json.dump(list(seen), f)

def parse_rss(feed, max_items=5):
    results = []
    try:
        r = requests.get(feed["url"], headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        if r.status_code != 200:
            return results
        root = ET.fromstring(r.content)
        if "http://www.w3.org/2005/Atom" in root.tag:
            ns = "{http://www.w3.org/2005/Atom}"
            for item in root.findall(f"{ns}entry")[:max_items]:
                title   = item.findtext(f"{ns}title", "N/A")
                link_el = item.find(f"{ns}link[@rel='alternate']")
                link    = link_el.get("href", "") if link_el is not None else ""
                guid    = item.findtext(f"{ns}id", link)
                results.append({"id": f"{feed['source']}_{guid}", "source": feed["source"], "emoji": feed.get("emoji", "📰"), "titre": title, "description": "", "url": link})
        else:
            channel = root.find("channel")
            if channel is None:
                return results
            for item in channel.findall("item")[:max_items]:
                title = item.findtext("title", "N/A")
                link  = item.findtext("link", "")
                desc  = item.findtext("description", "")
                guid  = item.findtext("guid", link)
                results.append({"id": f"{feed['source']}_{guid}", "source": feed["source"], "emoji": feed.get("emoji", "📰"), "titre": title, "description": desc[:300] if desc else "", "url": link})
    except Exception as e:
        print(f"Erreur RSS {feed['source']}: {e}")
    return results

# ===== COMMANDES SLASH =====
@tree.command(name="maintenance", description="Envoie un message de maintenance", guild=discord.Object(id=GUILD_ID))
@app_commands.checks.has_permissions(administrator=True)
async def maintenance(interaction: discord.Interaction):
    channel = client.get_channel(ANNONCES_CHANNEL_ID)
    if channel:
        embed = discord.Embed(title="🔧 Maintenance en cours — AlphaValais", color=0xE67E22)
        embed.description = "Maintenance en cours. Les alertes sont temporairement suspendues. Merci pour votre patience. 🏔️❤️\n\n— L'équipe AlphaValais"
        embed.set_footer(text=f"AlphaValais • {now_utc().strftime('%d.%m.%Y %H:%M')}")
        await channel.send(embed=embed)
        await interaction.response.send_message("✅ Message envoyé !", ephemeral=True)

@tree.command(name="finmaintenance", description="Annonce la fin de maintenance", guild=discord.Object(id=GUILD_ID))
@app_commands.checks.has_permissions(administrator=True)
async def finmaintenance(interaction: discord.Interaction):
    channel = client.get_channel(ANNONCES_CHANNEL_ID)
    if channel:
        embed = discord.Embed(title="✅ Maintenance terminée — AlphaValais", color=0x2ECC71)
        embed.description = "La maintenance est terminée ! 🎉 Tous les services sont de nouveau opérationnels.\n\nBons investissements ! 🏔️📈\n\n— L'équipe AlphaValais"
        embed.set_footer(text=f"AlphaValais • {now_utc().strftime('%d.%m.%Y %H:%M')}")
        await channel.send(embed=embed)
        await interaction.response.send_message("✅ Message envoyé !", ephemeral=True)

@tree.command(name="avertissement", description="Envoie un avertissement", guild=discord.Object(id=GUILD_ID))
@app_commands.checks.has_permissions(administrator=True)
async def avertissement(interaction: discord.Interaction, membre: discord.Member):
    channel = client.get_channel(ANNONCES_CHANNEL_ID)
    if channel:
        embed = discord.Embed(title="⚠️ Avertissement — AlphaValais", color=0xE74C3C)
        embed.description = f"Cher(e) {membre.mention},\n\nNous te demandons de relire les règles dans <#1504091258061742130>.\n\n⚠️ **En cas de récidive, des sanctions seront appliquées.**\n\n— L'équipe AlphaValais"
        embed.set_footer(text=f"AlphaValais • {now_utc().strftime('%d.%m.%Y %H:%M')}")
        await channel.send(embed=embed)
        await interaction.response.send_message("✅ Avertissement envoyé !", ephemeral=True)

@tree.command(name="testrade", description="Test complet du circuit OANDA", guild=discord.Object(id=GUILD_ID))
@app_commands.checks.has_permissions(administrator=True)
async def testrade(interaction: discord.Interaction):
    await interaction.response.send_message("🔌 Test OANDA en cours...", ephemeral=True)
    channels    = {"trades_en_cours": client.get_channel(TRADES_EN_COURS_ID), "trades_fermes": client.get_channel(TRADES_FERMES_ID)}
    signal_data = {"pair": "XAU/USD", "direction": "BUY", "entry": 4530.00, "sl": 4510.00, "tp": 4570.00, "tp1": 4550.00, "tp2": 4570.00, "tp3": 4590.00, "sl_pips": 20, "score": 100, "session": "TEST", "rsi": 50, "atr": 5.0}
    await log("🧪 Test trade lancé par admin")
    trade_result = await execute_trade(signal_data, channels)
    if trade_result:
        await interaction.followup.send(f"✅ Trade test ouvert ! ID: `{trade_result['contract_id']}`", ephemeral=True)
        await log(f"✅ Test trade ouvert | ID: {trade_result['contract_id']}")
    else:
        await interaction.followup.send("❌ Échec — vérifier OANDA_API_KEY", ephemeral=True)
        await log("❌ Test trade échoué")

@tree.command(name="stats", description="Affiche les statistiques du bot", guild=discord.Object(id=GUILD_ID))
@app_commands.checks.has_permissions(administrator=True)
async def stats(interaction: discord.Interaction):
    try:
        from trading import get_db
        conn = get_db()
        if not conn:
            await interaction.response.send_message("❌ DB non connectée", ephemeral=True)
            return
        cur  = conn.cursor()
        cur.execute("SELECT COUNT(*), SUM(profit), COUNT(CASE WHEN profit > 0 THEN 1 END) FROM trades WHERE status = 'CLOSED'")
        row  = cur.fetchone()
        cur.close()
        conn.close()
        nb    = row[0] or 0
        total = row[1] or 0
        wins  = row[2] or 0
        wr    = (wins / nb * 100) if nb > 0 else 0
        embed = discord.Embed(title="📊 Statistiques AlphaValais Bot", color=0x2ECC71)
        embed.add_field(name="Trades total",  value=str(nb),            inline=True)
        embed.add_field(name="Profit net",    value=f"{total:+.2f}",    inline=True)
        embed.add_field(name="Win rate",      value=f"{wr:.1f}%",       inline=True)
        embed.add_field(name="Trades gagnés", value=str(wins),          inline=True)
        embed.add_field(name="Trades perdus", value=str(nb - wins),     inline=True)
        embed.set_footer(text=f"AlphaValais • {now_utc().strftime('%d.%m.%Y %H:%M')}")
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await interaction.response.send_message(f"❌ Erreur stats: {e}", ephemeral=True)

# ===== ALERTES TOUTES LES HEURES =====
@tasks.loop(hours=1)
async def send_alerts():
    weekend = now_utc().weekday() >= 5
    await log("🔄 Cycle alertes démarré")

    if not weekend:
        crypto_channel = client.get_channel(CRYPTO_CHANNEL_ID)
        if crypto_channel:
            try:
                data  = get_crypto_prices()
                names = {"bitcoin": "Bitcoin", "ethereum": "Ethereum", "binancecoin": "BNB", "solana": "Solana", "ripple": "XRP", "cardano": "Cardano", "avalanche-2": "Avalanche", "polkadot": "Polkadot", "chainlink": "Chainlink", "tether": "USDT"}
                if data:
                    embed = discord.Embed(title="📈 Alertes Crypto", color=0x00FF00)
                    for coin_id, name in names.items():
                        if coin_id in data:
                            price  = data[coin_id].get("chf", 0)
                            change = data[coin_id].get("chf_24h_change", 0)
                            embed.add_field(name=f"{'🟢' if change >= 0 else '🔴'} {name}", value=f"CHF {price:,.2f} ({change:+.2f}%)", inline=True)
                    await crypto_channel.send(embed=embed)
                    await log("✅ Alertes crypto envoyées")
            except Exception as e:
                await log(f"❌ Erreur CRYPTO: `{e}`")

    if not weekend:
        bourse_channel = client.get_channel(BOURSE_CHANNEL_ID)
        if bourse_channel:
            try:
                data  = get_stock_prices()
                embed = discord.Embed(title="💹 Alertes Bourse", color=0x0099FF)
                for name, info in data.items():
                    embed.add_field(name=f"{'🟢' if info['change'] >= 0 else '🔴'} {name}", value=f"{info['price']:,.2f} ({info['change']:+.2f}%)", inline=True)
                await bourse_channel.send(embed=embed)
                await log("✅ Alertes bourse envoyées")
            except Exception as e:
                await log(f"❌ Erreur BOURSE: `{e}`")

    immo_channel = client.get_channel(IMMO_CHANNEL_ID)
    if immo_channel:
        seen = load_seen(SEEN_IMMO_FILE)
        all_listings = []
        for feed in IMMO_FEEDS:
            try:
                all_listings.extend(parse_rss(feed))
            except Exception as e:
                await log(f"❌ Erreur IMMO: `{e}`")
        new_listings = [l for l in all_listings if l["id"] not in seen]
        await log(f"🏠 Immo: {len(new_listings)} nouvelles annonces")
        for listing in new_listings[:15]:
            embed = discord.Embed(title=f"{listing['source']} — {listing['titre']}", url=listing["url"], color=0x3498DB)
            if listing["description"]:
                embed.add_field(name="📝 Description", value=listing["description"], inline=False)
            embed.set_footer(text=f"AlphaValais Immo • {now_utc().strftime('%d.%m.%Y %H:%M')}")
            await immo_channel.send(embed=embed)
            seen.add(listing["id"])
            await asyncio.sleep(1)
        save_seen(seen, SEEN_IMMO_FILE)

    economie_channel = client.get_channel(ECONOMIE_CHANNEL_ID)
    if economie_channel:
        seen     = load_seen(SEEN_ECONOMIE_FILE)
        all_news = []
        for feed in ECONOMIE_FEEDS:
            try:
                all_news.extend(parse_rss(feed))
            except Exception as e:
                await log(f"❌ Erreur ÉCONOMIE: `{e}`")
        new_news = [n for n in all_news if n["id"] not in seen]
        await log(f"📰 Économie: {len(new_news)} nouvelles actualités")
        for news in new_news[:10]:
            embed = discord.Embed(title=f"{news['emoji']} {news['titre']}", url=news["url"], color=0xE74C3C)
            if news["description"]:
                embed.add_field(name="📝 Résumé", value=news["description"][:300], inline=False)
            embed.add_field(name="📰 Source", value=news["source"], inline=True)
            embed.set_footer(text=f"AlphaValais Économie • {now_utc().strftime('%d.%m.%Y %H:%M')}")
            await economie_channel.send(embed=embed)
            seen.add(news["id"])
            await asyncio.sleep(1)
        save_seen(seen, SEEN_ECONOMIE_FILE)

    await log("✅ Cycle alertes terminé")

# ===== CONSTRUCTION RANGES (toutes les 5 min) =====
@tasks.loop(minutes=5)
async def build_ranges():
    global range_london, range_ny
    now = now_utc()
    h   = now.hour
    m   = now.minute
    dow = now.weekday()
    if dow >= 5:
        return

    if h == 0 and m < 5:
        range_london = {"high": None, "low": None, "built": False}
        range_ny     = {"high": None, "low": None, "built": False}
        reset_ranges_db()
        await log("🔄 Reset ranges journalier effectué (mémoire + DB)")

    # ===== RANGE LONDRES =====
    if not range_london["built"]:
        if (h == 7 and m >= 30) or (h == 8 and m <= 29):
            data = get_candles(interval="5min", outputsize=30)
            if data:
                current_high = max(data["highs"][-12:])
                current_low  = min(data["lows"][-12:])
                if range_london["high"] is None or current_high > range_london["high"]:
                    range_london["high"] = current_high
                if range_london["low"] is None or current_low < range_london["low"]:
                    range_london["low"] = current_low

        if h == 8 and m >= 30:
            if range_london["high"] and range_london["low"]:
                range_london["built"] = True
                save_range("London", range_london["high"], range_london["low"])
                await log(f"📐 Range Londres finalisée | High: {range_london['high']:.2f} | Low: {range_london['low']:.2f}")
            else:
                await log("⚠️ Range Londres vide")

        # FIX: vérifie que le range n'a pas déjà été finalisé dans le bloc précédent
        if not range_london["built"] and LONDON_OPEN <= h < LONDON_CLOSE:
            rh, rl = build_range_safe("London", LONDON_RANGE_START_H, LONDON_RANGE_START_M,
                                      LONDON_RANGE_END_H, LONDON_RANGE_END_M)
            if rh and rl:
                range_london = {"high": rh, "low": rl, "built": True}
                save_range("London", rh, rl)
                await log(f"📐 Range Londres récupérée (tardif) | High: {rh:.2f} | Low: {rl:.2f}")

    # ===== RANGE NEW YORK =====
    if not range_ny["built"]:
        if h == 13 and m <= 29:
            data = get_candles(interval="5min", outputsize=30)
            if data:
                current_high = max(data["highs"][-6:])
                current_low  = min(data["lows"][-6:])
                if range_ny["high"] is None or current_high > range_ny["high"]:
                    range_ny["high"] = current_high
                if range_ny["low"] is None or current_low < range_ny["low"]:
                    range_ny["low"] = current_low

        if h == 13 and m >= 30:
            if range_ny["high"] and range_ny["low"]:
                range_ny["built"] = True
                save_range("NY", range_ny["high"], range_ny["low"])
                await log(f"📐 Range NY finalisée | High: {range_ny['high']:.2f} | Low: {range_ny['low']:.2f}")
            else:
                await log("⚠️ Range NY vide")

        # FIX: vérifie que le range n'a pas déjà été finalisé dans le bloc précédent
        if not range_ny["built"] and ((h == NY_OPEN and m >= NY_OPEN_MIN) or (NY_OPEN < h < NY_CLOSE)):
            rh, rl = build_range_safe("NY", NY_RANGE_START_H, NY_RANGE_START_M,
                                      NY_RANGE_END_H, NY_RANGE_END_M)
            if rh and rl:
                range_ny = {"high": rh, "low": rl, "built": True}
                save_range("NY", rh, rl)
                await log(f"📐 Range NY récupérée (tardif) | High: {rh:.2f} | Low: {rl:.2f}")

# ===== ANALYSE XAU/USD (toutes les 5 min) =====
@tasks.loop(minutes=5)
async def send_xauusd_signals():
    global daily_trades
    if now_utc().weekday() >= 5:
        return
    if not get_current_session():
        return
    await log(f"🔍 Analyse XAU/USD en cours... Session: {get_current_session()}")

    forex_channel = client.get_channel(FOREX_CHANNEL_ID)
    if not forex_channel:
        return

    try:
        analysis = await analyze_xauusd()
        if not analysis:
            return

        direction = analysis["direction"]
        emoji     = "🟢" if direction == "BUY" else "🔴"
        action    = "ACHAT" if direction == "BUY" else "VENTE"

        balance     = get_balance()
        sl_distance = abs(analysis["entry"] - analysis["sl"])
        lot_size, _ = calculate_lot(balance, sl_distance, "XAU_USD")

        embed = discord.Embed(
            title=f"{emoji} {action} — 🥇 Or / Dollar (XAU/USD)",
            color=0x2ECC71 if direction == "BUY" else 0xE74C3C
        )
        embed.add_field(
            name="​",
            value=(
                f"```\n"
                f"Entry  : {analysis['entry']:.2f}\n"
                f"SL     : {analysis['sl']:.2f}\n"
                f"TP1    : {analysis['tp1']:.2f}  (R/R 1:1 — clôture 50%)\n"
                f"TP2    : {analysis['tp2']:.2f}  (R/R 1:2)\n"
                f"TP3    : {analysis['tp3']:.2f}  (R/R 1:3)\n"
                f"Lot    : {lot_size}\n"
                f"RSI    : {analysis['rsi']:.1f}\n"
                f"```"
            ),
            inline=False
        )
        embed.add_field(name="⚡ Force du signal", value=f"{build_signal_bar(100)} **100/100**", inline=False)
        embed.set_footer(text=f"AlphaValais • {now_utc().strftime('%d.%m.%Y %H:%M')} • Session {analysis['session']}")
        await forex_channel.send(embed=embed)
        await log(f"📊 Signal XAU/USD {direction} | Entry: {analysis['entry']:.2f} | SL: {analysis['sl']:.2f} | TP2: {analysis['tp2']:.2f}")

        channels     = {"trades_en_cours": client.get_channel(TRADES_EN_COURS_ID), "trades_fermes": client.get_channel(TRADES_FERMES_ID)}
        trade_result = await execute_trade(analysis, channels)
        if trade_result:
            daily_trades += 1
            await log(f"✅ Trade ouvert — XAU/USD {direction} | ID: {trade_result['contract_id']} | Lot: {trade_result['lot_size']}")
        else:
            await log(f"⚠️ Trade non exécuté pour XAU/USD")

    except Exception as e:
        await log(f"❌ Erreur analyse XAU/USD: `{e}`")

# ===== ON READY =====
@client.event
async def on_ready():
    print(f"Bot connecté : {client.user}")
    await tree.sync(guild=discord.Object(id=GUILD_ID))
    print("Commandes slash synchronisées !")
    await log("🚀 AlphaValais Bot démarré ! Stratégie XAU/USD Breakout Session — Full CDC")
    init_db()
    set_discord_client(client)

    global range_london, range_ny
    saved_london, saved_ny = get_saved_ranges()
    if saved_london:
        range_london = saved_london
        await log(f"📐 Range Londres récupérée depuis DB | High: {saved_london['high']:.2f} | Low: {saved_london['low']:.2f}")
    if saved_ny:
        range_ny = saved_ny
        await log(f"📐 Range NY récupérée depuis DB | High: {saved_ny['high']:.2f} | Low: {saved_ny['low']:.2f}")

    channels = {"trades_en_cours": client.get_channel(TRADES_EN_COURS_ID), "trades_fermes": client.get_channel(TRADES_FERMES_ID)}
    await resume_open_trades(channels)
    send_alerts.start()
    build_ranges.start()
    send_xauusd_signals.start()
    check_retest_minutely.start()

client.run(TOKEN)
