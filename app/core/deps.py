"""공통 FastAPI 의존성.

    get_current_user   AUTH_MODE 에 따라 헤더 또는 JWT 에서 user_id 추출
    SessionDep         Annotated[AsyncSession, Depends(get_session)]
    RedisDep           arq redis pool

도메인별 의존성은 각 도메인의 dependencies.py 에 둔다.
"""
