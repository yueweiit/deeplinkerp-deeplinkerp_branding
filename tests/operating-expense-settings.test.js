const test = require("node:test");
const assert = require("node:assert/strict");
const create = require("../deeplinkerp_branding/public/js/operating_expense_settings.js");

function dialogHost() {
    class Element {
        constructor(attrs = {}) { this.attrs = {...attrs}; this.events = {}; }
        addClass() { return this; }
        html() { return this; }
        show() { return this; }
        find() { return new Element(); }
        attr(name, value) { this.attrs[name] = value; return this; }
        prop(name, value) { this.attrs[name] = value; return this; }
        removeAttr(name) { delete this.attrs[name]; return this; }
        off(name) { delete this.events[name]; return this; }
        on(name, handler) { this.events[name] = handler; return this; }
        remove() { this.removed = true; }
    }
    let disposed = 0;
    class Dialog {
        constructor() {
            this.$wrapper = new Element();
            this.$body = new Element();
            this.close = new Element({"data-dismiss":"modal"});
        }
        get_close_btn() { return this.close; }
        show() { this.visible = true; }
        hide() { this.visible = false; this.$wrapper.events["hidden.bs.modal"]?.(); }
        clickClose() {
            const pending = this.close.events.click();
            // Model Bootstrap's delegated dismiss, which calls its own hide
            // without waiting for the custom async Dialog.hide() handler.
            if (this.close.attrs["data-dismiss"] === "modal") Dialog.prototype.hide.call(this);
            return pending;
        }
    }
    const host = {frappe:{ui:{Dialog}}, DeepLinkERPPurchasePayments:{disposeControls:()=>disposed++}};
    return {host, disposed:()=>disposed};
}

test("native close waits for confirmation and cancellation retains unsaved controls", async () => {
    const h = dialogHost(), drawer = create(h.host).createDialog("Settings");
    const input = {value:"Unsaved historical name"};
    drawer.controls.push(input);
    let resolve, prompts = 0;
    drawer.beforeClose = () => { prompts++; return new Promise(done => { resolve = done; }); };
    const cancelled = drawer.dialog.clickClose();
    assert.equal(prompts, 1);
    assert.equal(drawer.dialog.visible, true);
    assert.equal(drawer.alive(), true);
    await drawer.dialog.clickClose();
    assert.equal(prompts, 1, "repeated close must not open another confirmation");
    resolve(false);
    await cancelled;
    assert.equal(drawer.dialog.visible, true);
    assert.equal(drawer.dialog.$wrapper.removed, undefined);
    assert.equal(drawer.controls[0].value, "Unsaved historical name");
    assert.equal(h.disposed(), 0);
    const confirmed = drawer.dialog.clickClose();
    resolve(true);
    await confirmed;
    assert.equal(drawer.dialog.visible, false);
    assert.equal(drawer.dialog.$wrapper.removed, true);
    assert.equal(drawer.alive(), false);
    assert.equal(h.disposed(), 1);
});

test("busy settings cannot be closed and Escape uses the same confirmation guard", async () => {
    const h = dialogHost(), drawer = create(h.host).createDialog("Settings");
    let prompts = 0;
    drawer.beforeClose = async () => { prompts++; return false; };
    drawer.setBusy(true);
    await drawer.dialog.clickClose();
    assert.equal(drawer.dialog.visible, true);
    assert.equal(prompts, 0);
    drawer.setBusy(false);
    h.host.cur_dialog = drawer.dialog;
    drawer.dialog.$wrapper.events["keydown.cnSync"]({key:"Escape", preventDefault(){}, stopPropagation(){}});
    await Promise.resolve();
    assert.equal(prompts, 1);
    assert.equal(drawer.dialog.visible, true);
});
