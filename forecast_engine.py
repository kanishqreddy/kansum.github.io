"""
forecast_engine.py

Runs server-side (via GitHub Actions, NOT in the browser). Each run:
  1. Fetches current/historical price data for a basket of NSE stocks (<= Rs 500)
     using Yahoo Finance (via yfinance).
  2. Fetches recent news headlines for each company via Google News RSS
     (free, no API key) and computes a simple keyword-based sentiment score.
  3. Combines a momentum score + sentiment score into an overall score, and
     derives a "predicted tomorrow's price" (mechanical heuristic, clamped to +/-10%).
  4. Writes everything to forecast_data.json (read by the website).
  5. If run after 15:31 IST, also checks the PREVIOUS trading day's predictions
     against today's actual prices and appends results to accuracy_log.json.

IMPORTANT — READ THIS:
  The "predicted price" and "sentiment score" here are simple, transparent,
  rule-based heuristics. They are NOT financial advice, NOT validated
  forecasts, and are expected to be wrong often. The entire point of
  accuracy_log.json is to honestly measure HOW wrong, over time, so you can
  see whether this approach has any value (most likely: little to none --
  that's how markets work). Do not use these numbers to make investment
  decisions on Indmoney or anywhere else without your own independent research.
"""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import yfinance as yf
import feedparser

IST = timezone(timedelta(hours=5, minutes=30))
PRICE_MAX = 500.0
DATA_FILE = "forecast_data.json"
ACCURACY_FILE = "accuracy_log.json"

# Basket of NSE symbols (Yahoo format), typically priced <= Rs 500.
# Extend this list freely.
SYMBOLS = [
    "IDEA.NS", "YESBANK.NS", "SUZLON.NS", "IRFC.NS", "BHEL.NS", "NHPC.NS",
    "TATAPOWER.NS", "PNB.NS", "BANKBARODA.NS", "IOC.NS", "ONGC.NS", "SAIL.NS",
    "NMDC.NS", "RVNL.NS", "IRCTC.NS", "ZOMATO.NS", "PAYTM.NS", "TRIDENT.NS",
    "JPPOWER.NS", "RPOWER.NS", "VEDL.NS", "HUDCO.NS", "IRB.NS", "GMRINFRA.NS",
    "NATIONALUM.NS", "JINDALSTEL.NS", "ASHOKLEY.NS", "TATACOMM.NS", "CANBK.NS",
    "UNIONBANK.NS",
]

# --- Keyword-based sentiment lexicon (simple, transparent, extendable) ---
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
    """Fetch 1-month history + latest quote for a symbol."""
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period="1mo")
    if hist.empty or len(hist) < 6:
        raise ValueError(f"Not enough history for {symbol}")

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

    # Try to get a friendly name; fall back to symbol
    try:
        info = ticker.fast_info
        name = getattr(info, "shortName", None) or symbol
    except Exception:
        name = symbol

    # fast_info doesn't have name reliably; try .info as a fallback (slower)
    if name == symbol:
        try:
            full_info = ticker.info
            name = full_info.get("shortName") or full_info.get("longName") or symbol
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


def fetch_news(company_name, max_items=3):
    """
    Fetch recent news headlines for a company via Google News RSS (free, no key).
    Returns list of dicts: {title, link, published}
    """
    query = f"{company_name} share".replace(" ", "+")
    url = f"https://news.google.com/rss/search?q={query}+when:1d&hl=en-IN&gl=IN&ceid=IN:en"
    feed = feedparser.parse(url)

    items = []
    for entry in feed.entries[:max_items]:
        items.append({
            "title": entry.get("title", ""),
            "link": entry.get("link", ""),
            "published": entry.get("published", ""),
        })
    return items


def sentiment_score(news_items):
    """
    Very simple keyword-based sentiment score in range [-1, 1].
    Counts positive vs negative keyword matches across all fetched headlines.
    This is a transparent heuristic, not NLP -- it WILL get nuance wrong
    (e.g. "discount" can be read as either demand-positive or margin-negative).
    """
    if not news_items:
        return 0.0, []

    pos_hits = 0
    neg_hits = 0
    matched_terms = []

    full_text = " ".join(item["title"].lower() for item in news_items)

    for word in POSITIVE_WORDS:
        if word in full_text:
            pos_hits += 1
            matched_terms.append(f"+{word}")
    for word in NEGATIVE_WORDS:
        if word in full_text:
            neg_hits += 1
            matched_terms.append(f"-{word}")

    total = pos_hits + neg_hits
    if total == 0:
        return 0.0, matched_terms

    score = (pos_hits - neg_hits) / total  # range [-1, 1]
    return round(score, 2), matched_terms


def compute_score(stock, sent_score):
    """
    Overall score combining price momentum, volume, and news sentiment.
    Weights are arbitrary and not backtested -- this is an experiment.
    """
    vol_ratio_capped = min(stock["volRatio"], 3.0)
    score = (
        stock["todayChangePct"] * 0.5
        + stock["momentum5dPct"] * 0.25
        + (vol_ratio_capped - 1) * 10 * 0.15
        + sent_score * 10 * 0.10
    )
    return round(score, 2)


def predict_price(current_price, score):
    """Map score to an implied % move, clamped to +/- 10%."""
    pct = max(-10.0, min(10.0, score / 10.0))
    predicted = current_price * (1 + pct / 100)
    return round(predicted, 2), round(pct, 2)


def build_forecast():
    results = []
    for symbol in SYMBOLS:
        try:
            stock = fetch_price_data(symbol)
        except Exception as e:
            print(f"  [skip] {symbol}: {e}")
            continue

        if stock["price"] > PRICE_MAX:
            continue

        try:
            news_items = fetch_news(stock["name"])
        except Exception as e:
            print(f"  [news error] {symbol}: {e}")
            news_items = []

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

        time.sleep(0.5)  # be polite to free endpoints

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def run_accuracy_check(forecast_history):
    """
    Find the most recent forecast that hasn't been checked yet (not today,
    and not already marked actualsRecorded), fetch current prices for those
    symbols, and compute accuracy metrics.
    """
    today = get_ist_now().strftime("%Y-%m-%d")
    pending = [
        e for e in forecast_history
        if e["date"] != today and not e.get("actualsRecorded")
    ]
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
            hist = ticker.history(period="2d")
            if len(hist) < 1:
                continue
            actual_price = float(hist["Close"].values[-1])
            if len(hist) >= 2:
                prev_close = float(hist["Close"].values[-2])
                actual_pct = ((actual_price - prev_close) / prev_close) * 100
            else:
                actual_pct = None

            pred["actualPrice"] = round(actual_price, 2)
            pred["actualPct"] = round(actual_pct, 2) if actual_pct is not None else None
            pred["priceErrorPct"] = round(
                abs(pred["predictedPrice"] - actual_price) / actual_price * 100, 2
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

    # Pearson correlation between predicted score and actual % change
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

    positive_count = sum(1 for p in entry["predictions"] if p.get("actualPct", 0) and p["actualPct"] > 0)
    checked_count = sum(1 for p in entry["predictions"] if "actualPct" in p)
    hit_rate = round((positive_count / checked_count) * 100, 1) if checked_count else None
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
    print(f"  {len(forecast)} stocks scored (price <= Rs {PRICE_MAX})")

    today = now.strftime("%Y-%m-%d")

    forecast_history = load_json(DATA_FILE, {"history": []}).get("history", [])
    # Replace/insert today's entry
    forecast_history = [e for e in forecast_history if e["date"] != today]
    forecast_history.append({
        "date": today,
        "lastUpdated": now.isoformat(),
        "predictions": forecast,  # full list incl. news + scores
    })
    # Keep last 30 days only
    forecast_history = sorted(forecast_history, key=lambda e: e["date"])[-30:]

    # Run accuracy check if it's after 15:31 IST
    if now.hour > 15 or (now.hour == 15 and now.minute >= 31):
        run_accuracy_check(forecast_history)

    save_json(DATA_FILE, {"history": forecast_history, "generatedAt": now.isoformat()})
    print(f"Wrote {DATA_FILE}")


if __name__ == "__main__":
    main()
