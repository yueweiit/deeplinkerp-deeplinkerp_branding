{% include "deeplinkerp_branding/public/js/cn_cash_journal.js" %}
frappe.pages["china-cash-journal"].on_page_load = wrapper => { wrapper.cashJournal = new DeepLinkERPCNCashJournal.Workbench(wrapper); };
frappe.pages["china-cash-journal"].on_page_show = wrapper => {
    const journal = wrapper.cashJournal;
    if (journal?.loaded && !journal.hasChanges()) journal.load();
};
