const test = require("node:test");
const assert = require("node:assert/strict");
const { presetRange, RangePicker, calendarMonths } =
	require("../deeplinkerp_branding/public/js/mexico_expense_date_range.js")({});

test("today is an inclusive single-day range", () => {
	assert.deepEqual(presetRange("today", "2026-11-17"), {
		from_date: "2026-11-17",
		to_date: "2026-11-17",
	});
});
test("this month includes its full last day, including leap February", () => {
	assert.deepEqual(presetRange("month", "2024-02-10"), {
		from_date: "2024-02-01",
		to_date: "2024-02-29",
	});
	assert.deepEqual(presetRange("month", "2027-02-10"), {
		from_date: "2027-02-01",
		to_date: "2027-02-28",
	});
});
test("last three months clamp end-of-month dates and cross years", () => {
	assert.deepEqual(presetRange("quarter", "2026-05-31"), {
		from_date: "2026-02-28",
		to_date: "2026-05-31",
	});
	assert.deepEqual(presetRange("quarter", "2024-05-31"), {
		from_date: "2024-02-29",
		to_date: "2024-05-31",
	});
	assert.deepEqual(presetRange("quarter", "2027-01-17"), {
		from_date: "2026-10-17",
		to_date: "2027-01-17",
	});
});
test("last 180 days includes today and never depends on DST", () => {
	for (const today of ["2026-11-17", "2026-03-09", "2027-01-01"]) {
		const r = presetRange("180-days", today);
		assert.equal((Date.parse(r.to_date) - Date.parse(r.from_date)) / 86400000 + 1, 180);
	}
});
test("unknown presets are rejected instead of silently selecting a different range", () => {
	assert.throws(() => presetRange("unknown", "2026-11-17"));
});
test("closing while business date loads discards a stale quick selection", async () => {
	let resolve;
	const p = Object.create(RangePicker.prototype);
	let committed = false;
	Object.assign(p, {
		popup: {},
		request: 0,
		getToday: () => new Promise((r) => (resolve = r)),
		commit: () => {
			committed = true;
		},
	});
	const pending = p.choosePreset("today");
	p.popup = null;
	p.request++;
	resolve("2026-11-17");
	await pending;
	assert.equal(committed, false);
});
test("only the last quick selection can apply an asynchronous business date", async () => {
	const pending = [],
		commits = [];
	const p = Object.create(RangePicker.prototype);
	Object.assign(p, {
		popup: {},
		request: 0,
		getToday: () => new Promise((r) => pending.push(r)),
		commit: (r) => commits.push(r),
	});
	const first = p.choosePreset("today"),
		second = p.choosePreset("month");
	pending[1]("2026-11-17");
	await second;
	pending[0]("2026-11-17");
	await first;
	assert.deepEqual(commits, [{ from_date: "2026-11-01", to_date: "2026-11-30" }]);
});

const monthLabels = (value, today = "2026-11-16") =>
	calendarMonths(value, today).map(
		(date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`
	);

test("all presets show the endpoint month on the right", () => {
	for (const kind of ["today", "month", "quarter", "180-days"]) {
		const range = presetRange(kind, "2026-11-16");
		const months = monthLabels(range);
		assert.equal(months[1], "2026-11");
		assert.equal(
			months[0],
			["today", "month"].includes(kind) ? "2026-10" : range.from_date.slice(0, 7)
		);
	}
});
test("cross-year and long ranges show both endpoint months", () => {
	assert.deepEqual(monthLabels({ from_date: "2026-12-20", to_date: "2027-02-10" }), [
		"2026-12",
		"2027-02",
	]);
	assert.deepEqual(monthLabels({ from_date: "2025-02-28", to_date: "2027-02-10" }), [
		"2025-02",
		"2027-02",
	]);
});
test("same-month ranges keep the end on the right across January and leap years", () => {
	assert.deepEqual(monthLabels({ from_date: "2027-01-01", to_date: "2027-01-31" }), [
		"2026-12",
		"2027-01",
	]);
	assert.deepEqual(monthLabels({ from_date: "2024-02-29", to_date: "2024-02-29" }), [
		"2024-01",
		"2024-02",
	]);
});
test("calendar panels stay inside the supported year boundaries", () => {
	assert.deepEqual(monthLabels({ from_date: "1900-01-01", to_date: "1900-01-31" }), [
		"1900-01",
		"1900-01",
	]);
	assert.deepEqual(monthLabels({ from_date: "2200-12-01", to_date: "2200-12-31" }), [
		"2200-11",
		"2200-12",
	]);
});
test("empty dates default both panels around the business month", () => {
	assert.deepEqual(monthLabels({}), ["2026-10", "2026-11"]);
});
