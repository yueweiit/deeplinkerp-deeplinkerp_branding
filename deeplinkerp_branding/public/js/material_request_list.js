(function (root) {
 const translateText = (text, args = []) => String((globalThis.__ || (value => value))(text)).replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
 const engine = typeof module === "object" && module.exports ? require("./compact_list.js") : root.DeepLinkERPCompactList;
 const categories = Object.assign(Object.create(null), {consumable:"消耗品",raw_material:"原料申请",semi_finished:"半成品申请",unclassified:"未分类"});
 const columns = [["custom_odt","ODT",150],["custom_mes_issue_category","申请类别",110],["name","ERP申请号",180],["status","状态",120],["transaction_date","申请日期",96],["schedule_date","需求日期",96],["company","公司",130],["set_from_warehouse","来源仓库",160],["per_ordered","已发料/履行%",110],["custom_material_request_no","MES申请号",180],["owner","创建人",120]].map(([fieldname,label,width])=>({fieldname,label,width}));
 const grid = engine.create({doctype:"Material Request",columns,controllerKey:"dlpMaterialRequestGrid",routeClass:"dlp-material-request-grid-active",freezeUntil:"custom_mes_issue_category",numbers:["per_ordered"],dates:["transaction_date","schedule_date"],quickFields:["custom_odt","custom_mes_issue_category"],searchFields:["name","custom_material_request_no"],extraFields:["material_request_type"],controls:[
  {fieldname:"custom_odt",fieldtype:"Data",label:translateText("ODT")},
  {fieldname:"custom_mes_issue_category",fieldtype:"Select",label:translateText("申请类别"),options:"\nconsumable\nraw_material\nsemi_finished\nunclassified"},
  {fieldname:"search",fieldtype:"Data",label:translateText("ERP / MES申请号")}
 ],optionalFields:["custom_odt","custom_mes_issue_category","custom_material_request_no"],legacyDisplayFields:["custom_odt","custom_mes_issue_category"],optionLabels:{custom_mes_issue_category:categories},renderValue(field,doc,formatters,escape) {
  if(field === "custom_odt") return escape(formatters.allowed && !formatters.allowed.has(field) ? "" : doc.custom_odt || "");
  if(field === "custom_mes_issue_category") return escape((!formatters.allowed || formatters.allowed.has(field) ? (formatters.translate || (x=>x))(categories[doc.custom_mes_issue_category] || "") : "") || (formatters.allowed && !formatters.allowed.has("material_request_type") ? "" : (formatters.translate || (x=>x))(doc.material_request_type || "")));
 }});
 grid.categories = categories;
 if(typeof module === "object" && module.exports) module.exports = grid;
 root.DeepLinkERPMaterialRequestGrid = grid;
 grid.install(root);
})(typeof globalThis !== "undefined" ? globalThis : this);
