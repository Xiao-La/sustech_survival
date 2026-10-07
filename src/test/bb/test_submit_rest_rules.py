"""Tests for the five BB submission-semantics rules ported from
sustech-cli v0.11.1 (blackboard-assignment-form.ts +
blackboard-submission.ts).

Coverage map (per the task brief):
  Rule 1 — verify exact assignment target BEFORE any POST (initial 404 ⇒
           "no attempts yet" only after assignment + blank first-submission
           form are verified; all other errors stay errors)
  Rule 2 — never automatically replay the submission POST, including across
           HTTP redirects
  Rule 4 — keep the reviewed file's SHA-256 and refuse to submit if the
           bytes changed between preview and apply, using a distinct error
           class
  Rule 5 — text and comment submissions must use the editor field the
           fetched form actually exposes, and fail with a clear error
           when it is absent

Rule 3 (sanitized user-facing errors) is exercised in tandem with R1, R2
and R5 — every failure path produces a ``stage`` + sanitized ``status``
+ sanitized ``path`` triple. Direct rule-3 unit tests live in the helpers
section below.
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# A canonical blank first-submission BB form. Mirrors the TS reference:
#   - one <form id="uploadAssignmentFormId" method="post" enctype="multipart/form-data" action="...">
#   - one hidden CSRF nonce field
#   - course_id / content_id / attempt_id="" / group_id hidden fields
#   - one <input type="file" id="newFile_chooseLocalFile">
#   - one <textarea name="studentSubmission.text">
#   - one <textarea name="student_comments">
_BLANK_FORM_HTML = """<!DOCTYPE html>
<html>
<head><title>Upload Assignment: Report of exp 5</title></head>
<body>
<form enctype="multipart/form-data" method="post"
      name="uploadAssignmentForm"
      action="/webapps/assignment/uploadAssignment?action=submit&amp;course_id=_8328_1&amp;content_id=_610821_1"
      id="uploadAssignmentFormId"
      onsubmit="return checkDupeFile('submit')">
  <input type="hidden" name="blackboard.platform.security.NonceUtil.nonce"
         value="d4eb31bc-35c5-4541-80aa-73d6a5f0e56d" />
  <input type="hidden" name="blackboard.platform.security.NonceUtil.nonce.ajax"
         id="ajaxNonceId" value="0a139404-ec7a-4f7d-8b6e-7577a6083844" />
  <input type="hidden" name="isAjaxSubmit" id="isAjaxSubmit" value="true" />
  <input type="hidden" name="course_id" id="course_id" value="_8328_1" />
  <input type="hidden" name="content_id" id="content_id" value="_610821_1" />
  <input type="hidden" name="attempt_id" id="attempt_id" value="" />
  <input type="hidden" name="dispatch" id="dispatch" value="" />
  <input class="hiddenInput" type="file" tabindex="-1" multiple
         aria-hidden="true" id="newFile_chooseLocalFile" />
  <textarea name="studentSubmission.text" id="studentSubmission.text"></textarea>
  <textarea name="student_comments" id="student_comments"></textarea>
</form>
</body>
</html>
"""

_FORM_URL = (
    "https://bb.sustech.edu.cn/webapps/assignment/uploadAssignment"
    "?action=newAttempt&content_id=_610821_1&course_id=_8328_1&group_id="
)

_DESTINATION_JSON = json.dumps({
    "destinationUrl": "/webapps/assignment/uploadAssignment?course_id=_8328_1&content_id=_610821_1&mode=DEFAULT"
})


def _target_ok(course_id: str = "8328", content_id: str = "610821",
               *, is_assignment: bool = True,
               column_id: str | None = "_777_1") -> dict:
    return {
        "course_id": course_id, "content_id": content_id,
        "column_id": column_id, "assignment_name": "Report of exp 5",
        "course_name": "EE-100", "is_assignment": is_assignment,
    }


def _mock_form_response(*, html: str = _BLANK_FORM_HTML,
                        url: str = _FORM_URL,
                        status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.url = url
    r.text = html
    return r


def _mock_post_response(*, status: int = 200, body: str = _DESTINATION_JSON,
                        headers: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.text = body
    r.headers = headers if headers is not None else {"Content-Type": "text/x-json;charset=UTF-8"}
    if status < 300 and body.startswith("{"):
        r.json.return_value = json.loads(body)
    else:
        r.json.side_effect = ValueError("not json")
    return r


# ---------------------------------------------------------------------------
# RULE 1 — Verify the exact assignment target BEFORE any POST.
# ---------------------------------------------------------------------------

class TestRule1VerifyAssignmentTarget:
    """R1: resolve course + content (or column) and confirm it is the
    accessible assignment; never post before that."""

    def test_resolves_course_and_content(self):
        """verify_assignment_target() must call the project's normaliser
        (not lstrip/rstrip!) and resolve to the numeric core id."""
        from sustech_survival.bb.submit import verify_assignment_target
        with patch("sustech_survival.bb.download.resolve_course") as reverse_lookup, \
             patch("sustech_survival.bb.query.api",
                   return_value={"name": "Engineering Probability"}) as course_read, \
             patch("sustech_survival.bb.download.get_content_item",
                   return_value={
                       "id": "_610821_1",
                       "title": "Report of exp 5",
                       "contentHandler": {"id": "resource/x-bb-assignment",
                                          "gradeColumnId": "_777_1"},
                   }), \
             patch("sustech_survival.bb.download.get_column_id_for_content",
                   return_value="_777_1"):
            target = verify_assignment_target("_8328_1", "_610821_1")
        assert target["course_id"] == "8328"
        assert target["content_id"] == "610821"
        assert target["column_id"] == "_777_1"
        assert target["is_assignment"] is True
        assert target["course_name"] == "Engineering Probability"
        course_read.assert_called_once_with("/learn/api/public/v1/courses/_8328_1")
        reverse_lookup.assert_not_called()

    def test_rejects_unresolved_content(self):
        """An assignment that doesn't resolve must raise, NOT a silent
        'no items' failure. Mirrors the brief: a 403 / missing form must
        never be reported as success."""
        from sustech_survival.bb.submit import (
            verify_assignment_target, SubmissionFormError,
        )
        with patch("sustech_survival.bb.download.resolve_course",
                   return_value="8328"), \
             patch("sustech_survival.bb.query.api", return_value={"name": "EE-100"}), \
             patch("sustech_survival.bb.download.get_content_item",
                   return_value=None), \
             patch("sustech_survival.bb.download.get_column_id_for_content",
                   return_value=None):
            with pytest.raises(SubmissionFormError) as ei:
                verify_assignment_target("8328", "999999")
        assert "BLACKBOARD_ASSIGNMENT_NOT_ACCESSIBLE" in ei.value.code
        assert ei.value.stage == "verify_target"

    def test_rejects_non_assignment_handler(self, tmp_path):
        """A non-assignment content handler (e.g. resource/x-bb-document)
        must not be silently treated as an assignment."""
        from sustech_survival.bb.submit import submit_assignment_rest
        target = {
            "course_id": "8328", "content_id": "610821",
            "column_id": None, "assignment_name": "Syllabus",
            "course_name": "EE-100", "is_assignment": False,
        }
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        with patch("sustech_survival.bb.submit.verify_assignment_target",
                   return_value=target), \
             patch("sustech_survival.bb.submit._safe_initial_attempts_count",
                   return_value=0), \
             patch("sustech_survival.bb.submit._bb_session") as mock_sess:
            mock_sess.return_value.get.return_value = _mock_form_response()
            result = submit_assignment_rest("8328", "610821", str(pdf))
        # Submission MUST be refused, no POST.
        assert result.ok is False
        assert result.diagnostics.get("reason") == "not_an_assignment"
        mock_sess.return_value.post.assert_not_called()

    def test_initial_404_only_treated_as_no_attempts(self):
        """A 404 from the gradebook attempts endpoint is the ONLY error
        shape that counts as 'no attempts yet'."""
        from sustech_survival.bb.submit import _is_no_attempts_404
        # requests.HTTPError carries response.status_code
        e404 = Exception("not found")
        e404.response = MagicMock(status_code=404)
        e500 = Exception("server error")
        e500.response = MagicMock(status_code=500)
        e403 = Exception("forbidden")
        e403.response = MagicMock(status_code=403)
        e_no_resp = Exception("network")

        assert _is_no_attempts_404(e404) is True
        assert _is_no_attempts_404(e500) is False
        assert _is_no_attempts_404(e403) is False
        assert _is_no_attempts_404(e_no_resp) is False

    def test_no_attempts_count_propagates_non_404_errors(self):
        """A non-404 from the gradebook attempts endpoint must propagate —
        per R1, only 404 is the 'no attempts yet' shape."""
        from sustech_survival.bb.submit import _safe_initial_attempts_count
        # Simulate a 500 from the attempts endpoint
        e500 = Exception("boom")
        e500.response = MagicMock(status_code=500)
        with patch("sustech_survival.bb.query.api",
                   side_effect=e500), \
             patch("sustech_survival.bb.session.session",
                   return_value=MagicMock()):
            with pytest.raises(Exception) as ei:
                _safe_initial_attempts_count("8328", "_777_1")
        # It's the same exception object (or its kind).
        assert getattr(ei.value, "response", None) is e500.response

    def test_form_get_also_required_before_no_attempts(self):
        """R1 is precise: 'no attempts yet' is valid only AFTER both the
        assignment AND its blank first-submission view form have been
        verified. A populated attempt_id in the form is a re-submission
        form, not the blank view — the form GET itself must reject it."""
        from sustech_survival.bb.submit import _get_upload_form, SubmissionFormError
        html = _BLANK_FORM_HTML.replace(
            'value="" />', 'value="_12345_1" />', 1  # populate attempt_id
        )
        # Actually our placeholder matches a different token; do a precise edit:
        html = html.replace(
            '<input type="hidden" name="attempt_id" id="attempt_id" value="" />',
            '<input type="hidden" name="attempt_id" id="attempt_id" value="_12345_1" />',
        )
        with patch("sustech_survival.bb.submit._bb_session") as mock_sess:
            mock_sess.return_value.get.return_value = _mock_form_response(html=html)
            with pytest.raises(SubmissionFormError) as ei:
                _get_upload_form("8328", "610821")
        assert ei.value.code == "BLACKBOARD_FORM_NOT_BLANK"
        assert ei.value.stage == "prepare_form"

    def test_form_get_redirected_to_different_assignment_rejected(self):
        """R1: a response URL that doesn't match the requested assignment
        must be rejected BEFORE we trust the form fields."""
        from sustech_survival.bb.submit import _get_upload_form, SubmissionFormError
        wrong_url = (
            "https://bb.sustech.edu.cn/webapps/assignment/uploadAssignment"
            "?action=newAttempt&content_id=_OTHER_1&course_id=_8328_1&group_id="
        )
        with patch("sustech_survival.bb.submit._bb_session") as mock_sess:
            mock_sess.return_value.get.return_value = _mock_form_response(url=wrong_url)
            with pytest.raises(SubmissionFormError) as ei:
                _get_upload_form("8328", "610821")
        assert "different content_id" in ei.value.message
        assert ei.value.stage == "prepare_form"

    def test_form_get_rejects_missing_nonce(self):
        """A form with no usable CSRF nonce is unsafe to POST to."""
        from sustech_survival.bb.submit import _get_upload_form, SubmissionFormError
        html = _BLANK_FORM_HTML.replace(
            'value="d4eb31bc-35c5-4541-80aa-73d6a5f0e56d" />', 'value="" />'
        )
        with patch("sustech_survival.bb.submit._bb_session") as mock_sess:
            mock_sess.return_value.get.return_value = _mock_form_response(html=html)
            with pytest.raises(SubmissionFormError) as ei:
                _get_upload_form("8328", "610821")
        assert ei.value.code == "BLACKBOARD_FORM_MISSING_NONCE"

    def test_form_get_ambiguous_editor_fields_rejected(self):
        """R5 + R1: ambiguous <textarea name=...> means BB returned an
        unsafe form — refuse rather than silently picking one."""
        from sustech_survival.bb.submit import _get_upload_form, SubmissionFormError
        html = _BLANK_FORM_HTML.replace(
            '<textarea name="studentSubmission.text" id="studentSubmission.text"></textarea>',
            '<textarea name="studentSubmission.text" id="first"></textarea>'
            '<textarea name="studentSubmission.text" id="second"></textarea>',
        )
        with patch("sustech_survival.bb.submit._bb_session") as mock_sess:
            mock_sess.return_value.get.return_value = _mock_form_response(html=html)
            with pytest.raises(SubmissionFormError) as ei:
                _get_upload_form("8328", "610821")
        assert ei.value.code == "BLACKBOARD_FORM_AMBIGUOUS_EDITOR"


# ---------------------------------------------------------------------------
# RULE 2 — Never automatically replay the submission POST, including redirects.
# ---------------------------------------------------------------------------

class TestRule2NoReplay:
    """R2: the multipart POST is sent exactly once with
    ``allow_redirects=False``; a 3xx response is reported as an error so
    the caller can decide."""

    def test_post_has_allow_redirects_false(self, tmp_path):
        """The submission POST must be made with allow_redirects=False."""
        from sustech_survival.bb.submit import submit_assignment_rest
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        with patch("sustech_survival.bb.submit._bb_session") as mock_sess, \
             patch("sustech_survival.bb.submit.verify_assignment_target",
                   return_value=_target_ok()), \
             patch("sustech_survival.bb.submit._safe_initial_attempts_count",
                   return_value=0):
            mock_sess.return_value.get.return_value = _mock_form_response()
            mock_sess.return_value.post.return_value = _mock_post_response()
            submit_assignment_rest("8328", "610821", str(pdf))
        kw = mock_sess.return_value.post.call_args.kwargs
        assert kw.get("allow_redirects") is False

    def test_redirect_response_is_an_error(self, tmp_path):
        """A 302/303/307 from BB must NOT be auto-followed; we must
        surface it as an error and refuse to retry."""
        from sustech_survival.bb.submit import submit_assignment_rest
        from sustech_survival.bb.result import SubmitStatus
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        with patch("sustech_survival.bb.submit._bb_session") as mock_sess, \
             patch("sustech_survival.bb.submit.verify_assignment_target",
                   return_value=_target_ok()), \
             patch("sustech_survival.bb.submit._safe_initial_attempts_count",
                   return_value=0):
            mock_sess.return_value.get.return_value = _mock_form_response()
            mock_sess.return_value.post.return_value = _mock_post_response(
                status=302,
                body="",
                headers={"Content-Type": "text/plain",
                         "Location": "/webapps/assignment/uploadAssignment?mode=view"},
            )
            result = submit_assignment_rest("8328", "610821", str(pdf))
        assert result.ok is False
        assert result.status == SubmitStatus.FAILURE
        assert result.diagnostics.get("reason") == "redirect_refused"
        upstream = result.diagnostics.get("upstream", {})
        assert upstream.get("code") == "BLACKBOARD_SUBMISSION_REDIRECT"
        assert upstream.get("status") == 302
        # Sanitized path only — no Location host / credentials.
        assert "user:pass" not in upstream.get("path", "")
        # POST was called exactly once.
        assert mock_sess.return_value.post.call_count == 1

    def test_post_is_sent_exactly_once(self, tmp_path):
        """No retry decorator: a successful POST is not replayed even if
        BB returns a 5xx after — the caller decides."""
        from sustech_survival.bb.submit import submit_assignment_rest
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        with patch("sustech_survival.bb.submit._bb_session") as mock_sess, \
             patch("sustech_survival.bb.submit.verify_assignment_target",
                   return_value=_target_ok()), \
             patch("sustech_survival.bb.submit._safe_initial_attempts_count",
                   return_value=0):
            mock_sess.return_value.get.return_value = _mock_form_response()
            mock_sess.return_value.post.return_value = _mock_post_response(status=503)
            submit_assignment_rest("8328", "610821", str(pdf))
        assert mock_sess.return_value.post.call_count == 1


# ---------------------------------------------------------------------------
# RULE 4 — Distinct error class for SHA-256 drift between preview and apply.
# ---------------------------------------------------------------------------

class TestRule4FileChangedSinceReview:
    """R4: when the caller passes a ``reviewed_sha256`` from a previous
    preview/dry-run, we re-hash the file right before the POST and refuse
    with a distinct exception class on mismatch."""

    def test_distinct_error_class_exists(self):
        """The rule requires a distinct error class so callers can route
        on it. Verify it inherits from Exception (not from any other
        domain error) and carries the relevant hashes."""
        from sustech_survival.bb.submit import FileChangedSinceReviewError
        e = FileChangedSinceReviewError(
            path="/tmp/foo.pdf",
            reviewed_sha256="a" * 64,
            current_sha256="b" * 64,
            reviewed_size=10,
            current_size=12,
        )
        assert isinstance(e, Exception)
        assert e.reviewed_sha256 == "a" * 64
        assert e.current_sha256 == "b" * 64
        assert e.reviewed_size == 10
        assert e.current_size == 12
        assert "a" * 64 in str(e)
        assert "b" * 64 in str(e)

    def test_matching_hash_does_not_block(self, tmp_path):
        """When the on-disk hash matches the reviewed hash, the POST
        proceeds."""
        from sustech_survival.bb.submit import submit_assignment_rest
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        # Compute the actual SHA-256 of the file we just wrote:
        from sustech_survival.bb.submit import sha256_of_file
        sha = sha256_of_file(pdf)
        with patch("sustech_survival.bb.submit._bb_session") as mock_sess, \
             patch("sustech_survival.bb.submit.verify_assignment_target",
                   return_value=_target_ok()), \
             patch("sustech_survival.bb.submit._safe_initial_attempts_count",
                   return_value=0):
            mock_sess.return_value.get.return_value = _mock_form_response()
            mock_sess.return_value.post.return_value = _mock_post_response()
            result = submit_assignment_rest(
                "8328", "610821", str(pdf), reviewed_sha256=sha,
            )
        assert result.ok is True
        assert mock_sess.return_value.post.call_count == 1

    def test_changed_hash_blocks_with_distinct_class(self, tmp_path):
        """When the bytes change after the preview, the POST must NOT
        be sent and the result must surface the rule-4 failure with a
        distinct ``reason`` and the diff."""
        from sustech_survival.bb.submit import submit_assignment_rest
        from sustech_survival.bb.result import SubmitStatus
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        wrong_sha = "0" * 64  # clearly NOT what the file hashes to
        with patch("sustech_survival.bb.submit._bb_session") as mock_sess, \
             patch("sustech_survival.bb.submit.verify_assignment_target",
                   return_value=_target_ok()), \
             patch("sustech_survival.bb.submit._safe_initial_attempts_count",
                   return_value=0):
            mock_sess.return_value.get.return_value = _mock_form_response()
            mock_sess.return_value.post.return_value = _mock_post_response()
            result = submit_assignment_rest(
                "8328", "610821", str(pdf), reviewed_sha256=wrong_sha,
            )
        assert result.ok is False
        assert result.status == SubmitStatus.FAILURE
        assert result.diagnostics.get("reason") == "file_changed_since_review"
        assert result.diagnostics.get("reviewed_sha256") == wrong_sha
        assert result.diagnostics.get("current_sha256") and \
            result.diagnostics.get("current_sha256") != wrong_sha
        # The POST must NOT have been sent.
        mock_sess.return_value.post.assert_not_called()

    def test_no_reviewed_hash_means_no_check(self, tmp_path):
        """If the caller did not pass a reviewed_sha256 (the legacy path),
        rule 4 is a no-op — we don't synthesise one for them."""
        from sustech_survival.bb.submit import submit_assignment_rest
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        with patch("sustech_survival.bb.submit._bb_session") as mock_sess, \
             patch("sustech_survival.bb.submit.verify_assignment_target",
                   return_value=_target_ok()), \
             patch("sustech_survival.bb.submit._safe_initial_attempts_count",
                   return_value=0):
            mock_sess.return_value.get.return_value = _mock_form_response()
            mock_sess.return_value.post.return_value = _mock_post_response()
            result = submit_assignment_rest("8328", "610821", str(pdf))
        assert result.ok is True
        # Dry-run sets sha/size in diagnostics for the caller to anchor on.
        with patch("sustech_survival.bb.submit._bb_session") as mock_sess, \
             patch("sustech_survival.bb.submit.verify_assignment_target",
                   return_value=_target_ok()), \
             patch("sustech_survival.bb.submit._safe_initial_attempts_count",
                   return_value=0):
            mock_sess.return_value.get.return_value = _mock_form_response()
            dry = submit_assignment_rest("8328", "610821", str(pdf), dry_run=True)
        assert "sha256" in dry.diagnostics
        assert "size" in dry.diagnostics


# ---------------------------------------------------------------------------
# RULE 5 — Text / comment submissions use the editor the form exposes.
# ---------------------------------------------------------------------------

class TestRule5EditorField:
    """R5: text and comment submissions pick the editor field from the
    form the server actually returns — never a hard-coded list — and fail
    clearly if it's absent."""

    def _patched_form_session(self, html=_BLANK_FORM_HTML):
        @contextlib.contextmanager
        def _ctx():
            with patch("sustech_survival.bb.submit._bb_session") as mock_sess, \
                 patch("sustech_survival.bb.submit.verify_assignment_target",
                       return_value=_target_ok()), \
                 patch("sustech_survival.bb.submit._safe_initial_attempts_count",
                       return_value=0):
                mock_sess.return_value.get.return_value = _mock_form_response(html=html)
                yield mock_sess
        return _ctx()

    def test_text_uses_form_exposed_editor(self):
        """submit_text() must POST to /uploadAssignment with the editor
        field name the fetched form actually exposes."""
        from sustech_survival.bb.submit import submit_text
        with self._patched_form_session() as mock_sess:
            mock_sess.return_value.post.return_value = _mock_post_response()
            result = submit_text("610821", "hello world\n", course_id="8328")
        assert result.ok is True
        kw = mock_sess.return_value.post.call_args.kwargs
        # The data should carry studentSubmission.text (or its alias) with
        # the HTML-escaped / <br/>-converted text.
        data = kw["data"]
        # studentSubmission.text is the only candidate; it must be present.
        assert "studentSubmission.text" in data
        assert "hello world" in data["studentSubmission.text"]

    def test_text_without_editor_field_fails_clearly(self):
        """A form WITHOUT a text editor must produce a clear failure —
        rule R5 says: never silently send nothing."""
        from sustech_survival.bb.submit import submit_text, SubmissionFormError
        # Strip BOTH text and comment textareas from the form.
        import re
        html = re.sub(
            r'\s*<textarea name="studentSubmission.text"[^>]*></textarea>',
            '', _BLANK_FORM_HTML,
        )
        html = re.sub(
            r'\s*<textarea name="student_comments"[^>]*></textarea>',
            '', html,
        )
        assert 'studentSubmission.text' not in html, "test setup failed"
        with patch("sustech_survival.bb.submit._bb_session") as mock_sess, \
             patch("sustech_survival.bb.submit.verify_assignment_target",
                   return_value=_target_ok()), \
             patch("sustech_survival.bb.submit._safe_initial_attempts_count",
                   return_value=0):
            mock_sess.return_value.get.return_value = _mock_form_response(html=html)
            mock_sess.return_value.post.return_value = _mock_post_response()
            result = submit_text("610821", "hello", course_id="8328")
        assert result.ok is False
        assert result.diagnostics.get("reason") == "submission_form_error"
        assert result.diagnostics.get("upstream", {}).get("code") == \
            "BLACKBOARD_TEXT_EDITOR_MISSING"
        mock_sess.return_value.post.assert_not_called()

    def test_comment_uses_form_exposed_editor(self):
        """submit_comment() must POST with student_comments / studentComments.text
        whichever the form exposes."""
        from sustech_survival.bb.submit import submit_comment
        with self._patched_form_session() as mock_sess:
            mock_sess.return_value.post.return_value = _mock_post_response()
            result = submit_comment("610821", "thanks!", course_id="8328")
        assert result.ok is True
        kw = mock_sess.return_value.post.call_args.kwargs
        data = kw["data"]
        assert "student_comments" in data
        assert "thanks!" in data["student_comments"]

    def test_comment_without_editor_fails_clearly(self):
        """A form without a comment editor must produce a clear failure."""
        from sustech_survival.bb.submit import submit_comment
        # Strip the comment <textarea> out of the form HTML.
        import re
        html = re.sub(
            r'\s*<textarea name="student_comments"[^>]*></textarea>',
            '', _BLANK_FORM_HTML,
        )
        assert 'student_comments' not in html, "test setup failed"
        with patch("sustech_survival.bb.submit._bb_session") as mock_sess, \
             patch("sustech_survival.bb.submit.verify_assignment_target",
                   return_value=_target_ok()), \
             patch("sustech_survival.bb.submit._safe_initial_attempts_count",
                   return_value=0):
            mock_sess.return_value.get.return_value = _mock_form_response(html=html)
            mock_sess.return_value.post.return_value = _mock_post_response()
            result = submit_comment("610821", "thanks!", course_id="8328")
        assert result.ok is False
        assert result.diagnostics.get("upstream", {}).get("code") == \
            "BLACKBOARD_COMMENT_EDITOR_MISSING"
        mock_sess.return_value.post.assert_not_called()

    def test_text_uses_alternate_field_name_when_only_that_present(self):
        """If the form only exposes ``studentComments.text`` (the legacy
        alias), the submit must use it — NOT silently send nothing."""
        from sustech_survival.bb.submit import submit_comment
        # Rename student_comments → studentComments.text in the form
        # (alternate field name).
        import re
        html = re.sub(
            r'<textarea name="student_comments"[^>]*></textarea>',
            '<textarea name="studentComments.text" id="cmt2"></textarea>',
            _BLANK_FORM_HTML,
        )
        assert 'student_comments' not in html
        assert 'studentComments.text' in html
        with patch("sustech_survival.bb.submit._bb_session") as mock_sess, \
             patch("sustech_survival.bb.submit.verify_assignment_target",
                   return_value=_target_ok()), \
             patch("sustech_survival.bb.submit._safe_initial_attempts_count",
                   return_value=0):
            mock_sess.return_value.get.return_value = _mock_form_response(html=html)
            mock_sess.return_value.post.return_value = _mock_post_response()
            result = submit_comment("610821", "thanks!", course_id="8328")
        assert result.ok is True
        kw = mock_sess.return_value.post.call_args.kwargs
        assert "studentComments.text" in kw["data"]


# ---------------------------------------------------------------------------
# RULE 3 — Errors preserve stage + sanitized status + sanitized path,
#          never raw HTML.
# ---------------------------------------------------------------------------

class TestRule3SanitizedErrors:
    """R3: every failure exposes a (stage, status, path) triple where the
    status is an int and the path is a sanitized URL (no credentials, no
    fragments, no sensitive query keys). Raw HTML never leaks."""

    def test_sanitize_url_strips_credentials(self):
        from sustech_survival.bb.submit import _sanitize_url
        out = _sanitize_url("https://user:pass@bb.sustech.edu.cn/webapps/assignment/uploadAssignment?action=submit&course_id=_8328_1&content_id=_610821_1")
        assert out is None  # credentials ⇒ refuse entirely

    def test_sanitize_url_strips_fragments_and_sensitive_query(self):
        from sustech_survival.bb.submit import _sanitize_url
        out = _sanitize_url(
            "https://bb.sustech.edu.cn/webapps/assignment/uploadAssignment"
            "?action=submit&nonce=secret-uuid&course_id=_8328_1#frag"
        )
        assert out is not None
        assert "secret-uuid" not in out
        assert "#" not in out
        assert "course_id=_8328_1" in out

    def test_submission_form_error_diagnostic_is_clean(self):
        from sustech_survival.bb.submit import SubmissionFormError
        e = SubmissionFormError(
            "BB upload form GET returned status 403",
            stage="prepare_form",
            status=403,
            path="https://bb.sustech.edu.cn/webapps/assignment/uploadAssignment?action=newAttempt&content_id=_610821_1&course_id=_8328_1&group_id=",
            code="BLACKBOARD_SUBMISSION_FORM_ERROR",
        )
        diag = e.to_diagnostic()
        assert diag["code"] == "BLACKBOARD_SUBMISSION_FORM_ERROR"
        assert diag["stage"] == "prepare_form"
        assert diag["status"] == 403
        # path must be sanitized — query may be dropped, but the path must remain.
        assert "uploadAssignment" in diag["path"]
        assert diag["message"] == "BB upload form GET returned status 403"
        # And the diagnostic dict must never include the message's raw HTML.
        assert "<" not in diag["message"] or diag["message"] == "BB upload form GET returned status 403"

    def test_submit_assignment_failure_never_echoes_html(self, tmp_path):
        """A POST returning 200 with HTML body must surface a short,
        safe error string — NOT the raw HTML."""
        from sustech_survival.bb.submit import submit_assignment_rest
        from sustech_survival.bb.result import SubmitStatus
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        with patch("sustech_survival.bb.submit._bb_session") as mock_sess, \
             patch("sustech_survival.bb.submit.verify_assignment_target",
                   return_value=_target_ok()), \
             patch("sustech_survival.bb.submit._safe_initial_attempts_count",
                   return_value=0):
            mock_sess.return_value.get.return_value = _mock_form_response()
            huge_html = "<html>" + ("<script>alert('x')</script>" * 200) + "</html>"
            mock_sess.return_value.post.return_value = _mock_post_response(
                status=200, body=huge_html,
            )
            result = submit_assignment_rest("8328", "610821", str(pdf))
        assert result.ok is False
        assert result.status == SubmitStatus.FAILURE
        # No raw HTML and definitely no <script> tags in the message.
        assert "<script>" not in result.message
        assert "<html>" not in result.message
        # Sanitized upstream diagnostic on the failure.
        upstream = result.diagnostics.get("upstream", {})
        assert upstream.get("stage") == "submit_form"
        assert upstream.get("status") == 200
        assert "uploadAssignment" in upstream.get("path", "")


# ---------------------------------------------------------------------------
# Backward-compat — the legacy `sustech bb submit CONTENT_ID FILE` call.
# ---------------------------------------------------------------------------

class TestLegacyCallSurface:
    """The CLI still calls submit_file(content_id, file_path,
    course_id=None). It must keep returning the (ok, msg) tuple."""

    def test_submit_file_returns_legacy_tuple(self, tmp_path):
        from sustech_survival.bb.submit import submit_file
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        with patch("sustech_survival.bb.submit._bb_session") as mock_sess, \
             patch("sustech_survival.bb.submit.verify_assignment_target",
                   return_value=_target_ok()), \
             patch("sustech_survival.bb.submit._safe_initial_attempts_count",
                   return_value=0), \
             patch("sustech_survival.bb.download.resolve_course",
                   return_value="8328"):
            mock_sess.return_value.get.return_value = _mock_form_response()
            mock_sess.return_value.post.return_value = _mock_post_response()
            ok, msg = submit_file("610821", str(pdf))
        assert ok is True
        assert "destinationUrl" in msg


def test_file_comment_is_in_same_multipart_post(tmp_path):
    from sustech_survival.bb import submit
    file = tmp_path / 'report.pdf'
    file.write_bytes(b'%PDF-1.4 fixture')
    form = {'form_data': {}, 'file_input_id': 'newFile_LocalFile0',
            'text_field_names': {'student_comments'}, 'upload_url': _FORM_URL}
    with patch.object(submit, 'verify_assignment_target', return_value=_target_ok()), \
         patch.object(submit, '_safe_initial_attempts_count', return_value=0), \
         patch.object(submit, '_get_upload_form', return_value=form), \
         patch.object(submit, '_bb_session') as session:
        session.return_value.post.return_value = _mock_post_response()
        submit.submit_assignment_rest('8328', '610821', str(file), comment='Thanks <reader>')
    assert session.return_value.post.call_count == 1
    payload = session.return_value.post.call_args.kwargs
    assert payload['data']['student_comments'] == 'Thanks &lt;reader&gt;'
    assert 'newFile_LocalFile0' in payload['files']


def test_editor_submission_resolves_content_to_course():
    from sustech_survival.bb import submit
    with patch.object(submit, '_resolve_course_for', return_value='8328') as resolve, \
         patch.object(submit, 'verify_assignment_target', side_effect=RuntimeError('stop at fixture')) as verify:
        submit.submit_text('610821', 'My answer')
    resolve.assert_called_once_with('610821', None)
    verify.assert_called_once_with('8328', '610821')
