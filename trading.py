"""
trading.py — Version Finale Améliorée & Complète
AlphaValais Bot - XAUUSD Breakout Strategy
OANDA API v20 + PostgreSQL
"""

import asyncio
import os
import logging
import requests
import psycopg2
import csv
from datetime import datetime

# ==================== CONFIGURATION ====================
OANDA_API_KEY    = os.environ.get("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID", "101-004-39358895-002")
OANDA_BASE_URL   = "https://api-fxpractice.oanda.com"  # Changer en .com pour live
DATABASE_URL     = os.environ.get("DATABASE_URL", "")

# Paramètres risque
RISK_PERCENT        = 1.0
MAX_SPREAD          = 45
MIN_SL_DISTANCE     = 8.0
PARTIAL_CLOSE_PCT   = 0.50
BREAKEVEN_ATR_MULTI = 0.8
TRAILING_ATR_MULTI  = 1.3

CSV_FILE = "trades_log.csv"
logger   = logging.getLogger("trading")

# Mapping symboles OANDA
SYMBOL_MAP      = {"XAU/USD": "XAU_USD"}
PRICE_PRECISION = {"XAU_USD": 2}
PIP_SIZE        = {"XAU_USD": 0.1}

# Gestion pertes consécutives (reset chaque jour)
consecutive_losses   = 0
consecutive_loss_day = None

# ==================== CLIENT DISCORD ====================
# Stocke le client Discord et les IDs de channels pour obtenir des
# références fraîches même après une reconnexion du bot.
_discord_client        = None
_channel_en_cours_id   = None
_channel_fermes_id     = None

def set_discord_client(client, trades_en_cours_id=None, trades_fermes_id=None):
    global _discord_client, _channel_en_cours_id, _channel_fermes_id
    _discord_client      = client
    _channel_en_cours_id = trades_en_cours_id
    _channel_fermes_id   = trades_fermes_id

def _get_channels():
    """Retourne des références de channels fraîches via le client Discord."""
    if not _discord_client:
        return {}
    channels = {}
    if _channel_en_cours_id:
        ch = _discord_client.get_channel(_channel_en_cours_id)
        if ch:
            channels["trades_en_cours"] = ch
    if _channel_fermes_id:
        ch = _discord_client.get_channel(_channel_fermes_id)
        if ch:
            channels["trades_fermes"] = ch
    return channels

def _resolve_channels(passed_channels: dict) -> dict:
    """
    Préfère les channels fraîches du client Discord.
    Utilise les channels passées en argument comme fallback.
    """
    fresh = _get_channels()
    if fresh:
        return fresh
    return passed_channels or {}

# ==================== DATABASE ====================
def get_db():
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        logger.error(f"❌ DB Error: {e}")
        return None

def init_db():
    conn = get_db()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id              SERIAL PRIMARY KEY,
                trade_id        VARCHAR(50) UNIQUE,
                pair            VARCHAR(20),
                direction       VARCHAR(10),
                entry           FLOAT,
                sl              FLOAT,
                tp              FLOAT,
                tp1             FLOAT,
                tp2             FLOAT,
                lot_size        FLOAT,
                units           INTEGER,
                score           INTEGER,
                session         VARCHAR(50),
                status          VARCHAR(20) DEFAULT 'OPEN',
                partial_closed  BOOLEAN DEFAULT FALSE,
                breakeven_set   BOOLEAN DEFAULT FALSE,
                profit          FLOAT,
                opened_at       TIMESTAMP DEFAULT NOW(),
                closed_at       TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ranges (
                session VARCHAR(20) PRIMARY KEY,
                high    FLOAT,
                low     FLOAT,
                date    DATE DEFAULT CURRENT_DATE
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        logger.info("✅ Database initialized")
    except Exception as e:
        logger.error(f"❌ Init DB: {e}")

# ==================== RANGES ====================
def save_range(session: str, high: float, low: float):
    conn = get_db()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO ranges (session, high, low, date)
            VALUES (%s, %s, %s, CURRENT_DATE)
            ON CONFLICT (session) DO UPDATE
            SET high = %s, low = %s, date = CURRENT_DATE
        """, (session, high, low, high, low))
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"✅ Range {session} sauvegardée | High: {high} | Low: {low}")
    except Exception as e:
        logger.error(f"❌ save_range: {e}")

def get_saved_ranges():
    conn = get_db()
    if not conn:
        return None, None
    try:
        cur = conn.cursor()
        cur.execute("SELECT session, high, low FROM ranges WHERE date = CURRENT_DATE")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        ranges = {r[0]: {"high": r[1], "low": r[2], "built": True} for r in rows}
        return ranges.get("London"), ranges.get("NY")
    except Exception as e:
        logger.error(f"❌ get_saved_ranges: {e}")
        return None, None

def reset_ranges_db():
    """Supprime les ranges du jour en base (appelé au reset minuit)."""
    conn = get_db()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM ranges WHERE date = CURRENT_DATE")
        conn.commit()
        cur.close()
        conn.close()
        logger.info("🔄 Ranges DB réinitialisées")
    except Exception as e:
        logger.error(f"❌ reset_ranges_db: {e}")

# ==================== TRADES DB ====================
def save_trade(trade: dict):
    conn = get_db()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO trades (trade_id, pair, direction, entry, sl, tp, tp1, tp2, lot_size, units, score, session)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (trade_id) DO NOTHING
        """, (
            str(trade.get("contract_id", "")),
            trade.get("pair", ""),
            trade.get("direction", ""),
            trade.get("entry", 0),
            trade.get("sl", 0),
            trade.get("tp", 0),
            trade.get("tp1", trade.get("tp", 0)),
            trade.get("tp2", trade.get("tp", 0)),
            trade.get("lot_size", 0),
            trade.get("units", 0),
            trade.get("score", 0),
            trade.get("session", ""),
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ save_trade: {e}")

def update_trade_db(trade_id: str, **kwargs):
    conn = get_db()
    if not conn:
        return
    try:
        cur = conn.cursor()
        for key, val in kwargs.items():
            cur.execute(f"UPDATE trades SET {key} = %s WHERE trade_id = %s", (val, str(trade_id)))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ update_trade_db: {e}")

def get_open_trades():
    conn = get_db()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT trade_id, pair, direction, entry, sl, tp1, tp2, lot_size, units, partial_closed, breakeven_set
            FROM trades WHERE status = 'OPEN'
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [{
            "trade_id":       r[0],
            "pair":           r[1],
            "direction":      r[2],
            "entry":          r[3],
            "sl":             r[4],
            "tp1":            r[5],
            "tp2":            r[6],
            "tp":             r[6],
            "lot_size":       r[7],
            "units":          r[8],
            "partial_closed": r[9],
            "breakeven_set":  r[10],
        } for r in rows]
    except Exception as e:
        logger.error(f"❌ get_open_trades: {e}")
        return []

# ==================== EXPORT CSV ====================
def export_to_csv(trade: dict):
    try:
        file_exists = os.path.isfile(CSV_FILE)
        with open(CSV_FILE, "a", newline="") as f:
            fieldnames = ["trade_id", "pair", "direction", "entry", "sl", "tp",
                          "lot_size", "profit", "status", "session", "opened_at", "closed_at"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow({
                "trade_id":  trade.get("contract_id", ""),
                "pair":      trade.get("pair", ""),
                "direction": trade.get("direction", ""),
                "entry":     trade.get("entry", ""),
                "sl":        trade.get("sl", ""),
                "tp":        trade.get("tp", ""),
                "lot_size":  trade.get("lot_size", ""),
                "profit":    trade.get("profit", ""),
                "status":    trade.get("status", ""),
                "session":   trade.get("session", ""),
                "opened_at": trade.get("timestamp", ""),
                "closed_at": datetime.utcnow().isoformat(),
            })
    except Exception as e:
        logger.error(f"❌ export_to_csv: {e}")

# ==================== HELPERS OANDA ====================
def get_headers():
    return {
        "Authorization":          f"Bearer {OANDA_API_KEY}",
        "Content-Type":           "application/json",
        "Accept-Datetime-Format": "RFC3339",
    }

def get_balance():
    try:
        r = requests.get(
            f"{OANDA_BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/summary",
            headers=get_headers(), timeout=10
        )
        if r.status_code == 200:
            balance = float(r.json()["account"]["balance"])
            logger.info(f"✅ Balance: {balance:.2f}")
            return balance
    except Exception as e:
        logger.error(f"❌ get_balance: {e}")
    return 100000.0

def get_current_price(oanda_symbol: str, direction: str, fallback: float) -> float:
    """Récupère le prix actuel depuis OANDA pricing endpoint."""
    try:
        url = f"{OANDA_BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/pricing?instruments={oanda_symbol}"
        r   = requests.get(url, headers=get_headers(), timeout=10)
        if r.status_code == 200:
            prices = r.json().get("prices", [{}])[0]
            # Pour surveiller un BUY, on utilise le bid (prix de sortie/vente)
            # Pour surveiller un SELL, on utilise le ask (prix de sortie/rachat)
            if direction == "BUY":
                return float(prices.get("bids", [{"price": str(fallback)}])[0]["price"])
            else:
                return float(prices.get("asks", [{"price": str(fallback)}])[0]["price"])
    except Exception as e:
        logger.error(f"❌ get_current_price: {e}")
    return fallback

def check_spread(oanda_symbol: str):
    """Vérifie que le spread est acceptable avant d'ouvrir un trade."""
    try:
        url = f"{OANDA_BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/pricing?instruments={oanda_symbol}"
        r   = requests.get(url, headers=get_headers(), timeout=10)
        if r.status_code == 200:
            prices = r.json().get("prices", [{}])[0]
            ask    = float(prices.get("asks", [{"price": "0"}])[0]["price"])
            bid    = float(prices.get("bids", [{"price": "0"}])[0]["price"])
            pip    = PIP_SIZE.get(oanda_symbol, 0.0001)
            spread = (ask - bid) / pip
            if spread > MAX_SPREAD:
                logger.warning(f"⚠️ Spread trop élevé: {spread:.1f} pts (max {MAX_SPREAD})")
                return False, spread
            return True, spread
    except Exception as e:
        logger.error(f"❌ check_spread: {e}")
    return True, 0

# ==================== CALCUL LOT ====================
def calculate_lot(balance: float, sl_distance: float, symbol: str = "XAU_USD"):
    if sl_distance < MIN_SL_DISTANCE:
        logger.warning(f"⚠️ SL distance {sl_distance:.2f} < minimum {MIN_SL_DISTANCE} → ajustée")
        sl_distance = MIN_SL_DISTANCE

    risk_amount = balance * (RISK_PERCENT / 100.0)
    pip_value   = 1.0

    units    = risk_amount / (sl_distance * pip_value)
    lot_size = round(units / 100000, 2)
    lot_size = max(0.01, min(lot_size, 10.0))
    units    = int(lot_size * 100000)

    logger.info(f"📊 Calcul lot | Balance: {balance:.0f} | Risk: {risk_amount:.2f} | SL dist: {sl_distance:.2f} | Lot: {lot_size}")
    return lot_size, units

def round_price(price: float, oanda_symbol: str) -> float:
    return round(price, PRICE_PRECISION.get(oanda_symbol, 2))

# ==================== PERTES CONSÉCUTIVES ====================
def check_consecutive_losses():
    global consecutive_losses, consecutive_loss_day
    today = datetime.utcnow().date()
    if consecutive_loss_day != today:
        consecutive_losses   = 0
        consecutive_loss_day = today
    if consecutive_losses >= 2:
        logger.warning(f"⚠️ {consecutive_losses} pertes consécutives — trading arrêté aujourd'hui")
        return False
    return True

# ==================== EXÉCUTION TRADE ====================
async def execute_trade(signal: dict, discord_channels: dict = None):
    global consecutive_losses

    pair      = signal.get("pair")
    direction = signal.get("direction")
    entry     = signal.get("entry")
    sl        = signal.get("sl")
    tp        = signal.get("tp")
    tp1       = signal.get("tp1", tp)
    tp2       = signal.get("tp2", tp)

    oanda_symbol = SYMBOL_MAP.get(pair)
    if not oanda_symbol:
        logger.error(f"Symbole non supporté: {pair}")
        return None

    if not check_consecutive_losses():
        return None

    spread_ok, spread = check_spread(oanda_symbol)
    if not spread_ok:
        logger.warning(f"⚠️ Trade annulé — spread trop élevé: {spread:.1f}")
        return None

    try:
        balance     = get_balance()
        sl_distance = abs(entry - sl)
        lot_size, units = calculate_lot(balance, sl_distance, oanda_symbol)

        sl_r  = round_price(sl, oanda_symbol)
        tp2_r = round_price(tp2, oanda_symbol)

        if direction == "SELL":
            units = -units

        order_body = {
            "order": {
                "type":        "MARKET",
                "instrument":  oanda_symbol,
                "units":       str(units),
                "timeInForce": "FOK",
                "stopLossOnFill":   {"price": f"{sl_r:.2f}"},
                "takeProfitOnFill": {"price": f"{tp2_r:.2f}"},
            }
        }

        r    = requests.post(
            f"{OANDA_BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/orders",
            headers=get_headers(), json=order_body, timeout=12
        )
        data = r.json()

        if r.status_code in (200, 201):
            trade_id   = (
                data.get("orderFillTransaction", {}).get("tradeOpened", {}).get("tradeID") or
                data.get("relatedTransactionIDs", ["unknown"])[0]
            )
            fill_price = float(data.get("orderFillTransaction", {}).get("price", entry))

            logger.info(f"✅ TRADE OUVERT | {direction} {pair} | ID: {trade_id} | Lot: {lot_size} | Spread: {spread:.1f}pts")

            result = {
                "contract_id": trade_id,
                "pair":        pair,
                "direction":   direction,
                "entry":       fill_price,
                "sl":          sl_r,
                "tp":          tp2_r,
                "tp1":         round_price(tp1, oanda_symbol),
                "tp2":         tp2_r,
                "lot_size":    lot_size,
                "units":       abs(units),
                "timestamp":   datetime.utcnow().isoformat(),
                "score":       signal.get("score", 0),
                "session":     signal.get("session", ""),
                "rsi":         signal.get("rsi", 0),
                "atr":         signal.get("atr", 0),
            }

            save_trade(result)
            if discord_channels:
                await post_trade_open(discord_channels, result)

            asyncio.create_task(_monitor_trade(trade_id, result, discord_channels))
            return result

        else:
            logger.error(f"❌ OANDA Error: {r.status_code} — {data}")
            return None

    except Exception as e:
        logger.error(f"❌ execute_trade: {e}")
        return None

# ==================== CLÔTURE PARTIELLE ====================
async def partial_close(trade_id: str, units: int, direction: str) -> bool:
    try:
        close_units = int(units * PARTIAL_CLOSE_PCT)
        if direction == "BUY":
            close_units = -close_units

        body = {"units": str(close_units)}
        r    = requests.put(
            f"{OANDA_BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/trades/{trade_id}/close",
            headers=get_headers(), json=body, timeout=10
        )
        if r.status_code == 200:
            logger.info(f"✂️ Clôture partielle 50% | Trade {trade_id}")
            return True
        else:
            logger.error(f"❌ partial_close: {r.json()}")
            return False
    except Exception as e:
        logger.error(f"❌ partial_close: {e}")
        return False

# ==================== MODIFIER SL ====================
async def modify_sl(trade_id: str, new_sl: float) -> bool:
    try:
        body = {"stopLoss": {"price": f"{new_sl:.2f}", "timeInForce": "GTC"}}
        r    = requests.put(
            f"{OANDA_BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/trades/{trade_id}/orders",
            headers=get_headers(), json=body, timeout=10
        )
        if r.status_code == 200:
            logger.info(f"🔒 SL modifié → {new_sl} | Trade {trade_id}")
            return True
        else:
            logger.error(f"❌ modify_sl: {r.json()}")
            return False
    except Exception as e:
        logger.error(f"❌ modify_sl: {e}")
        return False

# ==================== MONITORING ====================
async def _monitor_trade(trade_id: str, trade_info: dict, discord_channels: dict = None):
    global consecutive_losses

    entry     = trade_info["entry"]
    sl        = trade_info["sl"]
    tp1       = trade_info.get("tp1", trade_info.get("tp", entry))
    direction = trade_info["direction"]
    units     = trade_info.get("units", 1000)
    pair      = trade_info["pair"]
    oanda_sym = SYMBOL_MAP.get(pair, "XAU_USD")
    pip       = PIP_SIZE.get(oanda_sym, 0.1)
    atr       = trade_info.get("atr", 5.0)

    partial_closed = trade_info.get("partial_closed", False)
    breakeven_set  = trade_info.get("breakeven_set", False)
    best_price     = entry

    url = f"{OANDA_BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/trades/{trade_id}"
    logger.info(f"📡 Monitoring démarré | Trade {trade_id} | {direction} {pair}")

    while True:
        try:
            await asyncio.sleep(30)

            r    = requests.get(url, headers=get_headers(), timeout=10)
            data = r.json()

            if r.status_code != 200:
                error_str = str(data)
                # OANDA retourne errorCode "TRADE_DOESNT_EXIST" ou "NO_SUCH_TRADE"
                # selon la version. Dans les deux cas, le trade est fermé.
                if any(k in error_str for k in ("NO_SUCH_TRADE", "TRADE_DOESNT_EXIST")):
                    logger.info(f"🧹 Trade {trade_id} introuvable sur OANDA — marqué CLOSED")
                    update_trade_db(trade_id, status="CLOSED", closed_at=datetime.utcnow())

                    # FIX: on essaie quand même de notifier Discord avec les infos disponibles
                    channels = _resolve_channels(discord_channels)
                    result = {
                        "contract_id": trade_id,
                        "pair":        pair,
                        "direction":   direction,
                        "entry":       entry,
                        "sl":          sl,
                        "tp":          tp1,
                        "lot_size":    trade_info.get("lot_size", 0),
                        "status":      "CLOSED",
                        "profit":      0.0,
                        "sell_price":  0.0,
                        "session":     trade_info.get("session", ""),
                        "timestamp":   trade_info.get("timestamp", ""),
                    }
                    await post_trade_closed(channels, result)
                    break

                logger.error(f"❌ Monitor {trade_id}: {data}")
                await asyncio.sleep(60)
                continue

            trade = data.get("trade", {})
            state = trade.get("state", "")

            # ===== TRADE FERMÉ (TP, SL ou fermeture manuelle) =====
            if state == "CLOSED":
                realized_pl = float(trade.get("realizedPL", 0))
                close_price = float(trade.get("averageClosePrice", 0))
                status      = "WON" if realized_pl >= 0 else "LOST"

                if realized_pl < 0:
                    consecutive_losses += 1
                else:
                    consecutive_losses = 0

                result = {
                    "contract_id": trade_id,
                    "pair":        pair,
                    "direction":   direction,
                    "entry":       entry,
                    "sl":          sl,
                    "tp":          tp1,
                    "lot_size":    trade_info.get("lot_size", 0),
                    "status":      status,
                    "profit":      realized_pl,
                    "sell_price":  close_price,
                    "session":     trade_info.get("session", ""),
                    "timestamp":   trade_info.get("timestamp", ""),
                }

                update_trade_db(trade_id, status="CLOSED", profit=realized_pl, closed_at=datetime.utcnow())
                export_to_csv(result)

                logger.info(f"🏁 Trade fermé | {trade_id} | {status} | P&L: {realized_pl:+.2f}")

                # FIX: utilise _resolve_channels pour obtenir des références fraîches
                channels = _resolve_channels(discord_channels)
                await post_trade_closed(channels, result)
                break

            # ===== PRIX ACTUEL =====
            current_price = get_current_price(oanda_sym, direction, entry)
            unrealized_pl = float(trade.get("unrealizedPL", 0))

            if direction == "BUY":
                pips_profit = (current_price - entry) / pip
                if current_price > best_price:
                    best_price = current_price
            else:
                pips_profit = (entry - current_price) / pip
                if current_price < best_price:
                    best_price = current_price

            # ===== CLÔTURE PARTIELLE À TP1 =====
            if not partial_closed:
                at_tp1 = (direction == "BUY"  and current_price >= tp1) or \
                         (direction == "SELL" and current_price <= tp1)
                if at_tp1:
                    success = await partial_close(trade_id, units, direction)
                    if success:
                        partial_closed = True
                        update_trade_db(trade_id, partial_closed=True)
                        channels = _resolve_channels(discord_channels)
                        ch = channels.get("trades_en_cours")
                        if ch:
                            emoji = "🟢" if direction == "BUY" else "🔴"
                            await ch.send(
                                f"{emoji} **CLÔTURE PARTIELLE 50%** — {pair}\n"
                                f"`Prix: {current_price:.2f} | P&L partiel: {unrealized_pl:+.2f}`"
                            )

            # ===== BREAKEVEN APRÈS TP1 =====
            if partial_closed and not breakeven_set:
                sl_new  = round_price(entry, oanda_sym)
                success = await modify_sl(trade_id, sl_new)
                if success:
                    breakeven_set = True
                    update_trade_db(trade_id, breakeven_set=True)
                    channels = _resolve_channels(discord_channels)
                    ch = channels.get("trades_en_cours")
                    if ch:
                        await ch.send(
                            f"🔒 **BREAKEVEN** activé — {pair}\n"
                            f"`SL déplacé à l'entrée: {sl_new:.2f}`"
                        )

            # ===== TRAILING STOP (basé sur ATR) =====
            trailing_trigger  = atr * BREAKEVEN_ATR_MULTI
            trailing_distance = atr * TRAILING_ATR_MULTI

            if breakeven_set and pips_profit >= trailing_trigger:
                if direction == "BUY":
                    trailing_sl = round_price(best_price - trailing_distance * pip, oanda_sym)
                    current_sl  = float(trade.get("stopLossOrder", {}).get("price", sl))
                    if trailing_sl > current_sl:
                        await modify_sl(trade_id, trailing_sl)
                else:
                    trailing_sl = round_price(best_price + trailing_distance * pip, oanda_sym)
                    current_sl  = float(trade.get("stopLossOrder", {}).get("price", sl))
                    if trailing_sl < current_sl:
                        await modify_sl(trade_id, trailing_sl)

        except Exception as e:
            logger.error(f"❌ _monitor_trade loop: {e}")
            await asyncio.sleep(60)

async def monitor_trade(trade_id: str, discord_channels: dict = None):
    """Wrapper pour reprendre la surveillance d'un trade existant."""
    open_trades = get_open_trades()
    trade_info  = next((t for t in open_trades if t["trade_id"] == str(trade_id)), None)
    if trade_info:
        await _monitor_trade(trade_id, trade_info, discord_channels)
    else:
        logger.warning(f"Trade {trade_id} introuvable en DB")

# ==================== REPRISE TRADES OUVERTS ====================
async def resume_open_trades(discord_channels: dict = None):
    open_trades = get_open_trades()
    if not open_trades:
        logger.info("📋 Aucun trade ouvert à reprendre")
        return
    logger.info(f"🔄 Reprise de {len(open_trades)} trade(s) ouverts")
    for t in open_trades:
        logger.info(f"   → {t['direction']} {t['pair']} | ID: {t['trade_id']}")
        asyncio.create_task(_monitor_trade(t["trade_id"], t, discord_channels))

# ==================== MESSAGES DISCORD ====================
async def post_trade_open(channels: dict, trade: dict):
    ch = channels.get("trades_en_cours")
    if not ch:
        logger.warning("⚠️ post_trade_open: channel trades_en_cours introuvable")
        return
    emoji  = "🟢" if trade["direction"] == "BUY" else "🔴"
    action = "ACHAT" if trade["direction"] == "BUY" else "VENTE"
    msg = (
        f"{emoji} **TRADE OUVERT** — {action} {trade['pair']}\n"
        f"```\n"
        f"Entry      : {trade['entry']}\n"
        f"Stop Loss  : {trade['sl']}\n"
        f"TP1 (50%)  : {trade['tp1']}\n"
        f"TP2 (50%)  : {trade['tp2']}\n"
        f"Lot        : {trade['lot_size']}\n"
        f"Score      : {trade.get('score', 100)}/100\n"
        f"Session    : {trade['session']}\n"
        f"Trade ID   : {trade['contract_id']}\n"
        f"Heure UTC  : {trade['timestamp'][:19]}\n"
        f"```"
    )
    await ch.send(msg)

async def post_trade_closed(channels: dict, trade: dict):
    ch = channels.get("trades_fermes")
    if not ch:
        logger.warning("⚠️ post_trade_closed: channel trades_fermes introuvable")
        return
    emoji  = "✅" if trade["profit"] >= 0 else "❌"
    profit = trade["profit"]
    # Affichage du prix de sortie (peut être 0 si trade_id introuvable)
    sell_price_str = f"{trade['sell_price']:.2f}" if trade.get("sell_price") else "N/A"
    msg = (
        f"{emoji} **TRADE FERMÉ** — {trade['status']}\n"
        f"```\n"
        f"Paire      : {trade['pair']}\n"
        f"Direction  : {trade['direction']}\n"
        f"P&L        : {profit:+.2f}\n"
        f"Prix sortie: {sell_price_str}\n"
        f"Trade ID   : {trade['contract_id']}\n"
        f"Fermé UTC  : {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"```"
    )
    await ch.send(msg)

logger.info("✅ trading.py — Version Finale Améliorée chargée")
