(function (root, factory) {
	if (typeof module === "object" && module.exports) module.exports = factory;
	root.DeepLinkERPMexicoExpenseDateRange = factory(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
	"use strict";
	const t = (text) => (root.__ ? root.__(text) : text);
	const iso = (date) =>
		[
			date.getFullYear(),
			String(date.getMonth() + 1).padStart(2, "0"),
			String(date.getDate()).padStart(2, "0"),
		].join("-");
	const localDate = (value) => {
		const [y, m, d] = value.split("-").map(Number);
		return new Date(y, m - 1, d);
	};
	function calendarMonths(value, today) {
		const start = localDate(value.from_date || today);
		const end = localDate(value.to_date || value.from_date || today);
		const left = new Date(start.getFullYear(), start.getMonth(), 1);
		const right = new Date(end.getFullYear(), end.getMonth(), 1);
		// The right panel always contains the selected end date. Avoid duplicate
		// months for a same-month range, except at the supported lower boundary.
		if (left.getTime() === right.getTime() && iso(left) > "1900-01-01") {
			left.setMonth(left.getMonth() - 1);
		}
		return [left, right];
	}
	function presetRange(kind, today) {
		const [y, m, d] = today.split("-").map(Number);
		const end = new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10);
		if (kind === "today") return { from_date: today, to_date: today };
		if (kind === "month") return { from_date: today.slice(0, 7) + "-01", to_date: end };
		if (kind === "quarter") {
			const lastDay = new Date(Date.UTC(y, m - 3, 0)).getUTCDate();
			return {
				from_date: new Date(Date.UTC(y, m - 4, Math.min(d, lastDay)))
					.toISOString()
					.slice(0, 10),
				to_date: today,
			};
		}
		if (kind === "180-days")
			return {
				from_date: new Date(Date.UTC(y, m - 1, d - 179)).toISOString().slice(0, 10),
				to_date: today,
			};
		throw new Error("Unknown date range preset");
	}
	class RangePicker {
		constructor({ parent, value, today, getToday, onChange }) {
			Object.assign(this, {
				parent,
				value: { ...value },
				today,
				getToday,
				onChange,
				pickers: [],
				request: 0,
			});
			this.trigger = root.document.createElement("button");
			this.trigger.type = "button";
			this.trigger.className = "mx-range-trigger";
			this.trigger.setAttribute("aria-haspopup", "dialog");
			this.trigger.setAttribute("aria-expanded", "false");
			parent.appendChild(this.trigger);
			this.renderValue();
			this.trigger.addEventListener("click", () =>
				this.popup ? this.close() : this.open()
			);
		}
		renderValue() {
			this.trigger.replaceChildren();
			for (const [index, value] of [this.value.from_date, this.value.to_date].entries()) {
				if (index) {
					const arrow = root.document.createElement("span");
					arrow.textContent = "→";
					arrow.className = "mx-range-arrow";
					this.trigger.appendChild(arrow);
				}
				const text = root.document.createElement("span");
				text.textContent = value
					? root.frappe.datetime.str_to_user(value)
					: t(index ? "End Date" : "Start Date");
				this.trigger.appendChild(text);
			}
			const icon = root.document.createElement("span");
			icon.className = "mx-range-icon";
			icon.setAttribute("aria-hidden", "true");
			icon.innerHTML = root.frappe.utils.icon("calendar", "sm");
			this.trigger.appendChild(icon);
			this.trigger.setAttribute(
				"aria-label",
				`${t("Date Range")}: ${this.trigger.textContent}`
			);
		}
		open() {
			if (this.popup || this.destroyed) return;
			const popup = root.document.createElement("div");
			popup.className = "mx-range-popup";
			popup.setAttribute("role", "dialog");
			popup.setAttribute("aria-label", t("Date Range"));
			this.popup = popup;
			this.trigger.setAttribute("aria-expanded", "true");
			const calendars = root.document.createElement("div");
			calendars.className = "mx-range-calendars";
			popup.appendChild(calendars);
			this.status = root.document.createElement("div");
			this.status.className = "mx-range-status";
			this.status.setAttribute("aria-live", "polite");
			popup.appendChild(this.status);
			const footer = root.document.createElement("div");
			footer.className = "mx-range-shortcuts";
			for (const [id, label] of [
				["today", "Today"],
				["month", "This Month"],
				["quarter", "Last 3 Months"],
				["180-days", "Last 180 Days"],
			]) {
				const button = root.document.createElement("button");
				button.type = "button";
				button.dataset.rangePreset = id;
				button.textContent = t(label);
				button.addEventListener("click", () => this.choosePreset(id));
				footer.appendChild(button);
			}
			const cancel = root.document.createElement("button");
			cancel.type = "button";
			cancel.className = "mx-range-cancel";
			cancel.textContent = t("Cancel");
			cancel.addEventListener("click", () => this.close(true));
			footer.appendChild(cancel);
			popup.appendChild(footer);
			root.document.body.appendChild(popup);
			const months = calendarMonths(this.value, this.today);
			let language = root.frappe.boot?.user?.language || "en";
			if (!root.$.fn.datepicker.language[language]) language = "en";
			for (let index = 0; index < 2; index++) {
				const holder = root.document.createElement("div");
				holder.className = "mx-range-calendar";
				calendars.appendChild(holder);
				root.$(holder).datepicker({
					inline: true,
					range: true,
					autoClose: false,
					toggleSelected: false,
					keyboardNav: false,
					language,
					firstDay: root.frappe.datetime.get_first_day_of_the_week_index(),
					dateFormat: root.frappe.boot?.sysdefaults?.date_format || "yyyy-mm-dd",
					startDate: months[index],
					minDate: new Date(1900, 0, 1),
					maxDate: new Date(2200, 11, 31),
					moveToOtherMonthsOnSelect: false,
					moveToOtherYearsOnSelect: false,
					onSelect: (_, dates, picker) => this.select(dates, picker),
				});
				const picker = root.$(holder).data("datepicker");
				this.pickers.push(picker);
				// Explicitly select even an already selected endpoint, so it can start a new range.
				holder.addEventListener(
					"click",
					(event) => {
						const cell = event.target.closest(
							".datepicker--cell-day:not(.-disabled-)"
						);
						if (!cell) return;
						event.stopImmediatePropagation();
						event.preventDefault();
						const { year, month, date } = cell.dataset;
						picker.selectDate(new Date(+year, +month, +date));
						this.makeAccessible();
						holder
							.querySelector(
								`[data-date="${date}"][data-month="${month}"][data-year="${year}"]`
							)
							?.focus();
					},
					true
				);
			}
			this.sync([this.value.from_date, this.value.to_date].filter(Boolean).map(localDate));
			this.makeAccessible();
			this.position();
			this.outside = (event) => {
				if (!popup.contains(event.target) && !this.parent.contains(event.target))
					this.close();
			};
			this.keydown = (event) => this.onKeydown(event);
			this.reposition = () => this.position();
			root.document.addEventListener("pointerdown", this.outside, true);
			root.document.addEventListener("focusin", this.outside, true);
			popup.addEventListener("keydown", this.keydown);
			this.trigger.addEventListener("keydown", this.keydown);
			root.addEventListener("resize", this.reposition);
			root.addEventListener("scroll", this.reposition, true);
			this.observer = new root.MutationObserver(() => {
				this.makeAccessible();
				this.position();
			});
			this.observer.observe(calendars, { childList: true, subtree: true });
			popup
				.querySelector(
					".datepicker--cell-day.-selected-:not(.-other-month-), .datepicker--cell-day:not(.-other-month-):not(.-disabled-)"
				)
				?.focus();
		}
		sync(dates, source) {
			this.syncing = true;
			try {
				for (const picker of this.pickers) {
					if (picker === source) continue;
					picker.clear();
					picker.selectDate(dates.map((date) => new Date(date)));
				}
			} finally {
				this.syncing = false;
			}
		}
		select(dates, source) {
			if (this.syncing || !Array.isArray(dates) || !dates.length) return;
			++this.request;
			this.sync(dates, source);
			this.status.textContent = "";
			if (dates.length === 1) return;
			const values = dates.map(iso).sort();
			this.commit({ from_date: values[0], to_date: values[1] });
		}
		async choosePreset(kind) {
			const request = ++this.request;
			try {
				const today = await this.getToday();
				if (!this.popup || request !== this.request) return;
				this.commit(presetRange(kind, today));
			} catch (_) {
				if (this.popup && request === this.request)
					this.status.textContent = t(
						"Could not update the date range. Please try again."
					);
			}
		}
		commit(value) {
			// The native calendar completes its click/render before the workbench rebuilds.
			clearTimeout(this.commitTimer);
			this.commitTimer = setTimeout(() => {
				if (!this.popup || this.destroyed) return;
				this.value = value;
				this.renderValue();
				this.close();
				this.onChange(value);
			}, 0);
		}
		makeAccessible() {
			if (!this.popup) return;
			this.popup
				.querySelectorAll(
					".datepicker--cell, .datepicker--nav-action, .datepicker--nav-title"
				)
				.forEach((cell) => {
					cell.setAttribute("role", "button");
					const disabled = cell.classList.contains("-disabled-");
					cell.setAttribute("tabindex", disabled ? "-1" : "0");
					cell.setAttribute("aria-disabled", String(disabled));
					if (cell.classList.contains("datepicker--cell-day")) {
						cell.setAttribute(
							"aria-label",
							root.frappe.datetime.str_to_user(
								iso(
									new Date(
										+cell.dataset.year,
										+cell.dataset.month,
										+cell.dataset.date
									)
								)
							)
						);
						cell.setAttribute(
							"aria-pressed",
							String(cell.classList.contains("-selected-"))
						);
					} else if (cell.dataset.action)
						cell.setAttribute(
							"aria-label",
							t(cell.dataset.action === "prev" ? "Previous" : "Next")
						);
				});
		}
		onKeydown(event) {
			if (event.key === "Escape") {
				event.preventDefault();
				event.stopPropagation();
				this.close(true);
				return;
			}
			const cell = event.target.closest(
				".datepicker--cell, .datepicker--nav-action, .datepicker--nav-title"
			);
			if (cell && ["Enter", " "].includes(event.key)) {
				event.preventDefault();
				cell.click();
			}
			if (
				cell?.classList.contains("datepicker--cell-day") &&
				["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)
			) {
				event.preventDefault();
				const delta = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[
					event.key
				];
				const next = new Date(
					+cell.dataset.year,
					+cell.dataset.month,
					+cell.dataset.date + delta
				);
				if (iso(next) < "1900-01-01" || iso(next) > "2200-12-31") return;
				const holder = cell.closest(".mx-range-calendar"),
					picker = root.$(holder).data("datepicker");
				picker.date = new Date(next.getFullYear(), next.getMonth(), 1);
				this.makeAccessible();
				holder
					.querySelector(
						`[data-date="${next.getDate()}"][data-month="${next.getMonth()}"][data-year="${next.getFullYear()}"]`
					)
					?.focus();
			}
		}
		position() {
			if (!this.popup) return;
			const anchor = this.trigger.getBoundingClientRect(),
				popup = this.popup.getBoundingClientRect();
			const left = Math.max(8, Math.min(anchor.left, root.innerWidth - popup.width - 8));
			const below = root.innerHeight - anchor.bottom - 8;
			const top =
				below >= popup.height
					? anchor.bottom + 5
					: Math.max(8, anchor.top - popup.height - 5);
			Object.assign(this.popup.style, { left: `${left}px`, top: `${top}px` });
		}
		close(focus = false) {
			++this.request;
			clearTimeout(this.commitTimer);
			this.observer?.disconnect();
			root.document.removeEventListener("pointerdown", this.outside, true);
			root.document.removeEventListener("focusin", this.outside, true);
			this.trigger.removeEventListener("keydown", this.keydown);
			root.removeEventListener("resize", this.reposition);
			root.removeEventListener("scroll", this.reposition, true);
			this.pickers.forEach((picker) => picker.destroy());
			this.pickers = [];
			this.popup?.remove();
			this.popup = null;
			this.trigger.setAttribute("aria-expanded", "false");
			if (focus) this.trigger.focus();
		}
		destroy() {
			this.destroyed = true;
			this.close();
			this.trigger.remove();
		}
	}
	return { RangePicker, presetRange, calendarMonths };
});
