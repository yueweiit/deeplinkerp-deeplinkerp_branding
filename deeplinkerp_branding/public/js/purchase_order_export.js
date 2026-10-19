(function (root, factory) {
	const exporter = factory();
	if (typeof module === "object" && module.exports) module.exports = exporter;
	root.DeepLinkERPPurchaseOrderExport = exporter;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
	"use strict";
 const translateText = (text, args = []) => String((globalThis.__ || (v => v))(text)).replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");

	// A narrow adapter for the single worksheet returned by reportview.export_query.
	// Preserve native XML/styles, strip Frappe's permission-check owner and optionally relabel text columns.
	const LIMITS = { maxArchiveBytes: 64 * 1024 * 1024, maxEntryBytes: 64 * 1024 * 1024, maxExpandedBytes: 128 * 1024 * 1024, maxEntries: 64 };
	const encoder = new TextEncoder();
	const decoder = new TextDecoder("utf-8", { fatal: true });
	const crcTable = Uint32Array.from({ length: 256 }, (_, value) => {
		for (let bit = 0; bit < 8; bit++) value = (value >>> 1) ^ ((value & 1) ? 0xedb88320 : 0);
		return value >>> 0;
	});

	function fail(message) { throw new Error(translateText(message)); }
	function crc32(bytes) {
		let crc = 0xffffffff;
		for (const byte of bytes) crc = (crc >>> 8) ^ crcTable[(crc ^ byte) & 0xff];
		return (crc ^ 0xffffffff) >>> 0;
	}
	function join(chunks, size = chunks.reduce((total, chunk) => total + chunk.length, 0)) {
		const result = new Uint8Array(size);
		let offset = 0;
		for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
		return result;
	}
	function bounds(bytes, offset, length) {
		if (offset < 0 || length < 0 || offset + length > bytes.length) fail(translateText("Excel ZIP 数据不完整或已损坏。"));
	}
	function noZip64(bytes, start, length) {
		const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
		for (let offset = start; offset < start + length;) {
			bounds(bytes, offset, 4);
			const type = view.getUint16(offset, true), size = view.getUint16(offset + 2, true);
			if (type === 1) fail(translateText("不支持 ZIP64 Excel；请缩小筛选范围后导出。"));
			if (offset + 4 + size > start + length) fail(translateText("Excel ZIP 扩展数据已损坏。"));
			offset += 4 + size;
		}
	}

	async function readStream(stream, maximum) {
		const reader = stream.getReader(), chunks = [];
		let size = 0;
		try {
			while (true) {
				const { done, value } = await reader.read();
				if (done) break;
				size += value.length;
				if (size > maximum) fail(translateText("Excel 数据大小过大；请缩小筛选范围后导出。"));
				chunks.push(value);
			}
		} catch (error) {
			await reader.cancel().catch(() => {});
			throw error;
		} finally { reader.releaseLock(); }
		return join(chunks, size);
	}

	async function inflate(bytes, expectedSize) {
		let decompressor;
		try { decompressor = new DecompressionStream("deflate-raw"); }
		catch (_) { fail(translateText("当前浏览器不支持 Excel 解压，请使用支持 DecompressionStream 的新版浏览器。")); }
		return readStream(new Blob([bytes]).stream().pipeThrough(decompressor), expectedSize);
	}

	async function readZip(input, options = {}) {
		const limits = { ...LIMITS, ...options };
		const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
		if (bytes.length > limits.maxArchiveBytes) fail(translateText("Excel ZIP 大小过大；请缩小筛选范围后导出。"));
		const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
		let end = -1;
		for (let offset = bytes.length - 22; offset >= Math.max(0, bytes.length - 65557); offset--) {
			if (view.getUint32(offset, true) === 0x06054b50 && offset + 22 + view.getUint16(offset + 20, true) === bytes.length) { end = offset; break; }
		}
		if (end < 0) fail(translateText("服务器未返回有效 Excel ZIP 文件。"));
		const count = view.getUint16(end + 10, true), directorySize = view.getUint32(end + 12, true), directoryOffset = view.getUint32(end + 16, true);
		if (count === 0xffff || directorySize === 0xffffffff || directoryOffset === 0xffffffff) fail(translateText("不支持 ZIP64 Excel；请缩小筛选范围后导出。"));
		if (view.getUint16(end + 4, true) || view.getUint16(end + 6, true) || view.getUint16(end + 8, true) !== count) fail(translateText("不支持分卷 Excel ZIP 文件。"));
		if (!count || count > limits.maxEntries || directoryOffset + directorySize !== end) fail(translateText("Excel ZIP 文件结构不符合原生导出格式。"));
		bounds(bytes, directoryOffset, directorySize);
		const entries = [], names = new Set(), occupied = [];
		let offset = directoryOffset, expanded = 0;
		for (let index = 0; index < count; index++) {
			bounds(bytes, offset, 46);
			if (view.getUint32(offset, true) !== 0x02014b50) fail(translateText("Excel ZIP 目录已损坏。"));
			const flags = view.getUint16(offset + 8, true), method = view.getUint16(offset + 10, true);
			const crc = view.getUint32(offset + 16, true), compressedSize = view.getUint32(offset + 20, true), size = view.getUint32(offset + 24, true);
			const nameLength = view.getUint16(offset + 28, true), extraLength = view.getUint16(offset + 30, true), commentLength = view.getUint16(offset + 32, true), local = view.getUint32(offset + 42, true);
			if ([size, compressedSize, local].includes(0xffffffff)) fail(translateText("不支持 ZIP64 Excel；请缩小筛选范围后导出。"));
			if (flags & 0x41 || ![0, 8].includes(method) || view.getUint16(offset + 34, true)) fail(translateText("Excel ZIP 压缩或加密格式不受支持。"));
			if (size > limits.maxEntryBytes || compressedSize > limits.maxArchiveBytes || (expanded += size) > limits.maxExpandedBytes) fail(translateText("Excel 解压数据大小过大；请缩小筛选范围后导出。"));
			bounds(bytes, offset + 46, nameLength + extraLength + commentLength);
			const nameBytes = bytes.subarray(offset + 46, offset + 46 + nameLength), name = decoder.decode(nameBytes);
			if (!name || names.has(name) || name.startsWith("/") || name.split("/").includes("..")) fail(translateText("Excel ZIP 文件名不符合原生导出格式。"));
			names.add(name);
			noZip64(bytes, offset + 46 + nameLength, extraLength);
			bounds(bytes, local, 30);
			if (view.getUint32(local, true) !== 0x04034b50 || view.getUint16(local + 8, true) !== method || view.getUint16(local + 6, true) !== flags) fail(translateText("Excel ZIP 文件头已损坏。"));
			const localName = view.getUint16(local + 26, true), localExtra = view.getUint16(local + 28, true), dataStart = local + 30 + localName + localExtra;
			bounds(bytes, local + 30, localName + localExtra);
			if (decoder.decode(bytes.subarray(local + 30, local + 30 + localName)) !== name || dataStart + compressedSize > directoryOffset) fail(translateText("Excel ZIP 文件位置已损坏。"));
			noZip64(bytes, local + 30 + localName, localExtra);
			occupied.push([local, dataStart + compressedSize]);
			const packed = bytes.subarray(dataStart, dataStart + compressedSize), data = method === 0 ? packed.slice() : await inflate(packed, size);
			if (data.length !== size || crc32(data) !== crc) fail(translateText("Excel ZIP CRC 校验失败，文件已损坏。"));
			entries.push({ name, data, time: view.getUint16(offset + 12, true), date: view.getUint16(offset + 14, true), attributes: view.getUint32(offset + 38, true) });
			offset += 46 + nameLength + extraLength + commentLength;
		}
		occupied.sort((a, b) => a[0] - b[0]);
		if (offset !== end || occupied.some((range, index) => index && occupied[index - 1][1] > range[0])) fail(translateText("Excel ZIP 文件范围已损坏。"));
		return entries;
	}

	function writeZip(entries) {
		if (!entries.length || entries.length > LIMITS.maxEntries) fail(translateText("Excel ZIP 文件数量不受支持。"));
		const localParts = [], centralParts = [], names = new Set();
		let offset = 0;
		for (const entry of entries) {
			if (names.has(entry.name)) fail(translateText("Excel ZIP 文件名重复。"));
			names.add(entry.name);
			const name = encoder.encode(entry.name), data = entry.data, crc = crc32(data);
			if (name.length > 65535 || data.length > LIMITS.maxEntryBytes) fail(translateText("Excel 数据大小过大。"));
			const local = new Uint8Array(30), central = new Uint8Array(46), lv = new DataView(local.buffer), cv = new DataView(central.buffer);
			for (const [position, value] of [[0, 0x04034b50], [14, crc], [18, data.length], [22, data.length]]) lv.setUint32(position, value, true);
			for (const [position, value] of [[4, 20], [6, 0x800], [10, entry.time || 0], [12, entry.date || 0], [26, name.length]]) lv.setUint16(position, value, true);
			for (const [position, value] of [[0, 0x02014b50], [16, crc], [20, data.length], [24, data.length], [38, entry.attributes || 0], [42, offset]]) cv.setUint32(position, value, true);
			for (const [position, value] of [[4, 20], [6, 20], [8, 0x800], [12, entry.time || 0], [14, entry.date || 0], [28, name.length]]) cv.setUint16(position, value, true);
			localParts.push(local, name, data); centralParts.push(central, name);
			offset += local.length + name.length + data.length;
			if (offset > LIMITS.maxExpandedBytes) fail(translateText("Excel 数据大小过大。"));
		}
		const central = join(centralParts), end = new Uint8Array(22), view = new DataView(end.buffer);
		view.setUint32(0, 0x06054b50, true); view.setUint16(8, entries.length, true); view.setUint16(10, entries.length, true);
		view.setUint32(12, central.length, true); view.setUint32(16, offset, true);
		return join([...localParts, central, end]);
	}

	function attribute(tag, name) { return tag.match(new RegExp(`(?:^|\\s)${name}\\s*=\\s*(["'])(.*?)\\1`))?.[2]; }
	function setAttribute(tag, name, value) { return tag.replace(new RegExp(`(\\b${name}\\s*=\\s*)(["'])(.*?)\\2`), (_, prefix, quote) => `${prefix}${quote}${value}${quote}`); }
	function columnIndex(label) {
		let result = 0;
		for (const letter of label) result = result * 26 + letter.charCodeAt(0) - 64;
		return result;
	}
	function columnLabel(index) {
		let label = "";
		while (index) { index--; label = String.fromCharCode(65 + index % 26) + label; index = Math.floor(index / 26); }
		return label;
	}
	function xmlText(xml) {
		return [...xml.matchAll(/<t\b[^>]*>([\s\S]*?)<\/t>/g)].map((match) => match[1]).join("").replace(/&(#x[\da-f]+|#\d+|amp|lt|gt|quot|apos);/gi, (_, entity) => {
			if (entity[0] === "#") return String.fromCodePoint(parseInt(entity.slice(entity[1].toLowerCase() === "x" ? 2 : 1), entity[1].toLowerCase() === "x" ? 16 : 10));
			return ({ amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" })[entity.toLowerCase()];
		});
	}

	function stripTrailingOwner(xml, { expectedColumns, ownerLabels = ["Owner", "Created By", "创建人"], sharedStrings = [], columnTransforms = {} }) {
		if (!Number.isInteger(expectedColumns) || expectedColumns < 1 || expectedColumns >= 16384) fail(translateText("Excel 列数量不符合预期。"));
		if (/<!DOCTYPE|<!ENTITY/i.test(xml) || !/<worksheet\b[^>]*xmlns="http:\/\/schemas.openxmlformats.org\/spreadsheetml\/2006\/main"/.test(xml)) fail(translateText("Excel 工作表格式不符合原生导出结构。"));
		const actualColumns = expectedColumns + 1, removed = columnLabel(actualColumns), retained = columnLabel(expectedColumns);
		const dimensions = [...xml.matchAll(/<dimension\b[^>]*\/>/g)];
		if (dimensions.length !== 1) fail(translateText("Excel 工作表 column shape 不符合预期。"));
		const dimension = attribute(dimensions[0][0], "ref"), range = dimension?.match(/^A1:([A-Z]+)([1-9]\d*)$/);
		if (!range || columnIndex(range[1]) !== actualColumns) fail(translateText("Excel 工作表 column shape 不符合预期，已停止导出。"));
		const sheets = [...xml.matchAll(/<sheetData\b[^>]*>([\s\S]*?)<\/sheetData>/g)];
		if (sheets.length !== 1) fail(translateText("Excel 工作表数据结构不符合预期。"));
		let header = false, lastRow = 0;
		const content = sheets[0][1].replace(/<row\b([^>]*)>([\s\S]*?)<\/row>/g, (row, attrs, body) => {
			const number = Number(attribute(attrs, "r"));
			if (!Number.isInteger(number) || number <= lastRow || number > 1048576) fail(translateText("Excel 工作表行结构不符合预期。"));
			lastRow = number;
			const seen = new Set();
			let extraHeader;
			let cells = body.replace(/<c\b[^>]*(?:\/>|>[\s\S]*?<\/c>)/g, (cell) => {
				const tag = cell.match(/^<c\b[^>]*>/)?.[0], reference = attribute(tag || cell, "r")?.match(/^([A-Z]+)([1-9]\d*)$/);
				if (!reference || Number(reference[2]) !== number) fail(translateText("Excel 单元格引用不符合预期。"));
				const column = columnIndex(reference[1]);
				if (column > actualColumns || seen.has(column)) fail(translateText("Excel 工作表 column shape 不符合预期。"));
				seen.add(column);
				if (column !== actualColumns) {
					const transform = columnTransforms[column];
					if (!transform) return cell;
					const type = attribute(tag || cell, "t");
					const blank = [undefined, "n"].includes(type) && !cell.match(/<v\b[^>]*>([\s\S]*?)<\/v>/)?.[1]?.trim();
					if (/<f\b/.test(cell) || (!blank && !["s", "inlineStr"].includes(type))) fail(translateText("Excel 审核状态列必须为文本，已停止导出。"));
					const value = blank ? "" : type === "s" ? sharedStrings[Number(cell.match(/<v>(\d+)<\/v>/)?.[1])] : xmlText(cell);
					if (value === undefined) fail(translateText("Excel 共享文本索引不符合预期。"));
					const text = String(number === 1 ? transform.header : transform.value(value)).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
					let opening = (tag || cell).replace(/\/>$/, ">");
					opening = type ? setAttribute(opening, "t", "inlineStr") : opening.replace(/>$/, ' t="inlineStr">');
					return `${opening}<is><t xml:space="preserve">${text}</t></is></c>`;
				}
				if (number === 1) {
					const type = attribute(tag || cell, "t");
					extraHeader = type === "s" ? sharedStrings[Number(cell.match(/<v>(\d+)<\/v>/)?.[1])] : type === "inlineStr" ? xmlText(cell) : null;
				}
				return "";
			});
			if (body.replace(/<c\b[^>]*(?:\/>|>[\s\S]*?<\/c>)/g, "").trim()) fail(translateText("Excel 单元格结构不符合原生导出格式。"));
			if (number === 1) {
				if (seen.size !== actualColumns || !ownerLabels.includes(extraHeader)) fail(translateText("Excel 尾列不是预期 owner / 创建人，已停止导出。"));
				header = true;
			}
			if (number > 1) for (const key of Object.keys(columnTransforms).map(Number).sort((a, b) => a - b)) {
				if (key < 1 || key > expectedColumns || seen.has(key)) continue;
				const text = String(columnTransforms[key].value("")).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
				const missing = `<c r="${columnLabel(key)}${number}" t="inlineStr"><is><t xml:space="preserve">${text}</t></is></c>`;
				let inserted = false;
				cells = cells.replace(/<c\b[^>]*(?:\/>|>[\s\S]*?<\/c>)/g, cell => {
					const label = attribute(cell.match(/^<c\b[^>]*>/)?.[0] || cell, "r")?.match(/^([A-Z]+)/)?.[1];
					if (!inserted && columnIndex(label) > key) { inserted = true; return missing + cell; }
					return cell;
				});
				if (!inserted) cells += missing;
			}
			const spans = attribute(attrs, "spans");
			if (spans) {
				if (!/^1:\d+$/.test(spans) || Number(spans.split(":")[1]) > actualColumns) fail(translateText("Excel 行范围不符合预期。"));
				attrs = setAttribute(attrs, "spans", `1:${Math.min(Number(spans.split(":")[1]), expectedColumns)}`);
			}
			return `<row${attrs}>${cells}</row>`;
		});
		if (!header || lastRow !== Number(range[2]) || sheets[0][1].replace(/<row\b[^>]*>[\s\S]*?<\/row>/g, "").trim()) fail(translateText("Excel 工作表行范围不符合预期。"));
		xml = xml.replace(sheets[0][0], sheets[0][0].replace(sheets[0][1], content));
		xml = xml.replace(dimensions[0][0], setAttribute(dimensions[0][0], "ref", `A1:${retained}${lastRow}`));
		xml = xml.replace(/<col\b[^>]*\/>/g, (col) => {
			const min = Number(attribute(col, "min")), max = Number(attribute(col, "max"));
			if (!Number.isInteger(min) || !Number.isInteger(max) || min < 1 || max < min || max > actualColumns) fail(translateText("Excel 列样式范围不符合预期。"));
			return min > expectedColumns ? "" : max > expectedColumns ? setAttribute(col, "max", expectedColumns) : col;
		});
		// Plain native exports have no structural references to the removed column.
		for (const match of xml.matchAll(/\bref\s*=\s*["']([^"']+)["']/g)) {
			if (new RegExp(`(?:^|:|\\s)${removed}[1-9]`).test(match[1])) fail(translateText("Excel 尾列含额外引用，已停止导出。"));
		}
		return xml;
	}

	async function removeNativeOwner(bytes, options) {
		const entries = await readZip(bytes), worksheets = entries.filter((entry) => /^xl\/worksheets\/[^/]+\.xml$/.test(entry.name));
		const workbook = entries.find((entry) => entry.name === "xl/workbook.xml");
		if (worksheets.length !== 1 || worksheets[0].name !== "xl/worksheets/sheet1.xml" || !workbook || !entries.some((entry) => entry.name === "[Content_Types].xml") || [...decoder.decode(workbook.data).matchAll(/<sheet\b/g)].length !== 1) fail(translateText("Excel 工作表数量不符合单表原生导出格式。"));
		const shared = entries.find((entry) => entry.name === "xl/sharedStrings.xml");
		const sharedStrings = shared ? [...decoder.decode(shared.data).matchAll(/<si\b[^>]*>([\s\S]*?)<\/si>/g)].map((match) => xmlText(match[1])) : [];
		worksheets[0].data = encoder.encode(stripTrailingOwner(decoder.decode(worksheets[0].data), { ...options, sharedStrings }));
		return writeZip(entries);
	}

	async function fetchNativeWorkbook(root, args, method = "frappe.desk.reportview.export_query") {
		const url = new URL(root.frappe.request.url || "/", root.location.href);
		if (url.origin !== root.location.origin) fail(translateText("Excel 导出必须使用同源 origin 请求。"));
		if (!root.frappe.csrf_token) fail(translateText("缺少导出所需的 CSRF 验证，请刷新页面后重试。"));
		const body = new URLSearchParams();
		for (const [key, value] of Object.entries({ ...args, cmd: method, ...(method === "frappe.desk.reportview.export_query" ? { file_format_type: "Excel" } : {}) })) {
			if (value !== null && value !== undefined) body.set(key, typeof value === "object" ? JSON.stringify(value) : String(value));
		}
		const response = await root.fetch(url.href, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Frappe-CSRF-Token": root.frappe.csrf_token }, body: body.toString() });
		const type = response.headers.get("content-type") || "";
		if (!response.ok) fail(translateText("原生 Excel 导出失败（HTTP {0}），请检查导出权限和筛选条件。", [response.status]));
		if (!/application\/(?:vnd\.openxmlformats-officedocument\.spreadsheetml\.sheet|octet-stream|vnd\.ms-excel)/i.test(type)) fail(translateText("原生 Excel 导出返回错误响应，已停止下载。"));
		const announced = Number(response.headers.get("content-length") || 0);
		if (announced > LIMITS.maxArchiveBytes) fail(translateText("Excel 文件大小过大；请缩小筛选范围后导出。"));
		const bytes = response.body ? await readStream(response.body, LIMITS.maxArchiveBytes) : new Uint8Array(await response.arrayBuffer());
		if (bytes.length > LIMITS.maxArchiveBytes || bytes[0] !== 0x50 || bytes[1] !== 0x4b) fail(translateText("服务器未返回有效 Excel ZIP 文件。"));
		return bytes;
	}

	async function exportExcel(root, args, options = {}) {
		const native = await fetchNativeWorkbook(root, args);
		const bytes = await removeNativeOwner(native, { ...options, expectedColumns: args.fields.length + 1, ownerLabels: [...new Set(["Owner", "Created By", "创建人", root.translateText("Owner"), root.translateText("Created By")])] });
		downloadWorkbook(root, bytes, args.title === "Purchase Order" ? "采购订单" : args.title === "Material Request" ? "物料申请" : args.title || args.doctype || "Export");
	}

	function downloadWorkbook(root, bytes, title) {
		const url = root.URL.createObjectURL(new root.Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
		const link = root.document.createElement("a");
		link.href = url; link.download = `${title}.xlsx`;
		root.document.body.appendChild(link);
		link.click(); link.remove();
		root.setTimeout(() => root.URL.revokeObjectURL(url), 1000);
	}

	return { crc32, readZip, writeZip, stripTrailingOwner, removeNativeOwner, fetchNativeWorkbook, exportExcel, downloadWorkbook };
});
