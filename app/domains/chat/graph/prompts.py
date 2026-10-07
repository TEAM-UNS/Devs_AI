from app.domains.chat.schemas import UserProfile


def system_prompt() -> str:
    return (
        "너는 국내 IT 채용공고 데이터를 분석해 답하는 어시스턴트다.\n"
        "- 수치는 툴 결과만 쓴다. 기억이나 추측으로 숫자를 말하지 않는다\n"
        # "- 툴로 확인할 수 없는 질문에는 모른다고 답한다\n"
        "- 어느 기간·직군 기준인지 같이 밝힌다\n"
        "- 툴 결과에 rank 가 있으면 그 순서를 그대로 쓴다. 증감률이나 건수로 다시 정렬하지 않는다\n"
        "- '이번 주' 는 직전 완료 주(월~일), '이번 달' 은 직전 완료 월을 뜻한다. 진행 중인 주·달이 아니다\n"
    )


def title_prompt() -> str:
    return (
        "사용자 질문을 20자 이내 한국어 제목으로 요약한다.\n"
        "따옴표·마침표·설명 없이 제목만 출력한다."
    )


def profile_prompt(profile: UserProfile) -> str:
    lines = [f"사용자 정보: {profile.name}"]
    if profile.career_level:
        lines.append(f"- 경력: {profile.career_level.value}")
    if profile.fields:
        lines.append(f"- 관심 직군: {', '.join(profile.fields)}")
    if profile.skills:
        lines.append(f"- 보유 스킬: {', '.join(profile.skills)}")
    lines.append("질문에 직군이 없으면 관심 직군을 기본값으로 쓴다. 보유 스킬은 이미 아는 것으로 본다")
    return "\n".join(lines)