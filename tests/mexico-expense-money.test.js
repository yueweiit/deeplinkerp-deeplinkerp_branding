const test = require('node:test');
const assert = require('node:assert/strict');
const factory = require('../deeplinkerp_branding/public/js/mexico_expense_money.js');

function harness(pattern = '#,###.##', right = false, hide = false) {
    const calls = [];
    const root = {
        frappe: {
            format: (value, df, opts, doc) => {calls.push({value, df, doc}); return Number(value).toFixed(2);},
            model: {get_value: () => right},
        },
        get_number_format: () => pattern,
        get_number_format_info: () => ({group_sep: pattern === '#.###,##' ? '.' : ',', decimal_str: pattern === '#.###,##' ? ',' : '.'}),
        get_currency_symbol: code => hide ? null : code,
    };
    return {money:factory(root), calls};
}

test('large converted totals retain exact cents without native float formatting', () => {
    const {money, calls} = harness();
    assert.equal(money.format('76399999999999.24','MXN'),'MXN 76,399,999,999,999.24');
    assert.equal(money.format('19099999999999.81','MXN'),'MXN 19,099,999,999,999.81');
    assert.equal(money.format('-76399999999999.24','MXN'),'MXN -76,399,999,999,999.24');
    assert.equal(money.format('999999999999999999999999.995','USD'),'USD 1,000,000,000,000,000,000,000,000.00');
    assert.equal(calls.length,0);
});

test('exact display follows Desk separators, currency placement and hidden symbols', () => {
    assert.equal(harness('#.###,##',true).money.format('76399999999999.24','MXN'),'76.399.999.999.999,24 MXN');
    assert.equal(harness('#,##,###.##').money.format('76399999999999.24','MXN'),'MXN 7,63,99,99,99,99,999.24');
    assert.equal(harness('#,###.##',false,true).money.format('76399999999999.24','MXN'),'76,399,999,999,999.24');
});

test('ordinary amounts retain native formatting and invalid data never becomes zero', () => {
    const {money,calls}=harness();
    assert.equal(money.format('12.30','USD'),'12.30');
    assert.equal(calls[0].doc.currency,'USD');
    assert.equal(calls[0].df.precision,2);
    for (const value of [null,undefined,'NaN','Infinity','<img>']) assert.equal(money.format(value,'USD'),'—');
    assert.equal(money.format('76399999999999.24','<img>'),'&lt;img&gt; 76,399,999,999,999.24');
});

test('parallel asset loading can evaluate the workbench before its money helper', () => {
    const fs=require('node:fs'), path=require('node:path'), vm=require('node:vm');
    const sandbox={frappe:{format:value=>String(value)}};
    vm.createContext(sandbox);
    const scripts=path.join(__dirname,'../deeplinkerp_branding/public/js');
    vm.runInContext(fs.readFileSync(path.join(scripts,'mexico_expenses.js'),'utf8'),sandbox);
    vm.runInContext(fs.readFileSync(path.join(scripts,'mexico_expense_money.js'),'utf8'),sandbox);
    const html=sandbox.DeepLinkERPMexicoExpenses.Workbench.prototype.lineAmount({amount:'76399999999999.24',currency:'MXN'});
    assert.equal(html,'MXN 76,399,999,999,999.24');
});
