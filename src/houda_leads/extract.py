"""HTML → 可見文字、連結、公司窗口、官網確認。不連網。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser as HTMLParser

HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TW_PHONE = re.compile(r"(?<!\d)(?:\+886-?|0)\d{1,2}-\d{3,4}-\d{3,4}(?!\d)")
COMPANY_SUFFIX = re.compile(r"(股份有限公司|有限公司|企業社|工業社|公司)$")
JP_AFFIX = re.compile(r"^(株式会社|有限会社)|(株式会社|有限会社)$")
EN_SUFFIX = re.compile(r"[\s,]*\b(co\.?,?\s*ltd|ltd|limited|inc|incorporated|corp|corporation|company|"
                       r"gmbh(?:\s*&\s*co\.?\s*kg)?|ag|kg|se|s\.?a|s\.?r\.?l|s\.?p\.?a|b\.?v|n\.?v|llc|plc|k\.?k|co)\.?$",
                       re.I)


@dataclass(frozen=True)
class Page:
    url: str
    html: str


@dataclass(frozen=True)
class PageText:
    url: str
    text: str


@dataclass(frozen=True)
class Contact:
    kind: str          # 業務信箱／代表信箱／聯絡表單／總機
    value: str
    source_url: str


def _collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


# 被拿掉的元素換成這個記號：證據片段不能跨過它（否則前後文字會黏成原文沒有的句子）
SENTINEL = ""
CHROME_SELECTOR = "nav, header, footer, aside, [role='navigation'], [role='banner'], [role='contentinfo']"
CHROME_CLASS = re.compile(r"(?:^|[-_\s])(?:nav|navbar|menu|megamenu|footer|header|breadcrumbs?|sidebar)(?:[-_\s]|$)", re.I)
MIN_CONTENT_RATIO = 0.3     # 拿掉選單後剩不到 3 成：多半是誤判，退回全文（猜的，可調整）


def _remove(nodes) -> None:
    for n in nodes:
        try:
            n.replace_with(SENTINEL)
        except Exception:      # 已經跟著父節點一起被拿掉了
            pass


def _tree_without_hidden(html: str, drop_chrome: bool = False) -> HTMLParser:
    """失敗模式 10：先拿掉看不見的元素。這只是第一道防線，證據逐字檢查還是要做。"""
    tree = HTMLParser(html)
    doomed = list(tree.css("script, style, noscript, template, [hidden], [aria-hidden='true']"))
    doomed += [n for n in tree.css("[style]") if HIDDEN_STYLE.search(n.attributes.get("style") or "")]
    _remove(doomed)
    if drop_chrome:
        chrome = list(tree.css(CHROME_SELECTOR))
        chrome += [n for n in tree.css("[class], [id]")
                   if CHROME_CLASS.search(n.attributes.get("class") or "") or CHROME_CLASS.search(n.attributes.get("id") or "")]
        _remove(chrome)
    return tree


def _text(tree: HTMLParser) -> str:
    root = tree.body or tree.root
    return _collapse(root.text(separator=" ")) if root else ""


def visible_text(html: str) -> str:
    """整頁看得見的文字（含選單、頁尾）：給官網確認、電話用（統編常常在頁尾）。"""
    return _collapse(_text(_tree_without_hidden(html)).replace(SENTINEL, " "))


def content_text(html: str) -> str:
    """分類用：再拿掉選單、頁首頁尾、側欄。保留 SENTINEL 當成證據的邊界。

    選單常列出「產品應用：電源、UPS…」，那是對方客戶的產業，不是對方做什麼（2026-10-20 試跑：統懋半導體）。
    """
    content = _text(_tree_without_hidden(html, drop_chrome=True))
    full = visible_text(html)
    if len(content.replace(SENTINEL, "").strip()) < MIN_CONTENT_RATIO * len(full):
        return _text(_tree_without_hidden(html))
    return content


def links(html: str, base_url: str) -> list[tuple[str, str]]:
    tree = _tree_without_hidden(html)
    out = []
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if href:
            out.append((urljoin(base_url, href), _collapse(a.text(separator=" "))))
    return out


def site_root(url: str) -> str:
    h = (urlsplit(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _same_site(domain: str, root: str) -> bool:
    domain = domain.lower()
    return domain == root or domain.endswith("." + root)


def contacts(pages: list[Page], site_url: str, sales_words: list[str]) -> list[Contact]:
    """只收對方自己網域的信箱：網頁文字裡出現的別的網域信箱（例如被注入的）一律不收（失敗模式 2）。"""
    root = site_root(site_url)
    seen, out = set(), []

    def add(kind, value, url):
        if value not in seen:
            seen.add(value)
            out.append(Contact(kind, value, url))

    for p in pages:
        for href, text in links(p.html, p.url):
            if href.lower().startswith("mailto:"):
                addr = href[7:].split("?")[0].strip()
                if EMAIL.fullmatch(addr) and _same_site(addr.split("@")[1], root):
                    local = addr.split("@")[0].lower()
                    kind = "業務信箱" if any(w.lower() in local for w in sales_words) else "代表信箱"
                    add(kind, addr, p.url)
            elif any(w in (href.lower() + text) for w in ("contact", "聯絡", "聯繫")) and _same_site(site_root(href), root):
                add("聯絡表單", href, p.url)
        for ph in TW_PHONE.findall(visible_text(p.html)):
            add("總機", ph, p.url)
    order = {"業務信箱": 0, "代表信箱": 1, "聯絡表單": 2, "總機": 3}
    return sorted(out, key=lambda c: order[c.kind])


def company_core(name: str) -> str:
    """去掉公司型態：中文（股份有限公司）、日文（株式会社）、英文（Co., Ltd.／GmbH／Inc. 可以疊好幾個）。"""
    s = COMPANY_SUFFIX.sub("", name.strip())
    s = JP_AFFIX.sub("", s).strip()
    prev = None
    while prev != s:
        prev, s = s, EN_SUFFIX.sub("", s).strip(" ,.")
    return s


def _name_on_page(core: str, joined: str) -> bool:
    if not re.fullmatch(r"[\x20-\x7e]+", core):
        return core in joined
    # 英文名：不分大小寫、空白壓成一個、要單字邊界（"Atom" 不能中 "atomic"）
    c = re.sub(r"\s+", " ", core).lower()
    return re.search(rf"(?<![a-z0-9]){re.escape(c)}(?![a-z0-9])", re.sub(r"\s+", " ", joined).lower()) is not None


def site_confirmation(texts: list[PageText], tax_id: str, company_name: str, id_label: str = "統編") -> str:
    """失敗模式 4：官網上要看得到統編（國外是註冊編號），或完整公司名（去掉公司型態，至少 4 個字），才算確認。"""
    joined = " ".join(t.text for t in texts)
    # 英國公司編號 01234567 在網頁上常寫成 1234567：去掉開頭的 0 也算（剩下至少 5 碼）
    ids = [tax_id] + ([tax_id.lstrip("0")] if tax_id and len(tax_id.lstrip("0")) >= 5 else [])
    for i in filter(None, dict.fromkeys(ids)):
        if re.search(rf"(?<![\dA-Za-z]){re.escape(i)}(?!\d)", joined):
            return id_label
    core = company_core(company_name)
    if len(core) >= 4 and _name_on_page(core, joined):
        return "公司名"
    return "未確認"
