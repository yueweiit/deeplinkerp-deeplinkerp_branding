const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// A bench checkout supplies the real Desk classes; no site, database or browser is needed.
const frappeSource = [process.env.FRAPPE_JS_ROOT, "../../../frappe/frappe/public/js/frappe",
    "../../../../apps/frappe/frappe/public/js/frappe"]
    .filter(Boolean).map(dir => path.resolve(__dirname, dir))
    .find(dir => fs.existsSync(path.join(dir, "utils/number_format.js")));

function useDeskCurrency(root, number_format) {
    const context = vm.createContext(root);
    context.window = context;
    context.lstrip = value => value.replace(/^0+/, "");
    context.replace_all = (value, from, to) => value.split(from).join(to);
    Object.assign(root.frappe, {
        boot:{sysdefaults:{number_format, currency:"CNY"}}, form:{}, provide() {},
        model:{get_value:()=>null}, dom:{remove_script_and_style:value=>value},
        meta:{get_field_currency:(_, doc)=>doc?.currency || "CNY"},
        run_serially:async steps=>{for (const step of steps) await step();},
    });
    function load(file) {
        const filename = path.join(frappeSource, file+".js");
        const source = fs.readFileSync(filename, "utf8").replace('import "./datatype";', "");
        vm.runInContext(source, context, {filename});
    }
    load("utils/datatype");
    load("utils/number_format");
    load("form/formatters");
    load("form/controls/base_control");
    const form = root.frappe.ui.form;
    form.ControlInput = form.Control;
    for (const name of ["data", "int", "float", "currency"]) load("form/controls/"+name);

    const fallback = form.make_control;
    form.make_control = options => {
        if (options.df.fieldtype !== "Currency") return fallback(options);
        // Only the mounted input and display area are substituted. Parsing, formatting,
        // set_value, validation and get_value all use the installed Desk implementation.
        const control = Object.create(form.ControlCurrency.prototype);
        return Object.assign(control, {df:options.df, set_disp_area() {}, $input:{
            attr() {}, val(value) { if (arguments.length) this.value = value; return this.value; },
        }});
    };
}

module.exports = {frappeSource, useDeskCurrency};
