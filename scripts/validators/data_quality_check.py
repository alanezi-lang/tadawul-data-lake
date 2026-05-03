"""
tadawul-data-lake | data_quality_check.py (v3)
يفحص جودة كل ملفات البيانات بعد كل جمع
الإضافة: إذا السوق كان مغلقاً اليوم → يخرج بنجاح بدون فحص
"""

import pandas as pd
import json
from datetime import date, datetime
from pathlib import Path
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

ROOT        = Path(__file__).resolve().parents[2]
DATA_DIR    = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
TODAY       = str(date.today())


def was_market_closed_today() -> bool:
    """يتحقق من سجل الجمع - إذا SKIPPED = السوق مغلق"""
    log_path = REPORTS_DIR / "collection_log.json"
    if not log_path.exists():
        return False
    try:
        logs = json.load(open(log_path))
        if logs and logs[-1].get("date") == TODAY and logs[-1].get("status") == "SKIPPED":
            return True
    except Exception:
        pass
    return False


def check(issues, level, check_name, message, **kwargs):
    issues.append({"level": level, "check": check_name, "message": message, **kwargs})
    symbol = "❌" if level == "CRITICAL" else ("⚠️ " if level == "WARNING" else "ℹ️ ")
    log.log(
        logging.ERROR   if level == "CRITICAL" else
        logging.WARNING if level == "WARNING"  else logging.INFO,
        f"{symbol} {level}: {message}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# فحوصات
# ═══════════════════════════════════════════════════════════════════════════

def check_stocks(issues):
    path = DATA_DIR / "stocks" / "daily" / f"{TODAY}.parquet"
    if not path.exists():
        check(issues, "CRITICAL", "stocks_file", f"ملف الأسهم غير موجود: {path.name}")
        return

    df = pd.read_parquet(path)
    n  = len(df)

    if n < 150:
        check(issues, "CRITICAL", "stock_count", f"أسهم مجموعة {n} أقل من 150", value=n)
    elif n < 220:
        check(issues, "WARNING", "stock_count", f"أسهم مجموعة {n} أقل من المتوقع", value=n)
    else:
        log.info(f"✅ الأسهم: {n} سهم")

    if "close" in df.columns:
        bad = df[df["close"] <= 0]
        if len(bad):
            check(issues, "CRITICAL", "negative_prices",
                  f"{len(bad)} سهم بسعر صفر أو سالب",
                  symbols=bad["symbol"].tolist())

    if "change_pct" in df.columns:
        extreme = df[df["change_pct"].abs() > 10.5]
        if len(extreme):
            check(issues, "WARNING", "circuit_breaker",
                  f"{len(extreme)} سهم تجاوز 10.5% - راجع يدوياً",
                  symbols=extreme["symbol"].tolist())

    if "volume_sar" not in df.columns:
        check(issues, "WARNING", "volume_sar_missing", "عمود volume_sar غير موجود")
    else:
        zero_vol = df[df["volume_sar"] == 0]
        if len(zero_vol) > 15:
            check(issues, "WARNING", "zero_volume_sar",
                  f"{len(zero_vol)} سهم بحجم تداول صفر بالريال")

    if "sector" in df.columns:
        unknown = df[df["sector"] == "Unknown"]
        if len(unknown) > 5:
            check(issues, "WARNING", "unknown_sectors",
                  f"{len(unknown)} سهم قطاعه غير معروف",
                  symbols=unknown["symbol"].tolist()[:10])


def check_sectors(issues):
    path = DATA_DIR / "market" / "sector_performance.parquet"
    if not path.exists():
        check(issues, "WARNING", "sectors_file", "ملف القطاعات غير موجود")
        return
    df    = pd.read_parquet(path)
    today = df[df["date"] == TODAY] if "date" in df.columns else pd.DataFrame()
    if today.empty:
        check(issues, "WARNING", "sectors_today", "لا بيانات قطاعات لليوم")
    else:
        log.info(f"✅ القطاعات: {len(today)} قطاع")


def check_external(issues):
    path = DATA_DIR / "external" / "external_factors.parquet"
    if not path.exists():
        check(issues, "CRITICAL", "external_file", "ملف البيانات الخارجية غير موجود")
        return
    df    = pd.read_parquet(path)
    today = df[df["date"] == TODAY] if "date" in df.columns else pd.DataFrame()
    if today.empty:
        check(issues, "CRITICAL", "external_today", "لا بيانات خارجية لليوم")
        return
    row = today.iloc[0]
    for col in ["brent_crude_close", "tasi_close", "vix_close", "us_10y_yield_close"]:
        if col not in row or pd.isna(row[col]):
            check(issues, "WARNING", f"missing_{col}", f"بيانات ناقصة: {col}")
    log.info(f"✅ الخارجي: Brent={row.get('brent_crude_close','N/A')} | "
             f"VIX={row.get('vix_close','N/A')} | TASI={row.get('tasi_close','N/A')}")


def check_saibor(issues):
    path = DATA_DIR / "external" / "saibor.parquet"
    if not path.exists():
        check(issues, "INFO", "saibor_file", "ملف SAIBOR غير موجود بعد")
        return
    df    = pd.read_parquet(path)
    today = df[df["date"] == TODAY] if "date" in df.columns else pd.DataFrame()
    if today.empty:
        check(issues, "INFO", "saibor_today", "لا بيانات SAIBOR لليوم")
    elif pd.isna(today.iloc[0].get("saibor_3m")):
        check(issues, "INFO", "saibor_3m_null", "SAIBOR 3M = None - راجع URL ساما")
    else:
        log.info(f"✅ SAIBOR 3M: {today.iloc[0]['saibor_3m']}%")


def check_foreign_ownership(issues):
    path = DATA_DIR / "market" / "foreign_ownership.parquet"
    if not path.exists():
        check(issues, "INFO", "foreign_file", "ملف ملكية الأجانب غير موجود بعد")
        return
    df    = pd.read_parquet(path)
    today = df[df["date"] == TODAY] if "date" in df.columns else pd.DataFrame()
    if today.empty:
        check(issues, "INFO", "foreign_today", "لا بيانات ملكية أجانب لليوم")
        return
    non_null = today["foreign_own_pct"].notna().sum()
    log.info(f"✅ ملكية أجانب: {non_null}/{len(today)} سهم")
    if non_null == 0:
        check(issues, "WARNING", "foreign_all_null",
              "ملكية الأجانب كلها None - راجع URL تداول")


def check_continuity(issues):
    daily_dir = DATA_DIR / "stocks" / "daily"
    if not daily_dir.exists():
        return
    files = sorted(daily_dir.glob("*.parquet"))
    if len(files) < 2:
        return
    dates = [datetime.strptime(f.stem, "%Y-%m-%d").date() for f in files]
    for i in range(1, len(dates)):
        gap = (dates[i] - dates[i - 1]).days
        if gap > 4:
            check(issues, "INFO", "data_gap",
                  f"فجوة {gap} أيام: {dates[i-1]} → {dates[i]}",
                  gap_days=gap)
    log.info(f"✅ الاستمرارية: {len(files)} يوم تداول مسجّل")


# ═══════════════════════════════════════════════════════════════════════════
# تقرير + حفظ
# ═══════════════════════════════════════════════════════════════════════════

def save_report(report: dict):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / "quality_log.json"
    logs = json.load(open(path)) if path.exists() else []
    logs.append(report)
    logs = logs[-365:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)


def main():
    log.info("🔍 فحص جودة البيانات...")

    # ── الخطوة الأولى: هل السوق كان مغلقاً اليوم؟ ──────────────────
    if was_market_closed_today():
        log.info("⛔ السوق مغلق اليوم - تخطي الفحص ✅")
        save_report({
            "date":      TODAY,
            "timestamp": datetime.utcnow().isoformat(),
            "status":    "SKIPPED",
            "reason":    "السوق مغلق",
            "summary":   {"critical": 0, "warnings": 0, "info": 0},
            "issues":    [],
        })
        sys.exit(0)

    # ── الخطوة الثانية: السوق فتح → افحص كل شيء ────────────────────
    issues = []
    check_stocks(issues)
    check_sectors(issues)
    check_external(issues)
    check_saibor(issues)
    check_foreign_ownership(issues)
    check_continuity(issues)

    critical = [i for i in issues if i["level"] == "CRITICAL"]
    warnings = [i for i in issues if i["level"] == "WARNING"]
    info     = [i for i in issues if i["level"] == "INFO"]

    status = "FAIL" if critical else ("WARN" if warnings else "PASS")

    save_report({
        "date":      TODAY,
        "timestamp": datetime.utcnow().isoformat(),
        "status":    status,
        "summary":   {"critical": len(critical), "warnings": len(warnings), "info": len(info)},
        "issues":    issues,
    })

    log.info(f"النتيجة: {status} | ❌{len(critical)} ⚠️{len(warnings)} ℹ️{len(info)}")

    if critical:
        log.error("❌ فشل الفحص")
        sys.exit(1)

    log.info("✅ الفحص اجتاز")


if __name__ == "__main__":
    main()
