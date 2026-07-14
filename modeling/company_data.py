from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol

import requests


SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
CNINFO_SEARCH_URL = "https://www.cninfo.com.cn/new/information/topSearch/query"
EASTMONEY_SUGGEST_URL = "https://searchapi.eastmoney.com/api/suggest/get"
# 东方财富搜索 API 的公开客户端参数，嵌入在 quote.eastmoney.com 公开网页 JavaScript 中，
# 可在浏览器网络请求中观察到，不属于私人凭证。如需覆盖可设置环境变量 EASTMONEY_SUGGEST_TOKEN。
EASTMONEY_SUGGEST_TOKEN = os.getenv(
    "EASTMONEY_SUGGEST_TOKEN", "D43BF722C8E33BDC906FB84D85E326E8"
)
CACHE_DIR = Path(
    os.getenv(
        "FM_DATA_CACHE_DIR",
        str(Path(__file__).resolve().parents[1] / ".cache" / "company_data"),
    )
)


class CompanySearchError(RuntimeError):
    """Raised when no search provider can return a usable result."""


_MATCH_SOURCE_TAGS: dict[str, str] = {
    "local": "本地索引",
    "sec": "SEC",
    "cninfo": "巨潮",
    "eastmoney_a_share": "东方财富",
    "yahoo": "Yahoo",
    "ticker_fallback": "代码兜底",
    "ticker_override": "手工补录",
    "name_fallback": "待确认",
}


def _match_source_tag(match_source: str) -> str:
    return _MATCH_SOURCE_TAGS.get(match_source, "")


@dataclass(frozen=True)
class CompanyCandidate:
    symbol: str
    name: str
    exchange: str = ""
    exchange_name: str = ""
    quote_type: str = "EQUITY"
    sector: str = ""
    industry: str = ""
    match_source: str = "external"
    cik: str = ""
    verification_status: str = ""

    @property
    def label(self) -> str:
        market = self.exchange_name or self.exchange
        parts = [f"{self.name} ({self.symbol})"]
        if market:
            parts.append(market)
        source_tag = _match_source_tag(self.match_source)
        if source_tag:
            parts.append(source_tag)
        return " · ".join(parts)

    @property
    def is_confirmed(self) -> bool:
        """Whether this is a confirmed standard security, not a fallback or pending entry."""
        return self.match_source not in {"ticker_fallback", "name_fallback"}

    @property
    def effective_verification_status(self) -> str:
        """Return verification_status if explicitly set, otherwise infer from match_source."""
        if self.verification_status:
            return self.verification_status
        if self.match_source == "name_fallback":
            return "unresolved"
        if self.match_source == "ticker_fallback":
            return "unresolved"
        return "verified"

    @property
    def normalized_symbol(self) -> str:
        """Normalized symbol for cross-provider deduplication.

        .SS and .SH both refer to the Shanghai Stock Exchange; normalize
        them so the same security from different providers is not duplicated.
        """
        symbol = self.symbol.upper()
        if symbol.endswith(".SS"):
            symbol = symbol[:-3] + ".SH"
        return symbol

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CompanyCandidate":
        fields = {
            "symbol",
            "name",
            "exchange",
            "exchange_name",
            "quote_type",
            "sector",
            "industry",
            "match_source",
            "cik",
            "verification_status",
        }
        return cls(**{key: str(value.get(key, "")) for key in fields})


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, limit: int) -> list[CompanyCandidate]:
        ...


LOCAL_COMPANIES: tuple[tuple[CompanyCandidate, tuple[str, ...]], ...] = (
    (
        CompanyCandidate(
            symbol="0700.HK",
            name="腾讯控股有限公司",
            exchange="HKG",
            exchange_name="香港交易所",
            sector="Communication Services",
            industry="Internet Content & Information",
            match_source="local",
        ),
        ("腾讯", "腾讯控股", "tencent", "0700", "0700.hk"),
    ),
    (
        CompanyCandidate(
            symbol="9988.HK",
            name="阿里巴巴集团控股有限公司",
            exchange="HKG",
            exchange_name="香港交易所",
            sector="Consumer Cyclical",
            industry="Internet Retail",
            match_source="local",
        ),
        ("阿里", "阿里巴巴", "alibaba", "9988", "9988.hk"),
    ),
    (
        CompanyCandidate(
            symbol="1810.HK",
            name="小米集团",
            exchange="HKG",
            exchange_name="香港交易所",
            sector="Technology",
            industry="Consumer Electronics",
            match_source="local",
        ),
        ("小米", "小米集团", "xiaomi", "1810", "1810.hk"),
    ),
    (
        CompanyCandidate(
            symbol="3690.HK",
            name="美团",
            exchange="HKG",
            exchange_name="香港交易所",
            sector="Consumer Cyclical",
            industry="Internet Retail",
            match_source="local",
        ),
        ("美团", "meituan", "3690", "3690.hk"),
    ),
    (
        CompanyCandidate(
            symbol="1211.HK",
            name="比亚迪股份有限公司",
            exchange="HKG",
            exchange_name="香港交易所",
            sector="Consumer Cyclical",
            industry="Auto Manufacturers",
            match_source="local",
        ),
        ("比亚迪", "byd", "1211", "1211.hk"),
    ),
    (
        CompanyCandidate(
            symbol="301165.SZ",
            name="锐捷网络股份有限公司",
            exchange="SZSE",
            exchange_name="深圳证券交易所",
            sector="Technology",
            industry="Communication Equipment",
            match_source="local",
        ),
        (
            "锐捷网络",
            "锐捷",
            "ruijie",
            "301165",
            "301165.sz",
        ),
    ),
    (
        CompanyCandidate(
            symbol="300750.SZ",
            name="宁德时代新能源科技股份有限公司",
            exchange="SHZ",
            exchange_name="深圳证券交易所",
            sector="Industrials",
            industry="Electrical Equipment",
            match_source="local",
        ),
        ("宁德时代", "catl", "300750", "300750.sz"),
    ),
    (
        CompanyCandidate(
            symbol="600519.SS",
            name="贵州茅台酒股份有限公司",
            exchange="SHH",
            exchange_name="上海证券交易所",
            sector="Consumer Defensive",
            industry="Beverages",
            match_source="local",
        ),
        ("贵州茅台", "茅台", "moutai", "600519", "600519.ss", "600519.sh"),
    ),
    (
        CompanyCandidate(
            symbol="603236.SH",
            name="上海移远通信技术股份有限公司",
            exchange="SSE",
            exchange_name="上海证券交易所",
            sector="Technology",
            industry="Communication Equipment",
            match_source="local",
        ),
        (
            "移远通信",
            "上海移远通信",
            "quectel",
            "603236",
            "603236.sh",
            "603236.ss",
        ),
    ),
    (
        CompanyCandidate(
            symbol="GOOGL",
            name="Alphabet Inc.",
            exchange="NMS",
            exchange_name="NASDAQ",
            sector="Communication Services",
            industry="Internet Content & Information",
            match_source="local",
            cik="0001652044",
        ),
        (
            "google",
            "googl",
            "goog",
            "alphabet",
            "alphabet inc",
            "谷歌",
            "谷歌母公司",
        ),
    ),
    (
        CompanyCandidate(
            symbol="AAPL",
            name="Apple Inc.",
            exchange="NMS",
            exchange_name="NASDAQ",
            sector="Technology",
            industry="Consumer Electronics",
            match_source="local",
            cik="0000320193",
        ),
        ("apple", "苹果", "苹果公司", "aapl"),
    ),
    (
        CompanyCandidate(
            symbol="MSFT",
            name="Microsoft Corporation",
            exchange="NMS",
            exchange_name="NASDAQ",
            sector="Technology",
            industry="Software",
            match_source="local",
            cik="0000789019",
        ),
        ("microsoft", "微软", "msft"),
    ),
    (
        CompanyCandidate(
            symbol="NVDA",
            name="NVIDIA Corporation",
            exchange="NMS",
            exchange_name="NASDAQ",
            sector="Technology",
            industry="Semiconductors",
            match_source="local",
            cik="0001045810",
        ),
        ("nvidia", "英伟达", "nvda"),
    ),
    (
        CompanyCandidate(
            symbol="TSLA",
            name="Tesla, Inc.",
            exchange="NMS",
            exchange_name="NASDAQ",
            sector="Consumer Cyclical",
            industry="Auto Manufacturers",
            match_source="local",
            cik="0001318605",
        ),
        ("tesla", "特斯拉", "tsla"),
    ),
)


def _normalize_query(value: str) -> str:
    return re.sub(r"[\s·,，.。\-_（）()]+", "", value).lower()


def _looks_like_ticker(query: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9.\-]{1,16}", query.strip()))


def sec_user_agent_is_configured() -> bool:
    value = os.getenv("SEC_USER_AGENT", "").strip()
    return bool(value and "your-email@example.com" not in value)


def sec_headers(host: str = "www.sec.gov") -> dict[str, str]:
    return {
        "User-Agent": os.getenv("SEC_USER_AGENT", "").strip(),
        "Accept-Encoding": "gzip, deflate",
        "Host": host,
    }


def _cache_path(name: str) -> Path:
    return CACHE_DIR / name


def _read_cache(name: str, ttl_hours: float) -> Any | None:
    path = _cache_path(name)
    if not path.exists():
        return None
    modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    if datetime.now(timezone.utc) - modified > timedelta(hours=ttl_hours):
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_stale_cache(name: str) -> Any | None:
    path = _cache_path(name)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(name: str, value: Any) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(name).write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


class LocalSearchProvider:
    name = "local"

    def search(self, query: str, limit: int) -> list[CompanyCandidate]:
        normalized = _normalize_query(query)
        exact: list[CompanyCandidate] = []
        partial: list[CompanyCandidate] = []
        for company, aliases in LOCAL_COMPANIES:
            normalized_aliases = {_normalize_query(alias) for alias in aliases}
            normalized_aliases.add(_normalize_query(company.name))
            normalized_aliases.add(_normalize_query(company.symbol))
            if normalized in normalized_aliases:
                exact.append(company)
            elif any(
                normalized in alias or alias in normalized
                for alias in normalized_aliases
                if len(normalized) >= 2
            ):
                partial.append(company)
        return (exact + partial)[:limit]

    def is_exact(self, query: str, company: CompanyCandidate) -> bool:
        normalized = _normalize_query(query)
        for item, aliases in LOCAL_COMPANIES:
            if item.symbol != company.symbol:
                continue
            values = {
                _normalize_query(item.name),
                _normalize_query(item.symbol),
                *(_normalize_query(alias) for alias in aliases),
            }
            return normalized in values
        return False


class SecEdgarSearchProvider:
    name = "sec"

    def _catalog(self) -> dict[str, Any]:
        if not sec_user_agent_is_configured():
            return {}
        ttl = float(os.getenv("COMPANY_DATA_CACHE_TTL_HOURS", "24"))
        cached = _read_cache("sec_company_tickers_exchange.json", ttl)
        if cached is not None:
            return cached

        try:
            response = requests.get(
                SEC_TICKERS_URL,
                headers=sec_headers(),
                timeout=float(os.getenv("COMPANY_API_TIMEOUT_SECONDS", "12")),
            )
            response.raise_for_status()
            payload = response.json()
            _write_cache("sec_company_tickers_exchange.json", payload)
            return payload
        except (requests.RequestException, ValueError):
            return _read_stale_cache("sec_company_tickers_exchange.json") or {}

    def search(self, query: str, limit: int) -> list[CompanyCandidate]:
        payload = self._catalog()
        fields = payload.get("fields", [])
        rows = payload.get("data", [])
        if not fields or not rows:
            return []

        normalized = _normalize_query(query)
        matches: list[tuple[int, CompanyCandidate]] = []
        for row in rows:
            item = dict(zip(fields, row))
            ticker = str(item.get("ticker", "")).strip()
            name = str(item.get("name", "")).strip()
            if not ticker or not name:
                continue
            normalized_ticker = _normalize_query(ticker)
            normalized_name = _normalize_query(name)
            if normalized == normalized_ticker:
                score = 0
            elif normalized == normalized_name:
                score = 1
            elif normalized in normalized_name or normalized_name in normalized:
                score = 2
            else:
                continue
            exchange = str(item.get("exchange", "")).strip()
            cik = str(item.get("cik", "")).strip().zfill(10)
            matches.append(
                (
                    score,
                    CompanyCandidate(
                        symbol=ticker,
                        name=name,
                        exchange=exchange,
                        exchange_name=exchange,
                        match_source="sec",
                        cik=cik,
                    ),
                )
            )

        matches.sort(key=lambda item: (item[0], len(item[1].name)))
        return [item[1] for item in matches[:limit]]


def _cninfo_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        rows: list[dict[str, Any]] = []
        for item in value:
            rows.extend(_cninfo_rows(item))
        return rows
    if isinstance(value, dict):
        lowered = {str(key).lower() for key in value}
        rows = [value] if lowered.intersection(
            {"code", "seccode", "secucode", "stockcode", "zwjc"}
        ) else []
        for item in value.values():
            rows.extend(_cninfo_rows(item))
        return rows
    return []


def _case_insensitive_value(item: dict[str, Any], *names: str) -> str:
    for name in names:
        if name in item and item[name] not in (None, ""):
            return re.sub(r"<[^>]+>", "", str(item[name])).strip()
        for key, value in item.items():
            if (
                str(key).lower() == name.lower()
                and value not in (None, "")
            ):
                return re.sub(r"<[^>]+>", "", str(value)).strip()
    return ""


def _a_share_market(code: str) -> tuple[str, str, str]:
    if code.startswith(("4", "8", "92")):
        return f"{code}.BJ", "BSE", "北京证券交易所"
    if code.startswith(("5", "6", "9")):
        return f"{code}.SH", "SSE", "上海证券交易所"
    return f"{code}.SZ", "SZSE", "深圳证券交易所"


class CninfoSearchProvider:
    name = "cninfo"

    def search(self, query: str, limit: int) -> list[CompanyCandidate]:
        value = query.strip()
        if not (
            re.search(r"[\u3400-\u9fff]", value)
            or re.fullmatch(r"\d{6}(?:\.(?:SH|SS|SZ|BJ))?", value, re.IGNORECASE)
        ):
            return []

        normalized_code = value.split(".")[0]
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
        cache_name = f"cninfo_company_search_{digest}.json"
        ttl = float(os.getenv("COMPANY_DATA_CACHE_TTL_HOURS", "24"))
        payload = _read_cache(cache_name, ttl)
        if payload is None:
            try:
                response = requests.get(
                    CNINFO_SEARCH_URL,
                    params={"keyWord": value, "maxNum": max(limit, 10)},
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 Chrome/125 Safari/537.36"
                        ),
                        "Accept": "application/json, text/plain, */*",
                        "Referer": "https://www.cninfo.com.cn/",
                    },
                    timeout=float(os.getenv("COMPANY_API_TIMEOUT_SECONDS", "12")),
                )
                response.raise_for_status()
                payload = response.json()
                _write_cache(cache_name, payload)
            except (requests.RequestException, ValueError):
                payload = _read_stale_cache(cache_name) or {}

        normalized = _normalize_query(value)
        matches: list[tuple[int, CompanyCandidate]] = []
        seen: set[str] = set()
        for item in _cninfo_rows(payload):
            code = _case_insensitive_value(
                item, "code", "secCode", "secuCode", "stockCode"
            )
            name = _case_insensitive_value(
                item, "zwjc", "secName", "secuName", "name", "companyName"
            )
            if not re.fullmatch(r"\d{6}", code) or not name or code in seen:
                continue
            normalized_name = _normalize_query(name)
            if normalized_code == code:
                score = 0
            elif normalized == normalized_name:
                score = 1
            elif normalized in normalized_name or normalized_name in normalized:
                score = 2
            else:
                continue
            seen.add(code)
            symbol, exchange, exchange_name = _a_share_market(code)
            matches.append(
                (
                    score,
                    CompanyCandidate(
                        symbol=symbol,
                        name=name,
                        exchange=exchange,
                        exchange_name=exchange_name,
                        match_source=self.name,
                    ),
                )
            )
        matches.sort(key=lambda item: (item[0], len(item[1].name)))
        return [item[1] for item in matches[:limit]]


def _eastmoney_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        rows: list[dict[str, Any]] = []
        for item in value:
            rows.extend(_eastmoney_rows(item))
        return rows
    if not isinstance(value, dict):
        return []
    lowered = {str(key).lower() for key in value}
    rows = [value] if (
        lowered.intersection({"code", "unifiedcode", "quotationcode"})
        and lowered.intersection({"name", "securityname"})
    ) else []
    for item in value.values():
        rows.extend(_eastmoney_rows(item))
    return rows


class EastmoneyAShareSearchProvider:
    name = "eastmoney_a_share"

    def search(self, query: str, limit: int) -> list[CompanyCandidate]:
        value = query.strip()
        if not (
            re.search(r"[\u3400-\u9fff]", value)
            or re.fullmatch(
                r"\d{6}(?:\.(?:SH|SS|SZ|BJ))?",
                value,
                re.IGNORECASE,
            )
        ):
            return []

        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
        cache_name = f"eastmoney_a_share_search_{digest}.json"
        ttl = float(os.getenv("COMPANY_DATA_CACHE_TTL_HOURS", "24"))
        payload = _read_cache(cache_name, ttl)
        if payload is None:
            try:
                response = requests.get(
                    EASTMONEY_SUGGEST_URL,
                    params={
                        "input": value,
                        "type": "14",
                        "token": EASTMONEY_SUGGEST_TOKEN,
                        "count": max(limit, 10),
                    },
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 Chrome/125 Safari/537.36"
                        ),
                        "Accept": "application/json, text/plain, */*",
                        "Referer": "https://quote.eastmoney.com/",
                    },
                    timeout=float(
                        os.getenv("COMPANY_SEARCH_TIMEOUT_SECONDS", "5")
                    ),
                )
                response.raise_for_status()
                payload = response.json()
                _write_cache(cache_name, payload)
            except (requests.RequestException, ValueError):
                payload = _read_stale_cache(cache_name) or {}

        normalized = _normalize_query(value)
        normalized_code = value.split(".")[0]
        matches: list[tuple[int, CompanyCandidate]] = []
        seen: set[str] = set()
        for item in _eastmoney_rows(payload):
            code = _case_insensitive_value(
                item,
                "Code",
                "UnifiedCode",
                "QuotationCode",
            )
            name = _case_insensitive_value(item, "Name", "SecurityName")
            if not re.fullmatch(r"\d{6}", code) or not name or code in seen:
                continue
            classify = _case_insensitive_value(item, "Classify").lower()
            security_type = _case_insensitive_value(
                item, "SecurityTypeName"
            ).lower()
            classification = f"{classify} {security_type}".strip()
            if classification and not any(
                term in classification
                for term in (
                    "astock",
                    "a股",
                    "沪a",
                    "深a",
                    "科创板",
                    "创业板",
                    "北证",
                )
            ):
                continue
            normalized_name = _normalize_query(name)
            if normalized_code == code:
                score = 0
            elif normalized == normalized_name:
                score = 1
            elif normalized in normalized_name or normalized_name in normalized:
                score = 2
            else:
                continue
            seen.add(code)
            symbol, exchange, exchange_name = _a_share_market(code)
            matches.append(
                (
                    score,
                    CompanyCandidate(
                        symbol=symbol,
                        name=name,
                        exchange=exchange,
                        exchange_name=exchange_name,
                        match_source=self.name,
                    ),
                )
            )
        matches.sort(key=lambda item: (item[0], len(item[1].name)))
        return [item[1] for item in matches[:limit]]


class YahooSearchProvider:
    name = "yahoo"

    def search(self, query: str, limit: int) -> list[CompanyCandidate]:
        try:
            response = requests.get(
                YAHOO_SEARCH_URL,
                params={
                    "q": query,
                    "quotesCount": limit,
                    "newsCount": 0,
                    "enableFuzzyQuery": "true",
                    "quotesQueryId": "tss_match_phrase_query",
                },
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 Chrome/125 Safari/537.36"
                    )
                },
                timeout=float(os.getenv("COMPANY_API_TIMEOUT_SECONDS", "12")),
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            return []

        results: list[CompanyCandidate] = []
        for item in payload.get("quotes", []):
            quote_type = str(item.get("quoteType", "")).upper()
            symbol = str(item.get("symbol", "")).strip()
            name = str(item.get("longname") or item.get("shortname") or symbol).strip()
            if not symbol or quote_type not in {"EQUITY", "MUTUALFUND"}:
                continue
            results.append(
                CompanyCandidate(
                    symbol=symbol,
                    name=name,
                    exchange=str(item.get("exchange", "")),
                    exchange_name=str(item.get("exchDisp", "")),
                    quote_type=quote_type,
                    sector=str(item.get("sector", "")),
                    industry=str(item.get("industry", "")),
                    match_source="yahoo",
                )
            )
            if len(results) >= limit:
                break
        return results


def _offline_candidate(query: str) -> CompanyCandidate:
    value = query.strip()
    if _looks_like_ticker(value):
        a_share_candidate = candidate_from_ticker(value, value)
        if a_share_candidate:
            return a_share_candidate
        symbol = value.upper()
        if re.fullmatch(r"\d{4,5}", symbol):
            symbol = f"{int(symbol):04d}.HK"
        return CompanyCandidate(
            symbol=symbol,
            name=value,
            match_source="ticker_fallback",
        )
    return CompanyCandidate(
        symbol="待确认",
        name=value,
        match_source="name_fallback",
    )


def candidate_from_ticker(
    ticker: str,
    company_name: str = "",
) -> CompanyCandidate | None:
    value = ticker.strip().upper()
    match = re.fullmatch(r"(\d{6})(?:\.(SH|SS|SZ|BJ))?", value)
    if not match:
        return None
    code, suffix = match.groups()
    inferred_symbol, exchange, exchange_name = _a_share_market(code)
    if suffix:
        normalized_suffix = "SH" if suffix == "SS" else suffix
        inferred_suffix = inferred_symbol.rsplit(".", 1)[-1]
        if normalized_suffix != inferred_suffix:
            return None
    return CompanyCandidate(
        symbol=inferred_symbol,
        name=company_name.strip() or code,
        exchange=exchange,
        exchange_name=exchange_name,
        match_source="ticker_override",
    )


def configured_search_providers() -> list[SearchProvider]:
    requested = [
        item.strip().lower()
        for item in os.getenv(
            "COMPANY_SEARCH_PROVIDERS",
            "local,eastmoney_a_share,cninfo,sec,yahoo",
        ).split(",")
        if item.strip()
    ]
    available: dict[str, SearchProvider] = {
        "local": LocalSearchProvider(),
        "eastmoney_a_share": EastmoneyAShareSearchProvider(),
        "cninfo": CninfoSearchProvider(),
        "sec": SecEdgarSearchProvider(),
        "yahoo": YahooSearchProvider(),
    }
    return [available[name] for name in requested if name in available]


def provider_statuses() -> list[dict[str, str]]:
    enabled = {provider.name for provider in configured_search_providers()}
    pdf_parser_ready = importlib.util.find_spec("pypdf") is not None
    return [
        {
            "name": "本地证券索引",
            "status": "已启用" if "local" in enabled else "未启用",
            "coverage": "常见中、美、港股；离线兜底",
        },
        {
            "name": "A 股全市场简称搜索",
            "status": "已启用" if "eastmoney_a_share" in enabled else "未启用",
            "coverage": "沪深北 A 股公司简称与六位股票代码；带本地缓存",
        },
        {
            "name": "巨潮证券目录",
            "status": "已启用" if "cninfo" in enabled else "未启用",
            "coverage": "A 股公司名称与股票代码识别",
        },
        {
            "name": "SEC EDGAR",
            "status": (
                "已启用"
                if "sec" in enabled and sec_user_agent_is_configured()
                else "需配置 SEC_USER_AGENT"
                if "sec" in enabled
                else "未启用"
            ),
            "coverage": "美股公司目录及官方披露",
        },
        {
            "name": "Yahoo 兼容搜索",
            "status": "已启用" if "yahoo" in enabled else "未启用",
            "coverage": "全球证券搜索；非正式稳定 API",
        },
        {
            "name": "香港交易所披露易",
            "status": "已启用" if pdf_parser_ready else "需安装 PDF 解析依赖",
            "coverage": "港股年度报告检索及 PDF 业务拆分",
        },
        {
            "name": "公开F10主营构成",
            "status": "已启用",
            "coverage": "A 股最新完整财年主营业务分部；结构化优先",
        },
        {
            "name": "巨潮资讯",
            "status": "已启用" if pdf_parser_ready else "需安装 PDF 解析依赖",
            "coverage": "A 股年度报告检索及通用 PDF 业务拆分回退",
        },
    ]


def search_companies(query: str, limit: int = 8) -> list[CompanyCandidate]:
    query = query.strip()
    if not query:
        return []

    providers = configured_search_providers()
    local = next(
        (provider for provider in providers if isinstance(provider, LocalSearchProvider)),
        None,
    )
    local_results = local.search(query, limit) if local else []
    if local_results and local and local.is_exact(query, local_results[0]):
        return local_results

    results: list[CompanyCandidate] = []
    seen: set[str] = set()
    for provider in providers:
        provider_results = (
            local_results if provider is local else provider.search(query, limit)
        )
        for candidate in provider_results:
            key = candidate.normalized_symbol
            if key in seen:
                continue
            seen.add(key)
            results.append(candidate)
            if len(results) >= limit:
                return results

    return results or [_offline_candidate(query)]


def can_start_research(
    candidate: CompanyCandidate,
    explicit_confirmation: bool = False,
) -> bool:
    """Determine whether research can proceed for a given candidate.

    Args:
        candidate: The company candidate to check.
        explicit_confirmation: Whether the user has explicitly confirmed
            they want to proceed with an unverified ticker.

    Returns:
        True if research can proceed, False otherwise.

    Rules:
        - name_fallback (symbol="待确认"): Never allowed; user must provide
          a valid 6-digit A-share code first.
        - ticker_fallback: Only allowed if explicit_confirmation=True.
          The candidate's verification_status will be set to
          "user_confirmed_pending_verification" by the caller.
        - All other match_source values (local, sec, cninfo, eastmoney_a_share,
          yahoo, ticker_override): Allowed immediately.
    """
    status = candidate.effective_verification_status
    if status == "unresolved":
        if candidate.match_source == "name_fallback":
            return False
        if candidate.match_source == "ticker_fallback":
            return explicit_confirmation
    if status == "user_confirmed_pending_verification":
        return True
    return True
