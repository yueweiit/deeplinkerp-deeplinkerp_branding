"""Legacy procurement-only cashier transport, isolated from operating OA sync."""
import ipaddress
import json
import socket

import frappe
import requests
from frappe import _ as _i18n

from .operating_expense_contract import SOURCE_SYSTEM, MAX_JSON_BYTES, attachment_path

SOURCE_URL = "https://payment.yueweiportal.com"


@frappe.whitelist()
def get_settings():
    from . import operating_expenses as expenses
    expenses._manager()
    settings = expenses._settings()
    return {"token_configured": bool(settings.get_password("api_token", raise_exception=False))}


@frappe.whitelist(methods=["POST"])
def save_settings(api_token):
    """Procurement owns credential rotation; operating settings own OA mappings."""
    from . import operating_expenses as expenses
    expenses._manager()
    if (not isinstance(api_token, str) or not api_token.strip() or len(api_token) > 4096
            or api_token != api_token.strip() or any(ord(char) < 32 or ord(char) == 127 for char in api_token)):
        frappe.throw(_i18n("采购出纳接口密钥格式无效"))
    settings = expenses._settings(for_update=True)
    settings.api_token = api_token
    # Preserve OA mappings, enabled status, preview and pagination checkpoints.
    expenses._save(settings)
    return {"token_configured": True}


def _base_url():
    override = frappe.conf.get("operating_expense_qa_url")
    if override:
        if frappe.local.site != "operating-expenses-qa.localhost" or frappe.conf.db_host != "db" or override != "http://host.docker.internal:64244":
            frappe.throw(_i18n("测试来源地址仅限专用隔离站点"))
        return override
    try:
        addresses = socket.getaddrinfo("payment.yueweiportal.com", 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(address[4][0]).is_global or ipaddress.ip_address(address[4][0]).is_multicast for address in addresses):
            frappe.throw(_i18n("来源主机必须解析为公开网络地址"))
    except OSError:
        frappe.throw(_i18n("出纳来源地址暂不可用，请稍后重试"))
    return SOURCE_URL


def _request(path, params=None, download=False, data=None):
    resolver = path == "/api/integrations/erp/resolve-applicant-companies"
    if path != "/api/integrations/erp/purchase-expenses" and not resolver:
        if path.startswith("/api/integrations/erp/purchase-expenses/"):
            from .purchase_source_contract import attachment_path as purchase_attachment_path
            purchase_attachment_path(path)
        else:
            attachment_path(path)
        if path.startswith("/oa-archive/"):
            frappe.throw(_i18n("OA 归档只能通过授权附件标识读取"))
    # Keep the existing procurement credential storage; operating OA never reads it.
    settings = frappe.get_single("Operating Expense Sync Settings")
    secret = settings.get_password("api_token", raise_exception=False)
    if not secret:
        frappe.throw(_i18n("同步密钥未配置"))
    limit = 20 * 1024 * 1024 if download else MAX_JSON_BYTES
    try:
        post = resolver
        transport = requests.post if post else requests.get
        kwargs = {"json": data} if post else {}
        with transport(_base_url() + path, params=params, headers={"Authorization": "Bearer " + secret}, **kwargs,
                          timeout=(5, 20), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise ValueError("source request failed")
            if int(response.headers.get("Content-Length", "0")) > limit:
                raise ValueError("source response too large")
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError("source response too large")
            if download:
                return bytes(data)
            result = json.loads(data)
            versions = {1, 2} if resolver else {1}
            if result.get("schema_version") not in versions or result.get("source_system") != SOURCE_SYSTEM or not isinstance(result.get("items"), list) or len(result["items"]) > 500:
                raise ValueError("invalid source schema")
            if not resolver and (not isinstance(result.get("end"), bool) or (not result["end"] and not result.get("next_cursor")) or not result.get("until")):
                raise ValueError("invalid source pagination")
            return result
    except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError):
        # Do not echo remote body, response headers, URLs, credentials or exception text.
        frappe.throw(_i18n("出纳来源暂不可用或协议无效，请稍后重试"))
