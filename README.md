# 📊 tadawul-data-lake

مستودع بيانات السوق السعودي (تداول) - يجمع بيانات يومية تلقائياً لبناء قاعدة بيانات تاريخية موثوقة.

---

## 🎯 الهدف

جمع بيانات يومية كاملة لكل أسهم السوق السعودي + المؤشرات الخارجية (النفط، الذهب، الفائدة الأمريكية) لمدة سنة+، بهدف:

- تحليل الارتباطات بين الأسهم والنفط
- بناء Lead-Lag relationships بين القطاعات
- Walk-forward backtesting لأنظمة التداول
- اكتشاف أنماط موسمية (رمضان، الحج، إعلانات أرباح)

---

## 📁 هيكل المشروع

```
tadawul-data-lake/
├── .github/workflows/
│   └── daily_collect.yml      # يشتغل تلقائياً 6:15م الرياض الأحد-الخميس
├── scripts/
│   ├── collectors/
│   │   └── collect_daily.py   # الجامع الرئيسي
│   └── validators/
│       └── data_quality_check.py
├── data/
│   ├── stocks/
│   │   ├── all_stocks.parquet  # بيانات تراكمية لكل الأسهم
│   │   └── daily/             # snapshot يومي منفصل
│   ├── external/
│   │   └── external_factors.parquet  # نفط، ذهب، VIX، مؤشرات
│   └── market/
│       └── market_breadth.parquet    # صاعد/هابط/ثابت
├── reports/
│   ├── collection_log.json    # سجل نجاح/فشل كل يوم
│   └── quality_log.json       # سجل فحص الجودة
└── requirements.txt
```

---

## ⏰ الجدول الزمني

| اليوم | وقت التشغيل (الرياض) | وقت التشغيل (UTC) |
|---|---|---|
| الأحد | 6:15 مساءً | 15:15 |
| الاثنين | 6:15 مساءً | 15:15 |
| الثلاثاء | 6:15 مساءً | 15:15 |
| الأربعاء | 6:15 مساءً | 15:15 |
| الخميس | 6:15 مساءً | 15:15 |
| الجمعة | ❌ لا يشتغل | - |
| السبت | ❌ لا يشتغل | - |

> **ملاحظة:** حتى لو اشتغل في يوم إجازة، النظام يتحقق تلقائياً إذا السوق فتح. إذا ما في بيانات جديدة → يخرج بهدوء بدون خطأ.

---

## 📊 البيانات المجموعة

### أسهم تداول
- OHLCV (open, high, low, close, volume) لكل الأسهم
- تغيير % يومي
- جميع القطاعات: Banking, Petrochemicals, Energy, Materials, Cement, REITs, Insurance, Retail, Telecom, Healthcare, Transportation

### مؤشرات خارجية
| المؤشر | المصدر | الهدف |
|---|---|---|
| Brent + WTI Crude | Yahoo Finance | ارتباط مع البتروكيماويات والطاقة |
| الذهب + الفضة | Yahoo Finance | risk-off indicator |
| VIX | Yahoo Finance | قياس الخوف العالمي |
| S&P 500, NASDAQ, FTSE, Nikkei, Shanghai | Yahoo Finance | مؤشرات عالمية |
| USD Index | Yahoo Finance | قوة الدولار |
| US 10Y + 3M Yield | Yahoo Finance | منحنى الفائدة |
| EUR/USD | Yahoo Finance | عملات |
| TASI | Yahoo Finance | المؤشر الرئيسي |
| اليوريا، الميثانول | Yahoo Finance | proxy للبتروكيماويات |
| **SAIBOR 1W/1M/3M/6M/12M** | **ساما** | **الفائدة السعودية → تأثير على البنوك** |

### أداء القطاعات اليومي
- 14 قطاع: Banking, Energy, Petrochemicals, Cement, Materials, Financial, Insurance, Consumer, Telecom, Healthcare, REITs, Utilities, Food, Transportation
- لكل قطاع: متوسط تغيير%، إجمالي volume_sar، عدد صاعد/هابط، أفضل/أسوأ سهم

### اتساع السوق (Market Breadth)
- عدد الأسهم الصاعدة / الهابطة / الثابتة
- نسبة Advance/Decline
- عدد الأسهم عند circuit breaker (±9.9%)
- عدد الأسهم القوية (>2% أو <-2%)
- إجمالي حجم التداول اليومي بالريال

### ملكية الأجانب
- نسبة الملكية الأجنبية لأعلى 20 سهم سيولة
- يُظهر دخول/خروج المؤسسات الأجنبية

### إعلانات توزيعات الأرباح
- يجمع من Argaam RSS يومياً
- يحفظ: رمز السهم، المبلغ، تاريخ الإعلان

---

## 🔧 التشغيل اليدوي

```bash
# تثبيت المكتبات
pip install -r requirements.txt

# تشغيل الجمع
python scripts/collectors/collect_daily.py

# فحص الجودة
python scripts/validators/data_quality_check.py
```

---

## 📈 كيف تستخدم البيانات بعد سنة

```python
import pandas as pd

# قراءة كل البيانات التاريخية
stocks = pd.read_parquet("data/stocks/all_stocks.parquet")
external = pd.read_parquet("data/external/external_factors.parquet")

# ارتباط الأسهم بالنفط
oil = external[["date", "brent_crude_close", "brent_crude_change_pct"]]
aramco = stocks[stocks["symbol"] == "2222"][["date", "change_pct"]]

merged = aramco.merge(oil, on="date")
correlation = merged["change_pct"].corr(merged["brent_crude_change_pct"])
print(f"ارتباط أرامكو بالنفط: {correlation:.3f}")

# Lead-Lag: هل الراجحي يقود البنوك؟
rajhi = stocks[stocks["symbol"] == "1120"].set_index("date")["change_pct"]
alinma = stocks[stocks["symbol"] == "1150"].set_index("date")["change_pct"]

for lag in range(1, 6):
    corr = rajhi.corr(alinma.shift(-lag))
    print(f"الراجحي يقود الإنماء بـ {lag} يوم: {corr:.3f}")
```

---

## 📝 سجل الجمع

راجع `reports/collection_log.json` لتاريخ كامل من نجاح/فشل كل يوم.

---

*بُني بـ Python + yfinance + GitHub Actions*
*يجمع بيانات السوق السعودي تلقائياً منذ مايو 2026*
