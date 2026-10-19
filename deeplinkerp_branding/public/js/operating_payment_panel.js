(function (root, factory) {
 if (typeof module === "object" && module.exports) module.exports = factory;
 root.DeepLinkERPOperatingPaymentPanel = factory(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
 "use strict";
 const t = (value, args = []) => String((root.__ || (text => text))(value)).replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
 const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
 const money=value=>root.DeepLinkERPOperatingExpenses?.money(value)??"—";
 const tabs = [["payments","付款明细"],["approvals","审批流"],["request","申请信息"],["vouchers","凭证"]];
 function cents(value) {
  if (typeof value !== "string" || !/^\d{1,14}(?:\.\d{1,2})?$/.test(value)) throw new Error(t("金额须明确且最多两位小数"));
  const [whole, fraction=""] = value.split(".");
  return BigInt(whole)*100n+BigInt(fraction.padEnd(2,"0"));
 }
 const decimal = value => `${value/100n}.${String(value%100n).padStart(2,"0")}`;
 const positiveRate=value=>typeof value==="string"&&/^\d{1,14}(?:\.\d{1,9})?$/.test(value)&&/[1-9]/.test(value);
 const classified = source => ["payment","reimbursement"].includes(source.effective_application_type||source.application_type);
 function proofUploaderOptions(paymentId,onSuccess) {
  // Native upload checks write access before invoking a custom method. Managed
  // payment records must stay read-only; the service authorizes this parent.
  return {method:"deeplinkerp_branding.services.operating_payment_service.upload_payment_proof",docname:paymentId,allow_multiple:false,allow_toggle_private:false,allow_web_link:false,allow_google_drive:false,allow_take_photo:false,disable_file_browser:true,restrictions:{allowed_file_types:[".pdf",".png",".jpg",".jpeg"],max_file_size:10*1024*1024},on_success:onSuccess};
 }
 function paymentPreview(balance, value) {
  const amount=cents(value), paid=cents(balance?.paid_amount), pending=cents(balance?.pending_amount);
  if (amount <= 0n || amount > pending) throw new Error(t("本次抵扣金额须大于零且不超过待付金额"));
  return {amount:decimal(amount),paid_amount:decimal(paid+amount),pending_amount:decimal(pending-amount)};
 }
 function balanceProgress(balance) {
  try {const amount=cents(balance?.amount),paid=cents(balance?.paid_amount);if(amount===0n||paid>amount)return null;const percent=Number(paid*1000n/amount)/10;return {percent,label:`${percent}%`};}catch(_){return null;}
 }
 function paymentEligibilityIssue(detail, finance) {
  const source=detail?.source||{};
  if(!finance) return t("当前权限不能办理付款登记。");
  if(!detail?.company) return t("法律公司未确认，请先核对财务映射。");
  if(!classified(source)) return t("请先在财务映射中明确付款申请或费用报销，不能默认按供应商付款。");
  if(source.source_conflict||source.currency_conflict) return t("来源或币种存在冲突，请先核对原单。");
  // A projected policy is authoritative, including an explicit false or missing flag.
  const policy=source.payment_eligibility??detail?.payment_eligibility;
  if(policy&&typeof policy==="object") return policy.can_register_payment===true?"":t(policy.notice||policy.reason||"当前审批节点不能登记付款。");
  if(source.approvals?.eligibility!=="eligible") return t("审批尚未通过，暂不能启用付款登记。");
  return "";
 }
 function takeoverIssue(detail, finance) {
  const issue=paymentEligibilityIssue(detail,finance);
  if(issue) return issue;
  const source=detail.source, identity=source.cashier_source_id||(source.source_system==="cashier-payment-archive"?source.source_id:null),oa=source.oa_identity;
  const instance=typeof oa==="string"?oa:oa?.process_instance_id||oa?.instance_id||oa?.dingding_id;
  return (typeof identity==="string"&&identity.trim()&&identity.length<=140)||(typeof instance==="string"&&/^[A-Za-z0-9_-]{1,200}$/.test(instance))?"":t("尚未匹配到唯一出纳申请或钉钉原单，请先同步并核对历史付款。");
 }
 function needsHistoryConfirmation(detail) {
  if(typeof detail?.erp_payments?.needs_zero_history_confirmation==="boolean")return detail.erp_payments.needs_zero_history_confirmation;
  const payments=detail?.source?.payments;
  return !Array.isArray(payments)||payments.length===0||payments.some(payment=>payment.evidence_status!=="recorded");
 }
 const canTakeover=(detail,finance)=>!takeoverIssue(detail,finance);
 function paymentHistoryKnown(detail) {
  try {cents(detail?.erp_payments?.balance?.paid_amount);cents(detail?.erp_payments?.balance?.pending_amount);return true;}
  catch (_) {return false;}
 }
 function canRegister(detail, finance) {
  try { return Boolean(!paymentEligibilityIssue(detail,finance) && detail.erp_payments?.managed && paymentHistoryKnown(detail) && cents(detail.erp_payments.balance?.pending_amount)>0n); }
  catch (_) { return false; }
 }
 function timelineHTML(row) {
  if (row.lookup_status === "conflict") return `<p class="text-warning">${esc(t("审批身份冲突，请在钉钉原单核对"))}</p>`;
  const events=Array.isArray(row.events)?row.events:[],tasks=Array.isArray(row.current_tasks)?row.current_tasks:[];
  const results={agree:"已同意",approved:"已同意",refuse:"已拒绝",rejected:"已拒绝",pending:"待处理",none:"—"};
  const node=event=>{
   const result=String(event.result??"").trim(), key=result.toLowerCase();
   const label=Object.prototype.hasOwnProperty.call(results,key)?results[key]:result||"—";
   const content=`<strong>${esc(event.stage||"—")}</strong> <span>${esc(event.operator||"—")}</span><small>${esc(event.time||"—")} · ${esc(t(label))}</small>${event.comment?`<p>${esc(event.comment)}</p>`:""}${[ ["attachments","节点附件"],["images","节点图片"] ].map(([key,attachmentLabel])=>Array.isArray(event[key])&&event[key].length?`<span class="text-muted">${esc(t(attachmentLabel))} · ${event[key].length} · ${esc(t("请查看钉钉原单"))}</span>`:"").join(" ")}`;
   const cc=String(event.type||event.action||event.stage||"").toUpperCase();
   return cc==="CC"||cc.includes("抄送")?`<li><details class="dlp-operating-cc"><summary>${esc(t("抄送"))} · ${esc(event.operator||"—")}</summary>${content}</details></li>`:`<li>${content}</li>`;
  };
  // The source API owns chronological order. Each current task and history event
  // is rendered once, then its escaped markup is reused: O(C + E).
  const nodes=events.map(node),list=items=>`<ol class="dlp-operating-timeline">${items.join("")}</ol>`;
  const activeTasks=tasks.filter(task=>["RUNNING","PENDING","WAITING","TODO","PROCESSING"].includes(String(task.status||"").toUpperCase()));
  const endedStatus=String(row.approval_status||"").toUpperCase(), ended=!activeTasks.length && ["COMPLETED","TERMINATED"].includes(endedStatus);
  const current=activeTasks.length?`<div class="dlp-operating-current-tasks">${activeTasks.map(task=>`<p><strong>${esc(t("当前"))}：${esc(task.stage||t("审批节点"))}</strong> · ${esc((Array.isArray(task.assignees)?task.assignees:[]).map(person=>person.name||person.id||"—").join("、")||"—")} · ${esc(t("审批中"))}<small>${esc(task.entered_at||"—")}</small></p>`).join("")}</div>`:`<p class="text-muted">${esc(t(ended?(endedStatus==="COMPLETED"?"审批已结束":"审批已终止"):"当前审批节点尚未同步，请查看钉钉原单"))}</p>`;
  const originator=typeof row.originator==="object"?row.originator?.name||row.originator?.id:row.originator;
  return `${current}${originator?`<p class="text-muted">${esc(t("发起人"))} · ${esc(originator)}</p>`:""}${nodes.length?list(nodes.length>5?[nodes[0],...nodes.slice(-2)]:nodes):`<p class="text-muted">${esc(t("暂无可核对的审批节点，请查看钉钉原单"))}</p>`}${nodes.length>5?`<details><summary>${esc(t("展开全部审批节点"))} (${nodes.length})</summary>${list(nodes)}</details>`:""}<p class="dlp-operating-future text-muted">${esc(t(ended?"以上为已取得的审批历史；完整记录以钉钉原单为准。":"后续节点尚未同步，请在钉钉查看完整流程。"))}</p><p class="dlp-operating-sync-stamp text-muted">${esc(t("来源更新时间"))} · ${esc(row.source_updated_at||"—")} · ${esc(t("最后同步"))} · ${esc(row.last_synced_at||"—")}</p>`;
 }
 const confirm = message => new Promise(resolve => root.frappe.confirm(message,()=>resolve(true),()=>resolve(false)));
 async function mount(drawer, detail, helpers) {
  const {makeControl,action,uiTask,request,reload,canFinance,sourceId}=helpers;
  const mountLoad=drawer.loadId,mounted=()=>drawer.alive()&&mountLoad===drawer.loadId;
  const body=drawer.panel.find(".dlp-payment-body"), source=detail.source;
  const tabsHolder=root.$(`<nav class="dlp-operating-tabs" role="tablist">${tabs.map(([key,label])=>`<button type="button" role="tab" data-operating-tab="${key}">${esc(t(label))}</button>`).join("")}</nav>`).insertAfter(body.find(".dlp-operating-hero"));
  const panes=body.find("[data-operating-pane]");
  let timelineLoaded=false;
  async function selectTab(tab) {
   drawer.activeOperatingTab=tab;
   tabsHolder.find("button").each((_i,node)=>{const active=node.dataset.operatingTab===tab; root.$(node).toggleClass("active",active).attr("aria-selected",String(active));});
   panes.each((_i,node)=>root.$(node).prop("hidden",node.dataset.operatingPane!==tab));
   if (tab!=="approvals" || timelineLoaded) return;
   timelineLoaded=true;
   const target=body.find(".dlp-operating-timeline-holder"), load=drawer.loadId;
   target.text(t("读取审批缓存…"));
   try {
    const row=await request("get_approval_timeline",{source_id:sourceId});
    if (drawer.alive() && load===drawer.loadId) target.html(timelineHTML(row));
   } catch(error) { if(drawer.alive() && load===drawer.loadId){target.text(error.message);timelineLoaded=false;} }
  }
  tabsHolder.on("click.dlpDrawer","button",event=>selectTab(event.currentTarget.dataset.operatingTab));
  const holder=body.find(".dlp-operating-register"), actions=root.$('<div class="dlp-operating-actions"></div>').appendTo(holder);
  const policy=source.payment_eligibility??detail.payment_eligibility;
  if(policy?.can_register_payment===true&&policy.reason==="required_approvals_passed_cashier_only")
   root.$('<p class="text-success" role="status"></p>').text(t("必要审批已通过，待出纳办理。")).insertAfter(body.find(".dlp-operating-payment-heading"));
  if (!detail.erp_payments?.managed || !paymentHistoryKnown(detail)) {
   let zeroConfirmed=false,needsZeroConfirmation=!detail.erp_payments?.managed && needsHistoryConfirmation(detail),confirmationControl=null;
   drawer.paymentDirty=()=>zeroConfirmed;
   const issue=takeoverIssue(detail,canFinance());
   const review=root.$('<div class="dlp-operating-history-review"></div>').insertAfter(body.find(".dlp-operating-payment-heading"));
   const status=root.$('<p class="text-muted" role="status"></p>').appendTo(review);
   async function requireHistoryConfirmation(){if(confirmationControl||issue)return;confirmationControl=await makeControl(drawer,root.$('<div></div>').appendTo(review),{fieldname:"zero_history_confirmed",fieldtype:"Check",label:t("已核对完整历史，确认这笔申请此前没有实际付款")},0,value=>{zeroConfirmed=Boolean(value);drawer.refreshEligibility();});}
   if(needsZeroConfirmation)await requireHistoryConfirmation();
   status.text(issue||detail.erp_payments?.notice||t(needsZeroConfirmation?"历史付款待核对，请核对钉钉原单和请款记录后确认。":"核对历史付款后，后续付款可在 ERP 登记。"));
   action(drawer,actions,"核对历史付款",async()=>{
    const claimed=await uiTask(drawer,async current=>{
     const preview=await request("preview_takeover",{source_id:sourceId,zero_history_confirmed:zeroConfirmed});
     if (!mounted() || !current() || !preview) return;
     status.text(t("{0} {1} {2} · {3} {4} {5} · {6} {7} {8}", [t("历史付款"), preview.history_count??"—", t("笔"), t("已付"), money(preview.balance?.paid_amount), preview.currency||"—", t("待付"), money(preview.balance?.pending_amount), preview.currency||"—"]));
     if(preview.needs_zero_history_confirmation===true){needsZeroConfirmation=true;await requireHistoryConfirmation();if(!zeroConfirmed){drawer.refreshEligibility();return;}}
     if (!preview.existing && !(await confirm(`${esc(t("已核对历史"))} ${esc(preview.history_count??"—")} ${esc(t("笔，待付"))} ${esc(preview.balance?.pending_amount??"—")} ${esc(preview.currency||"—")}。${esc(t("启用后，对应申请在请款网站不能再登记付款。确认接管？"))}`))) return;
     if (!mounted() || !current()) return;
     if (!preview.existing) await request("claim_takeover",{source_id:sourceId,expected_source_version:preview.source_version,expected_history_version:preview.history_version,...(preview.eligibility_fingerprint?{expected_eligibility_fingerprint:preview.eligibility_fingerprint}:{}),request_id:root.crypto.randomUUID(),zero_history_confirmed:zeroConfirmed});
     return true;
    });
    if (claimed && mounted()) await reload();
   },()=>Boolean(canTakeover(detail,canFinance()) && (!needsZeroConfirmation||zeroConfirmed)));
  } else if (canRegister(detail,canFinance())) {
   const values={payment_date:root.frappe.datetime.get_today(),amount:"",bank_amount:"",bank_account:"",party_type:detail.mapping?.party_type||((source.effective_application_type||source.application_type)==="reimbursement"?"Employee":"Supplier"),party:detail.mapping?.party||"",bank_reference:"",remark:""};
   const requestId=root.crypto.randomUUID(), load=drawer.loadId;
   const current=()=>drawer.alive()&&load===drawer.loadId;
   const form=root.$('<section class="dlp-operating-payment-form"></section>').insertAfter(body.find(".dlp-operating-payment-heading")).prop("hidden",true);
   const fields=root.$('<div class="dlp-operating-fields"></div>').appendTo(form),currencyFields=root.$('<div class="dlp-operating-fields dlp-operating-cross-currency"></div>').appendTo(form).prop("hidden",true);
   const currencyNotice=root.$('<p class="dlp-operating-currency-notice text-muted"></p>').appendTo(currencyFields);
   const preview=root.$('<p class="dlp-operating-payment-preview" role="status"></p>').appendTo(form);
   const advanced=root.$(`<details class="dlp-operating-advanced"><summary>${esc(t("跨币种与会计设置"))}</summary><div class="dlp-operating-fields"></div></details>`).appendTo(form);
   const ratesFields=root.$('<div class="dlp-operating-exchange-rates"></div>').appendTo(advanced);
   const formActions=root.$('<div class="dlp-operating-actions"></div>').appendTo(form);
   const initialValues=JSON.stringify(values);
   let opened=false,uploadProof=false,recorded=false,accounts=[],accountsIssue="",bankAmountControl,bankRateControl;
   drawer.paymentDirty=()=>!recorded && (JSON.stringify(values)!==initialValues || uploadProof);
   try{const response=await request("get_payment_accounts",{source_id:sourceId});if(!current())return;accounts=Array.isArray(response)?response:[];}catch(error){if(!current())return;accountsIssue=error.message;}
   const accountMap=new Map(accounts.map(account=>[account.name,account]));
   function update() {
    const account=accountMap.get(values.bank_account),cross=Boolean(account&&account.currency!==source.currency);
    if(account&&!cross)values.bank_amount=values.amount;
    currencyFields.prop("hidden",!cross);
    ratesFields.appendTo(cross?currencyFields:advanced);
    currencyNotice.text(t("{0} {1} · {2} {3}", [t("申请币种"), source.currency||"—", t("银行币种"), account?.currency||"—"]));
    try {const next=paymentPreview(detail.erp_payments.balance,values.amount);preview.text(t("{0} {1} {2} · {3} {4} {5}", [t("保存后累计已付"), next.paid_amount, source.currency, t("剩余待付"), next.pending_amount, source.currency]));}catch(error){preview.text(values.amount?error.message:`${t("最多可付")} ${detail.erp_payments.balance.pending_amount} ${source.currency}`);}
    if(!accounts.length)preview.text(accountsIssue||t("付款账户尚未配置，请先维护当前公司的银行或现金账户。"));
    drawer.refreshEligibility();
   }
   const definitions=[["amount","Data",null,t("本次付款金额 ({0})", [source.currency||"—"])],["payment_date","Date",null,"付款日期"],["bank_account","Select",[{value:"",label:""},...accounts.map(account=>({value:account.name,label:`${account.label||account.name} (${account.currency||"—"})`}))],"付款账户"],["payee_display","Data",null,"收款人"],["bank_reference","Data",null,"银行流水号（可选）"],["remark","Small Text",null,"备注（可选）"]];
   for (const [fieldname,fieldtype,options,label] of definitions) {
    if(!current())return;
    await makeControl(drawer,root.$("<div></div>").appendTo(fields),{fieldname,fieldtype,options,label,read_only:fieldname==="payee_display",reqd:["payment_date","amount","bank_account"].includes(fieldname)},fieldname==="payee_display"?source.payee_name||values.party:values[fieldname],value=>{const previous=accountMap.get(values.bank_account);values[fieldname]=String(value??"");if(fieldname==="bank_account"&&accountMap.get(values.bank_account)?.currency!==previous?.currency){values.bank_amount="";delete values.bank_exchange_rate;bankAmountControl?.set_value("");bankRateControl?.set_value("");}update();});
   }
   for(const [fieldname,label] of [["bank_amount","银行实际付款金额"],["source_exchange_rate",t("申请币种兑本位币汇率 ({0})", [source.currency||"—"])],["bank_exchange_rate","银行币种兑本位币汇率"]]){
    if(!current())return;
    const control=await makeControl(drawer,root.$("<div></div>").appendTo(fieldname==="bank_amount"?currencyFields:ratesFields),{fieldname,fieldtype:"Data",label,reqd:fieldname==="bank_amount"},"",value=>{if(value)values[fieldname]=String(value);else delete values[fieldname];update();});if(fieldname==="bank_amount")bankAmountControl=control;if(fieldname==="bank_exchange_rate")bankRateControl=control;
   }
   const partyHolder=root.$("<div></div>").appendTo(advanced.find(".dlp-operating-fields"));
   if(!values.party)advanced.prop("open",true);
   await makeControl(drawer,partyHolder,{fieldname:"party",fieldtype:"Link",options:values.party_type,label:t("维护收款往来方"),reqd:true},values.party,value=>{values.party=String(value??"");update();},values.party_type==="Employee"?()=>({filters:{company:detail.company}}):undefined);
   for (const [fieldname,fieldtype,options,label] of [["advance_account","Link","Account","预付款 / 往来科目"],["exchange_difference_account","Link","Account","汇兑损益科目"],["cost_center","Link","Cost Center","成本中心"],["project","Link","Project","项目"]]) {if(!current())return;await makeControl(drawer,root.$("<div></div>").appendTo(advanced.find(".dlp-operating-fields")),{fieldname,fieldtype,options,label},"",value=>{if(value) values[fieldname]=String(value);else delete values[fieldname];update();},options?()=>({filters:{company:detail.company}}):undefined);}
   await makeControl(drawer,root.$("<div></div>").appendTo(form),{fieldname:"upload_proof",fieldtype:"Check",label:t("保存后上传付款回单（可选）")},0,value=>{uploadProof=Boolean(value);});
   const ready=()=>{try {paymentPreview(detail.erp_payments.balance,values.amount);const account=accountMap.get(values.bank_account),needsRates=account?.currency!==source.currency||Boolean(values.advance_account);return !recorded&&current()&&opened&&canRegister(detail,canFinance())&&Boolean(account?.currency&&values.party&&values.payment_date)&&cents(values.bank_amount)>0n&&(!needsRates||(positiveRate(values.source_exchange_rate)&&positiveRate(values.bank_exchange_rate)));}catch(_){return false;}};
   const hasPayments=(source.payments||[]).length || (detail.erp_payments.payments||[]).length || cents(detail.erp_payments.balance.paid_amount)>0n;
   action(drawer,actions,hasPayments?"新增付款":"录入第一笔付款",()=>{opened=true;form.prop("hidden",false);update();},()=>!recorded&&canRegister(detail,canFinance()),true);
   action(drawer,formActions,"取消",()=>{opened=false;form.prop("hidden",true);drawer.refreshEligibility();});
   action(drawer,formActions,"保存付款记录",async()=>{
    if(drawer.busy || !ready())return;
    const payload=JSON.stringify(values),addProof=uploadProof;
    const result=await uiTask(drawer,async isCurrent=>{
     if (!(await confirm(esc(t("确认已经实际支付并登记本笔付款？此操作不向银行转账，不自动记账。"))))) return;
     if (!current() || !isCurrent()) return;
     const response=await request("register_payment",{source_id:sourceId,values:payload,expected_source_version:source.version,request_id:requestId});
     if(response?.payment_id)recorded=true;
     return response;
    });
    if(!result)return;
    const paymentId=result?.payment_id;
    if(current()&&addProof&&paymentId&&root.frappe.ui?.FileUploader)new root.frappe.ui.FileUploader(proofUploaderOptions(paymentId,()=>{if(drawer.alive())reload();}));
    if (drawer.alive()) await reload();
   },ready,true);
   form.append(`<p class="text-muted">${esc(t("仅登记已发生的付款，不向银行转账，不自动记账。回单可稍后补传。"))}</p>`);
   update();
  } else holder.prepend(`<p class="text-muted">${esc(paymentEligibilityIssue(detail,canFinance())||detail.erp_payments?.notice||t("已付清或当前审批／权限待核对，不能登记新付款。"))}</p>`);
  const local=body.find(".dlp-operating-local-payments");
  for (const payment of detail.erp_payments?.payments||[]) {
   const section=root.$(`<article class="dlp-operating-payment-card"><strong>${esc(payment.payment_date)} · ${esc(t("付款"))} ${money(payment.amount)} ${esc(payment.currency)}</strong><p>${esc(t("银行实际支付"))} ${money(payment.bank_amount)} ${esc(payment.bank_currency)} · ${esc(payment.bank_account)} · ${esc(payment.bank_reference||"—")}</p><p>${esc(payment.remark||"")}</p><small>${esc(t(payment.status==="Reversed"?"已撤销":"来源：ERP 登记"))} · ${esc(payment.registered_by)} ${esc(payment.reversal_reason||"")}</small><div class="dlp-operating-actions"></div></article>`).appendTo(local);
   action(drawer,section.find(".dlp-operating-actions"),"补传回单",()=>{
    new root.frappe.ui.FileUploader(proofUploaderOptions(payment.name,()=>{if(drawer.alive())reload();}));
   },()=>canFinance()&&payment.status==="Registered");
   section.append(`<p class="text-muted">${esc(t(payment.proof_status||"回单待补"))}</p>`);
   for(const file of payment.proofs||[]) root.$(`<a class="btn btn-link" target="_blank" rel="noopener noreferrer" href="/api/method/deeplinkerp_branding.services.operating_payment_service.download_payment_proof?source_id=${encodeURIComponent(sourceId)}&payment_id=${encodeURIComponent(payment.name)}&file_id=${encodeURIComponent(file.name)}">${esc(file.filename)}</a>`).appendTo(section);
   if(payment.status==="Registered") action(drawer,section.find(".dlp-operating-actions"),"撤销登记",async()=>{
    root.frappe.prompt({fieldname:"reason",fieldtype:"Small Text",label:t("撤销原因"),reqd:1},async values=>{
     if(!drawer.alive())return;
     const draft=(detail.events||[]).some(event=>event.payment_source_id==="erp-payment:"+payment.name&&event.docstatus===0);
     try {
      const reversed=await uiTask(drawer,async current=>{
       if(draft&&!(await confirm(esc(t("此付款有未记账凭证草稿。确认弃用该草稿并撤销付款登记？草稿保留恢复记录和事件审计，不会提交、记账或转账。")))))return;
       if(!mounted() || !current())return;
       await request("reverse_payment",{source_id:sourceId,payment_id:payment.name,reason:values.reason,request_id:root.crypto.randomUUID(),discard_drafts:draft});
       return true;
      });
      if(reversed && mounted())await reload();
     } catch(error){if(drawer.alive())drawer.error(error);}
    },t("撤销付款登记"));
   },()=>canFinance());
   if(payment.advance_configured && payment.status==="Registered") {
    const card=root.$(`<article class="dlp-operating-payment-card"><strong>${esc(t("预付款"))} · ${money(payment.amount)} ${esc(payment.currency)}</strong><p class="text-muted">${esc(t("仅预付／往来，不确认费用；凭证保持草稿，未核销。"))}</p><div class="dlp-operating-actions"></div><div class="dlp-operating-payment-voucher-preview"></div></article>`).appendTo(body.find('[data-operating-pane="vouchers"]').first());
    let voucher=null;
    const association=(detail.events||[]).find(event=>event.payment_source_id==="erp-payment:"+payment.name);
    if(association) card.append(`<p>${esc(t("对应凭证"))} · ${esc(association.journal_entry||association.issue||"—")}</p>`);
    action(drawer,card.find(".dlp-operating-actions"),"预览预付款凭证",async()=>{
     voucher=await uiTask(drawer,()=>helpers.voucherRequest("preview_voucher",{source_id:sourceId,payment_source_id:"erp-payment:"+payment.name}),"read");
     if(drawer.alive()&&voucher) card.find(".dlp-operating-payment-voucher-preview").html(helpers.previewHTML(voucher));
    },()=>canFinance()&&!association);
    action(drawer,card.find(".dlp-operating-actions"),"保存预付款凭证草稿",async()=>{
     const saved=await uiTask(drawer,async current=>{
      if(!(await confirm(esc(t("确认保存预付款凭证草稿？不确认费用、不自动记账。")))))return;
      if(!mounted() || !current())return;
      await helpers.voucherRequest("create_voucher_draft",{source_id:sourceId,payment_source_id:"erp-payment:"+payment.name,expected_fingerprint:voucher.fingerprint});
      return true;
     });
     if(saved && mounted())await reload();
    },()=>canFinance()&&Boolean(voucher)&&!association);
   }
  }
  await selectTab(drawer.activeOperatingTab||"payments");
 }
 return {tabs,paymentPreview,balanceProgress,timelineHTML,canRegister,canTakeover,takeoverIssue,needsHistoryConfirmation,proofUploaderOptions,mount};
});
