"""market DTO — ORM 엔티티가 도메인 밖으로 새지 않게 하는 경계.

지금은 크롤러가 쓰는 것만 있다. 챗봇 툴용 DTO(SalaryStats · SkillRelation 등)는
queries.py 를 구현할 때 여기에 추가한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SkillDictionaryRow:
    """추출기에 넘기는 별칭 사전 1행.

    위치 인자 튜플로 주고받으면 컬럼이 늘 때마다 조용히 어긋나서 DTO 로 둔다.
    이 타입을 market 이 소유하는 이유는 R1 때문이다 —
    market 은 crawler 를 import 할 수 없으므로 경계 타입도 market 쪽에 있어야 한다.
    """

    skill_id: int
    name: str
    is_ambiguous: bool = False
    is_common: bool = False
    # 대소문자 무시 별칭 (소문자). 정규 표기도 포함한다.
    aliases: list[str] = field(default_factory=list)
    # 대소문자 구분 별칭 (표기 그대로). CAN · ES · R · C 처럼 영어 문장과
    # 겹치는 짧은 토큰이 대상이다.
    cs_aliases: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class UnmatchedTag:
    """사전에 없는 사이트 태그. 사전 보강 대상 리포트에 쓴다."""

    tag: str
    count: int
