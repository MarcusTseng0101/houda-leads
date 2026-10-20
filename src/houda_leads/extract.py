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


def _tree_without_hidden(html: str) -> HTMLParser:
    """失敗模式 10：先拿掉看不見的元素。這只是第一道防線，證據逐字檢查還是要做。"""
    tree = HTMLParser(html)
    doomed = list(tree.css("script, style, noscript, template, [hidden], [aria-hidden='true']"))
    doomed += [n for n in tree.css("[style]") if HIDDEN_STYLE.search(n.attributes.get("style") or "")]
    for n in doomed:
        try:
            n.decompose()
        except Exception:      # 已經跟著父節點一起被拿掉了
            pass
    return tree


def visible_text(html: str) -> str:
    tree = _tree_without_hidden(html)
    root = tree.body or tree.root
    return _collapse(root.text(separator=" ")) if root else ""


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
    return COMPANY_SUFFIX.sub("", name.strip())


def site_confirmation(texts: list[PageText], tax_id: str, company_name: str) -> str:
    """失敗模式 4：官網上要看得到統編，或完整公司名（去掉公司型態，至少 4 個字），才算確認。"""
    joined = " ".join(t.text for t in texts)
    if tax_id and re.search(rf"(?<!\d){re.escape(tax_id)}(?!\d)", joined):
        return "統編"
    core = company_core(company_name)
    if len(core) >= 4 and core in joined:
        return "公司名"
    return "未確認"
