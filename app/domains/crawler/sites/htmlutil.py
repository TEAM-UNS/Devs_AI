# 사람인과 잡코리아가 함께 쓰는 HTML 파싱 유틸

import copy
import json
import logging
import re
from typing import Optional, Any

from bs4 import BeautifulSoup, Tag

log = logging.getLogger(__name__)


def block_text(node: Optional[Tag]) -> str:
    if node is None:
        return ""

    working = node
    if working.find("br"):
        working = BeautifulSoup(str(node), "lxml")
        for br in working.find_all("br"):
            br.replace_with("\n")

    text = working.get_text("\n")
    lines = [re.sub("[ \t\xa0\u200b]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line for line in lines if line)).strip()


def _iter_json_objects(payload: Any):
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_json_objects(item)
    elif isinstance(payload, dict):
        yield payload
        if graph := payload.get("@graph"):
            yield from _iter_json_objects(graph)


def jobposting_from_jsonld(soup: BeautifulSoup) -> Optional[dict[str, Any]]:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw or not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            log.debug("JSON-LD 파싱 실패 (무시)")
            continue

        for obj in _iter_json_objects(payload):
            types = obj.get("@type")
            types = types if isinstance(types, list) else [types]
            if "JobPosting" in types:
                return obj
    return None


def jsonld_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        for item in value:
            if found := jsonld_text(item):
                return found
        return None
    if isinstance(value, dict):
        for key in (
            "name",
            "value",
            "@value",
            "text",
            "description",
            "streetAddress",
            "addressLocality",
            "address",
        ):
            if key in value and (found := jsonld_text(value[key])):
                return found
    return None


def normalize_label(text: str) -> str:
    return re.sub(r"[\s:：*]+", "", text or "").strip()


def clean_value_text(node: Tag) -> str:
    duplicate = copy.copy(node)
    # a 태그는 지우지 않는다 (홈페이지 값이 대부분 a 안에만 있다)
    value_chrome = (
        "script, style, button, "
        "[class*='tooltip' i], [class*='tip_' i], [role='dialog'], [role='tooltip'], "
        ".blind, .sr-only, .screen_out, [aria-hidden='true'], [hidden]"
    )
    for junk in duplicate.select(value_chrome):
        junk.decompose()
    return duplicate.get_text(" ")


def label_value_pairs(soup: BeautifulSoup | Tag) -> dict[str, str]:
    pairs: dict[str, str] = {}

    def put(label: str, value: str) -> None:
        key = normalize_label(label)
        value = re.sub("[ \t\xa0\u200b]+", " ", (value or "").replace("\n", " ")).strip()
        if key and value and key not in pairs:
            pairs[key] = value

    for dl in soup.find_all("dl"):
        terms = dl.find_all("dt", recursive=True)
        for term in terms:
            value_node = term.find_next_sibling("dd")
            if value_node is not None:
                put(term.get_text(" "), clean_value_text(value_node))

    for header in soup.find_all("th"):
        value_node = header.find_next_sibling("td")
        if value_node is not None:
            put(header.get_text(" "), clean_value_text(value_node))

    for holder in soup.find_all(class_=re.compile(r"(tit|title|label|term)", re.IGNORECASE)):
        sibling = holder.find_next_sibling()
        if sibling is not None and sibling.name not in ("script", "style"):
            put(holder.get_text(" "), clean_value_text(sibling))

    return pairs


def largest_text_block(soup: BeautifulSoup, *, min_chars: int = 300) -> Optional[Tag]:
    best: Optional[Tag] = None
    best_len = min_chars

    for node in soup.find_all(("article", "section", "div", "td")):
        if node.find(("script", "style"), recursive=False):
            pass
        text = node.get_text(" ", strip=True)
        length = len(text)
        if length < best_len:
            continue
        if best is not None and best in node.parents and length < best_len * 1.15:
            continue
        best, best_len = node, length
    return best


def strip_boilerplate(soup: BeautifulSoup) -> BeautifulSoup:
    for tag in soup.find_all(("script", "style", "noscript", "iframe")):
        tag.decompose()
    boilerplate = (
        "nav, header, footer, aside, .related, .recommend, "
        "[class*='banner'], [class*='ad-'], [class*='recommend'], "
        "[class*='related'], [id*='recommend'], [role='navigation']"
    )
    for node in soup.select(boilerplate):
        node.decompose()
    return soup


def section_anchored_block(
    soup: BeautifulSoup,
    section_re: re.Pattern[str],
    *,
    min_chars: int = 300,
    max_chars: int = 30_000,
) -> Optional[Tag]:
    best: Optional[Tag] = None
    best_score = (0, 0)
    noise_re = re.compile(r"합격자소서|인적성|면접 후기|취업 전략|추천 ?공고|맞춤공고|로그인")

    for element in soup.find_all(string=section_re):
        node = element.parent
        for _ in range(12):
            if node is None or node.name in ("html", "[document]"):
                break
            text = node.get_text(" ", strip=True)
            if min_chars <= len(text) <= max_chars:
                score = (
                    len(section_re.findall(text)) - len(noise_re.findall(text)),
                    -len(text),
                )
                if score > best_score:
                    best, best_score = node, score
            node = node.parent
    return best


def pick(pairs: dict[str, str], key: str) -> Optional[str]:
    label_aliases: dict[str, tuple[str, ...]] = {
        "career": ("경력", "경력조건", "경력사항"),
        "education": ("학력", "학력조건"),
        "employment_type": ("근무형태", "고용형태", "근무형태/근무일시"),
        "salary": ("급여", "급여조건", "연봉"),
        "location": ("근무지역", "근무지", "근무예정지", "지역"),
        "posted_at": ("시작일", "접수기간", "공고기간"),
        "expires_at": ("마감일", "접수마감일", "접수기간"),
        "employee": ("사원수", "사원수(정규직)", "직원수"),
        "company_type": ("기업형태", "기업구분", "기업규모"),
        "industry": ("업종", "산업", "산업분류"),
        "founded": ("설립일", "설립년도"),
        "revenue": ("매출액", "매출"),
        "homepage": ("홈페이지", "기업홈페이지", "회사홈페이지"),
        "ceo": ("대표자명", "대표자", "대표"),
        "address": ("기업주소", "주소", "회사주소"),
        "working_hours": ("근무일시", "근무시간", "근무일"),
        "preferred": ("우대사항",),
    }
    for label in label_aliases.get(key, (key,)):
        if value := pairs.get(normalize_label(label)):
            return value
    return None


def parse_employee_count(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    if match := re.search(r"[\d,]+", text.replace(" ", "")):
        try:
            value = int(match.group(0).replace(",", ""))
        except ValueError:
            return None
        return value if 0 < value <= 50_000 else None
    return None


def parse_revenue(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    match = re.search(
        r"(?:(\d[\d,]*)\s*조)?\s*(?:(\d[\d,]*)\s*억)?\s*(?:(\d[\d,]*)\s*만)?\s*원",
        text.replace(" ", ""),
    )
    if not match or not any(match.groups()):
        return None
    total = 0
    units = (1_0000_0000_0000, 1_0000_0000, 1_0000)
    for raw, unit in zip(match.groups(), units, strict=True):
        if raw:
            total += int(raw.replace(",", "")) * unit
    return total or None


def parse_career(text: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    if not text:
        return None, None
    cleaned = text.strip()

    if re.search(r"무관|신입\s*[·/]?\s*경력|경력\s*무관", cleaned):
        return None, None
    if match := re.search(r"(\d+)\s*[~\-]\s*(\d+)\s*년", cleaned):
        low, high = int(match.group(1)), int(match.group(2))
        return (low, high) if low <= high else (high, low)
    if match := re.search(r"(\d+)\s*년\s*(?:이상|↑)", cleaned):
        return int(match.group(1)), None
    if re.search(r"신입", cleaned):
        return 0, 0
    if match := re.search(r"(\d+)\s*년", cleaned):
        return int(match.group(1)), None
    return None, None


def looks_like_image_posting(node: Optional[Tag], text: Optional[str]) -> bool:
    if node is None:
        return False
    if len((text or "").strip()) >= 200:
        return False
    return bool(collect_image_urls(node))


def classify_body(
    node: Optional[Tag], text: Optional[str], *, is_valid: bool
) -> tuple[bool, bool, list[str]]:
    if is_valid:
        return False, False, []
    if looks_like_image_posting(node, text):
        return True, False, collect_image_urls(node)
    return False, True, []


def _is_content_image(img: Tag) -> bool:
    non_content = re.compile(
        r"logo|icon|sprite|blank|spacer|avatar|profile|thumb|badge|로고|아이콘|배너|썸네일",
        re.IGNORECASE,
    )
    src = img.get("src") or img.get("data-src") or img.get("data-original") or ""
    if not src or non_content.search(src):
        return False
    if non_content.search(" ".join(img.get("class") or [])):
        return False
    if non_content.search(img.get("alt") or ""):
        return False
    for attr in ("width", "height"):
        raw = (img.get(attr) or "").strip().rstrip("px")
        if raw.isdigit() and int(raw) < 300:
            return False
    return True


def collect_image_urls(node: Optional[Tag], limit: int = 5) -> list[str]:
    if node is None:
        return []
    urls: list[str] = []
    for img in node.find_all("img"):
        if not _is_content_image(img):
            continue
        src = img.get("src") or img.get("data-src") or img.get("data-original")
        if src and src not in urls:
            urls.append(src)
        if len(urls) >= limit:
            break
    return urls
