from typing import Optional

from app.domains.report.schemas import ReportFacts


SYSTEM = """
너는 IT 채용 시장 분석가다. 주간 채용공고 집계를 읽고 한국어로 요약한다.

- 3~5문장, 문단 2개까지. "~습니다" 로 통일한다.
- 제목 없이 본문만 쓴다.
- "(신규)" 로 표시된 기술은 지난주 집계가 0이라 비율이 없다.
  "N% 상승" 이라 쓰지 말고 "새로 등장했다" 로 서술한다.
- 이번 주 집계를 통해 얻을 수 있는 인사이트를 서술한다.
"""


def build(facts: ReportFacts) -> str:
    lines = [
        f"주차: {facts.week_start_date} 시작",
        f"분야: {facts.major_name or '전체'}",
    ]

    if facts.posting_count is not None:
        lines.append(f"이번 주 수집 공고: {facts.posting_count:,}건")

    lines.append("\n가장 많이 찾은 기술 스택")
    for item in facts.popular:
        lines.append(
            f"  {item.rank}. {item.name} "
            f"{_change(item.previous_search_count, item.search_count, item.change_rate)}"
        )

    if facts.max_increase:
        lines.append(f"\n최대 상승: {facts.max_increase.skill_name} " + _change(
            facts.max_increase.previous_count,
            facts.max_increase.current_count,
            facts.max_increase.change_rate,
        ))

    if facts.max_decrease:
        lines.append(f"최대 하락: {facts.max_decrease.skill_name} " + _change(
            facts.max_decrease.previous_count,
            facts.max_decrease.current_count,
            facts.max_decrease.change_rate,
        ))

    lines.append("\n주요 기술 언급량")
    for item in facts.mentions:
        lines.append(f"  {item.name} {item.previous:,} → {item.current:,}")

    return "\n".join(lines)


def _change(previous: int, current: int, rate: Optional[float]) -> str:
    if rate is None:
        return f"{current:,}건 (신규)"

    return f"{previous:,} → {current:,}건 ({rate:+.1f}%)"