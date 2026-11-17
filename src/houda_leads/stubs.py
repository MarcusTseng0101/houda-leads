"""範圍外或後面步驟的入口。現在只有介面，不要實作（PROMPT.md「範圍外」「Build order」）。"""
from __future__ import annotations


class NotYet(NotImplementedError):
    pass


def classify_with_llm(redacted_text: str, industry_names: list[str]):
    """第 4 步：llm.py（唯一 import anthropic 的檔案）。沒有 tools，輸出用 pydantic 驗證。"""
    raise NotYet("第 4 步才實作 llm.py")


def load_factory_registry(path):
    """第 5 步：sources.py。要先拿一份真的工廠登記檔案寫 parser，不要照猜的欄位名寫。"""
    raise NotYet("第 5 步才實作 sources.py（需要一份真的工廠登記檔案）")


def discover_website(company_name: str, tax_id: str):
    """第 5 步：discover.py。不准把搜尋結果第一筆直接當官網（失敗模式 4）。"""
    raise NotYet("第 5 步才實作 discover.py")


def notify_new_factories(prev_snapshot, curr_snapshot):
    """範圍外：每季增量（SaaS 的價值）。"""
    raise NotYet("範圍外")


def send_outreach(*_args, **_kw):
    """範圍外：不做寄信。之後要做，必須接退訂清單和個資法第 8 條告知。"""
    raise NotYet("範圍外：不寄信")
