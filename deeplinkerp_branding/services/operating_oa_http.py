"""Bounded, read-only client for the DingTalk OA approval archive HTTP API.

Only operating expenses use this adapter. Procurement retains its own OA reader.
"""
from __future__ import annotations

import base64
import binascii
import json
from time import sleep
from urllib.parse import urlsplit

import frappe
import requests
from frappe import _

from . import operating_oa_source as oa

DEFAULT_URL = "http://8.135.70.130:3005"
DEFAULT_CORP_ID = "ding144583309b2fb01c35c2f4657eb6378f"
MAX_PAGE_BYTES = 20 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
MAX_PAGES = 1000
MAX_CACHED_ATTACHMENTS = 20000


class OAInvalidCursor(Exception):
    """The remote 24-hour cursor expired; the caller may restart a full scan."""


class OARateLimited(frappe.ValidationError):
    """The shared source-IP budget was exhausted; retry in a later job."""

    def __init__(self, retry_after):
        self.retry_after = retry_after
        super().__init__(_("运营支出 OA 读取触发限流，请稍后重试"))


def configuration():
    """Use server-side configuration only; no URL/corp ID comes from a browser."""
    url = str(frappe.conf.get("operating_expense_oa_api_url") or DEFAULT_URL).rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment):
        frappe.throw(_("运营支出 OA 读取地址配置无效"))
    try:
        parsed.port
    except ValueError:
        frappe.throw(_("运营支出 OA 读取地址配置无效"))
    configured = frappe.conf.get("operating_expense_oa_corp_ids")
    if configured is None:
        configured = DEFAULT_CORP_ID
    if isinstance(configured, str):
        configured = [part.strip() for part in configured.split(",")]
    if (not isinstance(configured, (list, tuple)) or not configured or len(configured) > 100
            or any(not isinstance(part, str) or not part.strip() or len(part) > 200 for part in configured)):
        frappe.throw(_("运营支出 OA 企业 ID 未配置或格式无效"))
    return url, tuple(dict.fromkeys(configured))


def _get(url, path, params, *, max_bytes=MAX_PAGE_BYTES):
    for attempt in range(2):
        try:
            with requests.get(url + path, params=params, timeout=(5, 30),
                              allow_redirects=False, stream=True) as response:
                if response.status_code == 400 and params.get("cursor"):
                    # Only this stable remote error code permits a safe full rescan.
                    # Never surface the remote response body or its URL.
                    body = bytearray()
                    for chunk in response.iter_content(2048):
                        body.extend(chunk)
                        if len(body) > 2048:
                            break
                    if len(body) <= 2048:
                        try:
                            if json.loads(body).get("code") == "INVALID_CURSOR":
                                raise OAInvalidCursor()
                        except (ValueError, TypeError, AttributeError):
                            pass
                if response.status_code == 429:
                    try:
                        retry_after = min(60, max(1, int(response.headers.get("Retry-After", "60"))))
                    except (ValueError, TypeError):
                        retry_after = 60
                    raise OARateLimited(retry_after)
                if response.status_code != 200:
                    raise ValueError("OA API status")
                if int(response.headers.get("Content-Length", "0")) > max_bytes:
                    raise ValueError("OA API size")
                content = bytearray()
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        raise ValueError("OA API size")
                return bytes(content)
        except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError):
            # A GET can be repeated after the peer drops the connection.
            # Never retry protocol errors, rate limits, or an invalid cursor.
            if attempt == 0:
                sleep(0.2)
                continue
        except (requests.RequestException, ValueError, TypeError):
            break
    # Never expose the unauthenticated API's URL, content, or raw failure.
    frappe.throw(_("运营支出 OA 读取暂不可用，请管理员检查服务与配置"))


def _page(url, resource, params):
    try:
        payload = json.loads(_get(url, "/api/v1/" + resource, params))
        data, pagination = payload["data"], payload["pagination"]
        if (payload.get("success") is not True or not isinstance(data, list)
                or len(data) > int(params["limit"]) or not isinstance(pagination, dict)
                or not isinstance(pagination.get("hasMore"), bool)
                or (pagination["hasMore"] and not pagination.get("nextCursor"))
                or not pagination.get("syncUntil")):
            raise ValueError("OA API page")
        oa.timestamp(pagination["syncUntil"])
        return data, pagination
    except (ValueError, TypeError, KeyError, AttributeError):
        frappe.throw(_("运营支出 OA 返回协议无效，请管理员核查"))


def approval_row(item):
    if not isinstance(item, dict):
        frappe.throw(_("运营支出 OA 返回协议无效，请管理员核查"))
    row = {new: item.get(old) for old, new in (
        ("corpId", "corp_id"), ("processInstanceId", "process_instance_id"),
        ("approvalNo", "business_id"), ("processCode", "process_code"),
        ("status", "status"), ("result", "result"),
        ("originatorUserId", "originator_user_id"),
        ("originatorUserName", "originator_user_name"),
        ("createTime", "create_time"), ("updatedAt", "updated_at"),
        ("deletedAt", "deleted_at"), ("formComponentValues", "form_component_values"))}
    if not all(isinstance(row.get(key), str) and row[key] for key in (
            "corp_id", "process_instance_id", "process_code", "create_time", "updated_at")):
        frappe.throw(_("运营支出 OA 返回身份或时间无效，请管理员核查"))
    try:
        oa.timestamp(row["create_time"])
        oa.timestamp(row["updated_at"])
    except ValueError:
        frappe.throw(_("运营支出 OA 返回身份或时间无效，请管理员核查"))
    # This API is scoped by corpId and cannot prove business-number uniqueness
    # across other corporations. Do not attach cashier evidence by number alone.
    row["business_count"] = 0
    return row


def attachment_row(item):
    if not isinstance(item, dict):
        frappe.throw(_("运营支出 OA 附件清单协议无效，请管理员核查"))
    row = {new: item.get(old) for old, new in (
        ("corpId", "corp_id"), ("processInstanceId", "process_instance_id"),
        ("fileId", "file_id"), ("fileName", "file_name"),
        ("sha256", "sha256"), ("actualSize", "actual_size"),
        ("archiveStatus", "archive_status"), ("downloadPath", "download_path"),
        ("contentQuality", "content_quality"),
        ("retiredAt", "retired_at"), ("sourceDeletedAt", "source_deleted_at"))}
    if not all(isinstance(row.get(key), str) and row[key] for key in ("corp_id", "process_instance_id", "file_id")):
        frappe.throw(_("运营支出 OA 附件清单身份无效，请管理员核查"))
    if row["retired_at"] or row["source_deleted_at"] or (row["archive_status"] == "archived" and not row["download_path"]):
        row["archive_status"] = "unavailable"
    return row


def _encode_cursor(until, index, remote, corps):
    data = [until, index, remote, list(corps)]
    return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()


def _decode_cursor(value, until, corps, segment_count):
    try:
        if not isinstance(value, str) or len(value) > 12000:
            raise ValueError()
        data = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
        if isinstance(data, list) and len(data) == 3 and data[0] == until and all(isinstance(v, str) for v in data):
            # The old PostgreSQL reader used a three-item cursor. A complete
            # HTTP rescan is safe and lets an in-progress sync migrate itself.
            return 0, None
        if (not isinstance(data, list) or len(data) != 4 or data[0] != until
                or type(data[1]) is not int or not 0 <= data[1] < segment_count
                or data[2] is not None and (not isinstance(data[2], str) or len(data[2]) > 8000)
                or data[3] != list(corps)):
            raise ValueError()
        return data[1], data[2]
    except (ValueError, TypeError, UnicodeError, binascii.Error):
        frappe.throw(_("运营支出 OA 分页游标无效，请重新开始同步"))


def read_page(url, corps, limit, until, cursor=None, identity=None):
    """One remote page at a time; cursor covers every configured corp/process."""
    if identity:
        corp, instance = identity["corp_id"], identity["process_instance_id"]
        if corp not in corps:
            frappe.throw(_("运营支出 OA 企业 ID 配置缺少已缓存来源，请管理员核查"))
        data, page = _page(url, "approvals", {"corpId": corp, "processInstanceId": instance, "limit": 2})
        rows = [approval_row(item) for item in data]
        if (page["hasMore"] or len(rows) > 1 or
                any(row["corp_id"] != corp or row["process_instance_id"] != instance for row in rows)):
            frappe.throw(_("运营支出 OA 精确查询身份不符，请管理员核查"))
        return rows, None
    segments = [(corp, code) for corp in corps for code in oa.PROCESS_CODES]
    index, remote = _decode_cursor(cursor, until, corps, len(segments)) if cursor else (0, None)
    limit = min(int(limit), 100)
    # Skip only a bounded number of empty segments in one RPC. An empty page
    # with hasMore still yields its cursor so a bad remote cannot spin here.
    for _segment_attempt in range(20):
        if index >= len(segments):
            return [], None
        corp, code = segments[index]
        params = {"corpId": corp, "processCode": code, "limit": limit}
        if remote:
            params["cursor"] = remote
        data, page = _page(url, "approvals", params)
        rows = [approval_row(item) for item in data]
        if any(row["corp_id"] != corp or row["process_code"] != code for row in rows):
            frappe.throw(_("运营支出 OA 分页来源身份不符，请管理员核查"))
        if page["hasMore"] and page["nextCursor"] == remote:
            frappe.throw(_("运营支出 OA 分页未前进，请管理员核查"))
        next_index = index if page["hasMore"] else index + 1
        next_remote = page["nextCursor"] if page["hasMore"] else None
        next_cursor = _encode_cursor(until, next_index, next_remote, corps) if next_index < len(segments) else None
        if rows or next_cursor is None or page["hasMore"]:
            return rows, next_cursor
        index, remote = next_index, next_remote
    return [], _encode_cursor(until, index, remote, corps) if index < len(segments) else None


def approval_number_counts(url, corp, numbers):
    """Prove fallback joins through the OA API's exact approvalNo filter."""
    cache = getattr(frappe.local, "_operating_oa_number_counts", None)
    if cache is None:
        cache = {}
        frappe.local._operating_oa_number_counts = cache
    counts = {}
    for number in set(numbers):
        key = (url, corp, number)
        if key in cache:
            counts[number] = cache[key]
            continue
        params = {"corpId": corp, "approvalNo": number, "limit": 2}
        data, page = _page(url, "approvals", params)
        instances = set()
        for item in data:
            if (not isinstance(item, dict) or item.get("corpId") != corp
                    or item.get("approvalNo") != number
                    or not isinstance(item.get("processInstanceId"), str) or not item["processInstanceId"]):
                frappe.throw(_("运营支出 OA 审批编号核对身份不符，请管理员核查"))
            if item["processInstanceId"] in instances:
                frappe.throw(_("运营支出 OA 审批编号核对身份重复，请管理员核查"))
            instances.add(item["processInstanceId"])
        # A further page proves ambiguity; fetching it would add no information.
        counts[number] = max(len(instances), 2 if page["hasMore"] else 0)
        cache[key] = counts[number]
    return counts


def attachment_manifests(url, rows, *, exact=False):
    """Fetch the independent attachment stream; filter exact approval identities."""
    result = []
    cache = getattr(frappe.local, "_operating_oa_attachment_manifests", None)
    if cache is None:
        cache = {}
        frappe.local._operating_oa_attachment_manifests = cache
    grouped = {}
    for row in rows:
        key = (row["corp_id"], None if exact else row["process_code"],
               row["process_instance_id"] if exact else None)
        grouped.setdefault(key, set()).add(row["process_instance_id"])
    for (corp, code, instance), instances in grouped.items():
        cache_key = (url, corp, code)
        if code and cache_key in cache:
            result.extend(item for item in cache[cache_key] if item["process_instance_id"] in instances)
            continue
        scanned = []
        cursor = None
        for _page_number in range(MAX_PAGES):
            params = {"corpId": corp, "limit": 200}
            if code:
                params["processCode"] = code
            else:
                if len(instances) != 1:
                    frappe.throw(_("运营支出 OA 附件精确查询身份无效，请管理员核查"))
                params["processInstanceId"] = instance or next(iter(instances))
            if cursor:
                params["cursor"] = cursor
            data, page = _page(url, "attachments", params)
            for item in data:
                manifest = attachment_row(item)
                if (manifest["corp_id"] != corp or
                        (code and item.get("processCode") != code) or
                        (not code and manifest["process_instance_id"] not in instances)):
                    frappe.throw(_("运营支出 OA 附件清单身份不符，请管理员核查"))
                if code and len(scanned) <= MAX_CACHED_ATTACHMENTS:
                    scanned.append(manifest)
                if manifest["process_instance_id"] in instances:
                    result.append(manifest)
            if not page["hasMore"]:
                if code and len(scanned) <= MAX_CACHED_ATTACHMENTS:
                    cache[cache_key] = scanned
                break
            if page["nextCursor"] == cursor:
                frappe.throw(_("运营支出 OA 附件分页未前进，请管理员核查"))
            cursor = page["nextCursor"]
        else:
            frappe.throw(_("运营支出 OA 附件清单超过本次上限，请管理员核查"))
    return result


def download_attachment(url, identity, expected_size):
    if expected_size > MAX_DOWNLOAD_BYTES:
        frappe.throw(_("附件超过本次下载上限"))
    return _get(url, "/api/v1/attachment-content", {
        "corpId": identity["corp_id"], "processInstanceId": identity["process_instance_id"],
        "fileId": identity["file_id"]}, max_bytes=MAX_DOWNLOAD_BYTES)
