from endoreg_db.authz.policy import get_needed_role


def test_case_routes_use_patient_roles() -> None:
    assert get_needed_role("case-list", "GET") == "patient:read"
    assert get_needed_role("case-detail", "PATCH") == "patient:write"
    assert get_needed_role("case-close", "POST") == "patient:write"
    assert get_needed_role("case-reopen", "POST") == "patient:write"
    assert get_needed_role("case-create-with-examination", "POST") == "patient:write"
    assert get_needed_role("case-attach-document", "POST") == "patient:write"
