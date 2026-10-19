"""Narrow additive metadata for a future reversal boundary; no business writes."""

try:
	from frappe import _
except ImportError:
	def _(message):
		return message


import re

TARGETS = (
	"Bin",
	"Purchase Order",
	"Purchase Receipt",
	"Purchase Invoice",
	"Payment Entry",
	"Repost Item Valuation",
)
FIELDNAME = "custom_purchase_reversal_operation"
DEFINITION = {
	"fieldname": FIELDNAME,
	"label": "采购冲销操作",
	"fieldtype": "Link",
	"options": "Integration Request",
	"hidden": 1,
	"read_only": 1,
	"no_copy": 1,
}
_REQUIRED = {
	"Bin": {"item_code", "warehouse"},
	"Purchase Order": {"company", "items"},
	"Purchase Receipt": {"company", "items"},
	"Purchase Invoice": {"company", "items"},
	"Payment Entry": {"company", "references"},
	"Repost Item Valuation": {"company", "status", "based_on"},
}


def _generated_expression(value):
	# MariaDB rewrites BINARY x as CAST(x AS CHAR CHARSET binary). Preserve
	# literal bytes/case: lowercasing literals would weaken exact terminality.
	parts = re.split(r"('[^']*')", str(value or ""))
	normalized = "".join(
		part if index % 2 else re.sub(r"[\s`]", "", part).lower() for index, part in enumerate(parts)
	)
	return re.sub(
		r"binary('[^']*'|integration_request_service|status|request_description)",
		r"cast(\1ascharcharsetbinary)",
		normalized,
	)


def _native_intent_index_plan():
	import frappe

	from .services.purchase_native_intent import ACTIVITY_COLUMN, ACTIVITY_EXPRESSION, ACTIVITY_INDEX

	by_name = {}
	for name in ("name", "integration_request_service", "status", "request_description", ACTIVITY_COLUMN):
		rows = frappe.db.sql(
			"SELECT COLUMN_NAME,COLUMN_TYPE,CHARACTER_SET_NAME,COLLATION_NAME,EXTRA,"
			"GENERATION_EXPRESSION FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
			"AND TABLE_NAME='tabIntegration Request' AND COLUMN_NAME=%s",
			(name,),
			as_dict=True,
		)
		if len(rows) > 1:
			frappe.throw(_("原生库存同步索引列名称定义冲突，未修改现有定义"), frappe.ValidationError)
		if rows:
			by_name[name] = rows[0]
	for name in ("name", "integration_request_service", "status", "request_description"):
		row = by_name.get(name)
		if not row or (row.COLUMN_TYPE, row.CHARACTER_SET_NAME, row.COLLATION_NAME) != (
			"varchar(140)",
			"utf8mb4",
			"utf8mb4_unicode_ci",
		):
			frappe.throw(_("原生库存同步索引所需列定义未经核查"), frappe.ValidationError)
	generated = by_name.get(ACTIVITY_COLUMN)
	if generated and (
		generated.COLUMN_TYPE != "tinyint(4)"
		or generated.EXTRA != "VIRTUAL GENERATED"
		or _generated_expression(generated.GENERATION_EXPRESSION)
		!= _generated_expression(ACTIVITY_EXPRESSION)
	):
		frappe.throw(_("原生库存同步生成索引定义冲突，未修改现有定义"), frappe.ValidationError)
	indexes = frappe.db.sql(
		"SHOW INDEX FROM `tabIntegration Request` WHERE Key_name=%s", (ACTIVITY_INDEX,), as_dict=True
	)
	existing = sorted(indexes, key=lambda row: row.Seq_in_index)
	if existing and (
		len(existing) != 3
		or [row.Column_name for row in existing]
		!= [
			by_name["integration_request_service"].COLUMN_NAME,
			generated.COLUMN_NAME if generated else ACTIVITY_COLUMN,
			by_name["name"].COLUMN_NAME,
		]
		or any(
			row.Non_unique != 1
			or row.Sub_part is not None
			or row.Index_type != "BTREE"
			or row.Ignored != "NO"
			for row in existing
		)
	):
		frappe.throw(_("原生库存同步复合索引定义冲突，未修改现有定义"), frappe.ValidationError)
	if existing and not generated:
		frappe.throw(_("原生库存同步复合索引缺少已核查生成列"), frappe.ValidationError)
	return not generated, not existing


def install_native_intent_index(plan=None):
	"""Only derived index metadata; never run inside a business transaction."""
	import frappe

	from .services.purchase_native_intent import ACTIVITY_COLUMN, ACTIVITY_EXPRESSION, ACTIVITY_INDEX

	plan = _native_intent_index_plan() if plan is None else plan
	state = getattr(frappe.local.db, "_purchase_session", None)
	if frappe.db.transaction_writes or (state and state.locks):
		frappe.throw(_("原生库存同步索引不能在业务事务内安装"), frappe.ValidationError)
	if plan[0]:
		frappe.db.sql(
			"ALTER TABLE `tabIntegration Request` ADD COLUMN `"
			+ ACTIVITY_COLUMN
			+ "` TINYINT AS ("
			+ ACTIVITY_EXPRESSION
			+ ") VIRTUAL"
		)
	if plan[1]:
		frappe.db.add_index(
			"Integration Request", ["integration_request_service", ACTIVITY_COLUMN, "name"], ACTIVITY_INDEX
		)
	if _native_intent_index_plan() != (False, False):
		frappe.throw(_("原生库存同步索引安装未通过实际定义核查"), frappe.ValidationError)


def after_migrate():
	import frappe
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	# Preflight every target before the first metadata write. Existing native or
	# custom definitions are never overwritten or treated as a different feature.
	for doctype in (*TARGETS, "Integration Request"):
		if not frappe.db.exists("DocType", doctype) or not frappe.db.has_column(doctype, "name"):
			frappe.throw(_("采购冲销元数据所需原生单据类型缺失"), frappe.ValidationError)
	missing = {}
	for doctype in TARGETS:
		meta = frappe.get_meta(doctype)
		if not all(meta.has_field(field) for field in _REQUIRED[doctype]):
			frappe.throw(_("采购冲销所需原生元数据版本不完整"), frappe.ValidationError)
		# Table fields have child tables, not parent SQL columns. All stored
		# native prerequisites and compatible pointers must physically exist.
		if not all(
			frappe.db.has_column(doctype, field) for field in _REQUIRED[doctype] - {"items", "references"}
		):
			frappe.throw(_("采购冲销所需原生数据库列缺失"), frappe.ValidationError)
		existing = meta.get_field(FIELDNAME)
		if existing:
			if (
				existing.get("is_virtual")
				or not frappe.db.has_column(doctype, FIELDNAME)
				or any(
					existing.get(key) != DEFINITION[key]
					for key in ("fieldtype", "options", "hidden", "read_only", "no_copy")
				)
			):
				frappe.throw(_("采购冲销操作字段定义冲突，未修改现有定义"), frappe.ValidationError)
		else:
			missing[doctype] = [dict(DEFINITION)]
	index_plan = _native_intent_index_plan()  # preflight both contracts before first DDL
	install_native_intent_index(index_plan)
	if missing:
		create_custom_fields(missing)
	for doctype in TARGETS:
		# Frappe's add_index checks the existing index before adding it.
		frappe.db.add_index(doctype, [FIELDNAME], "purchase_reversal_operation")
