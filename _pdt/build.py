#!/usr/bin/env python3
"""Priority Date Tracker 网页：每期排期公布后，按类别逐篇发布「本期排期变化分析」，中/英/西三语。

数据：_pdt/data/bulletins.json（每期每格的表A/表B）、uscis-charts.json（USCIS 当月用哪张表）。
节奏：新一期数据进来后，每个自然日最多发 ARTICLES_PER_DAY 篇，按 GROUPS 顺序（三种语言同时）。
内容：表格、USCIS 用表由程序按数据生成；解读文字（总结、分析、常见问题）由 Claude 写、再由 Claude 审稿 + 程序核对数字（见 ai.py），
      三关都过才发。审过的稿存在 _pdt/drafts/，带数据指纹；当期数据有变（补格子、USCIS 公布用表）就重写重审。
已发布的记在 published.json，只增不删；以前各期的文章生成后不再改动。
刻意不放逐月历史表：网页只给本期、上期，以及近 6 / 12 个月的汇总——完整历史留给 app。

用法（站点根目录）：python3 _pdt/build.py [--today YYYY-MM-DD] [--no-ai]
写新稿要能运行 Claude Code 命令行 `claude`（CI 里用订阅令牌 CLAUDE_CODE_OAUTH_TOKEN 登录）；
找不到 `claude` 或加 --no-ai 时只用已审过的稿重新生成页面。
标准输出：本次新发布的 URL（每行一个），给 IndexNow 推送用。
"""
import argparse, datetime as dt, hashlib, html, json, pathlib, re, shutil, sys, zoneinfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "_pdt" / "data"
DRAFTS = ROOT / "_pdt" / "drafts"
SECTION = "priority-date-tracker"
OUT = ROOT / SECTION
SITE = "https://autodecoder.app"
ARTICLES_PER_DAY = 2
MAX_NEW_WRITES_PER_RUN = 3   # 每次运行最多为几篇新文章写稿（防止审稿一直不过时反复花钱）

APP_STORE = "https://apps.apple.com/us/app/priority-date-tracker/id6753199018"
PLAY = "https://play.google.com/store/apps/details?id=autodecoderllc.prioritydatetracker"
USCIS_CHARTS = "https://www.uscis.gov/green-card/green-card-processes-and-procedures/visa-availability-priority-dates/adjustment-of-status-filing-charts-from-the-visa-bulletin"

# 发布顺序 = 关注度（大致按申请人数）。每篇可含多个子类别（EB-3 含 Other Workers，EB-5 含四个细分类）
GROUPS = [
    ("eb-2", ["EB-2"]),
    ("eb-3", ["EB-3", "EB-3 Other Workers"]),
    ("eb-1", ["EB-1"]),
    ("f2a", ["F2A"]),
    ("f4", ["F4"]),
    ("eb-5", ["EB-5 Unreserved", "EB-5 Rural", "EB-5 High Unemployment", "EB-5 Infrastructure"]),
    ("f1", ["F1"]),
    ("f3", ["F3"]),
    ("f2b", ["F2B"]),
    ("eb-4", ["EB-4", "EB-4 Religious Workers"]),
]
COUNTRIES = ["All Chargeability", "CHINA-mainland", "INDIA", "MEXICO", "PHILIPPINES"]
LANGS = ["en", "zh", "es"]
HREFLANG = {"en": "en", "zh": "zh-Hans", "es": "es"}

# ---------- 文案 ----------

CAT_NAME = {
    "en": {"EB-1": "EB-1 Priority Workers", "EB-2": "EB-2 Advanced Degree Professionals", "EB-3": "EB-3 Skilled Workers and Professionals",
           "EB-3 Other Workers": "EB-3 Other Workers", "EB-4": "EB-4 Special Immigrants", "EB-4 Religious Workers": "EB-4 Religious Workers",
           "EB-5 Unreserved": "EB-5 Unreserved", "EB-5 Rural": "EB-5 Rural (set-aside)", "EB-5 High Unemployment": "EB-5 High Unemployment (set-aside)",
           "EB-5 Infrastructure": "EB-5 Infrastructure (set-aside)", "F1": "F1 Unmarried Sons and Daughters of U.S. Citizens",
           "F2A": "F2A Spouses and Children of Permanent Residents", "F2B": "F2B Unmarried Adult Sons and Daughters of Permanent Residents",
           "F3": "F3 Married Sons and Daughters of U.S. Citizens", "F4": "F4 Brothers and Sisters of Adult U.S. Citizens"},
    "zh": {"EB-1": "EB-1 杰出人才", "EB-2": "EB-2 高学历专业人士", "EB-3": "EB-3 技术工人", "EB-3 Other Workers": "EB-3 非技术劳工",
           "EB-4": "EB-4 特殊移民", "EB-4 Religious Workers": "EB-4 宗教工作者", "EB-5 Unreserved": "EB-5 非保留类",
           "EB-5 Rural": "EB-5 乡村地区（保留类）", "EB-5 High Unemployment": "EB-5 高失业率地区（保留类）", "EB-5 Infrastructure": "EB-5 基础设施项目（保留类）",
           "F1": "F1 美国公民的未婚子女", "F2A": "F2A 永久居民的配偶及未成年子女", "F2B": "F2B 永久居民的未婚成年子女",
           "F3": "F3 美国公民的已婚子女", "F4": "F4 美国公民的兄弟姐妹"},
    "es": {"EB-1": "EB-1 Trabajadores Prioritarios", "EB-2": "EB-2 Profesionales con Títulos Avanzados", "EB-3": "EB-3 Trabajadores Calificados y Profesionales",
           "EB-3 Other Workers": "EB-3 Otros Trabajadores", "EB-4": "EB-4 Inmigrantes Especiales", "EB-4 Religious Workers": "EB-4 Trabajadores Religiosos",
           "EB-5 Unreserved": "EB-5 No Reservado", "EB-5 Rural": "EB-5 Rural (reservado)", "EB-5 High Unemployment": "EB-5 Alto Desempleo (reservado)",
           "EB-5 Infrastructure": "EB-5 Infraestructura (reservado)", "F1": "F1 Hijos Solteros de Ciudadanos",
           "F2A": "F2A Cónyuges e Hijos de Residentes Permanentes", "F2B": "F2B Hijos Adultos Solteros de Residentes Permanentes",
           "F3": "F3 Hijos Casados de Ciudadanos", "F4": "F4 Hermanos de Ciudadanos"},
}
COUNTRY_NAME = {
    "en": {"All Chargeability": "All other countries", "CHINA-mainland": "China (mainland-born)", "INDIA": "India", "MEXICO": "Mexico", "PHILIPPINES": "Philippines"},
    "zh": {"All Chargeability": "其他国家和地区", "CHINA-mainland": "中国大陆", "INDIA": "印度", "MEXICO": "墨西哥", "PHILIPPINES": "菲律宾"},
    "es": {"All Chargeability": "Resto del mundo", "CHINA-mainland": "China continental", "INDIA": "India", "MEXICO": "México", "PHILIPPINES": "Filipinas"},
}
# 多子类别文章在目录里的名字（单类别用 CAT_NAME）
GROUP_NAME = {
    "en": {"eb-5": "EB-5 Immigrant Investors (Unreserved and set-asides)"},
    "zh": {"eb-5": "EB-5 投资移民（非保留类与保留类）"},
    "es": {"eb-5": "EB-5 Inversionistas (no reservado y reservados)"},
}
MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
}
T = {
    "en": {
        "brand": "Priority Date Tracker", "home": "Autodecoder", "bulletins": "Visa Bulletin analysis",
        "table_a": "Final Action Dates (Table A)", "table_b": "Dates for Filing (Table B)",
        "col_country": "Chargeability", "col_change": "Change",
        "current": "Current (C)", "unavailable": "Unavailable (U)", "na": "—",
        "published": "Published", "data": "Data", "source": "Source",
        "official": "official Visa Bulletin", "uscis_page": "USCIS filing charts",
        "chart_h": "Which chart can you use to file Form I-485 this month?",
        "trend_h": "Last 6 and 12 months", "faq_h": "Questions", "more_h": "Other categories in this bulletin",
        "gap_h": "How far ahead is Table B?",
        "cta_h": "Get notified when your category moves",
        "cta_p": "Priority Date Tracker shows the latest Final Action and Filing dates for every EB and family category, with history charts. Premium sends a push notification the moment a new Visa Bulletin is out — and when your priority date becomes current.",
        "cta_ios": "Download on the App Store", "cta_android": "Get it on Google Play",
        "disclaimer": "For information only, not legal advice. Dates are from the U.S. Department of State Visa Bulletin; always confirm with the official bulletin and USCIS before filing. Priority Date Tracker is not affiliated with any government agency.",
        "attribution": "Analysis by Priority Date Tracker of U.S. Department of State Visa Bulletin data.",
        "hub_title": "Visa Bulletin Analysis by Category — Priority Date Tracker",
        "hub_h1": "Visa Bulletin analysis, one category at a time",
        "hub_intro": "Every month we break down the new Visa Bulletin category by category: Final Action Dates (Table A), Dates for Filing (Table B), which chart USCIS accepts, how each country moved since last month, and the 6- and 12-month trend.",
        "hub_app_h": "The app", "hub_latest": "Latest bulletin", "hub_older": "Earlier bulletins",
        "hub_desc": "Monthly Visa Bulletin analysis for EB-1, EB-2, EB-3, EB-4, EB-5 and family categories F1–F4: Final Action and Filing dates by country, changes, and trends.",
        "app_line": "Track U.S. Visa Bulletin priority dates for employment-based and family-based green cards on iPhone and Android. Free to use; Premium adds notifications and full history.",
    },
    "zh": {
        "brand": "Priority Date Tracker 绿卡排期追踪", "home": "Autodecoder", "bulletins": "排期分析",
        "table_a": "表A：最终裁定日（Final Action Dates）", "table_b": "表B：递交日（Dates for Filing）",
        "col_country": "国家/地区", "col_change": "变化",
        "current": "C（无排期）", "unavailable": "U（暂不可用）", "na": "—",
        "published": "发布", "data": "数据", "source": "来源",
        "official": "美国国务院排期公告原文", "uscis_page": "USCIS 递交用表说明",
        "chart_h": "本月递交 I-485 用表A还是表B？",
        "trend_h": "近 6 个月与 12 个月走势", "faq_h": "常见问题", "more_h": "本期其他类别",
        "gap_h": "表B 比表A 提前多少？",
        "cta_h": "排期一动就通知你",
        "cta_p": "Priority Date Tracker 显示所有职业移民与亲属移民类别最新的表A、表B 日期，并有历史走势图。高级版在新一期排期公布时立即推送，你的优先日期排到时也会提醒。",
        "cta_ios": "在 App Store 下载", "cta_android": "在 Google Play 下载",
        "disclaimer": "仅供参考，不构成法律建议。日期来自美国国务院签证公告（Visa Bulletin），递交前请以官方公告及 USCIS 公布为准。Priority Date Tracker 与任何政府机构无关。",
        "attribution": "Priority Date Tracker 根据美国国务院签证公告数据整理分析。",
        "hub_title": "绿卡排期分析（按类别）— Priority Date Tracker",
        "hub_h1": "每期排期，按类别逐一分析",
        "hub_intro": "每期新排期公布后，我们按类别逐一拆解：表A（最终裁定日）、表B（递交日）、USCIS 本月允许用哪张表、各国较上期的变化，以及近 6 个月和 12 个月的走势。",
        "hub_app_h": "App", "hub_latest": "最新一期", "hub_older": "往期",
        "hub_desc": "每月绿卡排期分析：EB-1、EB-2、EB-3、EB-4、EB-5 及亲属移民 F1–F4 各国表A、表B 日期、变化与走势。",
        "app_line": "在 iPhone 和安卓手机上追踪美国签证公告的职业移民和亲属移民排期。免费使用；高级版提供推送通知与完整历史数据。",
    },
    "es": {
        "brand": "Priority Date Tracker", "home": "Autodecoder", "bulletins": "Análisis del Boletín de Visas",
        "table_a": "Fechas de acción final (Tabla A)", "table_b": "Fechas de presentación (Tabla B)",
        "col_country": "País", "col_change": "Cambio",
        "current": "Al día (C)", "unavailable": "No disponible (U)", "na": "—",
        "published": "Publicado", "data": "Datos", "source": "Fuente",
        "official": "Boletín de Visas oficial", "uscis_page": "tablas de USCIS",
        "chart_h": "¿Qué tabla puede usar este mes para presentar el formulario I-485?",
        "trend_h": "Últimos 6 y 12 meses", "faq_h": "Preguntas", "more_h": "Otras categorías de este boletín",
        "gap_h": "¿Cuánto adelanta la Tabla B?",
        "cta_h": "Reciba un aviso cuando su categoría avance",
        "cta_p": "Priority Date Tracker muestra las fechas de acción final y de presentación más recientes de todas las categorías EB y familiares, con gráficos históricos. Premium le envía una notificación en cuanto sale un nuevo Boletín de Visas y cuando su fecha de prioridad queda al día.",
        "cta_ios": "Descargar en el App Store", "cta_android": "Disponible en Google Play",
        "disclaimer": "Solo con fines informativos; no es asesoría legal. Las fechas provienen del Boletín de Visas del Departamento de Estado de EE. UU.; confirme siempre con el boletín oficial y con USCIS antes de presentar. Priority Date Tracker no está afiliado a ninguna agencia gubernamental.",
        "attribution": "Análisis de Priority Date Tracker con datos del Boletín de Visas del Departamento de Estado de EE. UU.",
        "hub_title": "Análisis del Boletín de Visas por categoría — Priority Date Tracker",
        "hub_h1": "El Boletín de Visas, categoría por categoría",
        "hub_intro": "Cada mes analizamos el nuevo Boletín de Visas categoría por categoría: fechas de acción final (Tabla A), fechas de presentación (Tabla B), qué tabla acepta USCIS, cómo avanzó cada país respecto al mes anterior y la tendencia de 6 y 12 meses.",
        "hub_app_h": "La app", "hub_latest": "Boletín más reciente", "hub_older": "Boletines anteriores",
        "hub_desc": "Análisis mensual del Boletín de Visas para EB-1, EB-2, EB-3, EB-4, EB-5 y las categorías familiares F1–F4: fechas por país, cambios y tendencias.",
        "app_line": "Siga las fechas de prioridad del Boletín de Visas de EE. UU. para green cards por empleo y familiares en iPhone y Android. Gratis; Premium agrega notificaciones e historial completo.",
    },
}

# ---------- 数据工具 ----------

def parse_value(v):
    """'01NOV13' → date；'C' → 'C'；'U' → 'U'；缺失 → None。"""
    if not v:
        return None
    v = v.strip().upper()
    if v in ("C", "CURRENT"):
        return "C"
    if v == "U":
        return "U"
    m = re.fullmatch(r"(\d{2})([A-Z]{3})(\d{2})", v)
    if not m:
        return None
    mon = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"].index(m.group(2)) + 1
    yy = int(m.group(3))
    year = 2000 + yy if yy <= dt.date.today().year % 100 + 5 else 1900 + yy
    return dt.date(year, mon, int(m.group(1)))


def month_add(m, k):
    y, mo = map(int, m.split("-"))
    i = y * 12 + mo - 1 + k
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def month_label(m, lang):
    y, mo = map(int, m.split("-"))
    if lang == "zh":
        return f"{y}年{mo}月"
    if lang == "es":
        return f"{MONTHS['es'][mo - 1]} de {y}"
    return f"{MONTHS['en'][mo - 1]} {y}"


def date_label(d, lang):
    if lang == "zh":
        return f"{d.year}年{d.month}月{d.day}日"
    if lang == "es":
        return f"{d.day} de {MONTHS['es'][d.month - 1]} de {d.year}"
    return f"{MONTHS['en'][d.month - 1][:3]} {d.day}, {d.year}"


def value_label(v, lang):
    if v is None:
        return T[lang]["na"]
    if v == "C":
        return T[lang]["current"]
    if v == "U":
        return T[lang]["unavailable"]
    return date_label(v, lang)


def classify(prev, cur):
    """一步变化：'up' 前进 / 'down' 倒退 / 'same' 不变 / None 无法比较。"""
    if prev is None or cur is None:
        return None
    if prev == cur:
        return "same"
    if cur == "C" or prev == "U":
        return "up"
    if prev == "C" or cur == "U":
        return "down"
    return "up" if cur > prev else "down"


def days_months(days, lang):
    months = abs(days) / 30.44
    if lang == "zh":
        return f"{abs(days)} 天（约 {months:.1f} 个月）"
    if lang == "es":
        return f"{abs(days)} días (≈{months:.1f} meses)"
    return f"{abs(days)} days (≈{months:.1f} months)"


def change_label(prev, cur, lang):
    """与上期相比的变化（表格「变化」列）。"""
    k = classify(prev, cur)
    L = {
        "en": {"same": "No change", "none": "—", "to_c": "Became current", "from_c": "Retrogressed from current", "to_u": "Became unavailable",
               "from_u": "Reopened", "up": "Advanced ", "down": "Retrogressed "},
        "zh": {"same": "不变", "none": "—", "to_c": "变为 C（无排期）", "from_c": "由 C 倒退", "to_u": "变为 U（暂停）",
               "from_u": "重新开放", "up": "前进 ", "down": "倒退 "},
        "es": {"same": "Sin cambio", "none": "—", "to_c": "Quedó al día", "from_c": "Retrocedió desde al día", "to_u": "Quedó no disponible",
               "from_u": "Se reabrió", "up": "Avanzó ", "down": "Retrocedió "},
    }[lang]
    if k is None:
        return L["none"], ""
    if k == "same":
        return L["same"], "same"
    if cur == "C":
        return L["to_c"], "up"
    if prev == "C":
        return L["from_c"], "down"
    if cur == "U":
        return L["to_u"], "down"
    if prev == "U":
        return L["from_u"], "up"
    return L[k] + days_months((cur - prev).days, lang), k


class Data:
    def __init__(self):
        self.months = json.loads((DATA / "bulletins.json").read_text())["months"]
        p = DATA / "uscis-charts.json"
        self.charts = json.loads(p.read_text()) if p.exists() else {}

    def cell(self, month, cat, country, table):
        e = self.months.get(month)
        c = e and e["cells"].get(f"{cat}|{country}")
        return parse_value(c.get(table)) if c else None

    def complete(self, month, cats):
        """该期和上一期这些类别的 5 国表A/表B 是否都齐（缺格就先不发，等数据补全）。"""
        for m in (month, month_add(month, -1)):
            e = self.months.get(m)
            if not e:
                return False
            for cat in cats:
                for c in COUNTRIES:
                    x = e["cells"].get(f"{cat}|{c}")
                    if not x or not x.get("A") or not x.get("B"):
                        return False
        return True

    def released(self, month):
        r = self.months.get(month, {}).get("released")
        return dt.date.fromisoformat(r) if r else None


def official_url(month):
    y, mo = map(int, month.split("-"))
    fy = y + 1 if mo >= 10 else y
    return f"https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin/{fy}/visa-bulletin-for-{MONTHS['en'][mo - 1].lower()}-{y}.html"


# ---------- 文章内容 ----------

def esc(s):
    return html.escape(s, quote=True)


def url_for(lang, path=""):
    prefix = "" if lang == "en" else f"{lang}/"
    return f"/{SECTION}/{prefix}{path}"


def article_path(month, slug):
    return f"visa-bulletin/{month}/{slug}/"


def group_code(slug):
    return dict(GROUPS)[slug][0].split(" ")[0]   # "EB-3 Other Workers" → "EB-3"


def join_list(items, lang):
    """a、b、c / a, b and c / a, b y c"""
    if lang == "zh":
        return "，".join(items)
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + (" and " if lang == "en" else " y ") + items[-1]


def colon(lang):
    return "：" if lang == "zh" else ": "


def cat_label(cat, lang):
    """句子里的类别名：EB-2 这类代码原样；带子类别的（EB-3 Other Workers）用本地化名。"""
    return cat if " " not in cat else CAT_NAME[lang][cat]


def summary_sentences(data, month, cat, lang):
    """答案先行的总结：本期表A 几个国家前进/不变/倒退，最大变化是哪国。"""
    prev_m = month_add(month, -1)
    rows = []
    for c in COUNTRIES:
        p, n = data.cell(prev_m, cat, c, "A"), data.cell(month, cat, c, "A")
        rows.append((c, p, n, classify(p, n)))
    up = [r for r in rows if r[3] == "up"]
    down = [r for r in rows if r[3] == "down"]
    same = [r for r in rows if r[3] == "same"]
    biggest = max((r for r in up if isinstance(r[1], dt.date) and isinstance(r[2], dt.date)), key=lambda r: (r[2] - r[1]).days, default=None)
    current = [r for r in rows if r[2] == "C"]
    names = lambda rs: [COUNTRY_NAME[lang][r[0]] for r in rs]
    ml = month_label(month, lang)
    cl = cat_label(cat, lang)
    out = []
    if lang == "en":
        parts = []
        if up: parts.append(f"advanced for {len(up)} of 5 chargeability areas")
        if same: parts.append(f"held steady for {len(same)}")
        if down: parts.append(f"retrogressed for {len(down)} ({', '.join(names(down))})")
        out.append(f"In the {ml} Visa Bulletin, {cl} Final Action Dates " + (join_list(parts, lang) if parts else "could not be compared with last month") + ".")
        if biggest:
            out.append(f"The biggest move: {COUNTRY_NAME[lang][biggest[0]]}, up {days_months((biggest[2] - biggest[1]).days, lang)} to {date_label(biggest[2], lang)}.")
        if current:
            out.append(f"{cl} is current (no backlog) for {join_list(names(current), lang)}.")
    elif lang == "zh":
        parts = []
        if up: parts.append(f"{len(up)} 个国家/地区前进")
        if same: parts.append(f"{len(same)} 个不变")
        if down: parts.append(f"{len(down)} 个倒退（{'、'.join(names(down))}）")
        out.append(f"{ml}排期中，{cl} 表A（最终裁定日）" + ("：" + join_list(parts, lang) if parts else "无法与上期比较") + "。")
        if biggest:
            out.append(f"变化最大的是{COUNTRY_NAME[lang][biggest[0]]}，前进 {days_months((biggest[2] - biggest[1]).days, lang)}，到 {date_label(biggest[2], lang)}。")
        if current:
            out.append(f"以下国家/地区 {cl} 为 C（无排期）：{'、'.join(names(current))}。")
    else:
        parts = []
        if up: parts.append(f"avanzaron en {len(up)} de 5 países")
        if same: parts.append(f"no cambiaron en {len(same)}")
        if down: parts.append(f"retrocedieron en {len(down)} ({', '.join(names(down))})")
        out.append(f"En el Boletín de Visas de {ml}, las fechas de acción final de {cl} " + (join_list(parts, lang) if parts else "no se pueden comparar con el mes anterior") + ".")
        if biggest:
            out.append(f"El mayor avance: {COUNTRY_NAME[lang][biggest[0]]}, {days_months((biggest[2] - biggest[1]).days, lang)}, hasta el {date_label(biggest[2], lang)}.")
        if current:
            out.append(f"{cl} está al día (sin espera) para {join_list(names(current), lang)}.")
    return out


def chart_family(cat):
    return "family" if cat.startswith("F") else "employment"


def chart_sentence(data, month, cats, lang):
    fam = chart_family(cats[0])
    chart = data.charts.get(month, {}).get(fam)
    ml = month_label(month, lang)
    kind = {"en": {"family": "family-sponsored", "employment": "employment-based"},
            "zh": {"family": "亲属移民", "employment": "职业移民"},
            "es": {"family": "familiares", "employment": "por empleo"}}[lang][fam]
    if chart == "B":
        return {"en": f"For {ml}, USCIS says {kind} applicants must use the Dates for Filing chart (Table B) to file Form I-485. If your priority date is earlier than the Table B date for your category and country, you may be able to file your adjustment of status application this month (other eligibility requirements still apply).",
                "zh": f"USCIS 公布：{ml}{kind}递交 I-485 使用表B（Dates for Filing）。优先日期早于你所在类别、国家的表B 日期的申请人，本月一般可以递交调整身份申请（I-485），其他条件仍需符合。",
                "es": f"Para {ml}, USCIS indica que los solicitantes {kind} deben usar la tabla de fechas de presentación (Tabla B) para presentar el formulario I-485. Si su fecha de prioridad es anterior a la fecha de la Tabla B de su categoría y país, es posible que pueda presentar su solicitud de ajuste de estatus este mes (deben cumplirse los demás requisitos)."}[lang], "B"
    if chart == "A":
        return {"en": f"For {ml}, USCIS says {kind} applicants must use the Final Action Dates chart (Table A) to file Form I-485 — Table B cannot be used this month.",
                "zh": f"USCIS 公布：{ml}{kind}递交 I-485 只能用表A（Final Action Dates），本月不开放表B。",
                "es": f"Para {ml}, USCIS indica que los solicitantes {kind} deben usar la tabla de fechas de acción final (Tabla A) para presentar el formulario I-485; este mes no se puede usar la Tabla B."}[lang], "A"
    return {"en": f"USCIS has not yet announced which chart {kind} applicants must use for {ml}. It usually does so within about a week of the Visa Bulletin; check the USCIS filing charts page before filing.",
            "zh": f"USCIS 尚未公布{ml}{kind}用表A还是表B，通常在排期公布后一周左右公布。递交前请查看 USCIS 用表页面。",
            "es": f"USCIS aún no ha anunciado qué tabla deben usar los solicitantes {kind} en {ml}. Suele hacerlo alrededor de una semana después del boletín; consulte la página de USCIS antes de presentar."}[lang], None


def trend_lines(data, month, cat, country, lang):
    """近 6 / 12 期：表A 起止、净变化、前进/不变/倒退期数。只给汇总，不列逐月。"""
    out = []
    for span in (6, 12):
        start = month_add(month, -span)
        a0, a1 = data.cell(start, cat, country, "A"), data.cell(month, cat, country, "A")
        steps = [classify(data.cell(month_add(start, i), cat, country, "A"), data.cell(month_add(start, i + 1), cat, country, "A")) for i in range(span)]
        if a0 is None or a1 is None or None in steps:
            continue
        up, same, down = steps.count("up"), steps.count("same"), steps.count("down")
        if isinstance(a0, dt.date) and isinstance(a1, dt.date):
            d = (a1 - a0).days
            net = {"en": ("net +" if d > 0 else "net −" if d < 0 else "net ") + (days_months(d, lang) if d else "0"),
                   "zh": ("净前进 " if d > 0 else "净倒退 " if d < 0 else "净变化 ") + (days_months(d, lang) if d else "0"),
                   "es": ("neto +" if d > 0 else "neto −" if d < 0 else "neto ") + (days_months(d, lang) if d else "0")}[lang]
        else:
            net = ""
        sl = month_label(start, lang)
        if lang == "en":
            s = f"Last {span} bulletins (since {sl}): {value_label(a0, lang)} → {value_label(a1, lang)}" + (f", {net}" if net else "") + f". Advanced in {up}, unchanged in {same}, retrogressed in {down}."
        elif lang == "zh":
            s = f"近 {span} 期（{sl}起）：{value_label(a0, lang)} → {value_label(a1, lang)}" + (f"，{net}" if net else "") + f"。其中前进 {up} 期、不变 {same} 期、倒退 {down} 期。"
        else:
            s = f"Últimos {span} boletines (desde {sl}): {value_label(a0, lang)} → {value_label(a1, lang)}" + (f", {net}" if net else "") + f". Avanzó en {up}, sin cambio en {same}, retrocedió en {down}."
        out.append(s)
    return out


def gap_line(data, month, cat, country, lang):
    a, b = data.cell(month, cat, country, "A"), data.cell(month, cat, country, "B")
    cn = COUNTRY_NAME[lang][country]
    if isinstance(a, dt.date) and isinstance(b, dt.date):
        d = (b - a).days
        if d == 0:
            return {"en": f"{cn}: Table B equals Table A.", "zh": f"{cn}：表B 与表A 相同。", "es": f"{cn}: la Tabla B es igual a la Tabla A."}[lang]
        return {"en": f"{cn}: Table B is {days_months(d, lang)} {'ahead of' if d > 0 else 'behind'} Table A.",
                "zh": f"{cn}：表B 比表A {'提前' if d > 0 else '落后'} {days_months(d, lang)}。",
                "es": f"{cn}: la Tabla B va {days_months(d, lang)} {'por delante de' if d > 0 else 'por detrás de'} la Tabla A."}[lang]
    if b == "C" and a != "C":
        return {"en": f"{cn}: Table B is current while Table A is {value_label(a, lang)}.",
                "zh": f"{cn}：表B 为 C，表A 为 {value_label(a, lang)}。",
                "es": f"{cn}: la Tabla B está al día y la Tabla A es {value_label(a, lang)}."}[lang]
    return None


def table_html(data, month, cat, table, lang):
    prev_m = month_add(month, -1)
    t = T[lang]
    head = f"<tr><th>{esc(t['col_country'])}</th><th>{esc(month_label(month, lang))}</th><th>{esc(month_label(prev_m, lang))}</th><th>{esc(t['col_change'])}</th></tr>"
    rows = []
    for c in COUNTRIES:
        p, n = data.cell(prev_m, cat, c, table), data.cell(month, cat, c, table)
        label, cls = change_label(p, n, lang)
        rows.append(f"<tr><th scope=\"row\">{esc(COUNTRY_NAME[lang][c])}</th><td><strong>{esc(value_label(n, lang))}</strong></td>"
                    f"<td>{esc(value_label(p, lang))}</td><td class=\"chg {cls}\">{esc(label)}</td></tr>")
    return f"<div class=\"tbl\"><table><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table></div>"


def fact_sheet(data, month, slug, cats, lang):
    """给写稿/审稿的数据表：所有数字都用页面上同样的写法，AI 照抄，程序据此核对。"""
    ml, pm = month_label(month, lang), month_label(month_add(month, -1), lang)
    released = data.released(month)
    lines = [f"Language: {lang}", f"Bulletin: {ml} Visa Bulletin" + (f", released {date_label(released, lang)}" if released else ""),
             f"Previous bulletin: {pm}", f"Category group: {group_code(slug)}", "",
             f"Which chart USCIS accepts for I-485 this month: {chart_sentence(data, month, cats, lang)[0]}"]
    for cat in cats:
        lines += ["", f"== {CAT_NAME[lang][cat]} ({cat}) =="]
        lines += [f"Summary: {x}" for x in summary_sentences(data, month, cat, lang)]
        for table in ("A", "B"):
            lines.append(f"{T[lang]['table_a' if table == 'A' else 'table_b']}:")
            for c in COUNTRIES:
                p, n = data.cell(month_add(month, -1), cat, c, table), data.cell(month, cat, c, table)
                lines.append(f"- {COUNTRY_NAME[lang][c]}: {ml} {value_label(n, lang)}; {pm} {value_label(p, lang)}; {change_label(p, n, lang)[0]}")
        gaps = [g for g in (gap_line(data, month, cat, c, lang) for c in COUNTRIES) if g]
        if gaps:
            lines.append(f"{T[lang]['gap_h']}")
            lines += [f"- {g}" for g in gaps]
        lines.append(f"{T[lang]['trend_h']} ({T[lang]['table_a']}):")
        for c in COUNTRIES:
            for t in trend_lines(data, month, cat, c, lang):
                lines.append(f"- {COUNTRY_NAME[lang][c]}: {t}")
    return "\n".join(lines)


def facts_hash(facts):
    return hashlib.sha256(facts.encode()).hexdigest()[:16]


def draft_path(month, slug, lang):
    return DRAFTS / month / slug / f"{lang}.json"


def load_draft(month, slug, lang):
    f = draft_path(month, slug, lang)
    return json.loads(f.read_text()) if f.exists() else None


def store_buttons(lang):
    t = T[lang]
    hl = {"en": "", "zh": "&hl=zh-CN", "es": "&hl=es"}[lang]
    return (f"<p class=\"stores\"><a class=\"store\" href=\"{APP_STORE}\">{esc(t['cta_ios'])}</a>"
            f"<a class=\"store\" href=\"{PLAY}{hl}\">{esc(t['cta_android'])}</a></p>")


def cta_box(lang):
    t = T[lang]
    return f"<aside class=\"cta\"><h2>{esc(t['cta_h'])}</h2><p>{esc(t['cta_p'])}</p>{store_buttons(lang)}</aside>"


def lang_switch(paths, lang):
    names = {"en": "English", "zh": "中文", "es": "Español"}
    current = ' aria-current="true"'
    links = [f"<a href=\"{esc(paths[l])}\" hreflang=\"{HREFLANG[l]}\"{current if l == lang else ''}>{names[l]}</a>" for l in LANGS]
    return f"<nav class=\"langs\">{' · '.join(links)}</nav>"


def page(lang, title, description, canonical_paths, body, jsonld):
    alts = "".join(f"<link rel=\"alternate\" hreflang=\"{HREFLANG[l]}\" href=\"{SITE}{canonical_paths[l]}\">" for l in LANGS)
    alts += f"<link rel=\"alternate\" hreflang=\"x-default\" href=\"{SITE}{canonical_paths['en']}\">"
    return f"""<!doctype html>
<html lang="{HREFLANG[lang]}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{SITE}{canonical_paths[lang]}">
{alts}
<meta property="og:type" content="article">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{SITE}{canonical_paths[lang]}">
<link rel="stylesheet" href="/style.css">
<link rel="stylesheet" href="/{SECTION}/pdt.css">
<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>
</head>
<body>
<main class="pdt">
{lang_switch(canonical_paths, lang)}
{body}
<footer>
  <span><a href="/">Autodecoder LLC</a> · <a href="/support.html">Support</a> · <a href="/privacy.html">Privacy Policy</a></span>
  <span>&copy; {dt.date.today().year} Autodecoder LLC</span>
</footer>
</main>
</body>
</html>
"""


def build_article(data, month, slug, cats, lang, published_on, siblings, draft):
    t = T[lang]
    code = group_code(slug)
    ml = month_label(month, lang)
    title = {"en": f"{code} Visa Bulletin {ml}: Final Action & Filing Dates by Country",
             "zh": f"{ml}排期 {code}：表A/表B 各国日期与变化分析",
             "es": f"Boletín de Visas {ml} {code}: fechas de acción final y de presentación por país"}[lang]
    d = draft["draft"]
    chart_text, _ = chart_sentence(data, month, cats, lang)
    released = data.released(month)
    paths = {l: url_for(l, article_path(month, slug)) for l in LANGS}

    crumbs = (f"<nav class=\"crumbs\"><a href=\"/\">{esc(t['home'])}</a> › <a href=\"{url_for(lang)}\">{esc(t['brand'])}</a> › "
              f"<span>{esc(ml)} · {esc(code)}</span></nav>")
    meta = (f"<p class=\"meta\">{esc(t['published'])} {esc(date_label(published_on, lang))} · {esc(t['data'])}{colon(lang)}"
            f"{esc(ml)} Visa Bulletin" + (f" ({esc(date_label(released, lang))})" if released else "")
            + f" · {esc(t['source'])}{colon(lang)}<a href=\"{official_url(month)}\">{esc(t['official'])}</a>{'、' if lang == 'zh' else ', '}<a href=\"{USCIS_CHARTS}\">{esc(t['uscis_page'])}</a></p>")
    parts = [crumbs, f"<h1>{esc(title)}</h1>", meta,
             "<section class=\"answer\">" + "".join(f"<p>{esc(x)}</p>" for x in d["summary"]) + "</section>",
             f"<h2>{esc(t['chart_h'])}</h2><p>{esc(chart_text)} <a href=\"{USCIS_CHARTS}\">{esc(t['uscis_page'])}</a></p>"]
    for cat in cats:
        heading = f"<h2 class=\"sub\">{esc(CAT_NAME[lang][cat])}</h2>" if len(cats) > 1 else f"<p class=\"lede\">{esc(CAT_NAME[lang][cat])}</p>"
        parts.append(heading)
        parts.append(f"<h3>{esc(t['table_a'])}</h3>" + table_html(data, month, cat, "A", lang))
        parts.append(f"<h3>{esc(t['table_b'])}</h3>" + table_html(data, month, cat, "B", lang))
    for i, sec in enumerate(d["sections"]):
        parts.append(f"<h2>{esc(sec['heading'])}</h2>" + "".join(f"<p>{esc(x)}</p>" for x in sec["paragraphs"]))
        if i == 0:
            parts.append(cta_box(lang))
    if not d["sections"]:
        parts.append(cta_box(lang))
    parts.append(f"<h2>{esc(t['faq_h'])}</h2>" + "".join(f"<h3>{esc(f['q'])}</h3><p>{esc(f['a'])}</p>" for f in d["faq"]))
    if siblings:
        parts.append(f"<h2>{esc(t['more_h'])}</h2><ul class=\"links\">" + "".join(
            f"<li><a href=\"{url_for(lang, article_path(month, s))}\">{esc(month_label(month, lang))} {esc(group_code(s))}</a></li>" for s in siblings) + "</ul>")
    parts.append(f"<p class=\"attr\">{esc(t['attribution'])}</p><p class=\"disclaimer\">{esc(t['disclaimer'])}</p>")

    description = d["meta_description"]
    jsonld = {
        "@context": "https://schema.org", "@type": "Article", "headline": title, "description": description,
        "inLanguage": HREFLANG[lang], "datePublished": published_on.isoformat(), "dateModified": draft["written"],
        "mainEntityOfPage": f"{SITE}{paths[lang]}",
        "author": {"@type": "Organization", "name": "Autodecoder LLC", "url": SITE},
        "publisher": {"@type": "Organization", "name": "Autodecoder LLC", "url": SITE},
        "isBasedOn": official_url(month),
        "about": {"@type": "SoftwareApplication", "name": "Priority Date Tracker", "operatingSystem": "iOS, Android",
                  "applicationCategory": "ReferenceApplication", "sameAs": [APP_STORE, PLAY]},
    }
    return page(lang, title, description, paths, "\n".join(parts), jsonld)


def build_hub(data, published, lang):
    t = T[lang]
    paths = {l: url_for(l) for l in LANGS}
    by_month = {}
    for e in published:
        by_month.setdefault(e["month"], []).append(e["slug"])
    order = [g for g, _ in GROUPS]
    parts = [f"<nav class=\"crumbs\"><a href=\"/\">{esc(t['home'])}</a> › <span>{esc(t['brand'])}</span></nav>",
             f"<h1>{esc(t['hub_h1'])}</h1>", f"<p class=\"lede\">{esc(t['hub_intro'])}</p>"]
    for i, m in enumerate(sorted(by_month, reverse=True)):
        if i == 0:
            parts.append(f"<h2>{esc(t['hub_latest'])}: {esc(month_label(m, lang))}</h2>")
        elif i == 1:
            parts.append(f"<h2>{esc(t['hub_older'])}</h2>")
        if i > 0:
            parts.append(f"<h3>{esc(month_label(m, lang))}</h3>")
        slugs = sorted(by_month[m], key=order.index)
        parts.append("<ul class=\"links\">" + "".join(
            f"<li><a href=\"{url_for(lang, article_path(m, s))}\">{esc(GROUP_NAME[lang].get(s) or CAT_NAME[lang][dict(GROUPS)[s][0]])}</a></li>" for s in slugs) + "</ul>")
    parts.append(f"<h2>{esc(t['hub_app_h'])}: Priority Date Tracker</h2><p>{esc(t['app_line'])}</p>{store_buttons(lang)}")
    parts.append(f"<p class=\"disclaimer\">{esc(t['disclaimer'])}</p>")
    jsonld = {"@context": "https://schema.org", "@type": "SoftwareApplication", "name": "Priority Date Tracker",
              "operatingSystem": "iOS, Android", "applicationCategory": "ReferenceApplication", "description": t["app_line"],
              "url": f"{SITE}{paths[lang]}", "sameAs": [APP_STORE, PLAY],
              "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
              "publisher": {"@type": "Organization", "name": "Autodecoder LLC", "url": SITE}}
    return page(lang, t["hub_title"], t["hub_desc"], paths, "\n".join(parts), jsonld)


def write_sitemap(published):
    urls = [("/", None), ("/support.html", None), ("/privacy.html", None)]
    for l in LANGS:
        urls.append((url_for(l), "hub"))
    for e in published:
        for l in LANGS:
            urls.append((url_for(l, article_path(e["month"], e["slug"])), e))
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">']
    for path, e in urls:
        out.append(f"  <url><loc>{SITE}{path}</loc>")
        if e == "hub" or isinstance(e, dict):
            sub = "" if e == "hub" else article_path(e["month"], e["slug"])
            for l in LANGS:
                out.append(f'    <xhtml:link rel="alternate" hreflang="{HREFLANG[l]}" href="{SITE}{url_for(l, sub)}"/>')
        if isinstance(e, dict):
            out.append(f"    <lastmod>{e['date']}</lastmod>")
        out.append("  </url>")
    out.append("</urlset>")
    (ROOT / "sitemap.xml").write_text("\n".join(out) + "\n")


def ensure_drafts(data, month, slug, cats, today, writer, log):
    """三种语言都有与当前数据一致、且审过的稿就返回 True；缺的/过期的在有 writer 时现写现审。"""
    ok = True
    for lang in LANGS:
        facts = fact_sheet(data, month, slug, cats, lang)
        h = facts_hash(facts)
        d = load_draft(month, slug, lang)
        if d and d["facts_hash"] == h:
            continue
        if writer is None:
            ok = False
            continue
        log(f"写稿 {month} {slug} {lang}")
        try:
            from ai import write_reviewed
            r = write_reviewed(writer, facts, lang, log)
        except Exception as e:   # API 出错：这篇本次不发，下次再试
            log(f"  {lang} 写稿出错: {e}")
            r = None
        if r is None:
            return False   # 一种语言没过就停，其余语言不再花钱写
        f = draft_path(month, slug, lang)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"facts_hash": h, "written": today.isoformat(), "model": r["model"], "attempts": r["attempts"],
                                 "review": r["review"], "draft": r["draft"], "facts": facts}, ensure_ascii=False, indent=1) + "\n")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", help="YYYY-MM-DD（默认美东今天）")
    ap.add_argument("--no-ai", action="store_true", help="不写新稿，只用已审过的稿生成页面")
    args = ap.parse_args()
    today = dt.date.fromisoformat(args.today) if args.today else dt.datetime.now(zoneinfo.ZoneInfo("America/New_York")).date()
    log = lambda m: print(m, file=sys.stderr)

    writer = None
    if not args.no_ai and shutil.which("claude"):
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        from ai import Claude
        writer = Claude()
    elif not args.no_ai:
        log("⚠️ 找不到 Claude Code 命令行 claude：不写新稿，只用已审过的稿生成页面")

    data = Data()
    pub_file = DATA / "published.json"
    state = json.loads(pub_file.read_text()) if pub_file.exists() else {"articles": []}
    published = state["articles"]
    done = {(e["month"], e["slug"]) for e in published}

    # 最新一期：每个自然日最多发 ARTICLES_PER_DAY 篇（不补发前几天的）。
    # 缺数据或审稿没过的类别先跳过，后面的照常发，被跳过的第二天再试。
    latest = max(data.months)
    allowed = ARTICLES_PER_DAY
    count = sum(1 for e in published if e["date"] == today.isoformat())
    failed = state.setdefault("failed", {})   # "月份/类别" → 最近一次审稿没过的日期；当天不再重试
    new, attempts = [], 0
    for slug, cats in GROUPS:
        if count >= allowed or attempts >= MAX_NEW_WRITES_PER_RUN:
            break
        key = f"{latest}/{slug}"
        if (latest, slug) in done or not data.complete(latest, cats) or failed.get(key) == today.isoformat():
            continue
        attempts += 1
        if not ensure_drafts(data, latest, slug, cats, today, writer, log):
            if writer is not None:
                failed[key] = today.isoformat()
            continue
        failed.pop(key, None)
        e = {"month": latest, "slug": slug, "date": today.isoformat()}
        published.append(e)
        new.append(e)
        count += 1

    # 当期已发布的文章：数据有变就重写重审（没过就保留原稿）；往期文章不再改动
    for e in published:
        if e["month"] == latest and e not in new:
            ensure_drafts(data, latest, e["slug"], dict(GROUPS)[e["slug"]], today, writer, log)

    for e in published:
        cats = dict(GROUPS)[e["slug"]]
        siblings = [s for s, _ in GROUPS if s != e["slug"] and (e["month"], s) in {(x["month"], x["slug"]) for x in published}]
        for lang in LANGS:
            f = OUT / url_for(lang, article_path(e["month"], e["slug"])).removeprefix(f"/{SECTION}/") / "index.html"
            if e["month"] != latest and f.exists():
                continue
            draft = load_draft(e["month"], e["slug"], lang)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(build_article(data, e["month"], e["slug"], cats, lang, dt.date.fromisoformat(e["date"]), siblings, draft))
    for lang in LANGS:
        f = OUT / url_for(lang).removeprefix(f"/{SECTION}/") / "index.html"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(build_hub(data, published, lang))
    write_sitemap(published)
    pub_file.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n")

    for e in new:
        for lang in LANGS:
            print(f"{SITE}{url_for(lang, article_path(e['month'], e['slug']))}")
    if new:
        for lang in LANGS:
            print(f"{SITE}{url_for(lang)}")


if __name__ == "__main__":
    main()
