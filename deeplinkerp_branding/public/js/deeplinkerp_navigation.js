(function (root, factory) {
	const navigation = factory();
	if (typeof module === "object" && module.exports) {
		module.exports = navigation;
	}
	root.DeepLinkERPNavigation = navigation;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
	"use strict";

	const ICONS = Object.freeze({
		"dlp framework": "blocks",
		framework: "blocks",
		"ai assistant": "bot",
		"china finance": "file-text",
		organization: "network",
		accounting: "landmark",
		assets: "warehouse",
		buying: "tag",
		manufacturing: "factory",
		projects: "presentation",
		quality: "shield-check",
		selling: "briefcase",
		stock: "boxes",
		subcontracting: "repeat-2",
	});
	const ROOT_NAVIGATION_ORDER = Object.freeze([
		["organization", "组织"],
		["buying", "采购"],
		["selling", "销售"],
		["stock", "库存"],
		["manufacturing", "生产"],
		["assets", "资产"],
		["china finance", "中国财务", "财务"],
		["overseas costing", "overseas cost", "overseas cost workbench", "海外成本核算"],
		["projects", "项目"],
		["quality", "质量"],
		["subcontracting", "委外"],
		["accounting", "会计"],
		["ai assistant", "ai 助手"],
		["dlp framework"],
		["deeplinkerp settings", "deeplinkerp setting", "erpnext settings"],
	]);

	function normalizeIdentity(value) {
		return String(value || "")
			.trim()
			.toLowerCase()
			.replace(/[_-]+/g, " ")
			.replace(/\s+/g, " ");
	}

	function getNavigationIcon(label) {
		const normalized = normalizeIdentity(label);
		if (normalized.includes("setting")) return "settings";
		if (
			normalized.includes("overseas cost") ||
			normalized.includes("overseas costing") ||
			normalized.includes("海外成本") ||
			normalized.includes("综合成本")
		) {
			return "calculator";
		}
		return ICONS[normalized] || "layout-grid";
	}

	function desktopIconKey(icon) {
		return normalizeIdentity(icon && (icon.name || icon.label));
	}

	function prepareSidebarMenuItem(item) {
		if (!item || item.is_divider) return null;
		if (item.icon || item.icon_url) return item;
		return { ...item, icon: getNavigationIcon(item.label || item.name) };
	}

	function projectAuthorizedDesktopIcons(authorizedIcons, runtimeLayout) {
		const authorized = new Map();
		(authorizedIcons || []).forEach((icon) => {
			const key = desktopIconKey(icon);
			if (key && !authorized.has(key)) authorized.set(key, icon);
		});

		const projected = [];
		const included = new Set();
		(runtimeLayout || []).forEach((layoutIcon) => {
			const key = desktopIconKey(layoutIcon);
			const trustedIcon = authorized.get(key);
			if (!trustedIcon || included.has(key)) return;

			const icon = { ...trustedIcon };
			["idx", "hidden", "parent_icon"].forEach((field) => {
				if (Object.prototype.hasOwnProperty.call(layoutIcon, field)) {
					icon[field] = layoutIcon[field];
				}
			});
			projected.push(icon);
			included.add(key);
		});

		for (const [key, icon] of authorized) {
			if (!included.has(key)) projected.push({ ...icon });
		}
		return projected;
	}

	function replaceNavigationRoot(top, nativeItems, buildNavigation) {
		top.appendChild(nativeItems);
		Array.from(top.children)
			.filter((element) => element.classList.contains("dlp-mes-navigation"))
			.forEach((element) => element.remove());
		const navigation = buildNavigation(nativeItems);
		top.prepend(navigation);
		return navigation;
	}

	function claimNativeSidebarItems(sidebar) {
		if (!sidebar) return null;
		const nativeItems =
			sidebar.querySelector('[data-dlp-native-sidebar-items="true"]') ||
			sidebar.querySelector(":scope > .body-sidebar-top > .sidebar-items") ||
			sidebar.querySelector(".sidebar-items:not(.dlp-mes-navigation__workspace-items)");
		if (nativeItems) nativeItems.dataset.dlpNativeSidebarItems = "true";
		return nativeItems;
	}

	function observeNativeSidebarChanges(observer, nativeItems, nativeHeader) {
		observer.observe(nativeItems, {
			attributes: true,
			attributeFilter: ["class", "href"],
			childList: true,
			subtree: true,
		});
		if (nativeHeader) {
			observer.observe(nativeHeader, {
				characterData: true,
				childList: true,
				subtree: true,
			});
		}
		return observer;
	}

	function bindNativeSidebarClose(
		link,
		{ isNarrowViewport, closeSidebar, preserveDesktopExpansion = () => {} }
	) {
		link.addEventListener("click", (event) => {
			if (event.defaultPrevented) return;
			if (isNarrowViewport()) closeSidebar();
			else preserveDesktopExpansion();
		});
	}

	function bindNavigationRowInteractions(
		row,
		{
			group,
			branch,
			route = "",
			updateChevron,
			isNarrowViewport,
			closeSidebar,
			preserveDesktopExpansion,
		}
	) {
		if (branch) {
			bindNavigationBranchToggle(row, {
				group,
				branch,
				updateChevron,
			});
			return;
		}
		if (route) {
			bindNativeSidebarClose(row, {
				isNarrowViewport,
				closeSidebar,
				preserveDesktopExpansion,
			});
		}
	}

	function rememberDesktopSidebarExpansion(navigation, sidebarContainer) {
		if (!navigation || !sidebarContainer?.classList.contains("expanded")) return false;
		navigation.dataset.restoreSidebarExpanded = "true";
		return true;
	}

	function restoreDesktopSidebarExpansion(
		navigation,
		sidebarContainer,
		isNarrowViewport
	) {
		const shouldRestore = Boolean(
			navigation?.dataset.restoreSidebarExpanded === "true" &&
				sidebarContainer &&
				!isNarrowViewport
		);
		if (shouldRestore) sidebarContainer.classList.add("expanded");
		return shouldRestore;
	}

	function collectDisclosureKeys(navigation, attribute) {
		if (!navigation) return new Set();
		return new Set(
			Array.from(
				navigation.querySelectorAll(
					`.dlp-mes-navigation__group[data-${attribute}="true"]`
				)
			)
				.map((group) => group.dataset.navigationKey)
				.filter(Boolean)
		);
	}

	function collectDisclosureState(navigation) {
		const collapsedKeys = collectDisclosureKeys(navigation, "user-collapsed");
		const expandedKeys = collectDisclosureKeys(navigation, "user-expanded");
		if (navigation) {
			Array.from(navigation.querySelectorAll(".dlp-mes-navigation__group--open"))
				.map((group) => group.dataset.navigationKey)
				.filter((key) => key && !collapsedKeys.has(key))
				.forEach((key) => expandedKeys.add(key));
		}
		return {
			expandedKeys,
			collapsedKeys,
		};
	}

	function resolveItemOpen(item, disclosureState = {}) {
		if (disclosureState.collapsedKeys?.has(item.key)) return false;
		if (disclosureState.expandedKeys?.has(item.key)) return true;
		return Boolean(item.isOpen);
	}

	function bindNavigationBranchToggle(row, { group, branch, updateChevron }) {
		row.addEventListener("click", (event) => {
			event.preventDefault();
			const open = row.getAttribute("aria-expanded") !== "true";
			row.setAttribute("aria-expanded", String(open));
			branch.hidden = !open;
			group.classList.toggle("dlp-mes-navigation__group--open", open);
			if (open) {
				delete group.dataset.userCollapsed;
				group.dataset.userExpanded = "true";
			} else {
				delete group.dataset.userExpanded;
				group.dataset.userCollapsed = "true";
			}
			updateChevron(open);
		});
	}

	function normalizePath(pathname) {
		let path = pathname || "/desk";
		try {
			path = decodeURIComponent(path);
		} catch (error) {
			// Keep malformed paths usable for matching instead of breaking navigation.
		}
		path = `/${String(path).replace(/^\/+/, "")}`.replace(/\/{2,}/g, "/");
		return path.length > 1 ? path.replace(/\/+$/, "") : path;
	}

	function normalizeRoute(route) {
		if (Array.isArray(route)) {
			return {
				path: normalizePath(`/desk/${route.filter(Boolean).join("/")}`),
				sidebar: "",
			};
		}

		let parsed;
		try {
			parsed = new URL(String(route || "/desk"), "https://deeplinkerp.invalid");
		} catch (error) {
			parsed = new URL("/desk", "https://deeplinkerp.invalid");
		}
		return {
			path: normalizePath(parsed.pathname),
			sidebar: parsed.searchParams.get("sidebar") || "",
		};
	}

	function isInternalDeskLink(href, origin) {
		if (!href || !origin) return false;
		try {
			const base = new URL(origin);
			const target = new URL(href, base);
			const path = normalizePath(target.pathname);
			return target.origin === base.origin && (path === "/desk" || path.startsWith("/desk/"));
		} catch (error) {
			return false;
		}
	}

	function bindInternalSidebarNavigation(document, origin) {
		// Frappe renders URL items with target=_blank, even for its own Desk.
		// Capture works for late-rendered classic and DL sidebars. Keep native
		// routing (including modifier-clicks) and all genuinely external links.
		document.addEventListener("click", (event) => {
			const link = event.target?.closest?.(".body-sidebar-container a.item-anchor");
			if (link && isInternalDeskLink(link.getAttribute("href"), origin)) {
				link.removeAttribute("target");
			}
		}, true);
	}

	function routesMatch(candidate, current) {
		if (!candidate) return false;
		return normalizeRoute(candidate).path === normalizeRoute(current).path;
	}

	function nodeAliases(node) {
		return [node.label, node.translated_label, node.name, node.link_to]
			.map(normalizeIdentity)
			.filter(Boolean);
	}

	function getNavigationDisplayLabel(node) {
		const aliases = nodeAliases(node);
		if (aliases.includes("china finance") || aliases.includes("中国财务")) return "Finance";
		return node?.translated_label || node?.label || node?.name || "";
	}

	const ROOT_NAVIGATION_PRIORITY = new Map(
		ROOT_NAVIGATION_ORDER.flatMap((aliases, priority) =>
			aliases.map((alias) => [normalizeIdentity(alias), priority])
		)
	);

	function arrangeNavigationRoots(roots) {
		return roots
			.map((node, sourceIndex) => ({
				node,
				sourceIndex,
				priority: Math.min(
					Number.POSITIVE_INFINITY,
					...nodeAliases(node)
						.map((alias) => ROOT_NAVIGATION_PRIORITY.get(alias))
						.filter((priority) => priority !== undefined)
				),
			}))
			.sort((left, right) =>
				left.priority === right.priority
					? left.sourceIndex - right.sourceIndex
					: left.priority - right.priority
			)
			.map(({ node }) => node);
	}

	function arrangeClassicSidebar(controller) {
		const container = controller?.bar?.querySelector(".custom-filters-right-sidebar-items");
		if (!container) return false;
		const buttons = Array.from(container.children);
		const icons = new Map((controller.items || []).map((icon) => [icon.label, icon]));
		const ordered = arrangeNavigationRoots(buttons.map((element) => ({
			...(icons.get(element.dataset.iconLabel) || { label: element.dataset.iconLabel }),
			element,
		}))).map((item) => item.element);
		if (ordered.every((button, index) => button === buttons[index])) return false;
		ordered.forEach((button) => container.appendChild(button));
		return true;
	}

	function buildNavigationTree(desktopIcons) {
		const visibleIcons = (desktopIcons || []).filter(
			(icon) => icon && icon.hidden !== 1 && icon.hidden !== "1"
		);
		const nodes = visibleIcons.map((icon, index) => ({
			...icon,
			key: `${normalizeIdentity(icon.name || icon.label) || "desktop-icon"}:${index}`,
			children: [],
		}));
		const parents = new Map();
		nodes.forEach((node) => {
			nodeAliases(node).forEach((alias) => {
				if (!parents.has(alias)) parents.set(alias, node);
			});
		});

		const roots = [];
		nodes.forEach((node) => {
			const parent = parents.get(normalizeIdentity(node.parent_icon));
			if (parent && parent !== node) {
				parent.children.push(node);
			} else {
				roots.push(node);
			}
		});
		return arrangeNavigationRoots(roots);
	}

	function cloneSidebarItems(items) {
		const source = Array.isArray(items) ? items : [];
		const sanitized = source.map((item) =>
			Object.fromEntries(
				Object.entries(item || {}).filter(
					([field]) => field !== "parent" && field !== "nested_items"
				)
			)
		);
		if (typeof structuredClone === "function") {
			try {
				return structuredClone(sanitized);
			} catch (error) {
				// Boot sidebar data is JSON-safe; fall back if a browser cannot clone it.
			}
		}
		return JSON.parse(JSON.stringify(sanitized));
	}

	function cloneWorkspaceSidebars(sidebars) {
		return Object.fromEntries(
			Object.entries(sidebars || {}).map(([key, sidebar]) => [
				key,
				{
					...(sidebar || {}),
					items: cloneSidebarItems(sidebar?.items),
				},
			])
		);
	}

	function countWorkspaceSidebarItems(sidebars) {
		return Object.values(sidebars || {}).reduce(
			(total, sidebar) => total + (Array.isArray(sidebar?.items) ? sidebar.items.length : 0),
			0
		);
	}

	function findPath(items, predicate, ancestors) {
		for (const item of items) {
			const path = [...ancestors, item];
			if (predicate(item)) return path;
			const nested = findPath(item.children, predicate, path);
			if (nested.length) return nested;
		}
		return [];
	}

	function findBySidebar(items, sidebar) {
		const wanted = normalizeIdentity(sidebar);
		if (!wanted) return [];
		return findPath(items, (item) => nodeAliases(item).includes(wanted), []);
	}

	function nodeRoute(node) {
		return node.navigation_route || node.route || node.link || node.url || "";
	}

	function findByExactRoute(items, route) {
		return findPath(items, (item) => {
			const candidate = nodeRoute(item);
			return candidate && normalizeRoute(candidate).path === route.path;
		}, []);
	}

	function sidebarItemAliases(item) {
		return [item?.label, item?.name, item?.link_to]
			.map(normalizeIdentity)
			.filter(Boolean);
	}

	function flattenSidebarItems(items) {
		const flattened = [];
		const visit = (entries) => {
			(Array.isArray(entries) ? entries : []).forEach((item) => {
				if (!item) return;
				flattened.push(item);
				visit(item.nested_items);
			});
		};
		visit(items);
		return flattened;
	}

	function sidebarItemMatchScore(item, nativeActiveItem, normalizedRoute) {
		const activeLabel = normalizeIdentity(nativeActiveItem?.label);
		const aliases = sidebarItemAliases(item);
		const labelMatch = Boolean(activeLabel && aliases.includes(activeLabel));
		const activeRoute = normalizeRoute(nativeActiveItem?.href || normalizedRoute.path);
		const explicitRoutes = [item?.route, item?.href, item?.link, item?.url]
			.filter(Boolean)
			.map((candidate) => normalizeRoute(candidate).path);
		const explicitRouteMatch = explicitRoutes.includes(activeRoute.path);
		const routeTail = normalizeIdentity(activeRoute.path.split("/").filter(Boolean).at(-1));
		const linkTargetMatch = Boolean(
			item?.link_to && routeTail && normalizeIdentity(item.link_to) === routeTail
		);

		if (explicitRouteMatch) return 100 + (labelMatch ? 5 : 0);
		if (linkTargetMatch) return 80 + (labelMatch ? 5 : 0);
		return labelMatch ? 10 : 0;
	}

	function findByNativeActiveItem(items, workspaceSidebars, nativeActiveItem, normalizedRoute) {
		if (!nativeActiveItem?.label && !nativeActiveItem?.href) return [];
		const candidates = [];
		const visit = (nodes) => {
			nodes.forEach((node) => {
				const sidebar = getWorkspaceSidebar(node, workspaceSidebars);
				const score = Math.max(
					0,
					...flattenSidebarItems(sidebar?.items).map((item) =>
						sidebarItemMatchScore(item, nativeActiveItem, normalizedRoute)
					)
				);
				if (score > 0) candidates.push({ node, score });
				visit(node.children);
			});
		};
		visit(items);

		const bestScore = Math.max(0, ...candidates.map((candidate) => candidate.score));
		const best = candidates.filter((candidate) => candidate.score === bestScore);
		if (best.length !== 1) return [];
		return findPath(items, (item) => item.key === best[0].node.key, []);
	}

	function getWorkspaceSidebar(node, workspaceSidebars) {
		const entries = Object.entries(workspaceSidebars || {});
		const aliases = nodeAliases(node);
		for (const [key, sidebar] of entries) {
			if (
				aliases.includes(normalizeIdentity(key)) ||
				aliases.includes(normalizeIdentity(sidebar && (sidebar.label || sidebar.title)))
			) {
				return sidebar;
			}
		}
		return null;
	}

	function buildNavigationModel({
		desktopIcons = [],
		workspaceSidebars = {},
		route = "/desk",
		currentSidebar = "",
		nativeWorkspaceLabel = "",
		nativeActiveItem = null,
		nativeLeafSelected = false,
	} = {}) {
		const items = buildNavigationTree(desktopIcons);
		const normalizedRoute = normalizeRoute(route);
		const nativeActivePath = findByNativeActiveItem(
			items,
			workspaceSidebars,
			nativeActiveItem,
			normalizedRoute
		);
		const nativeWorkspacePath = findBySidebar(items, nativeWorkspaceLabel);
		const currentSidebarPath = findBySidebar(items, currentSidebar);
		const nativeOwnerPath = nativeActivePath.length
			? nativeActivePath
			: nativeWorkspacePath;
		const nativeOwnerKey = nativeOwnerPath.at(-1)?.key || "";
		let activePath = findBySidebar(items, normalizedRoute.sidebar);
		if (!activePath.length) activePath = findByExactRoute(items, normalizedRoute);
		if (!activePath.length) activePath = nativeActivePath;
		if (!activePath.length) activePath = nativeWorkspacePath;
		if (!activePath.length) activePath = currentSidebarPath;

		const activeKeys = new Set(activePath.map((item) => item.key));
		const activeLeaf = activePath.at(-1) || null;
		const exactRoute = Boolean(
			activeLeaf &&
			nodeRoute(activeLeaf) &&
			normalizeRoute(nodeRoute(activeLeaf)).path === normalizedRoute.path
		);
		let activeItem = null;
		let nativeHostKey = "";

		function decorate(nodes) {
			nodes.forEach((node) => {
				decorate(node.children);
				const nativeSidebar = getWorkspaceSidebar(node, workspaceSidebars);
				const isActive = Boolean(activeLeaf && node.key === activeLeaf.key);
				const hasRoutedNativeLeaf = Boolean(
					nativeSidebar &&
						flattenSidebarItems(nativeSidebar.items).some(
							(item) =>
								sidebarItemMatchScore(
									item,
									{ href: normalizedRoute.path },
									normalizedRoute
								) >= 80
						)
				);
				const hasNativeChildren = Boolean(
					(nativeSidebar && nativeSidebar.items?.length) || (isActive && nativeLeafSelected)
				);
				node.workspaceSidebar = nativeSidebar;
				node.hasNativeChildren = hasNativeChildren;
				node.isOpen = activeKeys.has(node.key) && (node.children.length > 0 || hasNativeChildren);
				const hasSelectedNativeLeaf = Boolean(
					hasNativeChildren &&
						(normalizedRoute.sidebar ||
							hasRoutedNativeLeaf ||
							(nativeLeafSelected && nativeOwnerKey === node.key))
				);
				node.isSelfActive =
					isActive &&
					!hasSelectedNativeLeaf &&
					(exactRoute || (!node.children.length && !hasNativeChildren));
				if (isActive) {
					activeItem = node;
					if (hasNativeChildren && (!nativeOwnerKey || nativeOwnerKey === node.key)) {
						nativeHostKey = node.key;
					}
				}
			});
		}
		decorate(items);

		return {
			items,
			normalizedRoute,
			activePathLabels: activePath.map((item) => item.label),
			activeItem,
			nativeHostKey,
			exactRoute,
		};
	}

	return Object.freeze({
		arrangeClassicSidebar,
		bindNavigationBranchToggle,
		bindNavigationRowInteractions,
		bindNativeSidebarClose,
		bindInternalSidebarNavigation,
		buildNavigationModel,
		buildNavigationTree,
		claimNativeSidebarItems,
		cloneSidebarItems,
		cloneWorkspaceSidebars,
		countWorkspaceSidebarItems,
		collectDisclosureState,
		getNavigationDisplayLabel,
		getNavigationIcon,
		isInternalDeskLink,
		normalizeRoute,
		observeNativeSidebarChanges,
		projectAuthorizedDesktopIcons,
		prepareSidebarMenuItem,
		rememberDesktopSidebarExpansion,
		replaceNavigationRoot,
		resolveItemOpen,
		restoreDesktopSidebarExpansion,
		routesMatch,
	});
});
