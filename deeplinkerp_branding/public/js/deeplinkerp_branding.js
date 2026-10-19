(function () {
 const translateText = (text, args = []) => String((globalThis.__ || (value => value))(text)).replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
	const ERPNextBrandName = "ERPNext";
	const DeeplinkERPBrandName = "Deeplinkerp";
	const ERPNextSettingsName = "ERPNext Settings";
	const DeeplinkERPSettingsName = "Deeplinkerp Settings";
	const ProductAppNames = new Set(["ai_assistant", "mes_integration"]);
	const ProductSidebarTitles = new Set(["AI Assistant", "Mes Integration", "MES Integration", "MES Integration Log"]);
	const ProductSidebarSubtitles = new Set(["AI Assistant", "Mes Integration", "MES Integration", "MES Integration Log"]);
	const FrappeFrameworkName = "Frappe Framework";
	const DLPFrameworkName = "DLP Framework";
	const translate = (text) => (typeof window.__ === "function" ? window.__(text) : text);
	const getBrandName = () => translate(DeeplinkERPBrandName);
	const getSettingsName = () => translate(DeeplinkERPSettingsName);
	const getFrameworkName = () => translate(DLPFrameworkName);
	const FaviconURL = "/assets/deeplinkerp_branding/logo/tab_logo.svg?v=0.0.6";
	const BrandLogoURL = "/assets/deeplinkerp_branding/logo/deeplinkerp_logo_radius.png?v=0.0.6";
	const isNarrowNavigationViewport = () =>
		typeof window.matchMedia === "function"
			? window.matchMedia("(max-width: 767.98px)").matches
			: window.innerWidth < 768;
	let eventsBound = false;
	let authorizedWorkspaceSidebarSnapshot = null;
	let nativeSidebarObserver = null;
	let observedNativeSidebarItems = null;
	let observedNativeSidebarHeader = null;
	let nativeSidebarRefreshTimer = null;

	function setFavicon() {
		document
			.querySelectorAll("link[rel~='icon'], link[rel='shortcut icon'], link[rel='apple-touch-icon']")
			.forEach((link) => link.remove());

		const favicon = document.createElement("link");
		favicon.rel = "icon";
		favicon.type = "image/svg+xml";
		favicon.href = FaviconURL;
		document.head.appendChild(favicon);

		const shortcut = document.createElement("link");
		shortcut.rel = "shortcut icon";
		shortcut.type = "image/svg+xml";
		shortcut.href = FaviconURL;
		document.head.appendChild(shortcut);
	}

	function replaceERPNextAppTitle() {
		if (!window.frappe?.boot) return;

		(frappe.boot.app_data || []).forEach((app) => {
			const brandName = getBrandName();
			const frameworkName = getFrameworkName();
			if (app.app_name === "erpnext" || app.app_title === ERPNextBrandName || app.app_title === DeeplinkERPBrandName) {
				if (app.app_title !== brandName) app.app_title = brandName;
			}
			if (app.app_name === "frappe" || app.app_title === FrappeFrameworkName || app.app_title === DLPFrameworkName) {
				if (app.app_title !== frameworkName) app.app_title = frameworkName;
			}
			if (ProductAppNames.has(app.app_name)) {
				if (app.app_title !== brandName) app.app_title = brandName;
			}
		});

		const brandName = getBrandName();
		const frameworkName = getFrameworkName();
		if (frappe.current_app?.app_name === "erpnext") {
			if (frappe.current_app.app_title !== brandName) frappe.current_app.app_title = brandName;
		}
		if (frappe.current_app?.app_name === "frappe") {
			if (frappe.current_app.app_title !== frameworkName) frappe.current_app.app_title = frameworkName;
		}
		if (ProductAppNames.has(frappe.current_app?.app_name)) {
			if (frappe.current_app.app_title !== brandName) frappe.current_app.app_title = brandName;
		}
	}

	function replaceBootLabels() {
		if (!window.frappe?.boot) return;

		const settingsSidebar = frappe.boot.workspace_sidebar_item?.["erpnext settings"];
		const settingsName = getSettingsName();
		if (settingsSidebar) {
			if (settingsSidebar.label !== settingsName) settingsSidebar.label = settingsName;
			if (settingsSidebar.title !== settingsName) settingsSidebar.title = settingsName;
		}

		Object.entries(frappe.boot.workspace_sidebar_item || {}).forEach(([key, sidebar]) => {
			if (ProductSidebarTitles.has(sidebar.label) || ProductSidebarTitles.has(sidebar.module) || ProductSidebarTitles.has(key)) {
				sidebar.app = sidebar.app || (sidebar.label === "AI Assistant" ? "ai_assistant" : "mes_integration");
			}
		});

		frappe.boot.module_app = frappe.boot.module_app || {};
		Object.assign(frappe.boot.module_app, {
			"ai assistant": "ai_assistant",
			ai_assistant: "ai_assistant",
			"mes integration": "mes_integration",
			mes_integration: "mes_integration",
			"mes integration log": "mes_integration",
			mes_integration_log: "mes_integration",
		});

		(frappe.boot.workspaces?.pages || []).forEach((workspace) => {
			if (
				workspace.name === ERPNextSettingsName ||
				workspace.label === ERPNextSettingsName ||
				workspace.title === ERPNextSettingsName
			) {
				if (workspace.label !== settingsName) workspace.label = settingsName;
				if (workspace.title !== settingsName) workspace.title = settingsName;
			}
		});
	}

	function addReplacement(replacements, source, target) {
		if (source && target && source !== target) {
			replacements[source] = target;
		}
	}

	function replaceTextContent(selector, replacements) {
		document.querySelectorAll(selector).forEach((element) => {
			const text = element.textContent.trim();
			if (replacements[text] && replacements[text] !== text) {
				element.textContent = replacements[text];
			}
		});
	}

	function replaceDisplayAttributes(replacements, selector = "[title], [data-original-title], [aria-label], [alt]") {
		const attributes = ["title", "data-original-title", "aria-label", "alt"];
		document.querySelectorAll(selector).forEach((element) => {
			attributes.forEach((attribute) => {
				const value = element.getAttribute(attribute);
				if (replacements[value] && replacements[value] !== value) {
					element.setAttribute(attribute, replacements[value]);
				}
			});
		});
	}

	function replaceVisibleBranding() {
		const replacements = {};
		const brandName = getBrandName();
		const settingsName = getSettingsName();
		const frameworkName = getFrameworkName();
		addReplacement(replacements, ERPNextBrandName, brandName);
		addReplacement(replacements, DeeplinkERPBrandName, brandName);
		addReplacement(replacements, ERPNextSettingsName, settingsName);
		addReplacement(replacements, DeeplinkERPSettingsName, settingsName);
		addReplacement(replacements, FrappeFrameworkName, frameworkName);
		addReplacement(replacements, DLPFrameworkName, frameworkName);
		const inventoryLabels = {};
		[
			"物料库存明细", "半成品库存明细", "成品库存明细", "模具库存明细",
		].forEach((label) => addReplacement(inventoryLabels, label, translate(label)));
		replaceTextContent(".body-sidebar-container .sidebar-item-label", inventoryLabels);
		replaceDisplayAttributes(inventoryLabels, ".body-sidebar-container [title], .body-sidebar-container [aria-label]");

		if (window.frappe?.app?.sidebar?.header_subtitle === FrappeFrameworkName || window.frappe?.app?.sidebar?.header_subtitle === DLPFrameworkName) {
			if (frappe.app.sidebar.header_subtitle !== frameworkName) frappe.app.sidebar.header_subtitle = frameworkName;
		}
		if (ProductSidebarSubtitles.has(window.frappe?.app?.sidebar?.header_subtitle)) {
			if (frappe.app.sidebar.header_subtitle !== brandName) frappe.app.sidebar.header_subtitle = brandName;
		}

		document.querySelectorAll(".title-container").forEach((container) => {
			const title = container.querySelector(".header-title")?.textContent.trim();
			const subtitle = container.querySelector(".header-subtitle");
			if (subtitle && ProductSidebarTitles.has(title) && subtitle.textContent.trim() !== brandName) {
				subtitle.textContent = brandName;
			}
		});

		document.querySelectorAll(".header-subtitle").forEach((subtitle) => {
			const text = subtitle.textContent.trim();
			if ((text === ERPNextBrandName || ProductSidebarSubtitles.has(text)) && text !== brandName) {
				subtitle.textContent = brandName;
			}
			if ((text === FrappeFrameworkName || text === DLPFrameworkName) && text !== frameworkName) {
				subtitle.textContent = frameworkName;
			}
		});

		replaceTextContent(
			[
				".sidebar-item-label",
				".sidebar-item-title",
				".icon-title",
				".menu-item-title",
				".workspace-title",
				".title-text",
				".ellipsis",
				".awesomplete [role='option']",
			].join(", "),
			replacements
		);

		replaceDisplayAttributes(replacements);
	}

	function applyBranding() {
		try {
			setFavicon();
			replaceERPNextAppTitle();
			replaceBootLabels();
			replaceVisibleBranding();
		} catch (error) {
			console.warn("Deeplinkerp branding failed to apply", error);
		}
	}

	function patchSidebarSubtitle() {
		if (
			!window.frappe?.ui?.Sidebar ||
			frappe.ui.Sidebar.prototype.__deeplinkerpPatched ||
			typeof frappe.ui.Sidebar.prototype.choose_app_name !== "function"
		) {
			return;
		}

		const originalChooseAppName = frappe.ui.Sidebar.prototype.choose_app_name;
		frappe.ui.Sidebar.prototype.choose_app_name = function (...args) {
			const result = originalChooseAppName.apply(this, args);
			if (this.header_subtitle === FrappeFrameworkName || this.header_subtitle === DLPFrameworkName) {
				this.header_subtitle = getFrameworkName();
			}
			if (
				ProductSidebarTitles.has(this.sidebar_title) ||
				ProductSidebarTitles.has(this.workspace_title) ||
				ProductSidebarSubtitles.has(this.header_subtitle) ||
				ProductAppNames.has(frappe.current_app?.app_name)
			) {
				this.header_subtitle = getBrandName();
			}
			if ((frappe.boot?.app_data || []).some((app) => app.app_title === this.header_subtitle)) {
				this.header_subtitle = translate(this.header_subtitle);
			}
			return result;
		};
		frappe.ui.Sidebar.prototype.__deeplinkerpPatched = true;
	}

	function patchSidebarMenuIcons() {
		const prototype = window.frappe?.ui?.SidebarHeader?.prototype;
		if (!prototype?.add_app_item || prototype.__deeplinkerpMenuIconsPatched) return;
		const original = prototype.add_app_item;
		prototype.add_app_item = function (item, ...args) {
			const prepared = DeepLinkERPNavigation.prepareSidebarMenuItem(item);
			if (prepared) return original.call(this, prepared, ...args);
		};
		prototype.__deeplinkerpMenuIconsPatched = true;
	}

	function getDesktopIcons() {
		const authorizedIcons = frappe.boot.desktop_icons || [];
		let runtimeLayout = frappe.desktop_icons || authorizedIcons;
		if (frappe.pages?.desktop?.desktop_page?.edit_mode && Array.isArray(frappe.new_desktop_icons)) {
			runtimeLayout = frappe.new_desktop_icons;
		}
		return DeepLinkERPNavigation.projectAuthorizedDesktopIcons(authorizedIcons, runtimeLayout);
	}

	function getIconsWithRoutes() {
		return getDesktopIcons().map((icon) => ({
			...icon,
			translated_label: translate(icon.label),
			navigation_route:
				typeof frappe.utils?.get_route_for_icon === "function"
					? frappe.utils.get_route_for_icon(icon)
					: icon.link || icon.url || "",
		}));
	}

	function getAuthorizedWorkspaceSidebars() {
		const currentSidebars = frappe.boot.workspace_sidebar_item || {};
		const currentItemCount =
			DeepLinkERPNavigation.countWorkspaceSidebarItems(currentSidebars);
		const snapshotItemCount =
			DeepLinkERPNavigation.countWorkspaceSidebarItems(
				authorizedWorkspaceSidebarSnapshot
			);

		if (!authorizedWorkspaceSidebarSnapshot || currentItemCount > snapshotItemCount) {
			authorizedWorkspaceSidebarSnapshot = DeepLinkERPNavigation.cloneWorkspaceSidebars(
				currentSidebars
			);
		}
		return authorizedWorkspaceSidebarSnapshot;
	}

	function scheduleNativeSidebarRefresh() {
		if (nativeSidebarRefreshTimer !== null) clearTimeout(nativeSidebarRefreshTimer);
		nativeSidebarRefreshTimer = setTimeout(() => {
			nativeSidebarRefreshTimer = null;
			refreshDeskEnhancements();
		}, 0);
	}

	function disconnectNativeSidebarObserver() {
		nativeSidebarObserver?.disconnect();
		nativeSidebarObserver = null;
		observedNativeSidebarItems = null;
		observedNativeSidebarHeader = null;
		if (nativeSidebarRefreshTimer !== null) {
			clearTimeout(nativeSidebarRefreshTimer);
			nativeSidebarRefreshTimer = null;
		}
	}

	function bindNativeSidebarObserver(sidebar, nativeItems) {
		if (typeof MutationObserver !== "function") return;
		const nativeHeader = sidebar.querySelector(".sidebar-header");
		if (
			nativeSidebarObserver &&
			observedNativeSidebarItems === nativeItems &&
			observedNativeSidebarHeader === nativeHeader
		) {
			return;
		}

		disconnectNativeSidebarObserver();
		nativeSidebarObserver = new MutationObserver(scheduleNativeSidebarRefresh);
		observedNativeSidebarItems = nativeItems;
		observedNativeSidebarHeader = nativeHeader;
		DeepLinkERPNavigation.observeNativeSidebarChanges(
			nativeSidebarObserver,
			nativeItems,
			nativeHeader
		);
	}

	function makeLineIcon(name, size = "sm") {
		if (typeof frappe.utils?.icon !== "function") return "";
		return frappe.utils.icon(name, size, "", "", "dlp-mes-navigation__line-icon", true);
	}

	function ensureBrandHeader(sidebar) {
		let fallback = sidebar.querySelector(".dlp-mes-navigation__brand-fallback");
		if (!fallback) {
			fallback = document.createElement("a");
			fallback.className = "dlp-mes-navigation__brand-fallback";
			DeepLinkERPNavigation.bindNativeSidebarClose(fallback, {
				isNarrowViewport: isNarrowNavigationViewport,
				closeSidebar: () => frappe.app.sidebar.close(),
				preserveDesktopExpansion: () =>
					DeepLinkERPNavigation.rememberDesktopSidebarExpansion(
						sidebar.querySelector(".dlp-mes-navigation"),
						sidebar.closest(".body-sidebar-container")
					),
			});

			sidebar.prepend(fallback);
		}
		fallback.href = "/desk";
		fallback.setAttribute("aria-label", "DeepLinkERP Desktop");
		let image = fallback.querySelector("img");
		if (!image) {
			image = document.createElement("img");
			fallback.appendChild(image);
		}
		image.src = BrandLogoURL;
		image.alt = "";
		let label = fallback.querySelector("span");
		if (!label) {
			label = document.createElement("span");
			fallback.appendChild(label);
		}
		label.textContent = "DeepLinkERP";
	}

	function getNativeActiveItem(nativeItems) {
		const activeItem = nativeItems.querySelector(".active-sidebar");
		if (!activeItem) return null;
		const anchor = activeItem.matches?.(".item-anchor")
			? activeItem
			: activeItem.querySelector(".item-anchor");
		const label = activeItem.querySelector(".sidebar-item-label")?.textContent?.trim() || "";
		return {
			label,
			href: anchor?.getAttribute("href") || "",
		};
	}

	function makeNavigationRow(item, isOpen = item.isOpen) {
		const hasChildren = item.children.length > 0 || item.hasNativeChildren;
		const route = item.navigation_route || item.route || item.link || item.url;
		const row = document.createElement(hasChildren ? "button" : route ? "a" : "button");
		row.className = "dlp-mes-navigation__row";
		if (hasChildren || !route) {
			row.type = "button";
		} else {
			row.href = route;
		}
		if (hasChildren) row.setAttribute("aria-expanded", String(isOpen));
		if (item.isSelfActive) row.setAttribute("aria-current", "page");

		const icon = document.createElement("span");
		icon.className = "dlp-mes-navigation__icon";
		icon.innerHTML = makeLineIcon(DeepLinkERPNavigation.getNavigationIcon(item.label));
		row.appendChild(icon);

		const label = document.createElement("span");
		label.className = "dlp-mes-navigation__label";
		label.textContent = translate(DeepLinkERPNavigation.getNavigationDisplayLabel(item));
		row.appendChild(label);

		if (hasChildren) {
			const chevron = document.createElement("span");
			chevron.className = "dlp-mes-navigation__chevron";
			chevron.innerHTML = makeLineIcon(isOpen ? "chevron-down" : "chevron-right");
			row.appendChild(chevron);
		}
		return row;
	}

	function prepareWorkspaceSidebarItems(items) {
		const context = {
			workspace_sidebar_items: DeepLinkERPNavigation.cloneSidebarItems(items),
		};
		frappe.ui.Sidebar.prototype.find_nested_items.call(context);
		return context.workspace_sidebar_items;
	}

	function normalizeRenderedSidebarTarget(link) {
		const href = link.getAttribute("href") || "";
		if (!DeepLinkERPNavigation.isInternalDeskLink(href, window.location.origin)) return false;
		link.removeAttribute("target");
		return true;
	}

	function bindRenderedSidebarLinks(container) {
		container.querySelectorAll(".item-anchor").forEach((link) => {
			if (link.dataset.dlpNavigationBound === "true") return;
			normalizeRenderedSidebarTarget(link);
			link.dataset.dlpNavigationBound = "true";
			DeepLinkERPNavigation.bindNativeSidebarClose(link, {
				isNarrowViewport: isNarrowNavigationViewport,
				closeSidebar: () => frappe.app.sidebar.close(),
				preserveDesktopExpansion: () =>
					DeepLinkERPNavigation.rememberDesktopSidebarExpansion(
						link.closest(".dlp-mes-navigation"),
						link.closest(".body-sidebar-container")
					),
			});
		});
	}

	function updateRenderedSidebarActive(container) {
		container.querySelectorAll(".standard-sidebar-item").forEach((sidebarItem) => {
			const anchor = sidebarItem.querySelector(".item-anchor");
			const isCurrent = DeepLinkERPNavigation.routesMatch(
				anchor?.getAttribute("href") || "",
				window.location.href
			);
			sidebarItem.classList.toggle("active-sidebar", isCurrent);
			if (isCurrent) anchor?.setAttribute("aria-current", "page");
			else anchor?.removeAttribute("aria-current");
		});
	}

	function renderWorkspaceSidebarBranch(item, branch) {
		const container = document.createElement("div");
		container.className = "sidebar-items dlp-mes-navigation__workspace-items";
		prepareWorkspaceSidebarItems(item.workspaceSidebar?.items || []).forEach((sidebarItem) => {
			frappe.app.sidebar.make_sidebar_item({
				container,
				item: sidebarItem,
			});
		});
		updateRenderedSidebarActive(container);
		bindRenderedSidebarLinks(container);
		branch.appendChild(container);
	}

	function renderNavigationItem(item, nativeItems, nativeHostKey, disclosureState, depth = 0) {
		const group = document.createElement("div");
		group.className = "dlp-mes-navigation__group";
		group.dataset.navigationKey = item.key;
		group.style.setProperty("--dlp-navigation-depth", depth);
		const isOpen = DeepLinkERPNavigation.resolveItemOpen(item, disclosureState);
		if (disclosureState.expandedKeys?.has(item.key)) group.dataset.userExpanded = "true";
		if (disclosureState.collapsedKeys?.has(item.key)) group.dataset.userCollapsed = "true";
		if (isOpen) group.classList.add("dlp-mes-navigation__group--open");
		if (item.isSelfActive) group.classList.add("dlp-mes-navigation__group--self-active");

		const row = makeNavigationRow(item, isOpen);
		group.appendChild(row);
		const needsBranch = item.children.length > 0 || item.hasNativeChildren;
		const route = item.navigation_route || item.route || item.link || item.url || "";
		const interactionOptions = {
			route,
			isNarrowViewport: isNarrowNavigationViewport,
			closeSidebar: () => frappe.app.sidebar.close(),
			preserveDesktopExpansion: () =>
				DeepLinkERPNavigation.rememberDesktopSidebarExpansion(
					row.closest(".dlp-mes-navigation"),
					row.closest(".body-sidebar-container")
				),
		};
		if (!needsBranch) {
			DeepLinkERPNavigation.bindNavigationRowInteractions(row, interactionOptions);
			return group;
		}

		const branch = document.createElement("div");
		branch.className = "dlp-mes-navigation__children";
		branch.hidden = !isOpen;
		item.children.forEach((child) => {
			branch.appendChild(
				renderNavigationItem(
					child,
					nativeItems,
					nativeHostKey,
					disclosureState,
					depth + 1
				)
			);
		});
		if (item.key === nativeHostKey) {
			branch.appendChild(nativeItems);
		} else if (item.hasNativeChildren) {
			renderWorkspaceSidebarBranch(item, branch);
		}
		group.appendChild(branch);

		DeepLinkERPNavigation.bindNavigationRowInteractions(row, {
			...interactionOptions,
			group,
			branch,
			updateChevron: (open) => {
				const chevron = row.querySelector(".dlp-mes-navigation__chevron");
				if (chevron) chevron.innerHTML = makeLineIcon(open ? "chevron-down" : "chevron-right");
			},
		});
		return group;
	}

	function updateNativeLeafAccessibility(nativeItems) {
		nativeItems.querySelectorAll(".item-anchor").forEach((anchor) => {
			anchor.removeAttribute("aria-current");
			if (anchor.closest(".standard-sidebar-item")?.classList.contains("active-sidebar")) {
				anchor.setAttribute("aria-current", "page");
			}
		});
	}

	function renderPersistentNavigation() {
		if (!window.frappe?.boot || !window.DeepLinkERPNavigation || !frappe.app?.sidebar) return;
		const sidebarContainer = frappe.app.sidebar.wrapper?.[0] || document.querySelector(".body-sidebar-container");
		const sidebar = sidebarContainer?.querySelector(".body-sidebar");
		const top = sidebar?.querySelector(".body-sidebar-top");
		const nativeItems = DeepLinkERPNavigation.claimNativeSidebarItems(sidebar);
		if (!sidebarContainer || !sidebar || !top || !nativeItems) return;
		bindNativeSidebarObserver(sidebar, nativeItems);
		bindRenderedSidebarLinks(nativeItems);
		updateRenderedSidebarActive(nativeItems);

		document.body.classList.add("dlp-mes-navigation-enabled");
		sidebar.classList.add("dlp-mes-navigation-sidebar");
		sidebarContainer.style.removeProperty("display");
		ensureBrandHeader(sidebar);
		const previousNavigation = top.querySelector(":scope > .dlp-mes-navigation");
		DeepLinkERPNavigation.restoreDesktopSidebarExpansion(
			previousNavigation,
			sidebarContainer,
			isNarrowNavigationViewport()
		);
		const disclosureState = DeepLinkERPNavigation.collectDisclosureState(
			previousNavigation
		);

		const model = DeepLinkERPNavigation.buildNavigationModel({
			desktopIcons: getIconsWithRoutes(),
			workspaceSidebars: getAuthorizedWorkspaceSidebars(),
			route: window.location.href,
			currentSidebar: frappe.app.sidebar.sidebar_title || "",
			nativeWorkspaceLabel:
				sidebar.querySelector(".sidebar-header .header-title")?.textContent?.trim() || "",
			nativeActiveItem: getNativeActiveItem(nativeItems),
			nativeLeafSelected: Boolean(nativeItems.querySelector(".active-sidebar")),
		});

		// Keep Frappe's live node and its handlers while replacing only our shell.
		DeepLinkERPNavigation.replaceNavigationRoot(top, nativeItems, () => {
			const navigation = document.createElement("nav");
			navigation.className = "dlp-mes-navigation";
			navigation.setAttribute("aria-label", translate("Modules"));
			model.items.forEach((item) => {
				navigation.appendChild(
					renderNavigationItem(
						item,
						nativeItems,
						model.nativeHostKey,
						disclosureState
					)
				);
			});
			if (!model.nativeHostKey) {
				const nativeParking = document.createElement("div");
				nativeParking.className = "dlp-mes-navigation__native-parking";
				nativeParking.hidden = true;
				nativeParking.appendChild(nativeItems);
				navigation.appendChild(nativeParking);
			}
			return navigation;
		});
		updateNativeLeafAccessibility(nativeItems);
		window.DeepLinkERPInterfaceMode?.finishDLPreparation(document);
		return true;
	}

	function enhanceDesktopIcons() {
		if (!window.DeepLinkERPNavigation || !window.frappe?.utils?.icon) return;
		document.querySelectorAll(".desktop-container .desktop-icon:not(.add-new-icon)").forEach((entry) => {
			const label = entry.dataset.id || entry.querySelector(".icon-title")?.textContent.trim();
			if (!label) return;
			const iconName = DeepLinkERPNavigation.getNavigationIcon(label);
			entry.classList.add("dlp-desktop-icon-enhanced");
			let visual = entry.querySelector(":scope > .dlp-desktop-line-icon");
			if (!visual) {
				visual = document.createElement("div");
				visual.className = "icon-container dlp-desktop-line-icon";
				entry.prepend(visual);
			}
			if (visual.dataset.dlpIcon !== iconName) {
				visual.innerHTML = makeLineIcon(iconName, "md");
				visual.dataset.dlpIcon = iconName;
			}
		});
	}

	function disableDLEnhancements() {
		disconnectNativeSidebarObserver();
		const navigation = document.querySelector(".dlp-mes-navigation");
		const top = navigation?.closest(".body-sidebar-top");
		const sidebar = navigation?.closest(".body-sidebar");
		const nativeItems = navigation
			? DeepLinkERPNavigation.claimNativeSidebarItems(sidebar)
			: null;
		if (top && nativeItems) top.prepend(nativeItems);
		navigation?.remove();
		const fallbackHeader = document.querySelector(".dlp-mes-navigation__brand-fallback");
		fallbackHeader?.remove();
		document.body.classList.remove("dlp-mes-navigation-enabled");
		document
			.querySelector(".body-sidebar.dlp-mes-navigation-sidebar")
			?.classList.remove("dlp-mes-navigation-sidebar");
		document.querySelectorAll(".dlp-desktop-line-icon").forEach((icon) => icon.remove());
		document
			.querySelectorAll(".desktop-icon.dlp-desktop-icon-enhanced")
			.forEach((entry) => entry.classList.remove("dlp-desktop-icon-enhanced"));
		window.DeepLinkERPInterfaceMode?.finishDLPreparation(document);
	}

	function refreshDeskEnhancements() {
		patchSidebarMenuIcons();
		patchSidebarSubtitle();
		applyBranding();
		if (window.DeepLinkERPInterfaceMode) {
			DeepLinkERPInterfaceMode.ensureUserMenu({ document, frappe, translate });
		}
		const dlModeEnabled =
			!window.DeepLinkERPInterfaceMode || DeepLinkERPInterfaceMode.isDLMode(frappe.boot);
		if (dlModeEnabled) {
			renderPersistentNavigation();
			enhanceDesktopIcons();
		} else {
			disableDLEnhancements();
			DeepLinkERPNavigation.arrangeClassicSidebar(window.CustomFiltersRightSidebar);
		}
	}

	function bindDeskEvents() {
		if (eventsBound) return;
		eventsBound = true;
		DeepLinkERPNavigation.bindInternalSidebarNavigation(document, window.location.origin);

		if (window.frappe?.router?.on) {
			frappe.router.on("change", refreshDeskEnhancements);
		}

		if (window.frappe?.after_ajax) {
			frappe.after_ajax(refreshDeskEnhancements);
		}

		if (window.jQuery) {
			jQuery(document).on("page-change form-refresh desktop_screen", refreshDeskEnhancements);
		}
	}

	function initialize() {
		refreshDeskEnhancements();
		bindDeskEvents();
	}

	patchSidebarMenuIcons();
	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", initialize);
	} else {
		initialize();
	}

	[100, 500, 1000, 2000].forEach((delay) => setTimeout(refreshDeskEnhancements, delay));
})();
