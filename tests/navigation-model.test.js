const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const modulePath = path.join(
	__dirname,
	"..",
	"deeplinkerp_branding",
	"public",
	"js",
	"deeplinkerp_navigation.js"
);
const navigation = fs.existsSync(modulePath) ? require(modulePath) : {};

test("sidebar compatibility skips dividers and supplies icons without changing actions", () => {
	assert.equal(navigation.prepareSidebarMenuItem({ is_divider: true }), null);
	const onClick = () => {};
	const item = { name: "reload", label: "Reload", onClick, condition: () => false };
	const result = navigation.prepareSidebarMenuItem(item);
	assert.equal(result.icon, "layout-grid");
	assert.equal(result.onClick, onClick);
	assert.equal(result.condition, item.condition);
	assert.equal(item.icon, undefined);
	const image = { label: "App", icon_url: "/assets/app/logo.svg" };
	assert.equal(navigation.prepareSidebarMenuItem(image), image);
});

function productionFunction(name) {
	assert.equal(typeof navigation[name], "function", `${name} must be exported by the navigation model`);
	return navigation[name];
}

class FakeClassList {
	constructor(names = []) {
		this.names = new Set(names);
	}

	contains(name) {
		return this.names.has(name);
	}

	add(name) {
		this.names.add(name);
	}

	remove(name) {
		this.names.delete(name);
	}

	toggle(name, force) {
		const enabled = force === undefined ? !this.names.has(name) : force;
		if (enabled) this.names.add(name);
		else this.names.delete(name);
		return enabled;
	}
}

class FakeElement {
	constructor(classes = []) {
		this.children = [];
		this.classList = new FakeClassList(classes);
		this.dataset = {};
		this.attributes = new Map();
		this.hidden = false;
		this.parentElement = null;
		this.listeners = {};
	}

	appendChild(child) {
		child.remove();
		this.children.push(child);
		child.parentElement = this;
		return child;
	}

	prepend(child) {
		child.remove();
		this.children.unshift(child);
		child.parentElement = this;
		return child;
	}

	remove() {
		if (!this.parentElement) return;
		this.parentElement.children = this.parentElement.children.filter((child) => child !== this);
		this.parentElement = null;
	}

	addEventListener(type, listener) {
		this.listeners[type] ||= [];
		this.listeners[type].push(listener);
	}

	setAttribute(name, value) {
		this.attributes.set(name, String(value));
	}

	getAttribute(name) {
		return this.attributes.get(name) ?? null;
	}

	removeAttribute(name) {
		this.attributes.delete(name);
	}

	querySelectorAll(selector) {
		const matchesDisclosure = (element) => {
			if (!element.classList.contains("dlp-mes-navigation__group")) return false;
			if (selector === ".dlp-mes-navigation__group--open") {
				return element.classList.contains("dlp-mes-navigation__group--open");
			}
			if (selector === '.dlp-mes-navigation__group[data-user-collapsed="true"]') {
				return element.dataset.userCollapsed === "true";
			}
			if (selector === '.dlp-mes-navigation__group[data-user-expanded="true"]') {
				return element.dataset.userExpanded === "true";
			}
			return false;
		};
		const matches = [];
		const visit = (element) => {
			element.children.forEach((child) => {
				if (matchesDisclosure(child)) matches.push(child);
				visit(child);
			});
		};
		visit(this);
		return matches;
	}

	dispatch(type) {
		const event = {
			currentTarget: this,
			defaultPrevented: false,
			preventDefault() {
				this.defaultPrevented = true;
			},
		};
		(this.listeners[type] || []).forEach((listener) => listener(event));
		return event;
	}
}

function classicSidebar(icons) {
	const container = new FakeElement();
	icons.forEach((icon) => {
		const button = new FakeElement(["custom-filters-right-sidebar-item"]);
		button.dataset.iconLabel = icon.label;
		container.appendChild(button);
	});
	return {
		items: icons,
		bar: { querySelector: (selector) => selector === ".custom-filters-right-sidebar-items" ? container : null },
		container,
	};
}

test("maps the approved MES line icons and keeps a deterministic fallback", () => {
	const getNavigationIcon = productionFunction("getNavigationIcon");
	const labels = [
		"DLP Framework",
		"AI Assistant",
		"China Finance",
		"Organization",
		"Accounting",
		"Assets",
		"Buying",
		"Manufacturing",
		"Projects",
		"Quality",
		"Selling",
		"Stock",
		"Subcontracting",
		"Deeplinkerp Settings",
		"Overseas Cost",
		"Unknown Module",
	];

	assert.deepEqual(
		labels.map((label) => getNavigationIcon(label)),
		[
			"blocks",
			"bot",
			"file-text",
			"network",
			"landmark",
			"warehouse",
			"tag",
			"factory",
			"presentation",
			"shield-check",
			"briefcase",
			"boxes",
			"repeat-2",
			"settings",
			"calculator",
			"layout-grid",
		]
	);
});

test("uses a navigation display label without replacing workspace identity", () => {
	const getNavigationDisplayLabel = productionFunction("getNavigationDisplayLabel");

	assert.equal(
		getNavigationDisplayLabel({ name: "China Finance", label: "China Finance" }),
		"Finance"
	);
	assert.equal(
		getNavigationDisplayLabel({
			name: "Buying",
			label: "Buying",
			translated_label: "采购",
		}),
		"采购"
	);
});

test("builds the visible desktop tree with configured root order without mutating boot data", () => {
	const buildNavigationTree = productionFunction("buildNavigationTree");
	const icons = [
		{ name: "Operations", label: "Operations", icon_type: "Folder" },
		{ name: "Stock", label: "Stock", parent_icon: "Operations" },
		{ name: "Hidden", label: "Hidden", hidden: 1 },
		{ name: "Selling", label: "Selling" },
		{ name: "Orphan", label: "Orphan", parent_icon: "Hidden" },
	];
	const snapshot = structuredClone(icons);

	const first = buildNavigationTree(icons);
	const second = buildNavigationTree(icons);

	assert.deepEqual(
		first.map((item) => [item.label, item.children.map((child) => child.label)]),
		[
			["Selling", []],
			["Operations", ["Stock"]],
			["Orphan", []],
		]
	);
	assert.deepEqual(second, first, "the model must be idempotent");
	assert.deepEqual(icons, snapshot, "boot data must remain authoritative and unmodified");
});

test("applies the approved fixed DL root order", () => {
	const buildNavigationTree = productionFunction("buildNavigationTree");
	const icons = [
		{ name: "Deeplinkerp Settings", label: "Deeplinkerp Settings" },
		{ name: "Selling", label: "Selling" },
		{ name: "DLP Framework", label: "DLP Framework" },
		{ name: "Manufacturing", label: "Manufacturing" },
		{ name: "Organization", label: "Organization" },
		{ name: "China Finance", label: "China Finance" },
		{ name: "Buying", label: "Buying" },
		{ name: "Overseas Costing", label: "Overseas Costing" },
		{ name: "Stock", label: "Stock" },
		{ name: "Quality", label: "Quality" },
		{ name: "Assets", label: "Assets" },
		{ name: "AI Assistant", label: "AI Assistant" },
		{ name: "Subcontracting", label: "Subcontracting" },
		{ name: "Projects", label: "Projects" },
		{ name: "Accounting", label: "Accounting" },
	];
	const snapshot = structuredClone(icons);

	assert.deepEqual(
		buildNavigationTree(icons).map((item) => item.name),
		[
			"Organization",
			"Buying",
			"Selling",
			"Stock",
			"Manufacturing",
			"Assets",
			"China Finance",
			"Overseas Costing",
			"Projects",
			"Quality",
			"Subcontracting",
			"Accounting",
			"AI Assistant",
			"DLP Framework",
			"Deeplinkerp Settings",
		]
	);
	assert.deepEqual(icons, snapshot, "ordering must not mutate Frappe boot data");

	const classic = classicSidebar(icons);
	const originalButtons = [...classic.container.children];
	let clicks = 0;
	originalButtons[1].addEventListener("click", () => clicks++);
	const arrangeClassicSidebar = productionFunction("arrangeClassicSidebar");
	assert.equal(arrangeClassicSidebar(classic), true);
	assert.deepEqual(
		classic.container.children.map((button) => button.dataset.iconLabel),
		buildNavigationTree(icons).map((item) => item.label)
	);
	assert.equal(arrangeClassicSidebar(classic), false, "unchanged order must not move nodes again");
	originalButtons[1].dispatch("click");
	assert.equal(clicks, 1, "classic click handlers survive sorting");
	assert.equal(new Set(classic.container.children).size, originalButtons.length);
	assert.ok(originalButtons.every((button) => classic.container.children.includes(button)));
	assert.deepEqual(classic.items, snapshot, "the native controller and desktop layout retain original order");
});

test("keeps unknown DL roots stable after known roots and preserves children", () => {
	const buildNavigationTree = productionFunction("buildNavigationTree");
	const icons = [
		{ name: "Custom B", label: "Custom B" },
		{ name: "Quality", label: "Quality" },
		{ name: "Buying Settings", label: "Buying Settings", parent_icon: "Buying" },
		{ name: "Buying", label: "Buying" },
		{ name: "Custom A", label: "Custom A" },
	];
	const snapshot = structuredClone(icons);
	const roots = buildNavigationTree(icons);

	assert.deepEqual(
		roots.map((item) => item.name),
		["Buying", "Quality", "Custom B", "Custom A"]
	);
	assert.deepEqual(roots[0].children.map((item) => item.name), ["Buying Settings"]);
	assert.deepEqual(icons, snapshot, "tree construction must not mutate its input");
	const classic = classicSidebar(icons.filter((icon) => !icon.parent_icon));
	productionFunction("arrangeClassicSidebar")(classic);
	assert.deepEqual(classic.container.children.map((button) => button.dataset.iconLabel), roots.map((item) => item.label));
});

test("classic sorting tolerates the optional sidebar missing and recognizes Chinese identities", () => {
	const arrangeClassicSidebar = productionFunction("arrangeClassicSidebar");
	for (const controller of [null, {}, { bar: { querySelector: () => null } }]) {
		assert.equal(arrangeClassicSidebar(controller), false);
	}
	const classic = classicSidebar([
		{ name: "Overseas Cost Workbench", label: "海外成本核算" },
		{ name: "Assets", label: "资产" },
		{ name: "China Finance", label: "中国财务" },
		{ name: "Buying", label: "采购" },
	]);
	arrangeClassicSidebar(classic);
	assert.deepEqual(classic.container.children.map((button) => button.dataset.iconLabel), ["采购", "资产", "中国财务", "海外成本核算"]);
});

test("clones authorized workspace items before native nesting mutates them", () => {
	const cloneSidebarItems = productionFunction("cloneSidebarItems");
	const source = [
		{ type: "Section Break", label: "Reports", child: 0 },
		{ type: "Link", label: "Purchase Order", child: 1, filters: '{"status":"Draft"}' },
	];
	const snapshot = structuredClone(source);

	const cloned = cloneSidebarItems(source);
	cloned[0].nested_items = [cloned[1]];
	cloned[1].parent = cloned[0];

	assert.deepEqual(source, snapshot);
	assert.notEqual(cloned, source);
	assert.notEqual(cloned[0], source[0]);
});

test("removes only Frappe runtime nesting fields from an already consumed sidebar", () => {
	const cloneSidebarItems = productionFunction("cloneSidebarItems");
	const section = { type: "Section Break", label: "Settings" };
	const child = { type: "Link", label: "Buying Settings" };
	section.nested_items = [child];
	child.parent = section;

	const cloned = cloneSidebarItems([section, child]);

	assert.deepEqual(
		cloned.map((item) => item.label),
		["Settings", "Buying Settings"]
	);
	assert.equal(Object.hasOwn(cloned[0], "nested_items"), false);
	assert.equal(Object.hasOwn(cloned[1], "parent"), false);
	assert.equal(section.nested_items[0], child, "the live Frappe data remains untouched");
});

test("snapshots every authorized workspace before Frappe consumes the shared boot arrays", () => {
	const cloneWorkspaceSidebars = productionFunction("cloneWorkspaceSidebars");
	const source = {
		Buying: {
			label: "Buying",
			items: [{ type: "Link", label: "Purchase Order" }],
		},
		Manufacturing: {
			label: "Manufacturing",
			items: [{ type: "Link", label: "Work Order" }],
		},
	};

	const snapshot = cloneWorkspaceSidebars(source);
	source.Buying.items.splice(0);
	source.Manufacturing.items[0].label = "Mutated";

	assert.equal(snapshot.Buying.items[0].label, "Purchase Order");
	assert.equal(snapshot.Manufacturing.items[0].label, "Work Order");
	assert.notEqual(snapshot.Buying, source.Buying);
	assert.notEqual(snapshot.Buying.items, source.Buying.items);
});

test("counts authorized workspace items so a late-populated boot snapshot can replace an empty one", () => {
	const countWorkspaceSidebarItems = productionFunction("countWorkspaceSidebarItems");

	assert.equal(countWorkspaceSidebarItems(null), 0);
	assert.equal(
		countWorkspaceSidebarItems({
			Buying: { items: [{ label: "Home" }, { label: "Purchase Order" }] },
			Manufacturing: { items: [{ label: "Work Order" }] },
			Empty: {},
		}),
		3
	);
});

test("projects stale runtime layout onto the boot authorization whitelist", () => {
	const projectAuthorizedDesktopIcons = productionFunction("projectAuthorizedDesktopIcons");
	const authorized = [
		{ name: "Stock", label: "Stock", link: "/desk/stock", idx: 1, hidden: 0 },
		{ name: "Operations", label: "Operations", icon_type: "Folder", idx: 2, hidden: 0 },
		{ name: "Selling", label: "Selling", link: "/desk/selling", idx: 3, hidden: 0 },
	];
	const runtimeLayout = [
		{ name: "Unauthorized", label: "Unauthorized", link: "/desk/secret", idx: 0 },
		{
			name: "Stock",
			label: "Tampered Stock",
			link: "https://invalid.example",
			idx: 9,
			hidden: 1,
			parent_icon: "Operations",
		},
		{ name: "Operations", label: "Old Operations", idx: 1, hidden: 0 },
		{ name: "Stock", label: "Duplicate Stock", idx: 10 },
	];
	const authorizedSnapshot = structuredClone(authorized);

	const projected = projectAuthorizedDesktopIcons(authorized, runtimeLayout);

	assert.deepEqual(projected.map((icon) => icon.name), ["Stock", "Operations", "Selling"]);
	assert.equal(projected.some((icon) => icon.name === "Unauthorized"), false);
	assert.deepEqual(
		projected[0],
		{
			name: "Stock",
			label: "Stock",
			link: "/desk/stock",
			idx: 9,
			hidden: 1,
			parent_icon: "Operations",
		},
		"only layout fields may override trusted boot metadata"
	);
	assert.deepEqual(authorized, authorizedSnapshot, "the authorization whitelist must not be mutated");
});

test("projects unsaved edit-state order, visibility, and folder placement without admitting new icons", () => {
	const projectAuthorizedDesktopIcons = productionFunction("projectAuthorizedDesktopIcons");
	const authorized = [
		{ name: "Stock", label: "Stock", idx: 1, hidden: 0 },
		{ name: "Operations", label: "Operations", icon_type: "Folder", idx: 2, hidden: 0 },
		{ name: "Selling", label: "Selling", idx: 3, hidden: 0 },
	];
	const editState = [
		{ name: "Selling", label: "Selling", idx: 0, hidden: 1, parent_icon: "Operations" },
		{ name: "Draft Only", label: "Draft Only", idx: 1, hidden: 0 },
		{ name: "Operations", label: "Operations", idx: 2, hidden: 0 },
	];

	const projected = projectAuthorizedDesktopIcons(authorized, editState);

	assert.deepEqual(projected.map((icon) => icon.name), ["Selling", "Operations", "Stock"]);
	assert.deepEqual(
		projected.map(({ name, idx, hidden, parent_icon = "" }) => [name, idx, hidden, parent_icon]),
		[
			["Selling", 0, 1, "Operations"],
			["Operations", 2, 0, ""],
			["Stock", 1, 0, ""],
		]
	);
});

test("normalizes direct, refreshed, and sidebar-qualified Desk routes", () => {
	const normalizeRoute = productionFunction("normalizeRoute");
	const routesMatch = productionFunction("routesMatch");

	assert.deepEqual(normalizeRoute("https://erp.test/desk/stock/?sidebar=Stock#view"), {
		path: "/desk/stock",
		sidebar: "Stock",
	});
	assert.deepEqual(normalizeRoute("/desk"), { path: "/desk", sidebar: "" });
	assert.deepEqual(normalizeRoute("/desk/item?sidebar=China%20Finance"), {
		path: "/desk/item",
		sidebar: "China Finance",
	});
	assert.equal(routesMatch("/desk/ai-chat", "https://erp.test/desk/ai-chat?tab=1"), true);
	assert.equal(routesMatch("/desk/assignment-rule", "/desk/ai-chat"), false);
});

test("classifies only same-origin Desk links as internal navigation", () => {
	const isInternalDeskLink = productionFunction("isInternalDeskLink");
	const origin = "https://erp.test";

	assert.equal(isInternalDeskLink("/desk/china-finance", origin), true);
	assert.equal(isInternalDeskLink("https://erp.test/desk/buying?view=1", origin), true);
	assert.equal(isInternalDeskLink("/desk", origin), true);
	assert.equal(isInternalDeskLink("https://external.test/desk/buying", origin), false);
	assert.equal(isInternalDeskLink("/files/report.pdf", origin), false);
	assert.equal(isInternalDeskLink("mailto:help@example.com", origin), false);
	assert.equal(isInternalDeskLink("not a valid url", "not an origin"), false);
});

test("classic and DL sidebar clicks stay in the current tab without overriding external links", () => {
	const bind = productionFunction("bindInternalSidebarNavigation");
	const handlers = [];
	const document = { addEventListener: (...args) => handlers.push(args) };
	bind(document, "https://erp.test");
	assert.equal(handlers.length, 1);
	assert.equal(handlers[0][0], "click");
	assert.equal(handlers[0][2], true, "normalize before native bubbling router handlers");
	const click = handlers[0][1];
	for (const href of ["/desk/account/view/tree?sidebar=China%20Finance", "/desk/chart-of-accounts"]) {
		const link = new FakeElement();
		link.setAttribute("href", href);
		link.setAttribute("target", "_blank");
		click({ target: { closest: () => link } });
		assert.equal(link.getAttribute("target"), null);
		assert.equal(link.getAttribute("href"), href, "preserve native route and sidebar context");
	}
	const external = new FakeElement();
	external.setAttribute("href", "https://aflow.dingtalk.com/source");
	external.setAttribute("target", "_blank");
	click({ target: { closest: () => external } });
	assert.equal(external.getAttribute("target"), "_blank");
	click({ target: { closest: () => null } });
});

test("resolves query, exact route, and native sidebar active states in authority order", () => {
	const buildNavigationModel = productionFunction("buildNavigationModel");
	const desktopIcons = [
		{ label: "Operations", icon_type: "Folder" },
		{ label: "Stock", parent_icon: "Operations", navigation_route: "/desk/stock" },
		{ label: "Accounting", navigation_route: "/desk/accounting" },
		{ label: "Website", navigation_route: "/desk/website" },
	];
	const workspaceSidebars = {
		stock: { label: "Stock", items: [{ type: "Link", link_to: "Item" }] },
		accounting: { label: "Accounting", items: [{ type: "Link", link_to: "Account" }] },
	};

	const selectedLeaf = buildNavigationModel({
		desktopIcons,
		workspaceSidebars,
		route: "/desk/item?sidebar=Stock",
		currentSidebar: "Accounting",
	});
	assert.deepEqual(selectedLeaf.activePathLabels, ["Operations", "Stock"]);
	assert.equal(selectedLeaf.activeItem.label, "Stock");
	assert.equal(selectedLeaf.activeItem.isOpen, true);
	assert.equal(selectedLeaf.activeItem.isSelfActive, false);
	assert.equal(selectedLeaf.nativeHostKey, selectedLeaf.activeItem.key);
	assert.equal(selectedLeaf.activeItem.workspaceSidebar, workspaceSidebars.stock);
	assert.equal(
		selectedLeaf.items.find((item) => item.label === "Accounting").workspaceSidebar,
		workspaceSidebars.accounting,
		"inactive authorized modules retain their own sidebar data"
	);

	const exactModule = buildNavigationModel({
		desktopIcons,
		workspaceSidebars,
		route: "/desk/accounting/",
		currentSidebar: "Accounting",
		nativeLeafSelected: false,
	});
	assert.deepEqual(exactModule.activePathLabels, ["Accounting"]);
	assert.equal(exactModule.activeItem.isOpen, true);
	assert.equal(exactModule.activeItem.isSelfActive, true);

	const routeOnly = buildNavigationModel({
		desktopIcons,
		workspaceSidebars,
		route: "/desk/website",
		currentSidebar: "Stock",
	});
	assert.equal(routeOnly.activeItem.label, "Website");
	assert.equal(
		routeOnly.activeItem.isSelfActive,
		true,
		"an exact module route overrides stale native sidebar state and is blue"
	);
});

test("uses the authorized active leaf owner before a stale Frappe sidebar title", () => {
	const buildNavigationModel = productionFunction("buildNavigationModel");
	const desktopIcons = [
		{ label: "Buying", navigation_route: "/desk/buying" },
		{ label: "Manufacturing", navigation_route: "/desk/manufacturing" },
	];
	const workspaceSidebars = {
		buying: {
			label: "Buying",
			items: [{ type: "Link", label: "采购订单", link_to: "Purchase Order" }],
		},
		manufacturing: {
			label: "Manufacturing",
			items: [{ type: "Link", label: "物料清单", link_to: "BOM" }],
		},
	};

	const model = buildNavigationModel({
		desktopIcons,
		workspaceSidebars,
		route: "/desk/bom",
		currentSidebar: "Buying",
		nativeActiveItem: { label: "物料清单", href: "/desk/bom" },
		nativeLeafSelected: true,
	});

	assert.equal(model.activeItem.label, "Manufacturing");
	assert.equal(model.nativeHostKey, model.activeItem.key);
	assert.equal(model.activeItem.isOpen, true);
	assert.deepEqual(model.activePathLabels, ["Manufacturing"]);
});

test("uses the active leaf href to disambiguate repeated child labels", () => {
	const buildNavigationModel = productionFunction("buildNavigationModel");
	const desktopIcons = [
		{ label: "Buying", navigation_route: "/desk/buying" },
		{ label: "Manufacturing", navigation_route: "/desk/manufacturing" },
	];
	const workspaceSidebars = {
		buying: {
			label: "Buying",
			items: [{ type: "Link", label: "设置", link_to: "Buying Settings" }],
		},
		manufacturing: {
			label: "Manufacturing",
			items: [{ type: "Link", label: "设置", link_to: "Manufacturing Settings" }],
		},
	};

	const model = buildNavigationModel({
		desktopIcons,
		workspaceSidebars,
		route: "/desk/manufacturing-settings",
		currentSidebar: "Buying",
		nativeActiveItem: { label: "设置", href: "/desk/manufacturing-settings" },
		nativeLeafSelected: true,
	});

	assert.equal(model.activeItem.label, "Manufacturing");
	assert.equal(model.nativeHostKey, model.activeItem.key);
});

test("uses the translated native workspace heading when boot sidebar items are already consumed", () => {
	const buildNavigationModel = productionFunction("buildNavigationModel");
	const desktopIcons = [
		{ label: "Buying", translated_label: "采购", navigation_route: "/desk/buying" },
		{
			label: "Manufacturing",
			translated_label: "生产",
			navigation_route: "/desk/manufacturing",
		},
	];
	const workspaceSidebars = {
		buying: { label: "Buying", items: [] },
		manufacturing: { label: "Manufacturing", items: [] },
	};

	const model = buildNavigationModel({
		desktopIcons,
		workspaceSidebars,
		route: "/desk/bom",
		currentSidebar: "Buying",
		nativeWorkspaceLabel: "生产",
		nativeActiveItem: { label: "物料清单", href: "/desk/bom" },
		nativeLeafSelected: true,
	});

	assert.equal(model.activeItem.label, "Manufacturing");
	assert.equal(model.nativeHostKey, model.activeItem.key);
	assert.equal(model.activeItem.isOpen, true);
});

test("opens the full folder path and hosts live third-level items on the translated child workspace", () => {
	const buildNavigationModel = productionFunction("buildNavigationModel");
	const desktopIcons = [
		{ label: "DLP Framework", icon_type: "Folder" },
		{
			label: "Build",
			translated_label: "构建",
			parent_icon: "DLP Framework",
			navigation_route: "/desk/build",
		},
	];

	const model = buildNavigationModel({
		desktopIcons,
		workspaceSidebars: { build: { label: "Build", items: [] } },
		route: "/desk/page",
		currentSidebar: "DLP Framework",
		nativeWorkspaceLabel: "构建",
		nativeActiveItem: { label: "页面", href: "/desk/page" },
		nativeLeafSelected: true,
	});

	assert.deepEqual(model.activePathLabels, ["DLP Framework", "Build"]);
	assert.equal(model.items[0].isOpen, true);
	assert.equal(model.activeItem.isOpen, true);
	assert.equal(model.nativeHostKey, model.activeItem.key);
});

test("does not attach a stale native workspace to a newly routed module", () => {
	const buildNavigationModel = productionFunction("buildNavigationModel");
	const desktopIcons = [
		{ label: "DLP Framework", icon_type: "Folder" },
		{
			label: "Automation",
			parent_icon: "DLP Framework",
			navigation_route: "/desk/assignment-rule",
		},
		{ label: "AI Assistant", navigation_route: "/desk/ai-chat" },
	];
	const workspaceSidebars = {
		automation: {
			label: "Automation",
			items: [{ type: "Link", label: "分派规则", link_to: "Assignment Rule" }],
		},
		"ai assistant": {
			label: "AI Assistant",
			items: [{ type: "Link", label: "企业智能业务助手", route: "/desk/ai-chat" }],
		},
	};

	const model = buildNavigationModel({
		desktopIcons,
		workspaceSidebars,
		route: "/desk/ai-chat",
		currentSidebar: "DLP Framework",
		nativeWorkspaceLabel: "Automation",
		nativeActiveItem: { label: "分派规则", href: "/desk/assignment-rule" },
		nativeLeafSelected: true,
	});

	assert.equal(model.activeItem.label, "AI Assistant");
	assert.equal(model.activeItem.hasNativeChildren, true);
	assert.equal(
		model.nativeHostKey,
		"",
		"the stale Automation node must be parked while AI renders its authorized snapshot"
	);
});

test("selects an authorized snapshot leaf when the stale native node has no active row", () => {
	const buildNavigationModel = productionFunction("buildNavigationModel");
	const model = buildNavigationModel({
		desktopIcons: [
			{ label: "DLP Framework", icon_type: "Folder" },
			{
				label: "Automation",
				parent_icon: "DLP Framework",
				navigation_route: "/desk/assignment-rule",
			},
			{ label: "AI Assistant", navigation_route: "/desk/ai-chat" },
		],
		workspaceSidebars: {
			automation: {
				label: "Automation",
				items: [{ type: "Link", label: "分派规则", link_to: "Assignment Rule" }],
			},
			"ai assistant": {
				label: "AI Assistant",
				items: [
					{ type: "Link", label: "企业智能业务助手", route: "/desk/ai-chat" },
				],
			},
		},
		route: "/desk/ai-chat",
		nativeWorkspaceLabel: "Automation",
		nativeLeafSelected: false,
	});

	assert.equal(model.activeItem.label, "AI Assistant");
	assert.equal(model.activeItem.isSelfActive, false);
	assert.equal(model.nativeHostKey, "");
});

test("observes only the live native items and hidden native header for late Frappe updates", () => {
	const observeNativeSidebarChanges = productionFunction("observeNativeSidebarChanges");
	const nativeItems = { name: "items" };
	const nativeHeader = { name: "header" };
	const calls = [];
	const observer = {
		observe(target, options) {
			calls.push({ target, options });
		},
	};

	observeNativeSidebarChanges(observer, nativeItems, nativeHeader);

	assert.deepEqual(calls, [
		{
			target: nativeItems,
			options: {
				attributes: true,
				attributeFilter: ["class", "href"],
				childList: true,
				subtree: true,
			},
		},
		{
			target: nativeHeader,
			options: { characterData: true, childList: true, subtree: true },
		},
	]);
});

test("keeps an exact workspace parent open without blue when the native leaf is selected", () => {
	const buildNavigationModel = productionFunction("buildNavigationModel");
	const desktopIcons = [
		{
			label: "Organization",
			navigation_route: "/desk/company",
		},
	];
	const workspaceSidebars = {
		organization: {
			label: "Organization",
			items: [{ type: "Link", link_to: "Company" }],
		},
	};

	const model = buildNavigationModel({
		desktopIcons,
		workspaceSidebars,
		route: "/desk/company",
		currentSidebar: "Organization",
		nativeLeafSelected: true,
	});

	assert.equal(model.activeItem.label, "Organization");
	assert.equal(model.activeItem.isOpen, true);
	assert.equal(model.activeItem.isSelfActive, false);
	assert.equal(model.nativeHostKey, model.activeItem.key);
});

test("keeps a sidebar-qualified exact module route open without making its parent blue", () => {
	const buildNavigationModel = productionFunction("buildNavigationModel");
	const desktopIcons = [
		{
			label: "Stock",
			navigation_route: "/desk/item?sidebar=Stock",
		},
	];
	const workspaceSidebars = {
		stock: { label: "Stock", items: [{ type: "Link", link_to: "Item" }] },
	};

	const model = buildNavigationModel({
		desktopIcons,
		workspaceSidebars,
		route: "/desk/item?sidebar=Stock",
	});

	assert.equal(model.activeItem.label, "Stock");
	assert.equal(model.activeItem.isOpen, true);
	assert.equal(model.activeItem.isSelfActive, false);
	assert.equal(model.nativeHostKey, model.activeItem.key);
});

test("keeps the Desk assets separate from website CSS and loads the model before the lifecycle", () => {
	const hooks = fs.readFileSync(
		path.join(__dirname, "..", "deeplinkerp_branding", "hooks.py"),
		"utf8"
	);
	const modelAsset = "/assets/deeplinkerp_branding/js/deeplinkerp_navigation.js";
	const interfaceModeAsset = "/assets/deeplinkerp_branding/js/deeplinkerp_interface_mode.js";
	const lifecycleAsset = "/assets/deeplinkerp_branding/js/deeplinkerp_branding.js";

	assert.match(
		hooks,
		/app_include_css\s*=\s*\[\s*"\/assets\/deeplinkerp_branding\/css\/deeplinkerp_navigation\.css\?v=0\.0\.10"/
	);
	assert.ok(hooks.indexOf(modelAsset) < hooks.indexOf(interfaceModeAsset));
	assert.ok(hooks.indexOf(interfaceModeAsset) < hooks.indexOf(lifecycleAsset));
	assert.match(hooks, /deeplinkerp_navigation\.js\?v=0\.0\.21/);
	assert.match(hooks, /deeplinkerp_interface_mode\.js\?v=0\.0\.3/);
  assert.match(hooks, /deeplinkerp_branding\.js\?v=0\.0\.30/);
	assert.match(hooks, /web_include_css\s*=\s*"\/assets\/deeplinkerp_branding\/css\/deeplinkerp_branding\.css"/);
});

test("mounts repeated route renders with one root and the same native sidebar node", () => {
	const replaceNavigationRoot = productionFunction("replaceNavigationRoot");
	const top = new FakeElement();
	const nativeItems = new FakeElement(["sidebar-items"]);
	top.appendChild(nativeItems);

	const firstRoot = replaceNavigationRoot(top, nativeItems, () => {
		const root = new FakeElement(["dlp-mes-navigation"]);
		root.appendChild(nativeItems);
		return root;
	});
	const secondRoot = replaceNavigationRoot(top, nativeItems, () => {
		const root = new FakeElement(["dlp-mes-navigation"]);
		root.appendChild(nativeItems);
		return root;
	});

	assert.notEqual(secondRoot, firstRoot);
	assert.equal(
		top.children.filter((child) => child.classList.contains("dlp-mes-navigation")).length,
		1
	);
	assert.equal(nativeItems.parentElement, secondRoot);
	assert.equal(secondRoot.children[0], nativeItems, "the live native node identity must be preserved");
});

test("claims the live native sidebar once instead of reusing a projected workspace branch", () => {
	const claimNativeSidebarItems = productionFunction("claimNativeSidebarItems");
	const nativeItems = { dataset: {} };
	const projectedItems = { dataset: {} };
	const queries = [];
	const sidebar = {
		querySelector(selector) {
			queries.push(selector);
			if (selector === '[data-dlp-native-sidebar-items="true"]') {
				return nativeItems.dataset.dlpNativeSidebarItems === "true" ? nativeItems : null;
			}
			if (selector === ":scope > .body-sidebar-top > .sidebar-items") return nativeItems;
			if (selector === ".sidebar-items:not(.dlp-mes-navigation__workspace-items)") {
				return projectedItems;
			}
			return null;
		},
	};

	assert.equal(claimNativeSidebarItems(sidebar), nativeItems);
	assert.equal(nativeItems.dataset.dlpNativeSidebarItems, "true");
	queries.length = 0;
	assert.equal(claimNativeSidebarItems(sidebar), nativeItems);
	assert.deepEqual(queries, ['[data-dlp-native-sidebar-items="true"]']);
});

test("custom mobile navigation links close through the native sidebar contract", () => {
	const bindNativeSidebarClose = productionFunction("bindNativeSidebarClose");
	const link = new FakeElement();
	let closeCalls = 0;
	bindNativeSidebarClose(link, {
		isNarrowViewport: () => true,
		closeSidebar: () => {
			closeCalls += 1;
		},
	});

	link.dispatch("click");
	assert.equal(closeCalls, 1);

	const desktopLink = new FakeElement();
	bindNativeSidebarClose(desktopLink, {
		isNarrowViewport: () => false,
		closeSidebar: () => {
			closeCalls += 1;
		},
	});
	desktopLink.dispatch("click");
	assert.equal(closeCalls, 1, "desktop navigation must not collapse the sidebar");
});

test("any routed parent toggles without navigation and records its disclosure state", () => {
	const bindNavigationBranchToggle = productionFunction("bindNavigationBranchToggle");
	const row = new FakeElement();
	const group = new FakeElement(["dlp-mes-navigation__group", "dlp-mes-navigation__group--open"]);
	const branch = new FakeElement();
	row.setAttribute("aria-expanded", "true");
	branch.hidden = false;
	const chevronStates = [];
	bindNavigationBranchToggle(row, {
		group,
		branch,
		route: "/desk/projects",
		itemIsOpen: true,
		updateChevron: (open) => chevronStates.push(open),
	});

	const collapseEvent = row.dispatch("click");
	assert.equal(collapseEvent.defaultPrevented, true);
	assert.equal(row.getAttribute("aria-expanded"), "false");
	assert.equal(branch.hidden, true);
	assert.equal(group.classList.contains("dlp-mes-navigation__group--open"), false);
	assert.equal(group.dataset.userCollapsed, "true");
	assert.equal(group.dataset.userExpanded, undefined);

	const expandEvent = row.dispatch("click");
	assert.equal(expandEvent.defaultPrevented, true);
	assert.equal(row.getAttribute("aria-expanded"), "true");
	assert.equal(branch.hidden, false);
	assert.equal(group.classList.contains("dlp-mes-navigation__group--open"), true);
	assert.equal(group.dataset.userCollapsed, undefined);
	assert.equal(group.dataset.userExpanded, "true");
	assert.deepEqual(chevronStates, [false, true]);
});

test("inactive routed parent expands in place instead of navigating or closing mobile sidebar", () => {
	const bindNavigationRowInteractions = productionFunction("bindNavigationRowInteractions");
	const row = new FakeElement();
	row.setAttribute("aria-expanded", "false");
	const group = new FakeElement(["dlp-mes-navigation__group"]);
	const branch = new FakeElement();
	branch.hidden = true;
	let closeCalls = 0;
	bindNavigationRowInteractions(row, {
		group,
		branch,
		route: "/desk/projects",
		itemIsOpen: false,
		updateChevron: () => {},
		isNarrowViewport: () => true,
		closeSidebar: () => {
			closeCalls += 1;
		},
	});

	const event = row.dispatch("click");
	assert.equal(event.defaultPrevented, true);
	assert.equal(row.getAttribute("aria-expanded"), "true");
	assert.equal(branch.hidden, false);
	assert.equal(closeCalls, 0);
});

test("current mobile routed parent toggles before close and keeps the sidebar expanded", () => {
	const bindNavigationRowInteractions = productionFunction("bindNavigationRowInteractions");
	const row = new FakeElement();
	row.setAttribute("aria-expanded", "true");
	const group = new FakeElement([
		"dlp-mes-navigation__group",
		"dlp-mes-navigation__group--open",
	]);
	const branch = new FakeElement();
	let closeCalls = 0;
	bindNavigationRowInteractions(row, {
		group,
		branch,
		route: "/desk/projects",
		itemIsOpen: true,
		updateChevron: () => {},
		isNarrowViewport: () => true,
		closeSidebar: () => {
			closeCalls += 1;
		},
	});

	const event = row.dispatch("click");
	assert.equal(event.defaultPrevented, true);
	assert.equal(branch.hidden, true);
	assert.equal(closeCalls, 0);
});

test("mobile routed leaf still closes through the native sidebar contract", () => {
	const bindNavigationRowInteractions = productionFunction("bindNavigationRowInteractions");
	const row = new FakeElement();
	let closeCalls = 0;
	bindNavigationRowInteractions(row, {
		route: "/desk/todo",
		isNarrowViewport: () => true,
		closeSidebar: () => {
			closeCalls += 1;
		},
	});

	const event = row.dispatch("click");
	assert.equal(event.defaultPrevented, false);
	assert.equal(closeCalls, 1);
});

test("desktop routed navigation neither closes the sidebar nor loses expanded on rerender", () => {
	const bindNavigationRowInteractions = productionFunction("bindNavigationRowInteractions");
	const rememberDesktopSidebarExpansion = productionFunction("rememberDesktopSidebarExpansion");
	const restoreDesktopSidebarExpansion = productionFunction("restoreDesktopSidebarExpansion");
	const oldNavigation = new FakeElement(["dlp-mes-navigation"]);
	const sidebarContainer = new FakeElement(["body-sidebar-container", "expanded"]);
	const row = new FakeElement();
	let closeCalls = 0;
	bindNavigationRowInteractions(row, {
		route: "/desk/buying",
		itemIsOpen: false,
		isNarrowViewport: () => false,
		closeSidebar: () => {
			closeCalls += 1;
		},
		preserveDesktopExpansion: () =>
			rememberDesktopSidebarExpansion(oldNavigation, sidebarContainer),
	});

	const event = row.dispatch("click");
	assert.equal(event.defaultPrevented, false);
	assert.equal(closeCalls, 0);
	assert.equal(oldNavigation.dataset.restoreSidebarExpanded, "true");

	sidebarContainer.classList.remove("expanded");
	assert.equal(
		restoreDesktopSidebarExpansion(oldNavigation, sidebarContainer, false),
		true
	);
	assert.equal(sidebarContainer.classList.contains("expanded"), true);
	assert.equal(
		restoreDesktopSidebarExpansion(oldNavigation, sidebarContainer, true),
		false,
		"narrow viewport must retain Frappe's mobile collapsed state"
	);
});

test("rerender preserves multiple open groups, explicit collapse, and fresh-load reset", () => {
	const collectDisclosureState = productionFunction("collectDisclosureState");
	const resolveItemOpen = productionFunction("resolveItemOpen");
	const buyingKey = "buying:0";
	const manufacturingKey = "manufacturing:1";
	const projectsKey = "projects:2";
	const accountingKey = "accounting:3";
	const oldNavigation = new FakeElement(["dlp-mes-navigation"]);
	const oldBuying = new FakeElement(["dlp-mes-navigation__group", "dlp-mes-navigation__group--open"]);
	oldBuying.dataset.navigationKey = buyingKey;
	oldBuying.dataset.userExpanded = "true";
	oldNavigation.appendChild(oldBuying);
	const oldManufacturing = new FakeElement(["dlp-mes-navigation__group", "dlp-mes-navigation__group--open"]);
	oldManufacturing.dataset.navigationKey = manufacturingKey;
	oldManufacturing.dataset.userExpanded = "true";
	oldNavigation.appendChild(oldManufacturing);
	const oldProjects = new FakeElement(["dlp-mes-navigation__group"]);
	oldProjects.dataset.navigationKey = projectsKey;
	oldProjects.dataset.userCollapsed = "true";
	oldNavigation.appendChild(oldProjects);
	const oldAccounting = new FakeElement([
		"dlp-mes-navigation__group",
		"dlp-mes-navigation__group--open",
	]);
	oldAccounting.dataset.navigationKey = accountingKey;
	oldNavigation.appendChild(oldAccounting);

	const state = collectDisclosureState(oldNavigation);
	assert.deepEqual([...state.expandedKeys], [buyingKey, manufacturingKey, accountingKey]);
	assert.deepEqual([...state.collapsedKeys], [projectsKey]);
	assert.equal(resolveItemOpen({ key: buyingKey, isOpen: false }, state), true);
	assert.equal(resolveItemOpen({ key: manufacturingKey, isOpen: false }, state), true);
	assert.equal(
		resolveItemOpen({ key: projectsKey, isOpen: true }, state),
		false,
		"explicit collapse survives an asynchronous rerender on the same route"
	);
	assert.equal(
		resolveItemOpen({ key: "quality:3", isOpen: true }, state),
		true,
		"a newly routed active module opens without closing other groups"
	);
	assert.equal(
		resolveItemOpen({ key: accountingKey, isOpen: false }, state),
		true,
		"an automatically open module survives same-page asynchronous correction"
	);

	const freshState = collectDisclosureState(null);
	assert.equal(resolveItemOpen({ key: buyingKey, isOpen: false }, freshState), false);
	assert.equal(resolveItemOpen({ key: projectsKey, isOpen: true }, freshState), true);
});

test("synchronizes the live native sidebar before resolving navigation ownership", () => {
	const lifecycle = fs.readFileSync(
		path.join(
			__dirname,
			"..",
			"deeplinkerp_branding",
			"public",
			"js",
			"deeplinkerp_branding.js"
		),
		"utf8"
	);
	const start = lifecycle.indexOf("function renderPersistentNavigation()");
	const end = lifecycle.indexOf("function enhanceDesktopIcons()", start);
	const renderSource = lifecycle.slice(start, end);

	assert.ok(start >= 0 && end > start);
	assert.match(renderSource, /updateRenderedSidebarActive\(nativeItems\)/);
	assert.ok(
		renderSource.indexOf("updateRenderedSidebarActive(nativeItems)") <
			renderSource.indexOf("DeepLinkERPNavigation.buildNavigationModel")
	);
});

test("integrates the executable lifecycle helpers through the existing single router binding", () => {
	const lifecycle = fs.readFileSync(
		path.join(
			__dirname,
			"..",
			"deeplinkerp_branding",
			"public",
			"js",
			"deeplinkerp_branding.js"
		),
		"utf8"
	);

	assert.equal((lifecycle.match(/frappe\.router\.on\("change"/g) || []).length, 1);
	assert.match(lifecycle, /function renderPersistentNavigation\(/);
	assert.match(lifecycle, /disableDLEnhancements\(\);\s*DeepLinkERPNavigation\.arrangeClassicSidebar\(window\.CustomFiltersRightSidebar\)/);
	assert.match(lifecycle, /refreshDeskEnhancements[\s\S]*renderPersistentNavigation\(\)/);
	assert.match(lifecycle, /projectAuthorizedDesktopIcons\(/);
	assert.match(lifecycle, /replaceNavigationRoot\(/);
	assert.match(lifecycle, /claimNativeSidebarItems\(sidebar\)/);
	assert.match(lifecycle, /bindRenderedSidebarLinks\(nativeItems\)/);
	assert.match(lifecycle, /bindNativeSidebarClose\(/);
	assert.match(lifecycle, /collectDisclosureState\(/);
	assert.match(lifecycle, /resolveItemOpen\(/);
	assert.match(lifecycle, /bindNavigationRowInteractions\(/);
	assert.match(lifecycle, /restoreDesktopSidebarExpansion\(/);
	assert.match(lifecycle, /function getAuthorizedWorkspaceSidebars\(/);
	assert.match(lifecycle, /let authorizedWorkspaceSidebarSnapshot\s*=\s*null/);
	assert.match(lifecycle, /countWorkspaceSidebarItems\(currentSidebars\)/);
	assert.match(lifecycle, /currentItemCount\s*>\s*snapshotItemCount/);
	assert.match(
		lifecycle,
		/cloneWorkspaceSidebars\(\s*currentSidebars/
	);
	assert.match(lifecycle, /workspaceSidebars:\s*getAuthorizedWorkspaceSidebars\(\)/);
	assert.match(lifecycle, /matchMedia\("\(max-width: 767\.98px\)"\)\.matches/);
	assert.match(
		lifecycle,
		/nativeLeafSelected:\s*Boolean\(nativeItems\.querySelector\("\.active-sidebar"\)\)/
	);
	assert.match(lifecycle, /function getNativeActiveItem\(nativeItems\)/);
	assert.match(lifecycle, /nativeActiveItem:\s*getNativeActiveItem\(nativeItems\)/);
	assert.match(lifecycle, /translated_label:\s*translate\(icon\.label\)/);
	assert.match(
		lifecycle,
		/nativeWorkspaceLabel:\s*sidebar\.querySelector\("\.sidebar-header \.header-title"\)/
	);
	assert.match(lifecycle, /function bindNativeSidebarObserver\(sidebar, nativeItems\)/);
	assert.match(lifecycle, /new MutationObserver\(scheduleNativeSidebarRefresh\)/);
	assert.match(lifecycle, /observeNativeSidebarChanges\(/);
	assert.match(lifecycle, /bindNativeSidebarObserver\(sidebar, nativeItems\)/);
	assert.match(lifecycle, /disconnectNativeSidebarObserver\(\)/);
	assert.match(lifecycle, /function updateRenderedSidebarActive\(container\)/);
	assert.match(lifecycle, /DeepLinkERPNavigation\.routesMatch\(/);
	assert.match(lifecycle, /classList\.toggle\("active-sidebar", isCurrent\)/);
	assert.match(
		lifecycle,
		/translate\(DeepLinkERPNavigation\.getNavigationDisplayLabel\(item\)\)/
	);
	assert.match(lifecycle, /function normalizeRenderedSidebarTarget\(link\)/);
	assert.match(lifecycle, /DeepLinkERPNavigation\.isInternalDeskLink\(/);
	assert.match(lifecycle, /link\.removeAttribute\("target"\)/);
	assert.match(lifecycle, /function bindDeskEvents\(\)[\s\S]*bindInternalSidebarNavigation\(document, window\.location\.origin\)/);
	assert.match(
		lifecycle,
		/function bindRenderedSidebarLinks\(container\)[\s\S]*normalizeRenderedSidebarTarget\(link\)/
	);
	assert.doesNotMatch(lifecycle, /cloneNode\(/);
});

test("keeps a dedicated DL brand entry instead of mutating Frappe's route header", () => {
	const lifecycle = fs.readFileSync(
		path.join(
			__dirname,
			"..",
			"deeplinkerp_branding",
			"public",
			"js",
			"deeplinkerp_branding.js"
		),
		"utf8"
	);

	assert.match(lifecycle, /function ensureBrandHeader\(sidebar\)/);
	assert.match(lifecycle, /sidebar\.prepend\(fallback\)/);
	assert.match(lifecycle, /fallbackHeader\?\.remove\(\)/);
	assert.doesNotMatch(lifecycle, /nativeHeader\.querySelector\("\.header-title"\)/);
	assert.doesNotMatch(lifecycle, /nativeHeader\.querySelector\("\.header-logo"\)/);
});

test("renders every authorized workspace branch through Frappe's native sidebar renderer", () => {
	const lifecycle = fs.readFileSync(
		path.join(
			__dirname,
			"..",
			"deeplinkerp_branding",
			"public",
			"js",
			"deeplinkerp_branding.js"
		),
		"utf8"
	);

	assert.match(lifecycle, /item\.children\.length\s*>\s*0\s*\|\|\s*item\.hasNativeChildren/);
	assert.match(lifecycle, /document\.createElement\(hasChildren\s*\?\s*"button"\s*:/);
	assert.match(lifecycle, /row\.type\s*=\s*"button"/);
	assert.match(lifecycle, /frappe\.ui\.Sidebar\.prototype\.find_nested_items\.call\(/);
	assert.match(lifecycle, /frappe\.app\.sidebar\.make_sidebar_item\(\{/);
	assert.match(lifecycle, /renderWorkspaceSidebarBranch\(item, branch\)/);
	assert.doesNotMatch(lifecycle, /generate_route\(/);
	assert.doesNotMatch(lifecycle, /switch\s*\([^)]*link_type/);
	assert.doesNotMatch(lifecycle, /\.cloneNode\(/);
});

test("scopes the dark shell, full-row active state, focus ring, and mobile overflow protection", () => {
	const stylesheetPath = path.join(
		__dirname,
		"..",
		"deeplinkerp_branding",
		"public",
		"css",
		"deeplinkerp_navigation.css"
	);
	assert.equal(fs.existsSync(stylesheetPath), true, "the Desk-only navigation stylesheet must exist");
	const stylesheet = fs.readFileSync(stylesheetPath, "utf8");

	assert.match(stylesheet, /body\.dlp-mes-navigation-enabled/);
	assert.match(stylesheet, /#001529/i);
	assert.match(stylesheet, /#1677ff/i);
	assert.match(stylesheet, /:focus-visible/);
	assert.match(stylesheet, /@media\s*\(max-width:/);
	assert.match(stylesheet, /overflow-x:\s*hidden/);
	const openParent = stylesheet.match(
		/\.dlp-mes-navigation__group--open\s*>\s*\.dlp-mes-navigation__row\s*\{([^}]*)\}/
	)?.[1];
	const levelOne = stylesheet.match(
		/\.dlp-mes-navigation\s*>\s*\.dlp-mes-navigation__group\s*>\s*\.dlp-mes-navigation__row\s*\{([^}]*)\}/
	)?.[1];
	const levelTwo = stylesheet.match(
		/\.dlp-mes-navigation\s*>\s*\.dlp-mes-navigation__group\s*>\s*\.dlp-mes-navigation__children\s*\{([^}]*)\}/
	)?.[1];
	const activeItem = stylesheet.match(
		/\.dlp-mes-navigation__group--self-active\s*>\s*\.dlp-mes-navigation__row\s*\{([^}]*)\}/
	)?.[1];
	assert.match(levelOne || "", /background:\s*#001b33/i);
	assert.match(levelTwo || "", /background:\s*#000f1c/i);
	assert.match(openParent || "", /background:\s*#001b33/i);
	assert.match(openParent || "", /color:\s*#fff/i);
	assert.match(activeItem || "", /background:\s*#1677ff/i);
	assert.match(activeItem || "", /color:\s*#fff/i);
	const supersededCustomFiltersSidebar = stylesheet.match(
		/body\.dlp-mes-navigation-enabled\s+\.custom-filters-right-sidebar-container\s*,\s*body\.dlp-mes-navigation-enabled\s+\.custom-filters-right-sidebar-flyout\s*\{([^}]*)\}/
	)?.[1];
	assert.match(supersededCustomFiltersSidebar || "", /display:\s*none\s*!important/);
	const chevron = stylesheet.match(/\.dlp-mes-navigation__chevron\s*\{([^}]*)\}/)?.[1];
	const chevronSvg = stylesheet.match(/\.dlp-mes-navigation__chevron svg\s*\{([^}]*)\}/)?.[1];
	const activeChevron = stylesheet.match(
		/\.dlp-mes-navigation__group--open\s*>\s*\.dlp-mes-navigation__row\s+\.dlp-mes-navigation__chevron\s*,\s*body\.dlp-mes-navigation-enabled[^{}]*\.dlp-mes-navigation__group--self-active\s*>\s*\.dlp-mes-navigation__row\s+\.dlp-mes-navigation__chevron\s*\{([^}]*)\}/
	)?.[1];
	assert.match(chevron || "", /color:\s*#d0dbe6/i);
	assert.match(chevron || "", /opacity:\s*1\s*!important/);
	assert.match(chevron || "", /width:\s*36px/);
	assert.match(chevron || "", /height:\s*36px/);
	assert.match(chevron || "", /flex:\s*0\s+0\s+36px/);
	assert.match(chevronSvg || "", /color:\s*inherit\s*!important/);
	assert.match(chevronSvg || "", /stroke:\s*currentColor\s*!important/i);
	assert.match(chevronSvg || "", /opacity:\s*1\s*!important/);
	assert.match(chevronSvg || "", /width:\s*18px/);
	assert.match(chevronSvg || "", /height:\s*18px/);
	assert.match(activeChevron || "", /color:\s*#fff/i);
	assert.match(
		stylesheet,
		/@media\s*\(min-width:\s*768px\)[\s\S]*body\.dlp-mes-navigation-enabled\s+\.body-sidebar-container\s*\{[^}]*display:\s*flex\s*!important/i
	);
});

test("uses the approved readable palette, pending shell, and thin transparent scrollbar", () => {
	const stylesheet = fs.readFileSync(
		path.join(
			__dirname,
			"..",
			"deeplinkerp_branding",
			"public",
			"css",
			"deeplinkerp_navigation.css"
		),
		"utf8"
	);
	const navigationRow = stylesheet.match(
		/body\.dlp-mes-navigation-enabled[^{}]*\.dlp-mes-navigation__row\s*\{([^}]*)\}/
	)?.[1];
	const nativeAnchor = stylesheet.match(
		/body\.dlp-mes-navigation-enabled[^{}]*\.sidebar-items\s+\.item-anchor\s*\{([^}]*)\}/
	)?.[1];

	assert.match(stylesheet, /html\.dlp-interface-mode-dl-pending\s+body::before/);
	assert.match(stylesheet, /html\.dlp-interface-mode-dl-pending\s+body::after/);
	assert.match(
		stylesheet,
		/html\.dlp-interface-mode-dl-pending[\s\S]*\.desktop-container[\s\S]*visibility:\s*hidden\s*!important/
	);
	assert.match(
		stylesheet,
		/html\.dlp-interface-mode-dl-pending\s+body\s+\.centered\.splash\s*\{[^}]*display:\s*none\s*!important/i
	);
	assert.match(navigationRow || "", /color:\s*#c1d0df/i);
	assert.match(nativeAnchor || "", /color:\s*#afbfce/i);
	assert.match(stylesheet, /\.standard-items-sections[\s\S]*#9fb4c8/i);
	assert.match(stylesheet, /\.avatar-name-email[\s\S]*#d8e3ed/i);
	assert.match(stylesheet, /scrollbar-width:\s*thin/);
	assert.match(stylesheet, /scrollbar-color:[^;]*transparent/);
	assert.match(
		stylesheet,
		/\.body-sidebar-top::-webkit-scrollbar\s*\{[^}]*width:\s*6px/i
	);
	assert.match(
		stylesheet,
		/\.body-sidebar-top::-webkit-scrollbar-track\s*\{[^}]*background:\s*transparent/i
	);
});

test("expands the root Desktop grid and keeps enhanced entries compact and horizontal", () => {
	const stylesheet = fs.readFileSync(
		path.join(
			__dirname,
			"..",
			"deeplinkerp_branding",
			"public",
			"css",
			"deeplinkerp_navigation.css"
		),
		"utf8"
	);
	const rootContainer = stylesheet.match(
		/body\.dlp-mes-navigation-enabled\s+\.desktop-container\s*>\s*\.icons-container\s*\{([^}]*)\}/
	)?.[1];
	const rootGrid = stylesheet.match(
		/body\.dlp-mes-navigation-enabled\s+\.desktop-container\s*>\s*\.icons-container\s*>\s*\.icons\s*\{([^}]*)\}/
	)?.[1];
	const enhancedEntry = stylesheet.match(
		/body\.dlp-mes-navigation-enabled\s+\.desktop-container\s*>\s*\.icons-container\s*>\s*\.icons\s*>\s*\.desktop-icon\.dlp-desktop-icon-enhanced\s*\{([^}]*)\}/
	)?.[1];
	const enhancedIcon = stylesheet.match(
		/body\.dlp-mes-navigation-enabled\s+\.desktop-icon\.dlp-desktop-icon-enhanced\s+\.dlp-desktop-line-icon\s*\{([^}]*)\}/
	)?.[1];

	assert.match(rootContainer || "", /width:\s*100%\s*!important/);
	assert.match(rootContainer || "", /flex:\s*1\s+1\s+auto/);
	assert.match(rootGrid || "", /display:\s*grid\s*!important/);
	assert.match(rootGrid || "", /grid-template-columns:\s*repeat\(auto-fill,\s*minmax\(180px,\s*1fr\)\)/);
	assert.match(rootGrid || "", /width:\s*100%\s*!important/);
	assert.match(enhancedEntry || "", /display:\s*flex\s*!important/);
	assert.match(enhancedEntry || "", /flex-direction:\s*row\s*!important/);
	assert.match(enhancedEntry || "", /width:\s*100%\s*!important/);
	assert.match(enhancedEntry || "", /height:\s*44px\s*!important/);
	assert.match(enhancedEntry || "", /min-height:\s*44px\s*!important/);
	assert.match(enhancedIcon || "", /width:\s*32px\s*!important/);
	assert.match(enhancedIcon || "", /height:\s*32px\s*!important/);
	assert.doesNotMatch(
		stylesheet,
		/body\.dlp-mes-navigation-enabled\s+\.desktop-container\s+\.desktop-icon\s*\{/
	);
});
