/* Insert into native form child tables. Reports, dialogs and Web Forms have
 * separate persistence rules and are intentionally outside this extension. */
(() => {
	if (window.DeepLinkERPGridInsert) return;
	const pending = new WeakSet();
	const watched = new WeakSet();
	const filterInput = new WeakMap();
	const validations = new WeakMap();

	function track_control(control) {
		if (!control?.validate_and_set_in_model || validations.has(control)) return;
		const state = { pending: new Set(), error: null };
		const validate = control.validate_and_set_in_model;
		control.validate_and_set_in_model = function (...args) {
			state.error = null;
			const result = validate.apply(this, args);
			if (result?.then) {
				state.pending.add(result);
				result.then(() => state.pending.delete(result), error => {
					state.pending.delete(result);
					state.error = error;
				});
			}
			return result;
		};
		validations.set(control, state);
	}

	function editable(grid) {
		const frm = grid?.frm;
		return Boolean(frm && grid.df?.fieldtype === "Table" && grid.is_editable()
			&& !grid.cannot_add_rows && !grid.df.cannot_add_rows
			&& !grid.get_selected_children?.().length
			&& !frm.save_disabled && !frm.toolbar?.read_only && !frappe.ui.form.is_saving
			&& frappe.perm.get_field_display_status(grid.df, frm.doc, grid.perm) === "Write");
	}

	function current(grid, parent, anchor) {
		return grid.frm.doc === parent && editable(grid)
			&& (parent[grid.df.fieldname] || []).includes(anchor);
	}

	async function flush_input(control) {
		// Wait for a blur/Link lookup already started by keyboard navigation.
		await frappe.after_ajax();
		if (!control) return;
		const state = validations.get(control);
		if (state) {
			while (state.pending.size) await Promise.all([...state.pending]);
			if (state.error) throw state.error;
		}
		if (control.get_status() !== "Write") return;
		if (control.inside_change_event) {
			throw new Error(__("字段仍在校验，请稍后再新增行"));
		}
		if (control.get_input_value && control.parse_validate_and_set_in_model) {
			await control.parse_validate_and_set_in_model(control.get_input_value());
			await frappe.after_ajax();
		}
	}

	function rollback_row(grid, parent, row) {
		let cleanup_error;
		try { frappe.model.clear_doc(row.doctype, row.name); }
		catch (error) { cleanup_error = error; }
		// clear_doc also removes the row from the parent; handle a missing locals
		// entry without leaving a row that a later save could persist.
		const rows = parent[grid.df.fieldname] || [];
		const index = rows.indexOf(row);
		if (index !== -1) rows.splice(index, 1);
		rows.forEach((item, index) => { item.idx = index + 1; });
		if (grid.frm.doc.doctype === "Journal Entry" && grid.df.fieldname === "accounts") {
			grid.frm.cscript.update_totals(grid.frm.doc);
		}
		if (cleanup_error) throw cleanup_error;
	}

	function journal_defaults(grid, row, anchor) {
		if (grid.frm.doc.doctype !== "Journal Entry" || grid.df.fieldname !== "accounts") return;
		// Run after native accounts_add: it copies a party account and fills the
		// difference. This explicit insertion starts an independent empty entry.
		Object.assign(row, {
			account: "", account_type: "", party_type: "", party: "", account_currency: "",
			exchange_rate: 1, debit: 0, credit: 0,
			debit_in_account_currency: 0, credit_in_account_currency: 0,
			user_remark: anchor.user_remark || "",
		});
		grid.frm.cscript.update_totals(grid.frm.doc);
	}

	function reveal(grid, row) {
		// A new empty row cannot match most table filters. Clear only this grid's
		// local filters, and tell the user why the other rows reappear.
		const filtered = Object.keys(grid.filter || {}).length > 0;
		if (filtered) {
			grid.filter = {};
			grid.grid_sortable?.option("disabled", false);
		}
		grid.refresh();
		const index = grid.get_data().findIndex(item => item.name === row.name);
		const page = Math.floor(index / grid.grid_pagination.page_length) + 1;
		grid.grid_pagination.go_to_page(page, true);
		const rendered = grid.grid_rows_by_docname[row.name];
		if (grid.allow_on_grid_editing()) {
			rendered?.toggle_editable_row(true);
			const controls = rendered?.on_grid_fields || [];
			const input = controls.find(control => control.df.fieldname === "account")
				|| controls.find(control => control.$input?.is(":visible:enabled"));
			input?.$input?.trigger("focus");
		} else {
			rendered?.toggle_view(true);
		}
		rendered?.wrapper?.[0]?.scrollIntoView({ block: "nearest" });
		if (filtered) frappe.show_alert({ message: __("已清除本表筛选，以显示新增行"), indicator: "blue" });
	}

	async function insert_after(grid, anchor, control) {
		const frm = grid?.frm, parent = frm?.doc;
		if (!frm || pending.has(frm) || !current(grid, parent, anchor)) return;
		pending.add(frm);
		frappe.dom.freeze(__("正在新增一行…"));
		let row, completed = false;
		try {
			// Native grid search applies a 500 ms debounce, including a page reset.
			// Let it finish before locating/inserting and clearing the filter.
			const delay = 550 - (Date.now() - (filterInput.get(grid) || 0));
			if (delay > 0) await new Promise(resolve => setTimeout(resolve, delay));
			await flush_input(control);
			if (!current(grid, parent, anchor)) return;
			frappe.ui.form.close_grid_form();
			// Use Frappe's child creation/renumbering and the same add event as
			// Grid.add_new_row, but await that event before applying our defaults.
			row = frappe.model.add_child(parent, grid.df.options, grid.df.fieldname, anchor.idx + 1);
			row.__unedited = true;
			await frm.script_manager.trigger(grid.df.fieldname + "_add", row.doctype, row.name);
			if (frm.doc !== parent || !(parent[grid.df.fieldname] || []).includes(row)) return;
			journal_defaults(grid, row, anchor);
			completed = true;
			reveal(grid, row);
			return row;
		} catch (error) {
			let detail = "";
			if (row && frm.doc === parent) {
				if (completed) {
					// The business add hook succeeded; a display error must not create
					// another row if the user retries the same action.
					try { reveal(grid, row); }
					catch (_) { try { grid.refresh(); } catch (_) { /* preserve the original error */ } }
					detail = __("新行仍在当前单据中，请检查后再操作");
				} else {
					try { rollback_row(grid, parent, row); }
					catch (_) { /* inspect the parent array below */ }
					if (!(parent[grid.df.fieldname] || []).includes(row)) {
						try { grid.refresh(); } catch (_) { /* row is already removed */ }
						detail = __("新增行已撤回，请核对当前单据后重试");
					} else {
						try { reveal(grid, row); }
						catch (_) { try { grid.refresh(); } catch (_) { /* preserve the original error */ } }
						detail = __("新行可能仍在当前单据中，请检查后再操作");
					}
				}
			}
			frappe.msgprint({ title: __("新增行未完成"),
				message: [frappe.utils.escape_html(error?.message || __("请检查当前输入后重试")), detail]
					.filter(Boolean).join("<br>"), indicator: "red" });
		} finally {
			pending.delete(frm);
			frappe.dom.unfreeze();
		}
	}

	function decorate(row) {
		if (!row?.doc || !row.grid?.frm) return;
		const grid = row.grid;
		watch_grid(grid);
		let button = row.row.children(".dlp-grid-insert-below");
		if (!editable(grid)) { button.remove(); return; }
		if (!button.length) {
			button = $('<button type="button" class="dlp-grid-insert-below">+</button>').prependTo(row.row);
			let control;
			button.on("pointerdown", event => {
				if (event.button !== 0) return;
				// Retain the active input until its native validation has completed.
				control = document.activeElement?.closest(".frappe-control")?.fieldobj;
				track_control(control);
				event.preventDefault();
				event.stopPropagation();
			}).on("focusin", event => {
				// Keyboard activation has no pointerdown: retain the editor that
				// just lost focus when Tab moved onto this button.
				control = event.relatedTarget?.closest?.(".frappe-control")?.fieldobj;
			}).on("click", event => {
				event.stopPropagation();
				const active = control;
				control = null;
				return insert_after(grid, row.doc, active);
			});
		}
		row.row.addClass("dlp-grid-insert-target");
		const label = __("在第 {0} 行下方新增一行", [row.doc.idx]);
		button.attr({ title: label, "aria-label": label });
		grid.wrapper.find(".grid-add-row").text(__("末尾新增"));
	}

	function watch_grid(grid) {
		if (!grid?.frm || !grid.wrapper || watched.has(grid)) return;
		watched.add(grid);
		grid.wrapper.on("keyup.dlp_grid_insert", ".grid-heading-row input", () => filterInput.set(grid, Date.now()));
		grid.wrapper.on("focusin.dlp_grid_insert", ".frappe-control :input", event => {
			track_control(event.target.closest(".frappe-control")?.fieldobj);
		});
		grid.wrapper.on("click.dlp_grid_insert", ".grid-row-check", () => {
			// Native Grid hides its Add row button while rows are selected.
			(grid.grid_rows || []).forEach(decorate);
		});
		grid.wrapper.on("change.dlp_grid_insert", event => {
			if (event.target !== grid.wrapper[0]) return;
			// Includes template rows, which do not emit grid-row-render.
			(grid.grid_rows || []).forEach(decorate);
			grid.wrapper.find(".grid-add-row").text(__("末尾新增"));
		});
	}

	$(document).on("grid-row-render.dlp_grid_insert", (_event, row) => decorate(row));
	$(document).on("form-refresh.dlp_grid_insert", (_event, frm) => {
		// Includes empty tables, for which there is no data-row render event.
		setTimeout(() => {
			for (const field of Object.values(frm.fields_dict || {})) {
				const grid = field.grid;
				if (!grid?.frm) continue;
				watch_grid(grid);
				grid.wrapper?.find(".grid-add-row").text(__("末尾新增"));
				(grid.grid_rows || []).forEach(decorate);
			}
		}, 0);
	});
	window.DeepLinkERPGridInsert = { insert_after, editable, decorate };
})();
