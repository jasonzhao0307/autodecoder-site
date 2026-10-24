"""AI 写稿 + 审稿（Claude）。

流程：写稿 → 程序核对（文中每个日期/天数/月数都必须出现在数据表里）→ 独立审稿（另一次调用，逐条对照数据）。
任一关不过：带着问题重写一次；还不过就返回 None，这一篇本次不发，下次运行再试。
表格、USCIS 用表等硬数据仍由程序生成，AI 只写解读文字（总结、分析、常见问题）。
"""
import json, re

MODEL = "claude-opus-5-5"
MAX_ATTEMPTS = 2

LANG_RULES = {
    "en": "Write in clear, plain American English.",
    "zh": "用简体中文写，面向在美国排绿卡的中文读者。用社区常用说法：排期、表A（最终裁定日）、表B（递交日）、绿卡、递交 I-485。不要写成翻译腔。",
    "es": "Escriba en español neutro y claro, para lectores latinoamericanos en EE. UU. Use «Boletín de Visas», «Tabla A (fechas de acción final)» y «Tabla B (fechas de presentación)».",
}

WRITER_SYSTEM = """You write the monthly Visa Bulletin analysis articles for Priority Date Tracker (an iPhone/Android app that tracks U.S. green card priority dates). Each article covers one category for one bulletin month.

The page already shows the full data tables, which chart USCIS accepts, a download box for the app, and a legal disclaimer. You write only the interpretive text around them.

Hard rules:
- Use only the facts in the fact sheet. Copy every date, day count and month count exactly as it is written there. Never compute new numbers, never round differently, never add a date that is not in the fact sheet.
- Do not predict future movement, do not guess why dates moved (no "due to high demand" unless the fact sheet says so), and do not give legal advice. Plain definitions of standard terms (Final Action Date, Dates for Filing, I-485, chargeability, "C", "U") are fine.
- Lead with the answer: the summary states what changed this month for this category in 2-4 short sentences.
- Be specific and useful to someone checking their own case: compare countries, point out the largest move, how far Table B is ahead of Table A, and what the 6- and 12-month record shows.
- Do not promote the app; the page handles that.
- Sections: 2-4 sections, each 1-3 short paragraphs, each section readable on its own (a reader or an AI assistant may quote one section alone). Headings are plain statements, not questions.
- FAQ: 3-4 questions real applicants would search for, each answered in under 80 words, answer first.
- meta_description: one sentence, under 155 characters, with the category, month and the main change."""

REVIEWER_SYSTEM = """You are the fact-checker for Priority Date Tracker's Visa Bulletin articles. You receive the fact sheet (the only source of truth) and a draft written from it. Approve the draft only if every check passes:

1. Every date, day count, month count, number of countries and direction of movement (advanced / unchanged / retrogressed / current / unavailable) matches the fact sheet exactly.
2. No information that is not in the fact sheet: no reasons for movement, no predictions or "expected" movement, no numbers from outside knowledge. Plain definitions of standard immigration terms are allowed.
3. No legal advice and no statements that someone definitely can or cannot file; describing which chart USCIS says to use is fine when it matches the fact sheet.
4. Written in the required language, natural and clear, no leftover English in a Chinese or Spanish draft other than official terms (I-485, USCIS, EB-2, Visa Bulletin, Final Action Dates, Dates for Filing).
5. The summary leads with this month's change for the category.

List every problem with the exact quote and what is wrong. If there are no problems, approve with an empty list. Be strict: one wrong number means not approved."""

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "array", "items": {"type": "string"}},
        "sections": {"type": "array", "items": {
            "type": "object",
            "properties": {"heading": {"type": "string"}, "paragraphs": {"type": "array", "items": {"type": "string"}}},
            "required": ["heading", "paragraphs"], "additionalProperties": False}},
        "faq": {"type": "array", "items": {
            "type": "object",
            "properties": {"q": {"type": "string"}, "a": {"type": "string"}},
            "required": ["q", "a"], "additionalProperties": False}},
        "meta_description": {"type": "string"},
    },
    "required": ["summary", "sections", "faq", "meta_description"],
    "additionalProperties": False,
}
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "issues": {"type": "array", "items": {
            "type": "object",
            "properties": {"quote": {"type": "string"}, "problem": {"type": "string"}},
            "required": ["quote", "problem"], "additionalProperties": False}},
    },
    "required": ["approved", "issues"],
    "additionalProperties": False,
}

# 程序核对：草稿里出现的这几类数字，必须在数据表里出现过
NUMBER_PATTERNS = {
    "en": [r"[A-Z][a-z]{2} \d{1,2}, \d{4}", r"\d+(?= days?\b)", r"\d+\.\d"],
    "zh": [r"\d{4}年\d{1,2}月\d{1,2}日", r"\d+(?= ?天)", r"\d+\.\d"],
    "es": [r"\d{1,2} de [a-záéíóú]+ de \d{4}", r"\d+(?= días?\b)", r"\d+\.\d"],
}


def draft_text(d):
    parts = list(d["summary"]) + [d["meta_description"]]
    for s in d["sections"]:
        parts += [s["heading"], *s["paragraphs"]]
    for f in d["faq"]:
        parts += [f["q"], f["a"]]
    return "\n".join(parts)


def number_check(draft, facts, lang):
    """返回草稿中没在数据表里出现过的日期/天数/月数。"""
    text = draft_text(draft)
    bad = []
    # 三种语言的写法都查：中文稿里出现英文格式的日期也算不合格
    for pat in {p for pats in NUMBER_PATTERNS.values() for p in pats}:
        allowed = set(re.findall(pat, facts))
        bad += [m for m in re.findall(pat, text) if m not in allowed]
    return sorted(set(bad))


class Claude:
    def __init__(self):
        import anthropic   # 只有真要写稿时才需要 SDK
        self.client = anthropic.Anthropic()

    def json_call(self, system, user, schema):
        resp = self.client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",   # 安全分类器误拒时由服务端换模型重试
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": schema}},
        )
        if resp.stop_reason != "end_turn":
            raise RuntimeError(f"stop_reason={resp.stop_reason} {getattr(resp, 'stop_details', None)}")
        text = next(b.text for b in resp.content if b.type == "text")
        return json.loads(text), resp.model


def write_reviewed(client, facts, lang, log):
    """写稿并审稿。通过返回 {draft, review, model, attempts}，不通过返回 None。"""
    feedback = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        user = f"{LANG_RULES[lang]}\n\n<fact_sheet>\n{facts}\n</fact_sheet>"
        if feedback:
            user += f"\n\nA previous draft was rejected for these problems. Fix all of them:\n{feedback}"
        draft, model = client.json_call(WRITER_SYSTEM, user, DRAFT_SCHEMA)

        bad = number_check(draft, facts, lang)
        if bad:
            feedback = "\n".join(f"- \"{b}\" does not appear in the fact sheet." for b in bad)
            log(f"  {lang} 第 {attempt} 稿：程序核对未过 {bad}")
            continue
        review, _ = client.json_call(
            REVIEWER_SYSTEM,
            f"Required language: {lang}\n\n<fact_sheet>\n{facts}\n</fact_sheet>\n\n<draft>\n{json.dumps(draft, ensure_ascii=False, indent=1)}\n</draft>",
            REVIEW_SCHEMA)
        if review["approved"] and not review["issues"]:
            return {"draft": draft, "review": review, "model": model, "attempts": attempt}
        feedback = "\n".join(f"- \"{i['quote']}\": {i['problem']}" for i in review["issues"]) or "- Reviewer did not approve."
        log(f"  {lang} 第 {attempt} 稿：审稿未过 {len(review['issues'])} 条")
    return None
