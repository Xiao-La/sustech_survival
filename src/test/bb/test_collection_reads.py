"""Complete, user-scoped BB reads against deterministic REST fixtures."""
from unittest.mock import Mock
import pytest
from sustech_survival.bb._collections import collection, user_attempts
from sustech_survival.bb import ddl, submit, download


def responses(pages):
    return lambda path, session: pages[path]


def test_user_attempts_filters_owners_and_follows_pages():
    path = '/learn/api/public/v1/courses/_10_1/gradebook/columns/_20_1/attempts'
    fetch = responses({
        '/learn/api/public/v1/users/me': {'id': '_1_1'},
        path: {'results': [{'id': '_30_1', 'userId': '_2_1'}],
               'paging': {'nextPage': path + '?offset=1'}},
        path + '?offset=1': {'results': [{'id': '_31_1', 'userId': '_1_1', 'created': '2026-10-01'}]},
    })
    assert [row['id'] for row in user_attempts('_10_1', '_20_1', object(), fetch)] == ['_31_1']


@pytest.mark.parametrize('data', [{}, {'results': None}, {'results': 'login page'}, {'results': [None]}])
def test_invalid_collections_are_not_empty(data):
    with pytest.raises(ValueError):
        collection('/items', object(), lambda *a: data)


def test_attempt_without_owner_is_unknown():
    fetch = lambda path, session: {'id': '_1_1'} if path.endswith('/me') else {'results': [{'id': '_30_1'}]}
    with pytest.raises(ValueError, match='ownership'):
        user_attempts('_10_1', '_20_1', object(), fetch)


def test_count_requires_grade_column():
    with pytest.raises(submit.SubmissionFormError, match='grade column'):
        submit._safe_initial_attempts_count('10', None)


def test_count_and_download_use_current_user_pages(monkeypatch):
    path = '/learn/api/public/v1/courses/_10_1/gradebook/columns/_20_1/attempts'
    fetch = responses({
        '/learn/api/public/v1/users/me': {'id': '_1_1'},
        path: {'results': [{'id': '_30_1', 'userId': '_2_1'}], 'paging': {'nextPage': path + '?offset=1'}},
        path + '?offset=1': {'results': [{'id': '_31_1', 'userId': '_1_1', 'created': '2026-10-01T00:00:00Z'}]},
    })
    import importlib
    session_module = importlib.import_module('sustech_survival.bb.session')
    monkeypatch.setattr(session_module, 'session', lambda: object())
    monkeypatch.setattr('sustech_survival.bb.query.api', fetch)
    monkeypatch.setattr(download, 'api', fetch)
    monkeypatch.setattr(download, 'session', lambda: object())
    assert submit._safe_initial_attempts_count('10', '20') == 1
    assert download.get_assignment_attempts('10', '20') == [('31_1', 1, '2026-10-01 00:00:00')]


def test_enrollments_have_no_hardcoded_term_and_include_all_pages(monkeypatch):
    path = '/learn/api/public/v1/users/_1_1/courses'
    fetch = responses({
        '/learn/api/public/v1/users/me': {'id': '_1_1'},
        path: {'results': [{'courseId': '_10_1'}], 'paging': {'nextPage': path + '?offset=1'}},
        path + '?offset=1': {'results': [{'courseId': '_11_1'}]},
        '/learn/api/public/v1/courses/_10_1': {'name': 'Older course'},
        '/learn/api/public/v1/courses/_11_1': {'name': 'Current course'},
    })
    monkeypatch.setattr(ddl, 'api', fetch)
    assert ddl.get_courses(object()) == [('_10_1', 'Older course'), ('_11_1', 'Current course')]


def test_failed_deadline_read_surfaces_in_context(monkeypatch):
    from sustech_survival.context import fetch_next_deadline
    monkeypatch.setattr(ddl, 'upcoming_deadlines', Mock(side_effect=RuntimeError('gradebook unavailable')))
    assert fetch_next_deadline()['error'] == 'unavailable'


def test_failed_course_columns_do_not_disappear(monkeypatch):
    monkeypatch.setattr(ddl, '_session', lambda: object())
    monkeypatch.setattr(ddl, 'get_courses', lambda *a: [('_10_1', 'Current course')])
    monkeypatch.setattr(ddl, 'get_gradebook_columns', Mock(side_effect=RuntimeError('unavailable')))
    with pytest.raises(RuntimeError, match='unavailable'):
        ddl.upcoming_deadlines()
