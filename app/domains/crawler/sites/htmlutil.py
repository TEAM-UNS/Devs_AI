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

import json
import logging
import re
from typing import Any

from bs4 import BeautifulSoup, Tag

log = logging.getLogger(__name__)

# 본문 텍스트가 이 길이 미만이면 "이미지 공고" 후보로 본다.
IMAGE_POSTING_MIN_CHARS = 200

# NBSP( ) 와 zero-width space(​). 채용 사이트 본문에 흔히 섞여 있어
# 그냥 두면 "Java​" 처럼 되어 스킬 매칭이 실패한다.
_WS = re.compile("[ 	 \u200b]+")
_BLANK_LINES = re.compile(r"\n{3,}")


# ═══════════════════════════════════════════════════════════════════════════
#  텍스트 추출
# ═══════════════════════════════════════════════════════════════════════════
def block_text(node: Tag | None) -> str:
    """<br> 과 블록 요소를 개행으로 살려서 텍스트를 뽑는다.

    get_text() 만 쓰면 "자격요건Java 3년" 처럼 한 줄로 붙어버려서
    섹션 헤더를 찾을 수 없게 된다. 추출기가 섹션 분할에 의존하므로
    개행 보존이 필수다.
    """
    if node is None:
        return ""

    working = node
    # <br> → 개행. 원본 트리를 건드리지 않도록 복사본에서 작업한다.
    if working.find("br"):
        working = BeautifulSoup(str(node), "lxml")
        for br in working.find_all("br"):
            br.replace_with("\n")

    text = working.get_text("\n")
    lines = [_WS.sub(" ", line).strip() for line in text.splitlines()]
    return _BLANK_LINES.sub("\n\n", "\n".join(line for line in lines if line)).strip()


# ═══════════════════════════════════════════════════════════════════════════
#  1순위 — JSON-LD
# ═══════════════════════════════════════════════════════════════════════════
def _iter_json_objects(payload: Any):
    """@graph · 배열 · 단일 객체를 모두 평평하게 훑는다."""
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_json_objects(item)
    elif isinstance(payload, dict):
        yield payload
        if graph := payload.get("@graph"):
            yield from _iter_json_objects(graph)


def jobposting_from_jsonld(soup: BeautifulSoup) -> dict[str, Any] | None:
    """schema.org JobPosting 을 찾아 돌려준다. 없으면 None.

    사이트마다 JSON-LD 를 여러 개 심는다(BreadcrumbList · Organization 등).
    @type 이 JobPosting 인 것만 고른다.
    """
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw or not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            # 주석·후행 콤마가 섞인 JSON-LD 가 실제로 존재한다. 조용히 넘어간다.
            log.debug("JSON-LD 파싱 실패 (무시)")
            continue

        for obj in _iter_json_objects(payload):
            types = obj.get("@type")
            types = types if isinstance(types, list) else [types]
            if "JobPosting" in types:
                return obj
    return None


def jsonld_text(value: Any) -> str | None:
    """JSON-LD 값에서 문자열을 꺼낸다. 중첩 객체·배열을 받아준다."""
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
        # streetAddress · addressLocality 는 jobLocation.address 에서 온다.
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
#  2순위 — 라벨-값
# ═══════════════════════════════════════════════════════════════════════════
# 라벨 정규화: 공백·콜론·별표를 떼고 비교한다. "대표자명*" 도 "대표자명" 으로.
_LABEL_NOISE = re.compile(r"[\s:：*]+")

# 논리 키 → 라벨 후보. 앞에 있는 것이 우선.
# 사람인·잡코리아가 같은 개념을 다른 라벨로 부르는 것을 여기서 흡수한다.
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


def label_value_pairs(soup: BeautifulSoup | Tag) -> dict[str, str]:
    """페이지의 모든 라벨-값 쌍을 dict 로 만든다.

    dl(dt/dd) · table(th/td) · "제목/설명" 형태의 클래스 쌍을 모두 훑는다.
    같은 라벨이 여러 번 나오면 **처음 것**을 남긴다. 공고 상세가 위쪽에,
    추천 공고나 푸터가 아래쪽에 오기 때문이다.
    """
    pairs: dict[str, str] = {}

    def put(label: str, value: str) -> None:
        key = normalize_label(label)
        value = _WS.sub(" ", (value or "").replace("\n", " ")).strip()
        if key and value and key not in pairs:
            pairs[key] = value

    # dl > dt/dd — 위치로 짝을 맞춘다 (dt 하나에 dd 여러 개인 경우는 첫 dd)
    for dl in soup.find_all("dl"):
        terms = dl.find_all("dt", recursive=True)
        for term in terms:
            value_node = term.find_next_sibling("dd")
            if value_node is not None:
                put(term.get_text(" "), value_node.get_text(" "))

    # table > th/td — 같은 행의 th 다음 td, 또는 세로형 테이블
    for header in soup.find_all("th"):
        value_node = header.find_next_sibling("td")
        if value_node is not None:
            put(header.get_text(" "), value_node.get_text(" "))

    # 클래스명이 tit/desc, tit/txt, title/content 로 짝지어진 흔한 패턴.
    # 클래스명에 의존하지만 어디까지나 보조 수단이다.
    for holder in soup.find_all(class_=re.compile(r"(tit|title|label|term)", re.IGNORECASE)):
        sibling = holder.find_next_sibling()
        if sibling is not None and sibling.name not in ("script", "style"):
            put(holder.get_text(" "), sibling.get_text(" "))

    return pairs


def largest_text_block(soup: BeautifulSoup, *, min_chars: int = 300) -> Tag | None:
    """본문으로 보이는 가장 큰 텍스트 덩어리를 찾는다.

    클래스명이 tailwind 처럼 의미 없는 사이트(개편 후 잡코리아)에서 쓴다.
    "가장 긴 텍스트를 가진, 자식 중 더 나은 후보가 없는 노드" 를 고른다.
    셀렉터를 안 쓰므로 개편에 영향을 받지 않는다.

    스크립트·스타일·네비게이션은 후보에서 제외한다.
    """
    best: Tag | None = None
    best_len = min_chars

    for node in soup.find_all(("article", "section", "div", "td")):
        if node.find(("script", "style"), recursive=False):
            pass  # 자식에 스크립트가 있어도 텍스트가 크면 후보다
        text = node.get_text(" ", strip=True)
        length = len(text)
        if length < best_len:
            continue
        # 부모보다 자식이 더 좁으면서 길이가 비슷하면 자식을 선호한다
        # (body 전체가 뽑히는 것을 막는다)
        if best is not None and best in node.parents and length < best_len * 1.15:
            continue
        best, best_len = node, length
    return best


NOISE_RE = re.compile(r"합격자소서|인적성|면접 후기|취업 전략|추천 ?공고|맞춤공고|로그인")

# 페이지 크롬. 본문 추출 전에 통째로 걷어낸다.
# 합격자소서 후기·AI추천공고·AI면접이 스킬로 잡힌 근본 원인이 이걸 안 지운 것이었다.
# 별칭을 하나씩 빼는 대응은 두더지잡기라 소스에서 제거한다.
BOILERPLATE = (
    "nav, header, footer, aside, .related, .recommend, "
    "[class*='banner'], [class*='ad-'], [class*='recommend'], "
    "[class*='related'], [id*='recommend'], [role='navigation']"
)


def strip_boilerplate(soup: BeautifulSoup) -> BeautifulSoup:
    """스크립트와 페이지 크롬을 제거한다.

    ★ 호출부는 반드시 복사본을 넘길 것. decompose 는 파괴적이라
      원본을 넘기면 나중에 JSON-LD 를 다시 읽을 때 사라져 있다.
    """
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
    """섹션 키워드를 가장 많이 품은 '적당한 크기' 조상 노드를 고른다.

    largest_text_block() 은 "가장 큰 덩어리" 를 고르는데, 사이드바나
    합격자소서 후기가 본문보다 클 수 있어 엉뚱한 곳을 잡는다.
    이 함수는 "자격요건" 같은 앵커에서 위로 올라가며 후보를 모으고,
    섹션 히트가 많고 잡음이 적은 쪽을 고른다. 클래스명을 쓰지 않는다.
    """
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
                    -len(text),  # 같은 점수면 더 좁은 쪽 (본문에 가깝다)
                )
                if score > best_score:
                    best, best_score = node, score
            node = node.parent
    return best


def pick(pairs: dict[str, str], key: str) -> str | None:
    """논리 키로 값을 꺼낸다. 라벨 표기 차이는 LABEL_ALIASES 가 흡수한다."""
    for label in LABEL_ALIASES.get(key, (key,)):
        if value := pairs.get(normalize_label(label)):
            return value
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  값 정규화
# ═══════════════════════════════════════════════════════════════════════════
_INT = re.compile(r"[\d,]+")


def parse_employee_count(text: str | None) -> int | None:
    """ "175 명 (2026년 기준)" → 175, "1,250명" → 1250."""
    if not text:
        return None
    if match := _INT.search(text.replace(" ", "")):
        try:
            value = int(match.group(0).replace(",", ""))
        except ValueError:
            return None
        # 5만 명이 넘는 값은 매출액 등을 잘못 읽은 것으로 본다.
        return value if 0 < value <= 50_000 else None
    return None


_REVENUE = re.compile(r"(?:(\d[\d,]*)\s*조)?\s*(?:(\d[\d,]*)\s*억)?\s*(?:(\d[\d,]*)\s*만)?\s*원")
_UNITS = (1_0000_0000_0000, 1_0000_0000, 1_0000)


def parse_revenue(text: str | None) -> int | None:
    """ "597억 2,533만원" → 59,725,330,000 (원 단위).

    한국 공시 표기는 조/억/만을 섞어 쓴다. 자리별로 더한다.
    """
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
    """ "경력 5년 ↑" → (5, None), "3~7년" → (3, 7), "신입" → (0, 0), "무관" → (None, None)."""
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
#  이미지 공고 판별
# ═══════════════════════════════════════════════════════════════════════════
def looks_like_image_posting(node: Tag | None, text: str | None) -> bool:
    """본문이 이미지 한 장인 공고인지.

    중소기업 공고에 흔하다. 텍스트가 없으니 스택이 안 잡히고, 그대로 두면
    "이 회사는 아무 기술도 안 쓴다" 로 집계되어 통계가 왜곡된다.

    ★ 호출 순서 주의: 본문 유효성 검사(extractor.is_valid_body)를 **먼저** 하고,
      실패했을 때만 이 함수를 부른다. 길이만 보면 안내문 500자짜리가
      "본문 있음" 으로 통과해 이미지 판정 자체가 안 걸린다.
    """
    if node is None:
        return False
    if len((text or "").strip()) >= IMAGE_POSTING_MIN_CHARS:
        return False
    return bool(collect_image_urls(node))


def classify_body(
    node: Tag | None, text: str | None, *, is_valid: bool
) -> tuple[bool, bool, list[str]]:
    """본문 판정 결과 → (body_is_image, body_extract_failed, image_urls).

    유효한 본문이면 둘 다 False.
    아니면 이미지가 있는지 보고 image / failed 를 가른다.
    """
    if is_valid:
        return False, False, []
    if looks_like_image_posting(node, text):
        return True, False, collect_image_urls(node)
    return False, True, []


# 공고 본문 이미지가 아닌 것들. 회사 로고·아이콘이 이미지 공고로 오분류되면
# "본문 없는 공고" 가 통째로 잘못 집계된다.
# (잡코리아 진단에서 102/103 이 …/LogoImage 였다.)
_NON_CONTENT_IMAGE = re.compile(
    r"logo|icon|sprite|blank|spacer|avatar|profile|thumb|badge|로고|아이콘|배너|썸네일",
    re.IGNORECASE,
)
# 공고 이미지는 보통 폭 300px 이상이다. 그보다 작으면 장식이다.
MIN_CONTENT_IMAGE_PX = 300


def _is_content_image(img: Tag) -> bool:
    src = img.get("src") or img.get("data-src") or img.get("data-original") or ""
    if not src or _NON_CONTENT_IMAGE.search(src):
        return False
    if _NON_CONTENT_IMAGE.search(" ".join(img.get("class") or [])):
        return False
    if _NON_CONTENT_IMAGE.search(img.get("alt") or ""):
        return False
    # 크기 정보가 있고 작으면 장식이다. 없으면 판단하지 않고 통과시킨다.
    for attr in ("width", "height"):
        raw = (img.get(attr) or "").strip().rstrip("px")
        if raw.isdigit() and int(raw) < MIN_CONTENT_IMAGE_PX:
            return False
    return True


def collect_image_urls(node: Tag | None, limit: int = 5) -> list[str]:
    """공고 본문 이미지 URL. 로고·아이콘은 제외한다.

    나중에 OCR 을 붙일 때 재수집하지 않으려고 저장해 둔다.
    """
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
