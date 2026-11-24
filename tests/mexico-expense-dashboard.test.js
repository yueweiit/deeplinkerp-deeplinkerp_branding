const test = require("node:test");
const assert = require("node:assert/strict");
const factory = require("../deeplinkerp_branding/public/js/mexico_expenses.js");
const dashboard = require("../deeplinkerp_branding/public/js/mexico_expense_dashboard.js")({});

function workbench() {
    let route = "mexico-expenses";
    const calls = [];
    const root = {location:{search:""}, frappe:{route_flags:{},get_route:()=>[route], set_route:(next,options)=>{
        calls.push({route:next,options}); route=next;
        root.location.search="?"+new URLSearchParams(Object.entries(options).map(([k,v])=>[k,JSON.stringify(v)]));
    }}};
    const api = factory(root), page=Object.create(api.Workbench.prototype);
    Object.assign(page,{view:"overview",report:"company",context:{today:"2026-11-24"},epoch:0,
        el:{innerHTML:"previous company data"},disposeFilterControls(){},disposeApprovalSidebar(){},
        filters:{from_date:"2026-10-01",to_date:"2026-10-31",currency:"USD",company:"",approval_type:"",basis:"period",status:"finance_approved",category:"",subcategory:"",confirmed_only:"",issue:"",review_bucket:"",unassigned_only:""}});
    page.dashboardData={scope:{...page.filters,confirmed_only:"1",reporting_currency:"USD"}};
    return {root,page,api,calls};
}

test("company navigation and return preserve month and currency across a refreshed child route", async()=>{
    const {page,root,api,calls}=workbench();
    page.dashboardNavigate({dashboard:"company",company:"A"});
    assert.equal(calls[0].route,"mexico-expense-reports");
    const child=Object.create(api.Workbench.prototype);
    Object.assign(child,{view:"reports",report:"company",filters:{...page.filters}});
    child.applyRouteFilters();
    assert.equal(child.companyDashboard,true);
    assert.equal(child.filters.company,"A");
    assert.equal(child.filters.from_date,"2026-10-01");
    child.dashboardData={scope:{...child.effectiveFilters()}};
    child.dashboardNavigate({dashboard:"details",company:"A",form:"purchase"});
    const options=calls.at(-1).options;
    assert.equal(options.mx_filters.confirmed_only,"1");
    assert.equal(options.mx_filters.approval_type,"purchase");
    assert.equal(options.mx_filters.currency,"USD");
    const details=Object.create(api.Workbench.prototype);
    Object.assign(details,{view:"details",report:"company",filters:{...page.filters}});
    details.applyRouteFilters();
    assert(details.returnNavigation().includes("return-company"));
    await details.click({target:{closest:()=>({dataset:{action:"return-company"}})}});
    child.applyRouteFilters();
    assert.equal(child.filters.approval_type,"");
    await child.click({target:{closest:()=>({dataset:{action:"return-home"}})}});
    assert.equal(calls.at(-1).route,"mexico-expenses");
    assert.equal(calls.at(-1).options.mx_filters.company,"");
    assert.equal(calls.at(-1).options.mx_filters.from_date,"2026-10-01");
});

test("amount and trend drilldowns use returned dates and never mutate the parent selection",()=>{
    const {page,calls}=workbench();
    page.dashboardData.scope.to_date="2026-10-24";
    page.dashboardNavigate({dashboard:"details",company:"A"});
    assert.equal(calls.at(-1).options.mx_filters.to_date,"2026-10-24");
    assert.equal(page.filters.company,"");
    assert.equal(page.filters.to_date,"2026-10-31");
    page.dashboardNavigate({dashboard:"trend",from:"2026-08-01",to:"2026-08-31"});
    assert.equal(calls.at(-1).options.mx_filters.from_date,"2026-08-01");
    assert.equal(calls.at(-1).options.mx_filters.to_date,"2026-08-31");
});

test("review links retain bucket and unknown-company scope without confirmed-only exclusion",()=>{
    const {page,calls}=workbench();
    page.dashboardNavigate({dashboard:"review",company:"A",bucket:"awaiting_finance"});
    let filters=calls.at(-1).options.mx_filters;
    assert.equal(filters.review_bucket,"awaiting_finance");
    assert.equal(filters.confirmed_only,"");
    assert.equal(filters.status,"active");
    page.dashboardNavigate({dashboard:"review",unassigned:"1",bucket:"attention"});
    filters=calls.at(-1).options.mx_filters;
    assert.equal(filters.unassigned_only,"1");
    assert.equal(filters.company,"");
});

test("current-month export and query filters stop at today without changing the month picker",()=>{
    const {page}=workbench();
    Object.assign(page.filters,{from_date:"2026-11-01",to_date:"2026-11-30"});
    assert.equal(page.effectiveFilters().to_date,"2026-11-24");
    assert.equal(page.filters.to_date,"2026-11-30");
});

test("legacy overview date bases and categories retain their meaning on the analysis page",()=>{
    for (const extra of [{basis:"request"},{status:"rejected"},{category:"tax"}]) {
        const {page,calls}=workbench();
        Object.assign(page.filters,extra);
        assert.equal(page.redirectLegacyOverview(),true);
        assert.equal(calls[0].route,"mexico-expense-reports");
        for(const [key,value] of Object.entries(extra))assert.equal(calls[0].options.mx_filters[key],value);
        assert.equal(calls[0].options.mx_dashboard,undefined);
    }
    const {page}=workbench();
    assert.equal(page.redirectLegacyOverview(),false);
});

test("fresh cached module entry uses the new business month, while explicit URLs survive",async()=>{
    const {root,page}=workbench();
    root.frappe.call=async()=>({message:{today:"2027-01-01",approval_forms:[]}});
    Object.assign(page,{refresh(){},setBreadcrumbs(){},filters:{...page.filters,company:"A",approval_type:"purchase"}});
    await page.show();
    assert.equal(page.filters.from_date,"2027-01-01");
    assert.equal(page.filters.company,"");
    assert.equal(page.filters.approval_type,"");
    root.location.search="?mx_filters="+encodeURIComponent(JSON.stringify({from_date:"2026-09-01",to_date:"2026-09-30",currency:"USD"}));
    await page.show();
    assert.equal(page.filters.from_date,"2026-09-01");
    assert.equal(page.filters.currency,"USD");
});

test("failed company context clears old data, blocks stale export, and retries the requested company",async()=>{
    const {page,root,api}=workbench();
    const rendered=[];
    Object.assign(page,{view:"reports",companyDashboard:true,setBreadcrumbs(){},refresh(){
        rendered.push({...this.filters});this.el.innerHTML=this.filters.company;
    }});
    page.filters.company="A";
    page.selectDashboardCompany("B");
    let rejectContext;
    root.frappe.call=()=>new Promise((resolve,reject)=>{rejectContext=reject;});
    const pending=page.show();
    assert.equal(page.dashboardData,null);
    assert.equal(page.contextNeedsReload,true);
    assert(!page.el.innerHTML.includes("previous company data"));
    assert(page.el.innerHTML.includes("Loading Mexico expenses"));
    await api.Workbench.prototype.refresh.call(page);
    // No export helper is installed: exporting stale filters would fail this test.
    await page.export();
    rejectContext(new Error("Context unavailable"));
    await pending;
    assert(page.el.innerHTML.includes('data-action="retry"'));
    assert.equal(rendered.length,0);
    await page.export();
    root.frappe.call=async()=>({message:{today:"2026-11-24",approval_forms:[]}});
    await page.click({target:{closest:()=>({dataset:{action:"retry"}})}});
    assert.equal(page.contextNeedsReload,false);
    assert.equal(page.el.innerHTML,"B");
    assert.deepEqual(rendered.map(f=>[f.company,f.from_date,f.currency]),[["B","2026-10-01","USD"]]);
});

test("late context successes and failures cannot overwrite a newer company selection",async()=>{
    for (const lateResult of ["resolve","reject"]) {
        const {page,root}=workbench(),pending=[],rendered=[];
        Object.assign(page,{view:"reports",companyDashboard:true,setBreadcrumbs(){},refresh(){
            rendered.push(this.filters.company);this.el.innerHTML=this.filters.company;
        }});
        root.frappe.call=()=>new Promise((resolve,reject)=>pending.push({resolve,reject}));
        page.selectDashboardCompany("B");const first=page.show();
        page.selectDashboardCompany("C");const second=page.show();
        pending[1].resolve({message:{today:"2026-11-24",approval_forms:[],freshness:"new"}});
        await second;
        if(lateResult==="resolve")pending[0].resolve({message:{today:"2026-11-23",approval_forms:[],freshness:"old"}});
        else pending[0].reject(new Error("Old request failed"));
        await first;
        assert.equal(page.context.freshness,"new");
        assert.equal(page.contextNeedsReload,false);
        assert.equal(page.el.innerHTML,"C");
        assert.deepEqual(rendered,["C"]);
    }
});

test("a refresh started on the old company cannot replace a subsequent company context",async()=>{
    for (const lateResult of ["resolve","reject"]) {
        const {page,root}=workbench(),pending=[],rendered=[];
        Object.assign(page,{view:"reports",companyDashboard:true,setBreadcrumbs(){},refresh(){rendered.push(this.filters.company);}});
        root.frappe.call=()=>new Promise((resolve,reject)=>pending.push({resolve,reject}));
        page.selectDashboardCompany("A");page.applyRouteFilters();
        const reload=page.reload();
        page.selectDashboardCompany("B");const show=page.show();
        pending[1].resolve({message:{today:"2026-11-24",approval_forms:[],freshness:"new"}});
        await show;
        if(lateResult==="resolve")pending[0].resolve({message:{today:"2026-11-23",approval_forms:[],freshness:"old"}});
        else pending[0].reject(new Error("Old refresh failed"));
        await reload;
        assert.equal(page.context.freshness,"new");
        assert.deepEqual(rendered,["B"]);
    }
});

test("leaving a cached page invalidates its pending context failure",async()=>{
    const {page,root}=workbench();let rejectContext;
    root.frappe.call=()=>new Promise((resolve,reject)=>{rejectContext=reject;});
    const pending=page.show();
    page.navigate("details",page.filters);
    page.epoch++; // The wrapper hide handler invalidates the previous page.
    page.el.innerHTML="hidden page";
    rejectContext(new Error("Old request failed"));
    await pending;
    assert.equal(page.el.innerHTML,"hidden page");
});

test("legacy redirects replace history while ordinary drilldowns remain navigable with Back",()=>{
    const {page,root}=workbench();
    const history=["workspace","mexico-expenses"];
    root.frappe.set_route=(next)=>{
        if(root.frappe.route_flags.replace_route)history[history.length-1]=next;
        else history.push(next);
        root.frappe.route_flags={};
    };
    page.filters.basis="request";
    assert.equal(page.redirectLegacyOverview(),true);
    assert.deepEqual(history,["workspace","mexico-expense-reports"]);
    history.pop();
    assert.equal(history.at(-1),"workspace");
    page.filters.basis="period";
    assert.equal(page.redirectLegacyOverview(),false);
    page.dashboardNavigate({dashboard:"company",company:"A"});
    assert.deepEqual(history,["workspace","mexico-expense-reports"]);
});

test("unavailable historical rates never render fake zero amounts or trend drilldowns",()=>{
    const data={total:"90.00",included:{applications:2,lines:3},review:{lines:0,awaiting_lines:0},
        comparison:{comparable:false,previous:null,delta:null,percent:null,unavailable_reason:"exchange_rate"},
        forms:[{approval_type:"expense",amount:"90.00",lines:3}],categories:[],trend:[],trend_unavailable_reason:"exchange_rate"};
    const html=dashboard.render(data,{currency:"MXN",company:"A",forms:[{id:"expense",label:"Expenses"}],categories:[],money:v=>`[${v}]`,number:String,help:()=>""});
    assert(html.includes("90.00"));
    assert(html.includes("Comparison unavailable"));
    assert(html.includes("Historical exchange rates are unavailable."));
    assert(html.includes('data-dashboard="details"'));
    assert(!html.includes('data-dashboard="trend"'));
    assert(!html.includes("[0.00]"));
});

test("empty companies, escaped names, unavailable comparisons and review counts are visible",()=>{
    const company='<img src=x onerror="bad()">';
    const data={total:"10.10",included:{applications:1,lines:1},review:{lines:2,awaiting_lines:3,unassigned_lines:1},comparison:null,
        companies:[{company,amount:"10.10",lines:1,share:"100.0",comparable:false,review_lines:1,awaiting_lines:3},
            {company:"Empty",amount:"0.00",lines:0,share:"0.0",comparable:false,review_lines:0,awaiting_lines:0}]};
    const html=dashboard.render(data,{currency:"MXN",forms:[],categories:[],money:v=>v,number:String,help:()=>""});
    assert(!html.includes('<img'));
    assert(html.includes('&lt;img'));
    assert(html.includes("Empty"));
    assert(html.includes("No Records"));
    assert(html.includes("Comparison unavailable"));
    assert(html.includes('data-unassigned="1"'));
    assert(html.includes('data-bucket="awaiting_finance"'));
});

test("returning from a company and selecting all retain the current month and currency",async()=>{
    const {page,root,calls}=workbench();
    Object.assign(page,{view:"reports",companyDashboard:true,homeFilters:{...page.filters}});
    Object.assign(page.filters,{company:"A",from_date:"2026-09-01",to_date:"2026-09-30",currency:"CNY"});
    await page.click({target:{closest:()=>({dataset:{action:"return-home"}})}});
    assert.equal(calls.at(-1).route,"mexico-expenses");
    assert.deepEqual([calls.at(-1).options.mx_filters.company,calls.at(-1).options.mx_filters.from_date,calls.at(-1).options.mx_filters.currency],["","2026-09-01","CNY"]);
    page.selectDashboardCompany("B");
    assert.equal(calls.at(-1).options.mx_filters.company,"B");
    assert.equal(calls.at(-1).options.mx_home.from_date,"2026-09-01");
    page.selectDashboardCompany("");
    assert.equal(calls.at(-1).route,"mexico-expenses");
    assert.equal(calls.at(-1).options.mx_filters.currency,"CNY");
    page.dashboardNavigate({dashboard:"details",form:"expense"});
    assert.equal(calls.at(-1).options.mx_home.from_date,"2026-09-01");
});

test("dashboard month changes reject future or malformed months without altering the selection",()=>{
    const {page}=workbench();let refreshed=0;page.refresh=()=>refreshed++;
    for(const month of ["2026-12","2027-01","2026-13","1899-12","bad"]){
        assert.equal(page.setDashboardMonth(month),false);
        assert.equal(page.filters.from_date,"2026-10-01");
    }
    assert.equal(page.setDashboardMonth("2024-02"),true);
    assert.equal(page.filters.to_date,"2024-02-29");
    assert.equal(refreshed,1);
});

test("cutoff follows the server response and data status remains actionable",()=>{
    const {page,root}=workbench();
    root.frappe.datetime={str_to_user:String};
    const cutoff={},status={classList:{toggle(name,value){this[name]=value;}}};
    page.el={querySelector:selector=>selector===".mx-dashboard-cutoff"?cutoff:status};
    page.updateDashboardStatus({scope:{to_date:"2026-11-23"},sync_complete:false});
    assert.equal(cutoff.textContent,"Through 2026-11-23");
    assert.equal(status.disabled,false);
    assert.equal(status.classList['mx-status-warning'],true);
});

test("missing trend months have no money or drilldown, while confirmed zero remains clickable",()=>{
    const data={total:"0.00",included:{applications:1,lines:1},review:{lines:0,awaiting_lines:0},comparison:null,forms:[],categories:[],trend:[
        {month:"2026-09",amount:null,lines:0,from_date:"2026-09-01",to_date:"2026-09-30"},
        {month:"2026-10",amount:"0.00",lines:1,from_date:"2026-10-01",to_date:"2026-10-31"}]};
    const html=dashboard.render(data,{currency:"MXN",company:"A",forms:[],categories:[],money:v=>String(v),number:String});
    assert.equal((html.match(/data-dashboard="trend"/g)||[]).length,1);
    assert(!html.includes('data-from="2026-09-01"'));
    assert(html.includes('data-from="2026-10-01"'));
    assert(html.includes('mx-month-empty'));
});

test("company breadcrumbs retain the current scope on their overview parent link",()=>{
    const links=[],children=[];
    const f={from_date:"2026-10-01",to_date:"2026-10-31",currency:"USD",company:"A"};
    const root={location:{search:"?mx_dashboard=1&mx_filters="+encodeURIComponent(JSON.stringify(f))},
        frappe:{get_route:()=>["mexico-expense-reports"],breadcrumbs:{set_custom_breadcrumbs(){},append_breadcrumb_element:(href,label)=>links.push({href,label}),$breadcrumbs:{append:el=>children.push(el)}}},
        document:{createElement:tag=>({tag,children:[],setAttribute(){},appendChild(node){this.children.push(node);}})}};
    factory(root).installBreadcrumbs();root.frappe.breadcrumbs.set_custom_breadcrumbs({});
    const parent=links.at(-1);
    assert.equal(parent.label,"Expense Overview");
    const filters=JSON.parse(new URL(parent.href,"http://test").searchParams.get("mx_filters"));
    assert.deepEqual(filters,{...f,company:""});
    assert.equal(children.at(-1).children[0].textContent,"A");
});
