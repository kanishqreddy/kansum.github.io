"""
forecast_engine.py (v2)

Server-side script run via GitHub Actions. Each run:
  1. Fetches price history for a basket of NSE stocks (<= Rs 500) via yfinance,
     WITH RETRIES (the v1 bug: a single transient failure dropped a symbol
     entirely, which is why only 1/30 showed up).
  2. Fetches recent news headlines via Google News RSS, computes a keyword
     sentiment score.
  3. Computes momentum + sentiment score -> predicted next-session price.
  4. Writes forecast_data.json (today's predictions, read by the live page).
  5. Maintains per-stock historical accuracy (rolling hit-rate per symbol),
     written into accuracy_by_symbol.json, so the frontend can show each
     stock's own track record instead of one fake global "confidence" number.
  6. If run after market close, checks the prior trading day's predictions
     against actual closes and appends to history.

This does NOT need to run every 60 seconds -- predictions only need to be
generated once per session (pre-market) and checked once per session
(post-close). The LIVE CURRENT PRICE the website shows during market hours
is fetched directly by the browser's JS (see forecast_lab_section.html),
not by this script, since GitHub Actions scheduling can't reliably hit
sub-minute granularity. This script owns: predictions, news, scoring,
accuracy history.
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone

import yfinance as yf
import feedparser

IST = timezone(timedelta(hours=5, minutes=30))
PRICE_MAX = 500.0
DATA_FILE = "forecast_data.json"
ACCURACY_BY_SYMBOL_FILE = "accuracy_by_symbol.json"
MAX_RETRIES = 3
RETRY_DELAY_SEC = 2

SYMBOLS = [
    "IDEA.NS", "YESBANK.NS", "SUZLON.NS", "IRFC.NS", "BHEL.NS", "NHPC.NS",
    "TATAPOWER.NS", "PNB.NS", "BANKBARODA.NS", "IOC.NS", "ONGC.NS", "SAIL.NS",
    "NMDC.NS", "RVNL.NS", "IRCTC.NS", "ZOMATO.NS", "PAYTM.NS", "TRIDENT.NS",
    "JPPOWER.NS", "RPOWER.NS", "VEDL.NS", "HUDCO.NS", "IRB.NS", "GMRINFRA.NS",
    "NATIONALUM.NS", "JINDALSTEL.NS", "ASHOKLEY.NS", "TATACOMM.NS", "CANBK.NS",
    "UNIONBANK.NS", "TATASTEEL.NS", "COALINDIA.NS", "POWERGRID.NS", "WIPRO.NS",
    "ITC.NS", "ZEEL.NS", "DLF.NS", "GAIL.NS", "ADANIPOWER.NS", "MOTHERSON.NS",
]

POSITIVE_WORDS = [
    "mou", "signs deal", "order win", "wins order", "bags order", "contract",
    "expansion", "record profit", "profit jumps", "profit rises", "beats estimates",
    "upgrade", "buy rating", "target raised", "raises target", "stake buy",
    "approval", "launch", "partnership", "acquire", "acquisition", "merger",
    "dividend", "bonus issue", "stock split", "fundraise", "capacity expansion",
    "tax benefit", "subsidy", "incentive", "discount scheme", "demand surge",
    "strong sales", "record sales", "outperform", "rally",
]
NEGATIVE_WORDS = [
    "fraud", "downgrade", "sell rating", "target cut", "cuts target", "loss",
    "profit falls", "profit drops", "decline", "ban", "penalty", "fine",
    "investigation", "probe", "lawsuit", "recall", "strike", "shutdown",
    "resignation", "default", "debt concern", "margin pressure", "weak demand",
    "miss estimates", "underperform", "crash", "plunge", "selloff",
]


def get_ist_now():
    return datetime.now(IST)


def fetch_price_data(symbol):
    """Fetch 1-month history for a symbol, with retries on transient failures."""
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="1mo")
            if hist.empty or len(hist) < 6:
                raise ValueError(f"insufficient history ({len(hist)} rows)")

            closes = hist["Close"].values
            volumes = hist["Volume"].values

            last = float(closes[-1])
            prev = float(closes[-2])
            five_back = float(closes[-6])

            today_change = ((last - prev) / prev) * 100
            momentum_5d = ((last - five_back) / five_back) * 100

            avg_vol = volumes[-6:-1].mean()
            last_vol = volumes[-1]
            vol_ratio = (last_vol / avg_vol) if avg_vol > 0 else 1.0

            name = symbol
            try:
                fi = ticker.fast_info
                name = getattr(fi, "shortName", None) or symbol
            except Exception:
                pass
            if name == symbol:
                try:
                    info = ticker.info
                    name = info.get("shortName") or info.get("longName") or symbol
                except Exception:
                    pass

            return {
                "symbol": symbol.replace(".NS", ""),
                "name": name,
                "price": round(last, 2),
                "todayChangePct": round(today_change, 2),
                "momentum5dPct": round(momentum_5d, 2),
                "volRatio": round(vol_ratio, 2),
            }
        except Exception as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SEC * attempt)  # backoff
    raise RuntimeError(f"{symbol} failed after {MAX_RETRIES} attempts: {last_exc}")


def fetch_news(company_name, max_items=3):
    query = f"{company_name} share".replace(" ", "+")
    url = f"https://news.google.com/rss/search?q={query}+when:1d&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        feed = feedparser.parse(url)
        items = []
        for entry in feed.entries[:max_items]:
            items.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
            })
        return items
    except Exception:
        return []


def sentiment_score(news_items):
    if not news_items:
        return 0.0, []
    full_text = " ".join(item["title"].lower() for item in news_items)
    pos_hits, neg_hits, matched = 0, 0, []
    for word in POSITIVE_WORDS:
        if word in full_text:
            pos_hits += 1
            matched.append(f"+{word}")
    for word in NEGATIVE_WORDS:
        if word in full_text:
            neg_hits += 1
            matched.append(f"-{word}")
    total = pos_hits + neg_hits
    if total == 0:
        return 0.0, matched
    return round((pos_hits - neg_hits) / total, 2), matched


def compute_score(stock, sent_score):
    vol_ratio_capped = min(stock["volRatio"], 3.0)
    return round(
        stock["todayChangePct"] * 0.5
        + stock["momentum5dPct"] * 0.25
        + (vol_ratio_capped - 1) * 10 * 0.15
        + sent_score * 10 * 0.10,
        2,
    )


def predict_price(current_price, score):
    pct = max(-10.0, min(10.0, score / 10.0))
    return round(current_price * (1 + pct / 100), 2), round(pct, 2)


def build_forecast():
    results = []
    failures = []
    for symbol in SYMBOLS:
        try:
            stock = fetch_price_data(symbol)
        except Exception as e:
            failures.append((symbol, str(e)))
            print(f"  [FAILED] {symbol}: {e}")
            continue

        if stock["price"] > PRICE_MAX:
            print(f"  [skip price>{PRICE_MAX}] {symbol}: Rs{stock['price']}")
            continue

        news_items = fetch_news(stock["name"])
        sent, matched_terms = sentiment_score(news_items)
        score = compute_score(stock, sent)
        predicted_price, predicted_pct = predict_price(stock["price"], score)

        stock.update({
            "news": news_items,
            "sentimentScore": sent,
            "sentimentTerms": matched_terms,
            "score": score,
            "predictedPrice": predicted_price,
            "predictedPct": predicted_pct,
        })
        results.append(stock)
        print(f"  [ok] {symbol}: Rs{stock['price']} score={score}")
        time.sleep(0.4)

    results.sort(key=lambda x: x["score"], reverse=True)
    print(f"\nSummary: {len(results)} succeeded, {len(failures)} failed")
    if failures:
        print("Failed symbols:", failures)
    return results


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def update_accuracy_by_symbol(accuracy_db, symbol, was_correct_direction, price_error_pct):
    """
    Maintain a rolling per-symbol track record: how often THIS stock's
    predicted direction matched the actual direction, and average price error.
    This is what lets the frontend show a real, earned "confidence" per stock
    instead of a made-up number.
    """
    entry = accuracy_db.get(symbol, {"checks": 0, "correct": 0, "errors": []})
    entry["checks"] += 1
    if was_correct_direction:
        entry["correct"] += 1
    entry["errors"].append(price_error_pct)
    entry["errors"] = entry["errors"][-30:]  # keep last 30
    accuracy_db[symbol] = entry
    return accuracy_db


def run_accuracy_check(forecast_history, accuracy_db):
    today = get_ist_now().strftime("%Y-%m-%d")
    pending = [e for e in forecast_history if e["date"] != today and not e.get("actualsRecorded")]
    if not pending:
        print("No pending forecasts to check.")
        return

    pending.sort(key=lambda e: e["date"], reverse=True)
    entry = pending[0]
    print(f"Checking forecast from {entry['date']} against actuals...")

    errors, pred_scores, actual_pcts = [], [], []

    for pred in entry["predictions"]:
        symbol_ns = pred["symbol"] + ".NS"
        try:
            ticker = yf.Ticker(symbol_ns)
            hist = ticker.history(period="3d")
            if len(hist) < 1:
                continue
            actual_price = float(hist["Close"].values[-1])
            actual_pct = None
            if len(hist) >= 2:
                prev_close = float(hist["Close"].values[-2])
                actual_pct = ((actual_price - prev_close) / prev_close) * 100

            pred["actualPrice"] = round(actual_price, 2)
            pred["actualPct"] = round(actual_pct, 2) if actual_pct is not None else None
            pred["priceErrorPct"] = round(abs(pred["predictedPrice"] - actual_price) / actual_price * 100, 2)
            pred["gainCaptured"] = (
                round(pred["actualPct"] - pred["predictedPct"], 2) if actual_pct is not None else None
            )

            predicted_direction_up = pred["predictedPct"] >= 0
            actual_direction_up = (actual_pct or 0) >= 0
            was_correct = predicted_direction_up == actual_direction_up
            pred["directionCorrect"] = was_correct

            accuracy_by_symbol_local = update_accuracy_by_symbol(
                accuracy_db, pred["symbol"], was_correct, pred["priceErrorPct"]
            )

            pred_scores.append(pred["score"])
            if actual_pct is not None:
                actual_pcts.append(actual_pct)
            errors.append(pred["priceErrorPct"])
        except Exception as e:
            print(f"  [accuracy skip] {symbol_ns}: {e}")
        time.sleep(0.3)

    if not errors:
        print("Could not fetch any actuals.")
        return

    corr = None
    if len(pred_scores) >= 2 and len(actual_pcts) == len(pred_scores):
        n = len(pred_scores)
        mean_x = sum(pred_scores) / n
        mean_y = sum(actual_pcts) / n
        num = sum((pred_scores[i] - mean_x) * (actual_pcts[i] - mean_y) for i in range(n))
        den_x = sum((x - mean_x) ** 2 for x in pred_scores)
        den_y = sum((y - mean_y) ** 2 for y in actual_pcts)
        if den_x > 0 and den_y > 0:
            corr = round(num / (den_x * den_y) ** 0.5, 3)

    correct_count = sum(1 for p in entry["predictions"] if p.get("directionCorrect"))
    checked_count = sum(1 for p in entry["predictions"] if "actualPct" in p)
    hit_rate = round((correct_count / checked_count) * 100, 1) if checked_count else None
    avg_error = round(sum(errors) / len(errors), 2)

    entry["actualsRecorded"] = True
    entry["summary"] = {
        "date": entry["date"],
        "correlation": corr,
        "hitRate": hit_rate,
        "avgPriceErrorPct": avg_error,
        "checkedCount": checked_count,
    }
    print(f"  correlation={corr} hitRate={hit_rate}% avgPriceError={avg_error}%")


def main():
    now = get_ist_now()
    print(f"Run started at {now.isoformat()} IST")

    print("Building forecast...")
    forecast = build_forecast()

    today = now.strftime("%Y-%m-%d")

    forecast_history = load_json(DATA_FILE, {"history": []}).get("history", [])
    accuracy_db = load_json(ACCURACY_BY_SYMBOL_FILE, {})

    forecast_history = [e for e in forecast_history if e["date"] != today]
    forecast_history.append({
        "date": today,
        "lastUpdated": now.isoformat(),
        "predictions": forecast,
    })
    forecast_history = sorted(forecast_history, key=lambda e: e["date"])[-30:]

    market_close_passed = now.hour > 15 or (now.hour == 15 and now.minute >= 31)
    if market_close_passed:
        run_accuracy_check(forecast_history, accuracy_db)

    # Attach each stock's own track record to today's predictions for display
    for pred in forecast:
        acc = accuracy_db.get(pred["symbol"])
        if acc and acc["checks"] > 0:
            pred["trackRecord"] = {
                "checks": acc["checks"],
                "hitRatePct": round((acc["correct"] / acc["checks"]) * 100, 1),
                "avgErrorPct": round(sum(acc["errors"]) / len(acc["errors"]), 2),
            }
        else:
            pred["trackRecord"] = None

    # Re-save forecast_history now that today's entry has trackRecord attached
    forecast_history = [e for e in forecast_history if e["date"] != today]
    forecast_history.append({
        "date": today,
        "lastUpdated": now.isoformat(),
        "predictions": forecast,
        "marketStatus": "post-close" if market_close_passed else (
            "pre-open" if now.hour < 9 or (now.hour == 9 and now.minute < 15) else "open"
        ),
    })
    forecast_history = sorted(forecast_history, key=lambda e: e["date"])[-30:]

    save_json(DATA_FILE, {"history": forecast_history, "generatedAt": now.isoformat()})
    save_json(ACCURACY_BY_SYMBOL_FILE, accuracy_db)
    print(f"Wrote {DATA_FILE} and {ACCURACY_BY_SYMBOL_FILE}")


if __name__ == "__main__":
    main()
