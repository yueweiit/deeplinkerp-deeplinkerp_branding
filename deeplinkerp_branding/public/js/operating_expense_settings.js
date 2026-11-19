/* Native centered settings shell; the existing workflow owns all server actions. */
(function (root, factory) {
    if (typeof module === "object" && module.exports) module.exports = factory;
    else root.DeepLinkERPOperatingExpenseSettings = factory(root);
})(typeof window !== "undefined" ? window : globalThis, function (root) {
    const t = (text, args) => root.__ ? root.__(text, args) : text;
    function groups(rows) {
        const result = new Map();
        rows.forEach((row, index) => {
            if (row.source_company.startsWith("source:")) return;
            const company = row.company || "";
            if (!result.has(company)) result.set(company, []);
            result.get(company).push({row, index});
        });
        return [...result].map(([company, aliases]) => ({company, aliases}));
    }
    function createDialog(title) {
        const dialog = new root.frappe.ui.Dialog({title, size:"large", static:true, fields:[]});
        let alive = true, closing = false;
        dialog.$wrapper.addClass("dlp-operating-settings-dialog");
        dialog.$body.html('<div class="dlp-payment-body"></div><footer></footer>');
        const drawer = {
            dialog, panel:dialog.$wrapper, controls:[], actions:[], loadId:0, busy:false,
            alive:()=>alive,
            setBusy(value) {
                this.busy = value;
                dialog.$body.attr("aria-busy", String(value));
                dialog.get_close_btn().prop("disabled", value);
                dialog.$body.find("input, select, textarea, button").prop("disabled", value);
            },
            error(error) { dialog.$body.find(".dlp-error").text(error.message || String(error)); },
        };
        const hide = dialog.hide.bind(dialog);
        dialog.hide = async () => {
            if (!alive || drawer.busy || closing) return;
            closing = true;
            try {
                if (drawer.beforeClose && !(await drawer.beforeClose())) return;
                if (!alive || drawer.busy) return;
                alive = false; drawer.loadId++;
                root.DeepLinkERPPurchasePayments.disposeControls(drawer.controls);
                hide();
            } finally { closing = false; }
        };
        // Bootstrap's delegated dismiss bypasses the async unsaved-changes guard.
        dialog.get_close_btn().removeAttr("data-dismiss").show().off("click").on("click", () => dialog.hide());
        dialog.$wrapper.on("keydown.cnSync", (event) => {
            if (event.key === "Escape" && root.cur_dialog === dialog) {
                event.preventDefault(); event.stopPropagation(); dialog.hide();
            }
        });
        dialog.$wrapper.on("hidden.bs.modal", () => dialog.$wrapper.remove());
        dialog.show();
        return drawer;
    }
    return {groups, createDialog};
});
