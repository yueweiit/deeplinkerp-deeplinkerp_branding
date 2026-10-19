(function(root) {
 const translateText = (text, args = []) => String((globalThis.__ || (value => value))(text)).replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
 const categories = Object.assign(Object.create(null), {consumable:"消耗品",raw_material:"原料申请",semi_finished:"半成品申请",unclassified:"未分类"});
 const escape = value => String(value ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
 const installed = new WeakSet();
 function summaryHTML(doc,allowed,translate=x=>x) {
  const odt = allowed.has("custom_odt") ? doc.custom_odt || "" : "";
  const category = allowed.has("custom_mes_issue_category") ? categories[doc.custom_mes_issue_category] : "";
  const fallback = allowed.has("material_request_type") ? translate(doc.material_request_type || "") : "";
  const mes = allowed.has("custom_material_request_no") ? doc.custom_material_request_no || "" : "";
  return `<div class="dlp-mr-form-primary"><span>ODT: ${escape(odt)}</span><span>${escape(category ? translate(category) : fallback)}</span></div>${mes ? `<div class="dlp-mr-form-secondary">${translateText("MES申请号: {0}", [escape(mes)])}</div>` : ""}`;
 }
 function refresh(frm) {
  if(!root.$ || !frm.layout?.wrapper) return;
  const allowed = new Set((frm.meta?.fields || []).filter(df=>!df.is_virtual && root.frappe.perm.has_perm("Material Request",df.permlevel || 0,"read")).map(df=>df.fieldname));
  let wrapper = frm.dlpMaterialRequestSummary;
  if(!wrapper) wrapper = frm.dlpMaterialRequestSummary = root.$(`<section class="dlp-mr-form-summary" aria-label="${translateText('申请摘要')}"></section>`).prependTo(frm.layout.wrapper);
  wrapper.html(summaryHTML(frm.doc,allowed,root.__ || (x=>x)));
 }
 function install() {
  const frappe = root.frappe;
  if(!frappe?.ui?.form?.on || installed.has(frappe)) return;
  installed.add(frappe);
  frappe.ui.form.on("Material Request",{refresh});
 }
 const api = {summaryHTML,refresh,install};
 if(typeof module === "object" && module.exports) module.exports = api;
 root.DeepLinkERPMaterialRequestSummary = api;
 install();
})(typeof globalThis !== "undefined" ? globalThis : this);
