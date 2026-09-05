"""HTML 파싱 공용 유틸 — 사람인 · 잡코리아가 공유한다.

3중 폴백의 1·2순위를 여기서 구현한다.

    1순위 jobposting_from_jsonld()
        <script type="application/ld+json"> 의 schema.org JobPosting.
        구글 구인구직 노출용이라 SEO 담당이 지키는 값이고, 개편에도 잘 살아남는다.
        제목·회사·경력·학력·근무지·급여·게시일·마감일이 한 번에 나온다.

    2순위 label_value_pairs() + pick()
        페이지의 dt/dd · th/td · .tit/.desc 쌍을 전부 긁어 dict 로 만든 뒤
        라벨 텍스트로 골라 쓴다.
        클래스명은 개편 때마다 바뀌지만 "경력" "사원수" 같은 한글 라벨은
        거의 안 바뀐다. 그래서 실무에서 가장 오래 버티는 방법이다.

    3순위 CSS 셀렉터는 각 사이트 파일의 SELECTORS dict 에 몰아둔다.

클래스명에 의존하는 코드를 이 파일에 추가하지 말 것.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any

from bs4 import BeautifulSoup, Tag

log = logging.getLogger(__name__)

IMAGE_POSTING_MIN_CHARS = 200

_WS = re.compile("[ 	 \u200b]+")
_BLANK_LINES = re.compile(r"\n{3,}")


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
def block_text(node: Tag | None) -> str:
    if node is None:
        return ""

    working = node
    if working.find("br"):
        working = BeautifulSoup(str(node), "lxml")
        for br in working.find_all("br"):
            br.replace_with("\n")

    text = working.get_text("\n")
    lines = [_WS.sub(" ", line).strip() for line in text.splitlines()]
    return _BLANK_LINES.sub("\n\n", "\n".join(line for line in lines if line)).strip()


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
def _iter_json_objects(payload: Any):
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_json_objects(item)
    elif isinstance(payload, dict):
        yield payload
        if graph := payload.get("@graph"):
            yield from _iter_json_objects(graph)


def jobposting_from_jsonld(soup: BeautifulSoup) -> dict[str, Any] | None:
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


def jsonld_text(value: Any) -> str | None:
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


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
_LABEL_NOISE = re.compile(r"[\s:：*]+")

LABEL_ALIASES: dict[str, tuple[str, ...]] = {
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


def normalize_label(text: str) -> str:
    return _LABEL_NOISE.sub("", text or "").strip()


# ★ <a> 는 지우지 않는다. 홈페이지 값은 스냅샷 228건 중 대부분이 <a> 안에만
VALUE_CHROME = (
    "script, style, button, "
    "[class*='tooltip' i], [class*='tip_' i], [role='dialog'], [role='tooltip'], "
    ".blind, .sr-only, .screen_out, [aria-hidden='true'], [hidden]"
)


def clean_value_text(node: Tag) -> str:
    duplicate = copy.copy(node)
    for junk in duplicate.select(VALUE_CHROME):
        junk.decompose()
    return duplicate.get_text(" ")


def label_value_pairs(soup: BeautifulSoup | Tag) -> dict[str, str]:
    pairs: dict[str, str] = {}

    def put(label: str, value: str) -> None:
        key = normalize_label(label)
        value = _WS.sub(" ", (value or "").replace("\n", " ")).strip()
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


def largest_text_block(soup: BeautifulSoup, *, min_chars: int = 300) -> Tag | None:
    best: Tag | None = None
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


NOISE_RE = re.compile(r"합격자소서|인적성|면접 후기|취업 전략|추천 ?공고|맞춤공고|로그인")

BOILERPLATE = (
    "nav, header, footer, aside, .related, .recommend, "
    "[class*='banner'], [class*='ad-'], [class*='recommend'], "
    "[class*='related'], [id*='recommend'], [role='navigation']"
)


def strip_boilerplate(soup: BeautifulSoup) -> BeautifulSoup:
    for tag in soup.find_all(("script", "style", "noscript", "iframe")):
        tag.decompose()
    for node in soup.select(BOILERPLATE):
        node.decompose()
    return soup


def section_anchored_block(
    soup: BeautifulSoup,
    section_re: re.Pattern[str],
    *,
    min_chars: int = 300,
    max_chars: int = 30_000,
) -> Tag | None:
    best: Tag | None = None
    best_score = (0, 0)

    for element in soup.find_all(string=section_re):
        node = element.parent
        for _ in range(12):
            if node is None or node.name in ("html", "[document]"):
                break
            text = node.get_text(" ", strip=True)
            if min_chars <= len(text) <= max_chars:
                score = (
                    len(section_re.findall(text)) - len(NOISE_RE.findall(text)),
                    -len(text),
                )
                if score > best_score:
                    best, best_score = node, score
            node = node.parent
    return best


def pick(pairs: dict[str, str], key: str) -> str | None:
    for label in LABEL_ALIASES.get(key, (key,)):
        if value := pairs.get(normalize_label(label)):
            return value
    return None


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
_INT = re.compile(r"[\d,]+")


def parse_employee_count(text: str | None) -> int | None:
    if not text:
        return None
    if match := _INT.search(text.replace(" ", "")):
        try:
            value = int(match.group(0).replace(",", ""))
        except ValueError:
            return None
        return value if 0 < value <= 50_000 else None
    return None


_REVENUE = re.compile(r"(?:(\d[\d,]*)\s*조)?\s*(?:(\d[\d,]*)\s*억)?\s*(?:(\d[\d,]*)\s*만)?\s*원")
_UNITS = (1_0000_0000_0000, 1_0000_0000, 1_0000)


def parse_revenue(text: str | None) -> int | None:
    if not text:
        return None
    match = _REVENUE.search(text.replace(" ", ""))
    if not match or not any(match.groups()):
        return None
    total = 0
    for raw, unit in zip(match.groups(), _UNITS, strict=True):
        if raw:
            total += int(raw.replace(",", "")) * unit
    return total or None


_CAREER_ANY = re.compile(r"무관|신입\s*[·/]?\s*경력|경력\s*무관")
_CAREER_NEW = re.compile(r"신입")
_CAREER_RANGE = re.compile(r"(\d+)\s*[~\-]\s*(\d+)\s*년")
_CAREER_MIN = re.compile(r"(\d+)\s*년\s*(?:이상|↑)")
_CAREER_ONE = re.compile(r"(\d+)\s*년")


def parse_career(text: str | None) -> tuple[int | None, int | None]:
    if not text:
        return None, None
    cleaned = text.strip()

    if _CAREER_ANY.search(cleaned):
        return None, None
    if match := _CAREER_RANGE.search(cleaned):
        low, high = int(match.group(1)), int(match.group(2))
        return (low, high) if low <= high else (high, low)
    if match := _CAREER_MIN.search(cleaned):
        return int(match.group(1)), None
    if _CAREER_NEW.search(cleaned):
        return 0, 0
    if match := _CAREER_ONE.search(cleaned):
        return int(match.group(1)), None
    return None, None


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
def looks_like_image_posting(node: Tag | None, text: str | None) -> bool:
    if node is None:
        return False
    if len((text or "").strip()) >= IMAGE_POSTING_MIN_CHARS:
        return False
    return bool(collect_image_urls(node))


def classify_body(
    node: Tag | None, text: str | None, *, is_valid: bool
) -> tuple[bool, bool, list[str]]:
    if is_valid:
        return False, False, []
    if looks_like_image_posting(node, text):
        return True, False, collect_image_urls(node)
    return False, True, []


_NON_CONTENT_IMAGE = re.compile(
    r"logo|icon|sprite|blank|spacer|avatar|profile|thumb|badge|로고|아이콘|배너|썸네일",
    re.IGNORECASE,
)
MIN_CONTENT_IMAGE_PX = 300


def _is_content_image(img: Tag) -> bool:
    src = img.get("src") or img.get("data-src") or img.get("data-original") or ""
    if not src or _NON_CONTENT_IMAGE.search(src):
        return False
    if _NON_CONTENT_IMAGE.search(" ".join(img.get("class") or [])):
        return False
    if _NON_CONTENT_IMAGE.search(img.get("alt") or ""):
        return False
    for attr in ("width", "height"):
        raw = (img.get(attr) or "").strip().rstrip("px")
        if raw.isdigit() and int(raw) < MIN_CONTENT_IMAGE_PX:
            return False
    return True


def collect_image_urls(node: Tag | None, limit: int = 5) -> list[str]:
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
