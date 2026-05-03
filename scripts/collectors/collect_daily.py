"""
tadawul-data-lake | collect_daily.py  (v2)
يجمع بيانات السوق السعودي الكاملة كل يوم تداول

الإضافات في v2:
  - حجم التداول بالريال (volume_sar) لكل سهم
  - أداء القطاعات اليومي (sector performance)
  - SAIBOR من ساما
  - نسبة ملكية الأجانب من تداول
  - إعلانات توزيعات الأرباح من Argaam
  - أسعار بتروكيماويات إضافية

المنطق: إذا ما في بيانات جديدة (إجازة/عطلة) → يخرج بهدوء بدون أي خطأ
"""

import yfinance as yf
import pandas as pd
import requests
import json
import sys
import re
from datetime import date, datetime
from pathlib import Path
import logging

# ─── إعداد الـ logging ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── المسارات ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"

# ─── تصنيف الأسهم حسب القطاع ────────────────────────────────────────────────
SECTORS = {
    "Banking":        [1010, 1020, 1030, 1050, 1060, 1080, 1120, 1140, 1150, 1180, 1182],
    "Energy":         [2222, 2380, 2381],
    "Petrochemicals": [2010, 2020, 2060, 2150, 2160, 2170, 2200, 2210, 2220, 2223,
                       2230, 2240, 2250, 2290, 2310, 2330, 2350, 2360, 2370],
    "Cement":         [3001, 3002, 3003, 3004, 3005, 3007, 3008, 3010, 3020, 3030,
                       3040, 3050, 3060, 3080, 3090, 3091, 3092],
    "Materials":      [2040, 2050, 2070, 2080, 2081, 2082, 2083, 2084, 2085,
                       2090, 2100, 2110, 2120, 2130, 2140, 2190, 2270, 2280,
                       2300, 2320, 2370, 2382],
    "Financial":      [1111, 1202, 1211, 1212, 1213, 1302, 1303, 1304,
                       1320, 1321, 1322, 1324, 1810, 1830, 1831, 1833, 1834],
    "Insurance":      [8010, 8030, 8060, 8200, 8210, 8230, 8240, 8313],
    "Consumer":       [4001, 4003, 4004, 4005, 4007, 4009, 4013, 4015, 4016, 4017,
                       4019, 4050, 4071, 4072, 4083, 4084, 4100, 4142, 4145, 4147,
                       4160, 4162, 4163, 4164, 4165, 4170, 4190, 4191, 4193, 4200,
                       4210, 4240, 4261, 4262, 4263, 4265, 4280, 4300],
    "Telecom":        [7010, 7020, 7030, 7040, 7200, 7202, 7203],
    "Healthcare":     [2002, 4002, 4004, 4007, 4009, 4013, 4015],
    "REITs":          [1750, 1760, 1770, 1780, 1790, 4020, 4230, 4250,
                       4330, 4331, 4332, 4333, 4334, 4335,
                       4336, 4337, 4338, 4339, 4340],
    "Utilities":      [5110],
    "Food":           [6004, 6006, 6010, 6012, 6014, 6016, 6017, 6018, 6019, 6050, 6070],
    "Transportation": [4030, 4031, 4260, 4264],
}

# قاموس رمز → قطاع للبحث السريع
SYMBOL_TO_SECTOR = {
    str(sym): sector
    for sector, syms in SECTORS.items()
    for sym in syms
}

ALL_SYMBOLS = list({sym for syms in SECTORS.values() for sym in syms})
ALL_TICKERS = [f"{s}.SR" for s in ALL_SYMBOLS]

# ─── مؤشرات خارجية ───────────────────────────────────────────────────────────
EXTERNAL_TICKERS = {
    # نفط
    "BZ=F":      "brent_crude",
    "CL=F":      "wti_crude",
    # معادن
    "GC=F":      "gold",
    "SI=F":      "silver",
    # مخاطرة
    "^VIX":      "vix",
    # مؤشرات عالمية
    "^GSPC":     "sp500",
    "^IXIC":     "nasdaq",
    "^FTSE":     "ftse100",
    "^N225":     "nikkei",
    "000001.SS": "shanghai",
    # عملات وفائدة
    "DX-Y.NYB":  "usd_index",
    "^TNX":      "us_10y_yield",
    "^IRX":      "us_3m_yield",
    "EURUSD=X":  "eur_usd",
    # TASI
    "^TASI.SR":  "tasi",
    # بتروكيماويات (proxies)
    "UREA1!":    "urea_futures",  # اليوريا → سابك للمغذيات
    "LIN":       "linde_plc",    # غازات صناعية → proxy إيثيلين
    "MEOH":      "methanol",     # ميثانول
}


# ═══════════════════════════════════════════════════════════════════════════
# 1. حالة السوق
# ═══════════════════════════════════════════════════════════════════════════

def get_last_market_date(ticker="^TASI.SR"):
    try:
        df = yf.download(ticker, period="5d", progress=False, auto_adjust=True)
        if df.empty:
            return None
        return df.index[-1].date()
    except Exception as e:
        log.error(f"خطأ TASI: {e}")
        return None


def is_market_open_today() -> bool:
    today = date.today()
    last = get_last_market_date()
    if last is None:
        log.warning("لم نتحقق من السوق")
        return False
    if last == today:
        log.info(f"✅ السوق مفتوح: {today}")
        return True
    log.info(f"⛔ السوق مغلق ({today}) | آخر تداول: {last}")
    return False


# ═══════════════════════════════════════════════════════════════════════════
# 2. الأسهم + volume_sar
# ═══════════════════════════════════════════════════════════════════════════

def collect_stocks() -> pd.DataFrame:
    log.info(f"جمع {len(ALL_TICKERS)} سهم...")
    today = date.today()

    try:
        raw = yf.download(
            tickers=ALL_TICKERS,
            period="5d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
            timeout=60,
        )
    except Exception as e:
        log.error(f"فشل التحميل: {e}")
        return pd.DataFrame()

    records = []
    for ticker in ALL_TICKERS:
        symbol = ticker.replace(".SR", "")
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if ticker not in raw.columns.get_level_values(0):
                    continue
                stock_df = raw[ticker].dropna()
            else:
                stock_df = raw.dropna()

            if stock_df.empty or stock_df.index[-1].date() != today:
                continue

            row   = stock_df.iloc[-1]
            close = float(row["Close"])
            vol   = int(row["Volume"])
            avg_p = (float(row["High"]) + float(row["Low"])) / 2
            vol_sar = int(vol * avg_p)

            # تغيير %
            if len(stock_df) >= 2:
                prev_c     = float(stock_df.iloc[-2]["Close"])
                change_pct = ((close - prev_c) / prev_c) * 100
            else:
                prev_c = change_pct = None

            # نسبة volume مقارنة بمتوسط آخر 20 يوم
            hist = stock_df.iloc[-min(21, len(stock_df)):-1]
            if len(hist) >= 3:
                avg_hist_vol_sar = float(
                    (hist["Volume"] * ((hist["High"] + hist["Low"]) / 2)).mean()
                )
                vol_ratio = round(vol_sar / avg_hist_vol_sar, 3) if avg_hist_vol_sar else None
            else:
                vol_ratio = None

            records.append({
                "date":            str(today),
                "symbol":          symbol,
                "sector":          SYMBOL_TO_SECTOR.get(symbol, "Unknown"),
                "open":            round(float(row["Open"]), 4),
                "high":            round(float(row["High"]), 4),
                "low":             round(float(row["Low"]), 4),
                "close":           round(close, 4),
                "volume_shares":   vol,
                "volume_sar":      vol_sar,        # ← جديد: القيمة بالريال
                "volume_vs_avg20": vol_ratio,      # ← جديد: نسبة فوق/تحت المتوسط
                "change_pct":      round(change_pct, 4) if change_pct is not None else None,
                "prev_close":      round(prev_c, 4) if prev_c is not None else None,
            })

        except Exception as e:
            log.warning(f"⚠️  {symbol}: {e}")

    log.info(f"✅ {len(records)} سهم")
    return pd.DataFrame(records)


# ═══════════════════════════════════════════════════════════════════════════
# 3. أداء القطاعات (يُحسب من الأسهم - لا API إضافي)
# ═══════════════════════════════════════════════════════════════════════════

def calculate_sector_performance(stocks_df: pd.DataFrame) -> pd.DataFrame:
    if stocks_df.empty:
        return pd.DataFrame()

    today   = str(date.today())
    records = []

    for sector, syms in SECTORS.items():
        sub = stocks_df[stocks_df["symbol"].isin([str(s) for s in syms])]
        if sub.empty:
            continue

        valid     = sub["change_pct"].dropna()
        total_vol = int(sub["volume_sar"].sum()) if "volume_sar" in sub.columns else 0

        best   = sub.loc[valid.idxmax(), "symbol"] if len(valid) else None
        worst  = sub.loc[valid.idxmin(), "symbol"] if len(valid) else None

        records.append({
            "date":              today,
            "sector":            sector,
            "stock_count":       len(sub),
            "avg_change_pct":    round(float(valid.mean()), 4)   if len(valid) else None,
            "median_change_pct": round(float(valid.median()), 4) if len(valid) else None,
            "advancing":         int((valid > 0).sum()),
            "declining":         int((valid < 0).sum()),
            "unchanged":         int((valid == 0).sum()),
            "total_volume_sar":  total_vol,
            "best_stock":        best,
            "worst_stock":       worst,
        })

    df = pd.DataFrame(records)
    if not df.empty:
        v = df.dropna(subset=["avg_change_pct"])
        if not v.empty:
            leader  = v.loc[v["avg_change_pct"].idxmax()]
            laggard = v.loc[v["avg_change_pct"].idxmin()]
            log.info(f"🏆 {leader['sector']} {leader['avg_change_pct']:+.2f}% | "
                     f"⬇️  {laggard['sector']} {laggard['avg_change_pct']:+.2f}%")
    return df


# ═══════════════════════════════════════════════════════════════════════════
# 4. SAIBOR من ساما
# ═══════════════════════════════════════════════════════════════════════════

def collect_saibor() -> dict:
    """
    SAIBOR = Saudi Interbank Offered Rate
    يؤثر مباشرة على هوامش أرباح البنوك السعودية.
    ساما تنشره يومياً - نحاول جلبه ونحفظ None إذا فشل.
    """
    today  = str(date.today())
    result = {
        "date":       today,
        "saibor_1w":  None,
        "saibor_1m":  None,
        "saibor_3m":  None,   # الأهم
        "saibor_6m":  None,
        "saibor_12m": None,
        "source":     "sama",
    }

    try:
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        url = "https://www.sama.gov.sa/ar-SA/EconomicReports/SaiborRates/SaiborRate.json"
        resp = requests.get(url, headers=headers, timeout=10)

        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                latest = data[0]
                result["saibor_1w"]  = latest.get("oneWeek")
                result["saibor_1m"]  = latest.get("oneMonth")
                result["saibor_3m"]  = latest.get("threeMonths")
                result["saibor_6m"]  = latest.get("sixMonths")
                result["saibor_12m"] = latest.get("twelveMonths")
                log.info(f"✅ SAIBOR 3M: {result['saibor_3m']}%")
        else:
            log.warning(f"⚠️  SAIBOR HTTP {resp.status_code}")

    except Exception as e:
        log.warning(f"⚠️  SAIBOR: {e}")

    return result


# ═══════════════════════════════════════════════════════════════════════════
# 5. ملكية الأجانب (Tier 1 فقط)
# ═══════════════════════════════════════════════════════════════════════════

def collect_foreign_ownership() -> pd.DataFrame:
    """
    تداول ينشر نسبة ملكية الأجانب يومياً لكل سهم.
    نجمع للأسهم الكبيرة فقط (Tier 1 - 20 سهم).
    إذا تغيّر HTML الموقع، يحفظ None ويكمل.
    """
    today    = str(date.today())
    TIER1    = ["1120", "2222", "1180", "1150", "1211", "7010", "2010",
                "2020", "7203", "2082", "1060", "8313", "1010", "1140",
                "2230", "7020", "1322", "4250", "2350", "2290"]
    records  = []
    headers  = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    for symbol in TIER1:
        try:
            # تداول ينشر JSON للأسهم في endpoint مخصص
            url  = f"https://api.tadawul.com.sa/api/shares/{symbol}/ownership"
            resp = requests.get(url, headers=headers, timeout=10)

            foreign_pct = None

            if resp.status_code == 200:
                data = resp.json()
                # نبحث عن مفتاح الأجانب بعدة أشكال محتملة
                for key in ["foreignOwnership", "foreign_ownership", "foreignPercentage"]:
                    if key in data:
                        foreign_pct = float(data[key])
                        break

            # إذا فشل الـ API، نحاول HTML
            if foreign_pct is None:
                page_url = f"https://www.tadawul.com.sa/wps/portal/tadawul/markets/equities/securities/security-details/?security={symbol}"
                page = requests.get(page_url, headers=headers, timeout=15)
                if page.status_code == 200:
                    for pattern in [
                        r'foreignOwnership["\s:]+([0-9.]+)',
                        r'foreign[_-]?ownership["\s:]+([0-9.]+)',
                        r'"foreignPercentage"\s*:\s*([0-9.]+)',
                    ]:
                        m = re.search(pattern, page.text, re.IGNORECASE)
                        if m:
                            foreign_pct = float(m.group(1))
                            break

            records.append({
                "date":            today,
                "symbol":          symbol,
                "foreign_own_pct": foreign_pct,
            })

        except Exception as e:
            log.warning(f"⚠️  أجانب {symbol}: {e}")
            records.append({"date": today, "symbol": symbol, "foreign_own_pct": None})

    collected = sum(1 for r in records if r["foreign_own_pct"] is not None)
    log.info(f"{'✅' if collected > 0 else '⚠️ '} ملكية أجانب: {collected}/{len(TIER1)} سهم")
    return pd.DataFrame(records)


# ═══════════════════════════════════════════════════════════════════════════
# 6. توزيعات الأرباح من Argaam
# ═══════════════════════════════════════════════════════════════════════════

def collect_dividends() -> pd.DataFrame:
    """
    Argaam RSS يحتوي على إعلانات توزيعات الأرباح فور صدورها.
    نجمع هذا يومياً لمعرفة: هل الإشارة جاءت قبل/بعد إعلان التوزيع؟
    """
    today    = str(date.today())
    records  = []
    div_keys = ["توزيع", "أرباح", "dividend", "ريال للسهم", "توزيعات"]

    feeds = [
        "https://www.argaam.com/ar/rss/dividends",
        "https://www.argaam.com/ar/rss/companies",
    ]

    for feed_url in feeds:
        try:
            resp = requests.get(feed_url, timeout=10,
                                headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                continue

            text  = resp.text
            items = re.findall(r'<item>(.*?)</item>', text, re.DOTALL)

            for item in items[:30]:
                title_m = re.search(r'<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>', item)
                if not title_m:
                    continue
                title = (title_m.group(1) or title_m.group(2) or "").strip()

                if not any(k in title for k in div_keys):
                    continue

                symbol_m = re.search(r'\b([0-9]{4})\b', title)
                amount_m = re.search(r'([0-9]+\.?[0-9]*)\s*ريال', title)

                records.append({
                    "date":            today,
                    "symbol":          symbol_m.group(1) if symbol_m else None,
                    "title":           title,
                    "dividend_amount": float(amount_m.group(1)) if amount_m else None,
                    "source":          "argaam",
                })

        except Exception as e:
            log.warning(f"⚠️  Argaam {feed_url}: {e}")

    # إزالة المكرر
    if records:
        df = pd.DataFrame(records).drop_duplicates(subset=["title"])
        log.info(f"✅ توزيعات: {len(df)} إعلان")
        return df

    return pd.DataFrame(columns=["date", "symbol", "title", "dividend_amount", "source"])


# ═══════════════════════════════════════════════════════════════════════════
# 7. المؤشرات الخارجية
# ═══════════════════════════════════════════════════════════════════════════

def collect_external() -> pd.DataFrame:
    log.info("جمع المؤشرات الخارجية...")
    today  = date.today()
    record = {"date": str(today)}

    for ticker, name in EXTERNAL_TICKERS.items():
        try:
            df = yf.download(ticker, period="3d", progress=False, auto_adjust=True)
            if df.empty:
                record[f"{name}_close"]      = None
                record[f"{name}_change_pct"] = None
                continue

            row   = df.iloc[-1]
            close = round(float(row["Close"]), 4)
            record[f"{name}_close"] = close

            if len(df) >= 2:
                prev = float(df.iloc[-2]["Close"])
                record[f"{name}_change_pct"] = round(((close - prev) / prev) * 100, 4)
            else:
                record[f"{name}_change_pct"] = None

        except Exception as e:
            log.warning(f"⚠️  {ticker}: {e}")
            record[f"{name}_close"]      = None
            record[f"{name}_change_pct"] = None

    return pd.DataFrame([record])


# ═══════════════════════════════════════════════════════════════════════════
# 8. اتساع السوق
# ═══════════════════════════════════════════════════════════════════════════

def calculate_market_breadth(stocks_df: pd.DataFrame) -> dict:
    if stocks_df.empty or "change_pct" not in stocks_df.columns:
        return {}

    valid      = stocks_df["change_pct"].dropna()
    advancing  = int((valid > 0).sum())
    declining  = int((valid < 0).sum())
    unchanged  = int((valid == 0).sum())
    strong_up  = int((valid >= 2).sum())
    strong_dn  = int((valid <= -2).sum())
    total_vol  = int(stocks_df["volume_sar"].sum()) if "volume_sar" in stocks_df.columns else 0

    return {
        "date":                    str(date.today()),
        "total_stocks":            len(valid),
        "advancing":               advancing,
        "declining":               declining,
        "unchanged":               unchanged,
        "advance_decline_ratio":   round(advancing / declining, 3) if declining > 0 else None,
        "strong_up_2pct":          strong_up,
        "strong_dn_2pct":          strong_dn,
        "market_strength_ratio":   round(strong_up / strong_dn, 3) if strong_dn > 0 else None,
        "circuit_breaker_up":      int((valid >= 9.9).sum()),
        "circuit_breaker_down":    int((valid <= -9.9).sum()),
        "avg_change_pct":          round(float(valid.mean()), 4),
        "median_change_pct":       round(float(valid.median()), 4),
        "total_market_volume_sar": total_vol,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 9. حفظ البيانات
# ═══════════════════════════════════════════════════════════════════════════

def save_parquet(df: pd.DataFrame, path: Path):
    if df.empty:
        log.warning(f"⚠️  فارغ - لم يُحفظ: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    today_str = str(date.today())
    if path.exists():
        existing = pd.read_parquet(path)
        if "date" in existing.columns:
            existing = existing[existing["date"] != today_str]
        df = pd.concat([existing, df], ignore_index=True)
    df.to_parquet(path, index=False, compression="snappy")
    log.info(f"💾 {path.name} ({len(df)} صف)")


def update_collection_log(status: str, details: dict):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / "collection_log.json"
    logs = json.load(open(path)) if path.exists() else []
    logs.append({"date": str(date.today()),
                 "timestamp": datetime.utcnow().isoformat(),
                 "status": status, **details})
    logs = logs[-365:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    log.info("=" * 65)
    log.info("🚀 tadawul-data-lake v2 | بدء الجمع")
    log.info(f"📅 {date.today()} | UTC {datetime.utcnow().strftime('%H:%M')}")
    log.info("=" * 65)

    # 1. هل السوق فتح؟
    if not is_market_open_today():
        update_collection_log("SKIPPED", {"reason": "السوق مغلق"})
        log.info("✅ خروج نظيف")
        sys.exit(0)

    # 2. الأسهم
    stocks_df = collect_stocks()
    if stocks_df.empty:
        update_collection_log("FAILED", {"reason": "فشل جمع الأسهم"})
        sys.exit(1)

    # 3. القطاعات
    sectors_df = calculate_sector_performance(stocks_df)

    # 4. اتساع السوق
    breadth = calculate_market_breadth(stocks_df)

    # 5. الخارجي
    external_df = collect_external()

    # 6. SAIBOR
    saibor    = collect_saibor()
    saibor_df = pd.DataFrame([saibor])

    # 7. ملكية الأجانب
    foreign_df = collect_foreign_ownership()

    # 8. توزيعات
    dividends_df = collect_dividends()

    # ── الحفظ ────────────────────────────────────────────────────────
    today = date.today()

    save_parquet(stocks_df,           DATA_DIR / "stocks"   / "all_stocks.parquet")
    save_parquet(sectors_df,          DATA_DIR / "market"   / "sector_performance.parquet")
    save_parquet(pd.DataFrame([breadth]), DATA_DIR / "market" / "market_breadth.parquet")
    save_parquet(external_df,         DATA_DIR / "external" / "external_factors.parquet")
    save_parquet(saibor_df,           DATA_DIR / "external" / "saibor.parquet")

    if not foreign_df.empty:
        save_parquet(foreign_df,      DATA_DIR / "market"   / "foreign_ownership.parquet")
    if not dividends_df.empty:
        save_parquet(dividends_df,    DATA_DIR / "events"   / "dividends.parquet")

    # Snapshot يومي
    snap = DATA_DIR / "stocks" / "daily" / f"{today}.parquet"
    snap.parent.mkdir(parents=True, exist_ok=True)
    stocks_df.to_parquet(snap, index=False, compression="snappy")

    # ── السجل ────────────────────────────────────────────────────────
    update_collection_log("SUCCESS", {
        "stocks_collected":       len(stocks_df),
        "stocks_expected":        len(ALL_TICKERS),
        "coverage_pct":           round(len(stocks_df) / len(ALL_TICKERS) * 100, 1),
        "advancing":              breadth.get("advancing", 0),
        "declining":              breadth.get("declining", 0),
        "market_volume_sar_bn":   round(breadth.get("total_market_volume_sar", 0) / 1e9, 2),
        "tasi_close":             external_df.get("tasi_close", [None]).iloc[0]
                                  if "tasi_close" in external_df.columns else None,
        "brent_close":            external_df.get("brent_crude_close", [None]).iloc[0]
                                  if "brent_crude_close" in external_df.columns else None,
        "saibor_3m":              saibor.get("saibor_3m"),
        "sectors_collected":      len(sectors_df),
        "foreign_stocks":         len(foreign_df),
        "dividend_announcements": len(dividends_df),
    })

    vol_bn = round(breadth.get("total_market_volume_sar", 0) / 1e9, 1)
    log.info("=" * 65)
    log.info(f"✅ اكتمل | {len(stocks_df)} سهم | "
             f"↑{breadth.get('advancing','?')} ↓{breadth.get('declining','?')} | "
             f"حجم: {vol_bn}B ر.س | SAIBOR 3M: {saibor.get('saibor_3m', 'N/A')}%")
    log.info("=" * 65)


if __name__ == "__main__":
    main()
