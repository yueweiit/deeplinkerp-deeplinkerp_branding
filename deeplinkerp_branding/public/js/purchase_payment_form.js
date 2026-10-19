if (!window.dlpPurchasePaymentFormInstalled) {
 const translateText = (text) => (window.__ || (value => value))(text);
 window.dlpPurchasePaymentFormInstalled = true;
 for (const doctype of ["Purchase Receipt", "Purchase Order", "Purchase Invoice", "Payment Entry"]) {
  frappe.ui.form.on(doctype, {refresh: frm => DeepLinkERPPurchasePayments.formRefresh(frm)});
 }
 const supported = new Set(["Purchase Order", "Purchase Receipt", "Purchase Invoice", "Payment Entry"]);
 const requests = new WeakMap();
 let dispatch = null;
 const canonical = value => {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort()
   .filter(key => !["__onload", "__last_sync_on", "__unsaved"].includes(key)).map(key => [key, canonical(value[key])]));
  return value;
 };
 const nativeSave = frappe.ui.form.save;
 frappe.ui.form.save = function(frm, action, callback, btn) {
  if (!supported.has(frm.doc.doctype) || window.cur_frm !== frm) return nativeSave.apply(this, arguments);
  if (frm.dlpReversalIdentity?.doctype === frm.doc.doctype && frm.dlpReversalIdentity?.name === frm.doc.name &&
      frm.dlpReversal && (frm.dlpReversal.stage !== 'completed' || frm.dlpReversal.refresh_failed)) {
   frappe.msgprint(translateText("冲销未完成，相关操作暂缓办理。"));
   return;
  }
  const snapshot = JSON.stringify(canonical(frm.doc));
  const signature = action + ":" + snapshot;
  let pending = requests.get(frm);
  if (!pending || pending.signature !== signature) {
   pending = {signature, snapshot, action, name: frm.doc.name, doctype: frm.doc.doctype, key: window.crypto.randomUUID()};
   requests.set(frm, pending);
  }
  const prior = dispatch;
  dispatch = pending;
  try {
   return nativeSave.call(this, frm, action, response => {
    const renamed = response?.docs?.some(doc => doc.doctype === pending.doctype &&
     doc.localname === pending.name && doc.name === frm.doc.name);
    if (window.cur_frm !== frm || frm.doc.doctype !== pending.doctype || (frm.doc.name !== pending.name && !renamed)) return;
    if (response?.purchase_reversal && response.purchase_reversal.stage !== 'completed') {
     frm.dlpReversal = response.purchase_reversal;
     frm.dlpReversalIdentity = {doctype: frm.doc.doctype, name: frm.doc.name};
     frappe.show_alert?.({message:translateText("取消已受理，库存重算尚未完成。"),indicator:'orange'});
    }
    if (response && !response.exc && response.docs?.some(doc => doc.doctype === pending.doctype) && requests.get(frm) === pending) requests.delete(frm);
    return callback?.(response);
   }, btn);
  } finally { dispatch = prior; }
 };
 // Native save.js stays the transport owner. This prefilter runs synchronously
 // only for the exact request dispatched by the current procurement form.
 $.ajaxPrefilter(options => {
  const pending = dispatch;
  if (!pending) return;
  let url;
  try { url = new URL(options.url || "", window.location.href); } catch { return; }
  if (url.origin !== window.location.origin) return;
  const data = new URLSearchParams(typeof options.data === "string" ? options.data : options.data || {});
  if (data.has("request_id")) return;
  const method = url.pathname.startsWith("/api/method/") ? url.pathname.slice("/api/method/".length) : null;
  if (method === "frappe.desk.form.save.savedocs") {
   let doc;
   try { doc = JSON.parse(data.get("doc")); } catch { return; }
   if (data.get("action") !== pending.action || doc.doctype !== pending.doctype || doc.name !== pending.name ||
       JSON.stringify(canonical(doc)) !== pending.snapshot) return;
  } else if (method === "frappe.desk.form.save.cancel") {
   if (pending.action !== "cancel" || data.get("doctype") !== pending.doctype || data.get("name") !== pending.name) return;
   data.set("doc", pending.snapshot);
  } else return;
  data.set("request_id", pending.key);
  options.data = data.toString();
 });
}
