"""chat_* 테이블 CRUD.

    create_session / get_session(user_id 조건 필수) / list_sessions
    update_title / soft_delete_session
    append_message      seq 채번은 세션 행 잠금 후 message_count 기준
    save_tool_calls     chart_payload 포함
    load_history        표시용 메시지 + 툴콜(차트) 함께 로드

모든 조회에 user_id 조건을 건다. 소유자 확인을 서비스에 미루지 않는다.
"""
