from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urljoin
from urllib.parse import urlparse, urlunparse

import requests

from modeling.company_data import CompanyCandidate


CACHE_DIR = Path(
    os.getenv(
        "FM_DATA_CACHE_DIR",
        str(Path(__file__).resolve().parents[1] / ".cache" / "company_data"),
    )
)
CNINFO_SEARCH_URL = "https://www.cninfo.com.cn/new/information/topSearch/query"
CNINFO_ANNOUNCEMENT_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STATIC_ROOT = "https://static.cninfo.com.cn/"
HKEX_PREFIX_URL = "https://www1.hkexnews.hk/search/prefix.do"
HKEX_TITLE_URL = "https://www1.hkexnews.hk/search/titleSearchServlet.do"
HKEX_ROOT = "https://www1.hkexnews.hk/"
SSE_ANNOUNCEMENT_URL = (
    "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do"
)
SSE_ROOT = "https://static.sse.com.cn/"


@dataclass(frozen=True)
class OfficialAnnouncement:
    title: str
    url: str
    published_date: str
    provider: str
    document_type: str = "PDF"


def _browser_headers(referer: str) -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 Chrome/125 Safari/537.36"
        ),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
    }


def _cache_file(prefix: str, key: str, suffix: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    return CACHE_DIR / f"{prefix}_{digest}.{suffix}"


def _fresh(path: Path, ttl_hours: float) -> bool:
    if not path.exists():
        return False
    modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return datetime.now(timezone.utc) - modified <= timedelta(hours=ttl_hours)


def _decode_json_or_jsonp(text: str) -> Any:
    value = text.strip()
    if value.startswith(("{", "[")):
        return json.loads(value)
    match = re.search(r"^[^(]*\((.*)\)\s*;?\s*$", value, flags=re.DOTALL)
    if not match:
        raise ValueError("Unsupported response format")
    return json.loads(match.group(1))


def _request_json(
    method: str,
    url: str,
    *,
    cache_key: str,
    headers: dict[str, str],
    ttl_hours: float = 6,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    diagnostics: list[str] | None = None,
) -> Any:
    path = _cache_file("market_json", cache_key, "json")
    if _fresh(path, ttl_hours):
        try:
            if diagnostics is not None:
                diagnostics.append(f"{url}：使用本地缓存。")
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    try:
        response = requests.request(
            method,
            url,
            params=params,
            data=data,
            headers=headers,
            timeout=float(os.getenv("COMPANY_API_TIMEOUT_SECONDS", "12")),
        )
        response.raise_for_status()
        payload = _decode_json_or_jsonp(response.text)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        if diagnostics is not None:
            diagnostics.append(f"{url}：请求成功。")
        return payload
    except (requests.RequestException, ValueError, OSError) as exc:
        if diagnostics is not None:
            diagnostics.append(f"{url}：{type(exc).__name__}。")
        try:
            if diagnostics is not None:
                diagnostics.append(f"{url}：远程请求失败，改用旧缓存。")
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}


def download_document(announcement: OfficialAnnouncement) -> bytes:
    path = _cache_file("market_document", announcement.url, "pdf")
    if path.exists():
        try:
            return path.read_bytes()
        except OSError:
            pass
    candidates = [announcement.url]
    parsed = urlparse(announcement.url)
    if parsed.netloc == "static.sse.com.cn":
        candidates.append(
            urlunparse(parsed._replace(netloc="www.sse.com.cn"))
        )

    for url in candidates:
        try:
            response = requests.get(
                url,
                headers={
                    **_browser_headers("https://www.sse.com.cn/"),
                    "Accept": "application/pdf,*/*",
                },
                timeout=float(os.getenv("COMPANY_API_TIMEOUT_SECONDS", "12")) * 3,
            )
            response.raise_for_status()
            content = response.content
            if not content.startswith(b"%PDF"):
                continue
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            return content
        except (requests.RequestException, OSError):
            continue
    return b""


def fetch_document_text(announcement: OfficialAnnouncement) -> tuple[str, str]:
    """Fetch PDF text directly, then fall back to a public URL-to-text reader."""
    from modeling.pdf_disclosures import extract_pdf_text

    document = download_document(announcement)
    if document:
        return extract_pdf_text(document), "direct_pdf"

    if os.getenv("PUBLIC_PDF_TEXT_FALLBACK", "true").lower() not in {
        "1",
        "true",
        "yes",
    }:
        return "", ""

    reader_url = f"https://r.jina.ai/{announcement.url}"
    try:
        response = requests.get(
            reader_url,
            headers={"User-Agent": "TickerDNA/1.0"},
            timeout=float(os.getenv("COMPANY_API_TIMEOUT_SECONDS", "12")) * 4,
        )
        response.raise_for_status()
        text = response.text
        if len(text.strip()) >= 1000:
            return text, "public_pdf_text_reader"
    except requests.RequestException:
        pass
    return "", ""


def _dict_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        rows: list[dict[str, Any]] = []
        for item in value:
            rows.extend(_dict_rows(item))
        return rows
    if isinstance(value, dict):
        rows = [value] if any(
            key.lower()
            in {
                "title",
                "announcementtitle",
                "stockcode",
                "secucode",
                "code",
                "orgid",
            }
            for key in value
        ) else []
        for item in value.values():
            rows.extend(_dict_rows(item))
        return rows
    return []


def _value(item: dict[str, Any], *names: str) -> str:
    for name in names:
        if name in item and item[name] not in (None, ""):
            return str(item[name]).strip()
        for key, value in item.items():
            if (
                str(key).lower() == name.lower()
                and value not in (None, "")
            ):
                return str(value).strip()
    return ""


def _clean_title(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).replace("&amp;", "&").strip()


def _date_value(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, timezone.utc).date().isoformat()
    text = str(value or "").strip()
    if re.fullmatch(r"\d{13}", text):
        return datetime.fromtimestamp(
            int(text) / 1000, timezone.utc
        ).date().isoformat()
    for pattern in ("%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], pattern).date().isoformat()
        except ValueError:
            continue
    match = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return text


def _is_full_year_title(title: str) -> bool:
    normalized = title.lower()
    excluded = ("摘要", "summary", "取消", "cancel", "更正", "correction")
    if any(term in normalized for term in excluded):
        return False
    return any(
        term in normalized
        for term in (
            "年度报告",
            "年度報告",
            "annual report",
            "全年业绩",
            "全年業績",
            "年度业绩",
            "年度業績",
            "annual results",
        )
    )


def _announcement_score(item: OfficialAnnouncement) -> tuple[str, int]:
    title = item.title.lower()
    priority = 1 if any(
        term in title for term in ("全年业绩", "全年業績", "annual results")
    ) else 0
    return item.published_date, -priority


class SseAnnouncementProvider:
    name = "上海证券交易所"

    def __init__(self) -> None:
        self.last_diagnostics: list[str] = []

    def supports(self, company: CompanyCandidate) -> bool:
        symbol = company.symbol.upper()
        return symbol.endswith((".SH", ".SS")) or company.exchange.upper() in {
            "SSE",
            "SHH",
        }

    def latest_annual(self, company: CompanyCandidate) -> OfficialAnnouncement | None:
        self.last_diagnostics = []
        if not self.supports(company):
            return None
        code = company.symbol.split(".")[0]
        referer = (
            "https://www.sse.com.cn/assortment/stock/list/info/"
            f"announcement/index.shtml?productId={code}"
        )
        payload = _request_json(
            "GET",
            SSE_ANNOUNCEMENT_URL,
            cache_key=f"sse_annual_{code}",
            headers=_browser_headers(referer),
            params={
                "jsonCallBack": "callback",
                "isPagination": "true",
                "productId": code,
                "keyWord": "",
                "securityType": "0101,120100,020100,020200,120200",
                "reportType2": "DQGG",
                "reportType": "YEARLY",
                "pageHelp.pageSize": 50,
                "pageHelp.pageNo": 1,
                "pageHelp.beginPage": 1,
                "pageHelp.endPage": 5,
            },
            diagnostics=self.last_diagnostics,
        )
        candidates: list[OfficialAnnouncement] = []
        for item in _dict_rows(payload):
            title = _clean_title(
                _value(item, "TITLE", "title", "bulletinTitle")
            )
            path = _value(item, "URL", "url", "bulletinUrl")
            if not title or not path or not _is_full_year_title(title):
                continue
            candidates.append(
                OfficialAnnouncement(
                    title=title,
                    url=urljoin(SSE_ROOT, path),
                    published_date=_date_value(
                        _value(item, "SSEDATE", "date", "publishDate")
                    ),
                    provider=self.name,
                )
            )
        return max(candidates, key=_announcement_score) if candidates else None


class CninfoAnnouncementProvider:
    name = "巨潮资讯"

    def __init__(self) -> None:
        self.last_diagnostics: list[str] = []

    def supports(self, company: CompanyCandidate) -> bool:
        symbol = company.symbol.upper()
        exchange = company.exchange.upper()
        return symbol.endswith((".SS", ".SH", ".SZ", ".BJ")) or exchange in {
            "SHH",
            "SHZ",
            "SSE",
            "SZSE",
            "BSE",
        }

    def latest_annual(self, company: CompanyCandidate) -> OfficialAnnouncement | None:
        self.last_diagnostics = []
        code = company.symbol.split(".")[0]
        referer = "https://www.cninfo.com.cn/new/fulltextSearch"
        headers = _browser_headers(referer)
        search_payload = _request_json(
            "GET",
            CNINFO_SEARCH_URL,
            cache_key=f"cninfo_company_{code}",
            headers=headers,
            params={"keyWord": code, "maxNum": 10},
            ttl_hours=24,
            diagnostics=self.last_diagnostics,
        )
        company_rows = _dict_rows(search_payload)
        exact = next(
            (
                item
                for item in company_rows
                if _value(item, "code", "secCode", "secuCode", "stockCode") == code
            ),
            {},
        )
        org_id = _value(exact, "orgId", "orgid")
        stock = f"{code},{org_id}" if org_id else code
        end = date.today()
        start = end.replace(year=end.year - 4)
        symbol = company.symbol.upper()
        primary_column = (
            "sse"
            if symbol.endswith((".SH", ".SS"))
            or company.exchange.upper() in {"SSE", "SHH"}
            else "bjse"
            if symbol.endswith(".BJ") or company.exchange.upper() == "BSE"
            else "szse"
        )
        strategies = [
            (primary_column, stock, ""),
            (primary_column, "", code),
            (primary_column, "", company.name),
        ]
        if primary_column != "szse":
            strategies.append(("szse", stock, ""))

        announcement_payload: Any = {}
        for column, stock_value, search_key in strategies:
            announcement_payload = _request_json(
                "POST",
                CNINFO_ANNOUNCEMENT_URL,
                cache_key=(
                    f"cninfo_annual_{column}_{stock_value}_{search_key}"
                ),
                headers=headers,
                data={
                    "pageNum": 1,
                    "pageSize": 50,
                    "column": column,
                    "tabName": "fulltext",
                    "plate": "",
                    "stock": stock_value,
                    "searchkey": search_key,
                    "secid": "",
                    "category": "category_ndbg_szsh",
                    "trade": "",
                    "seDate": f"{start.isoformat()}~{end.isoformat()}",
                    "sortName": "announcementTime",
                    "sortType": "desc",
                    "isHLtitle": "true",
                },
                diagnostics=self.last_diagnostics,
            )
            if any(
                _is_full_year_title(
                    _clean_title(
                        _value(item, "announcementTitle", "title", "bulletinTitle")
                    )
                )
                for item in _dict_rows(announcement_payload)
            ):
                break
        candidates: list[OfficialAnnouncement] = []
        for item in _dict_rows(announcement_payload):
            title = _clean_title(
                _value(item, "announcementTitle", "title", "bulletinTitle")
            )
            path = _value(item, "adjunctUrl", "url", "announcementUrl")
            if not title or not path or not _is_full_year_title(title):
                continue
            candidates.append(
                OfficialAnnouncement(
                    title=title,
                    url=urljoin(CNINFO_STATIC_ROOT, path),
                    published_date=_date_value(
                        _value(
                            item,
                            "announcementTime",
                            "announcementDate",
                            "publishDate",
                        )
                    ),
                    provider=self.name,
                )
            )
        if candidates:
            return max(candidates, key=_announcement_score)
        sse = SseAnnouncementProvider()
        announcement = sse.latest_annual(company)
        self.last_diagnostics.extend(sse.last_diagnostics)
        return announcement


class HkexAnnouncementProvider:
    name = "香港交易所披露易"

    def __init__(self) -> None:
        self.last_diagnostics: list[str] = []

    def supports(self, company: CompanyCandidate) -> bool:
        return company.symbol.upper().endswith(".HK") or company.exchange.upper() == "HKG"

    def latest_annual(self, company: CompanyCandidate) -> OfficialAnnouncement | None:
        self.last_diagnostics = []
        code = company.symbol.split(".")[0].zfill(5)
        referer = "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=zh"
        headers = _browser_headers(referer)
        prefix_payload = _request_json(
            "GET",
            HKEX_PREFIX_URL,
            cache_key=f"hkex_issuer_{code}",
            headers=headers,
            params={
                "callback": "callback",
                "lang": "ZH",
                "type": "A",
                "name": code,
                "market": "SEHK",
            },
            ttl_hours=24,
            diagnostics=self.last_diagnostics,
        )
        issuer_rows = _dict_rows(prefix_payload)
        issuer = next(
            (
                item
                for item in issuer_rows
                if _value(item, "stockCode", "code").lstrip("0")
                == code.lstrip("0")
            ),
            issuer_rows[0] if issuer_rows else {},
        )
        stock_id = _value(issuer, "stockId", "id")
        if not stock_id:
            return None

        end = date.today()
        start = end.replace(year=end.year - 4)
        payload = _request_json(
            "POST",
            HKEX_TITLE_URL,
            cache_key=f"hkex_annual_{stock_id}",
            headers=headers,
            data={
                "sortDir": "0",
                "sortByOptions": "DateTime",
                "category": "0",
                "market": "SEHK",
                "stockId": stock_id,
                "documentType": "-1",
                "from": start.strftime("%Y%m%d"),
                "to": end.strftime("%Y%m%d"),
                "title": "",
                "searchType": "1",
                "t1code": "40000",
                "t2Gcode": "-2",
                "t2code": "-2",
                "rowRange": "100",
                "lang": "ZH",
            },
            diagnostics=self.last_diagnostics,
        )
        candidates: list[OfficialAnnouncement] = []
        for item in _dict_rows(payload):
            title = _clean_title(_value(item, "TITLE", "title", "documentTitle"))
            path = _value(item, "LINK", "link", "url", "filePath")
            if not title or not path or not _is_full_year_title(title):
                continue
            candidates.append(
                OfficialAnnouncement(
                    title=title,
                    url=urljoin(HKEX_ROOT, path),
                    published_date=_date_value(
                        _value(item, "DATE_TIME", "dateTime", "date")
                    ),
                    provider=self.name,
                )
            )
        return max(candidates, key=_announcement_score) if candidates else None
