"""Contract checks for the operating expense OA HTTP adapter."""
import base64
import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from deeplinkerp_branding.services import operating_oa_http as client
from deeplinkerp_branding.services import operating_oa_source as oa


def approval(corp="corp-a", instance="instance-a", code=None):
    return {"corpId": corp, "processInstanceId": instance,
            "processCode": code or oa.PROCESS_CODES[0], "approvalNo": "1001",
            "createTime": "2026-03-01T00:00:00Z", "updatedAt": "2026-03-02T00:00:00Z",
            "status": "COMPLETED", "result": "agree", "formComponentValues": []}


def page(data, cursor=None):
    return data, {"hasMore": cursor is not None, "nextCursor": cursor,
                  "syncUntil": "2026-03-03T00:00:00Z"}


def fail(message):
    raise ValueError(message)


class OAHttpTests(unittest.TestCase):
    UNTIL = "2026-03-03T00:00:00Z"

    def setUp(self):
        translation = patch.object(client, "_", side_effect=lambda message: message)
        translation.start()
        self.addCleanup(translation.stop)

    def test_default_corp_and_explicit_site_override(self):
        with patch.object(client.frappe, "conf", {"overseas_costing_dingtalk_corp_id": "other-corp"}):
            self.assertEqual(client.configuration(), ("http://8.135.70.130:3005", (client.DEFAULT_CORP_ID,)))
        with patch.object(client.frappe, "conf", {"operating_expense_oa_corp_ids": ["corp-a"]}):
            self.assertEqual(client.configuration(), ("http://8.135.70.130:3005", ("corp-a",)))
        with patch.object(client.frappe, "conf", {"operating_expense_oa_api_url": "http://oa:3000/path",
                                                  "operating_expense_oa_corp_ids": ["corp-a"]}), \
                patch.object(client.frappe, "throw", side_effect=fail):
            with self.assertRaises(ValueError):
                client.configuration()

    def test_segments_keep_corporation_and_process_scope(self):
        seen = []
        def fake_page(_url, resource, params):
            seen.append((resource, dict(params)))
            code = params["processCode"]
            if code == oa.PROCESS_CODES[0] and "cursor" not in params:
                return page([approval()], "remote-next")
            if code == oa.PROCESS_CODES[0]:
                return page([approval(instance="instance-b")])
            return page([])
        with patch.object(client, "_page", side_effect=fake_page):
            first, cursor = client.read_page("http://oa:3000", ("corp-a",), 500, self.UNTIL)
            second, cursor = client.read_page("http://oa:3000", ("corp-a",), 500, self.UNTIL, cursor)
            third, cursor = client.read_page("http://oa:3000", ("corp-a",), 500, self.UNTIL, cursor)
        self.assertEqual([r["process_instance_id"] for r in first + second], ["instance-a", "instance-b"])
        self.assertEqual(third, [])
        self.assertIsNone(cursor)
        self.assertEqual(seen[0][1]["limit"], 100)
        self.assertEqual(seen[1][1]["cursor"], "remote-next")
        self.assertEqual(len(seen), len(oa.PROCESS_CODES) + 1)

    def test_old_postgres_cursor_restarts_full_scan(self):
        old = base64.urlsafe_b64encode(json.dumps([self.UNTIL, "corp-a", "instance-a"]).encode()).decode()
        with patch.object(client, "_page", return_value=page([])) as remote:
            rows, cursor = client.read_page("http://oa:3000", ("corp-a",), 100, self.UNTIL, old)
        self.assertEqual(rows, [])
        self.assertIsNone(cursor)
        self.assertEqual(remote.call_args_list[0].args[2]["processCode"], oa.PROCESS_CODES[0])

    def test_corp_mismatch_and_invalid_cursor_fail_closed(self):
        with patch.object(client.frappe, "throw", side_effect=fail):
            with self.assertRaises(ValueError):
                client.read_page("http://oa:3000", ("corp-a",), 1, self.UNTIL,
                                 client._encode_cursor(self.UNTIL, 0, None, ("corp-b",)))
            with patch.object(client, "_page", return_value=page([approval(corp="other")])):
                with self.assertRaises(ValueError):
                    client.read_page("http://oa:3000", ("corp-a",), 1, self.UNTIL)

    def test_business_number_cannot_prove_global_uniqueness(self):
        row = client.approval_row(approval())
        self.assertEqual(row["business_count"], 0)
        self.assertFalse(oa.matches(row, {"corp_id": "corp-a", "approval_no": "1001",
                                          "approval_identity_status": "explicit"}, require_corp=True))

    def test_approval_passes_department_and_raw_workflow_from_oa(self):
        value = approval()
        value.update(originatorDeptId="dept-1", originatorDeptName="Mapped department",
                     rawPayload={"tasks": [], "operationRecords": []})
        row = client.approval_row(value)
        self.assertEqual(row["originator_dept_id"], "dept-1")
        self.assertEqual(row["originator_dept_name"], "Mapped department")
        self.assertEqual(row["raw_payload"], value["rawPayload"])

    def test_original_payload_corrects_applicant_without_borrowing_approver_name(self):
        value = approval()
        value.update(originatorUserId="current-reviewer", originatorUserName="Reviewer Name",
                     originatorDeptName="Reviewer Department", rawPayload={"originatorUserId": "applicant-id",
                     "originatorDeptName": "Applicant Department", "originatorDeptId": "42"})
        row = client.approval_row(value)
        self.assertEqual(row["originator_user_id"], "applicant-id")
        self.assertIsNone(row["originator_user_name"])
        self.assertEqual(row["originator_dept_name"], "Applicant Department")
        self.assertEqual(row["originator_dept_id"], "42")
        value["rawPayload"] = {"originatorUserId": "applicant-id"}
        self.assertIsNone(client.approval_row(value)["originator_dept_name"])

    def test_attachment_pages_filter_exact_identity_and_retirement(self):
        archived = {"corpId": "corp-a", "processInstanceId": "instance-a",
                    "processCode": oa.PROCESS_CODES[0], "fileId": "file-a",
                    "archiveStatus": "archived", "downloadPath": "/api/v1/attachment-content?x=1"}
        retired = dict(archived, fileId="file-b", retiredAt="2026-03-03T00:00:00Z")
        unrelated = dict(archived, processInstanceId="unrelated", fileId="file-c")
        with patch.object(client.frappe, "local", SimpleNamespace()), patch.object(
                client, "_page", side_effect=[page([archived], "next"), page([retired, unrelated])]):
            rows = client.attachment_manifests("http://oa:3000", [{"corp_id": "corp-a",
                "process_code": oa.PROCESS_CODES[0], "process_instance_id": "instance-a"}])
        self.assertEqual([r["file_id"] for r in rows], ["file-a", "file-b"])
        self.assertEqual(rows[1]["archive_status"], "unavailable")

    def test_exact_attachment_lookup_uses_process_instance_id(self):
        with patch.object(client.frappe, "local", SimpleNamespace()), patch.object(client, "_page", return_value=page([])) as remote:
            client.attachment_manifests("http://oa:3000", [{"corp_id": "corp-a",
                "process_code": oa.PROCESS_CODES[0], "process_instance_id": "instance-a"}], exact=True)
        self.assertEqual(remote.call_args.args[2]["processInstanceId"], "instance-a")
        self.assertNotIn("processCode", remote.call_args.args[2])

    def test_batch_attachment_scan_is_reused_but_exact_refresh_bypasses_cache(self):
        item = {"corpId": "corp-a", "processInstanceId": "instance-a",
                "processCode": oa.PROCESS_CODES[0], "fileId": "file-a"}
        row = {"corp_id": "corp-a", "process_code": oa.PROCESS_CODES[0], "process_instance_id": "instance-a"}
        with patch.object(client.frappe, "local", SimpleNamespace()), patch.object(
                client, "_page", side_effect=[page([item]), page([])]) as remote:
            self.assertEqual(len(client.attachment_manifests("http://oa:3000", [row])), 1)
            self.assertEqual(len(client.attachment_manifests("http://oa:3000", [row])), 1)
            self.assertEqual(client.attachment_manifests("http://oa:3000", [row], exact=True), [])
        self.assertEqual(remote.call_count, 2)
        self.assertEqual(remote.call_args.args[2]["processInstanceId"], "instance-a")

    def test_download_uses_fixed_path_and_exact_identity(self):
        with patch.object(client, "_get", return_value=b"file") as request:
            self.assertEqual(client.download_attachment("http://oa:3000", {
                "corp_id": "corp-a", "process_instance_id": "instance-a", "file_id": "file-a"}, 4), b"file")
        self.assertEqual(request.call_args.args[1], "/api/v1/attachment-content")
        self.assertEqual(request.call_args.args[2]["fileId"], "file-a")

    def test_transport_rejects_redirect_and_oversized_body_without_exposing_remote_text(self):
        class Response:
            status_code = 302
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def iter_content(self, _size): yield b"private remote body"
        response = Response()
        with patch.object(client.requests, "get", return_value=response) as request, \
                patch.object(client.frappe, "throw", side_effect=fail):
            with self.assertRaisesRegex(ValueError, "运营支出 OA 读取暂不可用") as raised:
                client._get("http://oa:3000", "/api/v1/approvals", {"corpId": "corp-a"})
            self.assertNotIn("private remote body", str(raised.exception))
            self.assertFalse(request.call_args.kwargs["allow_redirects"])
            self.assertNotIn("headers", request.call_args.kwargs)
            response.status_code = 200
            response.iter_content = lambda _size: iter([b"ab", b"cd"])
            with self.assertRaises(ValueError):
                client._get("http://oa:3000", "/api/v1/approvals", {}, max_bytes=3)

    def test_transient_disconnect_retries_once_without_leaking_remote_details(self):
        class Response:
            status_code = 200
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def iter_content(self, _size): yield b"safe response"
        for error_type in (client.requests.exceptions.ConnectionError, client.requests.exceptions.ChunkedEncodingError):
            with self.subTest(error_type=error_type.__name__), \
                    patch.object(client.requests, "get", side_effect=[error_type("private source URL"), Response()]) as request, \
                    patch.object(client, "sleep") as pause:
                self.assertEqual(client._get("http://oa:3000", "/api/v1/attachments", {}), b"safe response")
                self.assertEqual(request.call_count, 2)
                pause.assert_called_once_with(0.2)
        with patch.object(client.requests, "get", side_effect=client.requests.exceptions.ConnectionError("private source URL")) as request, \
                patch.object(client, "sleep"), patch.object(client.frappe, "throw", side_effect=fail):
            with self.assertRaisesRegex(ValueError, "运营支出 OA 读取暂不可用") as raised:
                client._get("http://oa:3000", "/api/v1/attachments", {})
            self.assertNotIn("private source URL", str(raised.exception))
            self.assertEqual(request.call_count, 2)

    def test_only_remote_invalid_cursor_is_restartable_and_rate_limit_is_distinct(self):
        class Response:
            headers = {}
            def __init__(self, status, body=b""):
                self.status_code, self.body = status, body
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def iter_content(self, _size): yield self.body
        with patch.object(client.requests, "get", return_value=Response(400, b'{"code":"INVALID_CURSOR"}')):
            with self.assertRaises(client.OAInvalidCursor):
                client._get("http://oa:3000", "/api/v1/approvals", {"cursor": "old"})
        with patch.object(client.requests, "get", return_value=Response(400, b'{"code":"INVALID_REQUEST"}')), \
                patch.object(client.frappe, "throw", side_effect=fail):
            with self.assertRaisesRegex(ValueError, "暂不可用"):
                client._get("http://oa:3000", "/api/v1/approvals", {"cursor": "bad"})
        limited = Response(429)
        limited.headers = {"Retry-After": "17"}
        with patch.object(client.requests, "get", return_value=limited) as request:
            with self.assertRaises(client.OARateLimited) as raised:
                client._get("http://oa:3000", "/api/v1/approvals", {})
        request.assert_called_once()
        self.assertEqual(raised.exception.retry_after, 17)
