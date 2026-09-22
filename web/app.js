"use strict";

const $ = (id) => document.getElementById(id);
const api = async (path, body) => {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({ ok: false, error: response.statusText }));
  if (!response.ok || data.ok === false) throw new Error(data.error || response.statusText);
  return data;
};

const state = {
  settings: { width_dots: 576, encoding: "gbk", cut: "partial", feed_lines: 4 },
  tab: "text",
  bitmap: null,          // { data: Uint8Array, width, height }
  blocks: [],
  title: "小票模板",
};

/* ----------------------------------------------------------- text metrics */
const wideChar = (ch) => {
  const code = ch.codePointAt(0);
  return (code >= 0x1100 && code <= 0x115f) || (code >= 0x2e80 && code <= 0xa4cf) ||
         (code >= 0xac00 && code <= 0xd7a3) || (code >= 0xf900 && code <= 0xfaff) ||
         (code >= 0xfe30 && code <= 0xfe6f) || (code >= 0xff00 && code <= 0xff60) ||
         (code >= 0xffe0 && code <= 0xffe6) || (code >= 0x20000 && code <= 0x3fffd);
};
const textWidth = (text) => [...String(text)].reduce((sum, ch) => sum + (wideChar(ch) ? 2 : 1), 0);
const fitText = (text, columns) => {
  let out = "", used = 0;
  for (const ch of [...String(text)]) {
    const width = wideChar(ch) ? 2 : 1;
    if (used + width > columns) break;
    out += ch; used += width;
  }
  return out;
};
const padText = (text, columns, align = "left", filler = " ") => {
  text = text === undefined || text === null ? "" : String(text);
  const space = Math.max(0, columns - textWidth(text));
  if (align === "right") return filler.repeat(space) + text;
  if (align === "center") {
    const left = Math.floor(space / 2);
    return filler.repeat(left) + text + filler.repeat(space - left);
  }
  return text + filler.repeat(space);
};
const wrapText = (text, columns) => {
  const lines = [];
  for (const paragraph of String(text ?? "").split("\n")) {
    let line = "", used = 0;
    for (const ch of [...paragraph]) {
      const width = wideChar(ch) ? 2 : 1;
      if (used + width > columns) { lines.push(line); line = ""; used = 0; }
      line += ch; used += width;
    }
    lines.push(line);
  }
  return lines;
};

/* ------------------------------------------------------------- preview ops */
const SIZE_FACTOR = {
  normal: 1, "double-width": 1, "double-height": 1, double: 2, large: 3,
};
const SIZE_WIDTH_FACTOR = {
  normal: 1, "double-width": 2, "double-height": 1, double: 2, large: 3,
};

function opsFromText() {
  const align = $("text-align").value;
  const size = $("text-size").value;
  return [{
    kind: "text", text: $("text-body").value, align, size,
    bold: $("text-bold").checked, underline: Number($("text-underline").value),
    invert: $("text-invert").checked, font: $("text-font").value,
  }, { kind: "feed", lines: settingsNumber("feed_lines", 4) }, { kind: "cut" }];
}

function opsFromCode() {
  const type = $("code-type").value;
  if (type === "qr") {
    return [{ kind: "placeholder", label: "二维码 QR", height: 140, align: "center" },
            { kind: "cut" }];
  }
  return [{ kind: "placeholder", label: "条码 " + type.toUpperCase(),
            height: Number($("bc-height").value) + 24, align: "center" }, { kind: "cut" }];
}

function opsFromBlocks() {
  const columns = Math.max(1, Math.floor(settingsNumber("width_dots", 576) / 12));
  const ops = [];
  for (const block of state.blocks) {
    switch (block.type) {
      case "text":
        ops.push({ kind: "text", text: block.text, align: block.align, size: block.size,
                   bold: block.bold, underline: block.underline, invert: block.invert,
                   font: block.font });
        break;
      case "kv": {
        const space = Math.max(1, columns - textWidth(block.key) - textWidth(block.value));
        ops.push({ kind: "text", text: `${block.key}${" ".repeat(space)}${block.value}`,
                   align: "left", size: "normal", bold: block.bold });
        break;
      }
      case "row": {
        const widths = block.widths || [];
        const cells = block.cells || [];
        const line = widths.map((width, index) =>
          padText(fitText(cells[index] ?? "", width), width,
                  (block.aligns || [])[index] || "left")).join("");
        ops.push({ kind: "text", text: line, align: "left", size: "normal", bold: block.bold });
        break;
      }
      case "divider":
        ops.push({ kind: "text", text: (block.char || "-").repeat(columns), align: "left" });
        break;
      case "qr":
        ops.push({ kind: "placeholder", label: "二维码 QR", height: 140, align: block.align });
        break;
      case "barcode":
        ops.push({ kind: "placeholder", label: "条码 " + String(block.symbology).toUpperCase(),
                   height: Number(block.height || 80) + 24, align: block.align });
        break;
      case "image":
        ops.push({ kind: "bitmap", data: base64ToBytes(block.bitmap || ""),
                   width: Number(block.width || 576), height: Number(block.height || 0) });
        break;
      case "feed": ops.push({ kind: "feed", lines: Number(block.lines || 1) }); break;
      case "cut": ops.push({ kind: "cut" }); break;
      case "drawer":
        ops.push({ kind: "note", label: "弹钱箱", height: 18 }); break;
      case "beep":
        ops.push({ kind: "note", label: "蜂鸣 x" + (block.times || 1), height: 18 }); break;
      default: break;
    }
  }
  if (!state.blocks.some((b) => b.type === "cut")) ops.push({ kind: "cut" });
  return ops;
}

function opsFromImage() {
  if (!state.bitmap) return [{ kind: "note", label: "没有图片", height: 40 }];
  return [{ kind: "bitmap", data: state.bitmap.data, width: state.bitmap.width,
            height: state.bitmap.height }, { kind: "cut" }];
}

const currentOps = () => ({
  text: opsFromText, image: opsFromImage, code: opsFromCode, receipt: opsFromBlocks,
}[state.tab] || (() => []))();

/* ---------------------------------------------------------------- drawing */
const canvas = $("preview");
const context = canvas.getContext("2d");

function lineHeightFor(size) {
  return 30 * (SIZE_FACTOR[size] || 1);
}

function renderPreview() {
  const width = settingsNumber("width_dots", 576);
  const columns = Math.max(1, Math.floor(width / 12));
  const ops = currentOps();

  let height = 12;
  for (const op of ops) {
    if (op.kind === "text") {
      const wrapped = [];
      for (const line of String(op.text ?? "").split("\n")) {
        const chunks = wrapText(line, columns);
        wrapped.push(...chunks);
      }
      height += Math.max(1, wrapped.length) * lineHeightFor(op.size || "normal");
    } else if (op.kind === "feed") height += Number(op.lines || 1) * 30;
    else if (op.kind === "cut") height += 26;
    else if (op.kind === "bitmap") height += (op.height || 0) + 10;
    else height += (op.height || 20) + 10;
  }
  canvas.width = width;
  canvas.height = Math.max(120, Math.ceil(height + 16));
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.textBaseline = "top";

  let y = 10;
  for (const op of ops) {
    if (op.kind === "text") {
      const size = op.size || "normal";
      const scaleY = SIZE_FACTOR[size] || 1;
      const scaleX = SIZE_WIDTH_FACTOR[size] || 1;
      const cell = 24 * scaleY;
      context.font = `${op.bold ? "bold " : ""}${cell}px "Courier New", monospace`;
      context.fillStyle = op.invert ? "#000" : "#000";
      const lines = [];
      for (const line of String(op.text ?? "").split("\n")) lines.push(...wrapText(line, columns));
      for (const line of lines) {
        const lineWidthDots = textWidth(line) * 12 * scaleX;
        let x = 0;
        if (op.align === "center") x = Math.max(0, (canvas.width - lineWidthDots) / 2);
        else if (op.align === "right") x = Math.max(0, canvas.width - lineWidthDots);
        if (op.invert) {
          context.fillStyle = "#000";
          context.fillRect(x, y, Math.max(lineWidthDots, 12), cell);
          context.fillStyle = "#fff";
        } else {
          context.fillStyle = "#000";
        }
        let cursor = x;
        for (const ch of [...line]) {
          const advance = (wideChar(ch) ? 24 : 12) * scaleX;
          if (ch !== " ") context.fillText(ch, cursor, y, advance + 2);
          cursor += advance;
        }
        if (op.underline) {
          context.fillRect(x, y + cell - 2, lineWidthDots, op.underline === 2 ? 3 : 1);
        }
        y += lineHeightFor(size);
      }
      if (lines.length === 0) y += lineHeightFor(size);
    } else if (op.kind === "feed") {
      y += Number(op.lines || 1) * 30;
    } else if (op.kind === "cut") {
      context.setLineDash([6, 5]);
      context.strokeStyle = "#888";
      context.beginPath();
      context.moveTo(0, y + 10);
      context.lineTo(canvas.width, y + 10);
      context.stroke();
      context.setLineDash([]);
      y += 26;
    } else if (op.kind === "bitmap") {
      drawBitmap(op.data, op.width, op.height, y);
      y += (op.height || 0) + 10;
    } else {
      const boxHeight = op.height || 20;
      const boxWidth = Math.min(canvas.width - 8, 220);
      let x = 4;
      if (op.align === "center") x = (canvas.width - boxWidth) / 2;
      else if (op.align === "right") x = canvas.width - boxWidth - 4;
      context.setLineDash([5, 4]);
      context.strokeStyle = "#666";
      context.strokeRect(x, y, boxWidth, boxHeight);
      context.setLineDash([]);
      context.fillStyle = "#666";
      context.font = '14px "Segoe UI", sans-serif';
      context.fillText(op.label || "元素", x + 8, y + 8);
      y += boxHeight + 10;
    }
  }
  $("preview-meta").textContent =
    `${canvas.width}×${canvas.height} 点 · ${(canvas.width / 8).toFixed(0)}mm 宽`;
}

function drawBitmap(data, width, height, top) {
  if (!data || !data.length || !width || !height) return;
  const bytesPerLine = width / 8;
  const image = context.createImageData(width, height);
  for (let row = 0; row < height; row++) {
    for (let column = 0; column < width; column++) {
      const bit = data[row * bytesPerLine + (column >> 3)] & (0x80 >> (column & 7));
      const value = bit ? 0 : 255;
      const index = (row * width + column) * 4;
      image.data[index] = image.data[index + 1] = image.data[index + 2] = value;
      image.data[index + 3] = 255;
    }
  }
  context.putImageData(image, 0, top);
}

/* ------------------------------------------------------------- image tools */
async function loadImage(file) {
  const url = URL.createObjectURL(file);
  const image = new Image();
  await new Promise((resolve, reject) => {
    image.onload = resolve;
    image.onerror = reject;
    image.src = url;
  });
  URL.revokeObjectURL(url);
  return image;
}

function toGrey(image, widthDots) {
  widthDots -= widthDots % 8;
  const height = Math.max(1, Math.round(image.height * widthDots / image.width));
  const scratch = document.createElement("canvas");
  scratch.width = widthDots;
  scratch.height = height;
  const ctx = scratch.getContext("2d");
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, widthDots, height);
  ctx.drawImage(image, 0, 0, widthDots, height);
  const pixels = ctx.getImageData(0, 0, widthDots, height).data;
  const grey = new Uint8Array(widthDots * height);
  for (let i = 0, p = 0; i < grey.length; i++, p += 4) {
    grey[i] = (pixels[p] * 299 + pixels[p + 1] * 587 + pixels[p + 2] * 114) / 1000;
  }
  return { grey, width: widthDots, height };
}

function dither(grey, width, height, mode, threshold) {
  const out = new Uint8Array(width * height);
  if (mode === "threshold") {
    for (let i = 0; i < out.length; i++) out[i] = grey[i] < threshold ? 1 : 0;
    return out;
  }
  const taps = mode === "atkinson"
    ? [[1, 0, 1], [2, 0, 1], [-1, 1, 1], [0, 1, 1], [1, 1, 1], [0, 2, 1]]
    : [[1, 0, 7], [-1, 1, 3], [0, 1, 5], [1, 1, 1]];
  const divisor = mode === "atkinson" ? 8 : 16;
  let errors = new Float32Array(width + 2);
  for (let y = 0; y < height; y++) {
    const next = new Float32Array(width + 2);
    for (let x = 0; x < width; x++) {
      let value = grey[y * width + x] + errors[x + 1];
      value = Math.max(0, Math.min(255, value));
      const dot = value < threshold ? 1 : 0;
      out[y * width + x] = dot;
      const error = value - (dot ? 0 : 255);
      for (const [dx, dy, weight] of taps) {
        const target = dy ? next : errors;
        const index = x + 1 + dx;
        if (index >= 0 && index < width + 2) target[index] += error * weight / divisor;
      }
    }
    errors = next;
  }
  return out;
}

function trim(mono, width, height) {
  let top = 0, bottom = height - 1;
  const rowHasInk = (y) => {
    for (let x = 0; x < width; x++) if (mono[y * width + x]) return true;
    return false;
  };
  while (top < bottom && !rowHasInk(top)) top++;
  while (bottom > top && !rowHasInk(bottom)) bottom--;
  if (top === 0 && bottom === height - 1) return { mono, height };
  const slice = mono.slice(top * width, (bottom + 1) * width);
  return { mono: slice, height: bottom - top + 1 };
}

function pack(mono, width, height) {
  const bytesPerLine = width / 8;
  const data = new Uint8Array(bytesPerLine * height);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      if (mono[y * width + x]) data[y * bytesPerLine + (x >> 3)] |= 0x80 >> (x & 7);
    }
  }
  return data;
}

function bytesToBase64(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

function base64ToBytes(value) {
  try {
    const binary = atob(value);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  } catch { return new Uint8Array(0); }
}

let sourceImage = null;
function rebuildBitmap() {
  if (!sourceImage) return;
  const width = Number($("image-width").value);
  const mode = $("image-dither").value;
  const threshold = Number($("image-threshold").value);
  const { grey, width: w, height: h } = toGrey(sourceImage, width);
  let mono = dither(grey, w, h, mode, threshold);
  let height = h;
  if ($("image-trim").checked) ({ mono, height } = trim(mono, w, height));
  state.bitmap = { data: pack(mono, w, height), width: w, height };
  $("image-info").textContent =
    `${sourceImage.width}×${sourceImage.height} px → ${w}×${height} 点，${state.bitmap.data.length} 字节`;
  renderPreview();
}

/* ------------------------------------------------------------- receipt ui */
const BLOCK_DEFAULTS = {
  text: () => ({ type: "text", text: "文本内容", align: "left", size: "normal",
                 bold: false, underline: 0, invert: false, font: "a" }),
  kv: () => ({ type: "kv", key: "项目", value: "0.00", bold: false }),
  row: () => ({ type: "row", cells: ["品名", "1", "0.00"], widths: [28, 6, 12],
                aligns: ["left", "center", "right"], bold: false }),
  divider: () => ({ type: "divider", char: "-" }),
  qr: () => ({ type: "qr", data: "https://example.com", module: 6, ecc: "M",
               align: "center" }),
  barcode: () => ({ type: "barcode", data: "123456789012", symbology: "code128",
                    height: 80, width: 2, hri: "below", align: "center" }),
  feed: () => ({ type: "feed", lines: 1 }),
  cut: () => ({ type: "cut" }),
};

const BLOCK_FIELDS = {
  text: [
    { key: "text", label: "内容", type: "textarea" },
    { key: "align", label: "对齐", type: "select", options: ["left", "center", "right"] },
    { key: "size", label: "字号", type: "select",
      options: ["normal", "double-width", "double-height", "double", "large"] },
    { key: "bold", label: "加粗", type: "checkbox" },
    { key: "underline", label: "下划线", type: "number" },
    { key: "invert", label: "反白", type: "checkbox" },
    { key: "font", label: "字体", type: "select", options: ["a", "b"] },
  ],
  kv: [
    { key: "key", label: "名称", type: "text" },
    { key: "value", label: "值", type: "text" },
    { key: "bold", label: "加粗", type: "checkbox" },
  ],
  row: [
    { key: "cells", label: "单元格（逗号分隔）", type: "list" },
    { key: "widths", label: "列宽（逗号分隔）", type: "numbers" },
    { key: "bold", label: "加粗", type: "checkbox" },
  ],
  divider: [{ key: "char", label: "字符", type: "text" }],
  qr: [
    { key: "data", label: "内容", type: "text" },
    { key: "module", label: "模块大小", type: "number" },
    { key: "ecc", label: "纠错", type: "select", options: ["L", "M", "Q", "H"] },
    { key: "align", label: "对齐", type: "select", options: ["left", "center", "right"] },
  ],
  barcode: [
    { key: "data", label: "内容", type: "text" },
    { key: "symbology", label: "类型", type: "select",
      options: ["code128", "ean13", "code39", "upca", "itf"] },
    { key: "height", label: "条高", type: "number" },
    { key: "width", label: "条宽", type: "number" },
    { key: "hri", label: "文字", type: "select",
      options: ["below", "above", "both", "none"] },
  ],
  feed: [{ key: "lines", label: "行数", type: "number" }],
  cut: [],
};

function renderBlocks() {
  const container = $("blocks");
  container.innerHTML = "";
  state.blocks.forEach((block, index) => {
    const element = document.createElement("div");
    element.className = "block";
    const head = document.createElement("div");
    head.className = "block-head";
    head.innerHTML = `<span class="name">${index + 1}. ${block.type}</span>`;
    const tools = document.createElement("span");
    for (const [label, action] of [["↑", -1], ["↓", 1], ["✕", 0]]) {
      const button = document.createElement("button");
      button.className = "tiny";
      button.textContent = label;
      button.onclick = () => {
        if (action === 0) state.blocks.splice(index, 1);
        else {
          const target = index + action;
          if (target < 0 || target >= state.blocks.length) return;
          [state.blocks[index], state.blocks[target]] = [state.blocks[target], state.blocks[index]];
        }
        renderBlocks(); renderPreview();
      };
      tools.appendChild(button);
    }
    head.appendChild(tools);
    element.appendChild(head);

    const grid = document.createElement("div");
    grid.className = "block-grid";
    for (const field of BLOCK_FIELDS[block.type] || []) {
      const wrapper = document.createElement("label");
      wrapper.className = "field";
      wrapper.innerHTML = `<span>${field.label}</span>`;
      let input;
      if (field.type === "select") {
        input = document.createElement("select");
        for (const option of field.options) {
          const item = document.createElement("option");
          item.value = option; item.textContent = option;
          input.appendChild(item);
        }
      } else if (field.type === "checkbox") {
        input = document.createElement("input");
        input.type = "checkbox";
      } else if (field.type === "number") {
        input = document.createElement("input");
        input.type = "number";
      } else if (field.type === "textarea") {
        input = document.createElement("textarea");
        input.rows = 2;
      } else {
        input = document.createElement("input");
        input.type = "text";
      }
      if (field.type === "checkbox") input.checked = !!block[field.key];
      else if (field.type === "list") input.value = (block[field.key] || []).join(", ");
      else if (field.type === "numbers") input.value = (block[field.key] || []).join(", ");
      else input.value = block[field.key] ?? "";
      input.oninput = input.onchange = () => {
        if (field.type === "checkbox") block[field.key] = input.checked;
        else if (field.type === "number") block[field.key] = Number(input.value);
        else if (field.type === "list")
          block[field.key] = input.value.split(",").map((v) => v.trim()).filter(Boolean);
        else if (field.type === "numbers")
          block[field.key] = input.value.split(",").map((v) => Number(v.trim()) || 0);
        else block[field.key] = input.value;
        renderPreview();
      };
      wrapper.appendChild(input);
      grid.appendChild(wrapper);
    }
    element.appendChild(grid);
    container.appendChild(element);
  });
}

/* ------------------------------------------------------------------- tabs */
function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((button) =>
    button.classList.toggle("active", button.dataset.tab === tab));
  document.querySelectorAll(".panel").forEach((panel) =>
    panel.classList.toggle("active", panel.id === "panel-" + tab));
  if (tab === "history") refreshHistory();
  renderPreview();
}

/* ---------------------------------------------------------------- actions */
const settingsNumber = (key, fallback) => Number(state.settings[key] ?? fallback);

function jobForTab() {
  if (state.tab === "text") {
    return { kind: "text", text: $("text-body").value, align: $("text-align").value,
             size: $("text-size").value, font: $("text-font").value,
             bold: $("text-bold").checked, invert: $("text-invert").checked,
             underline: Number($("text-underline").value) };
  }
  if (state.tab === "image") {
    if (!state.bitmap) throw new Error("请先选择图片");
    return { kind: "bitmap", bitmap: bytesToBase64(state.bitmap.data),
             width: state.bitmap.width, height: state.bitmap.height };
  }
  if (state.tab === "code") {
    const type = $("code-type").value;
    if (type === "qr") {
      return { kind: "qr", data: $("code-data").value,
               module: Number($("qr-module").value), ecc: $("qr-ecc").value };
    }
    return { kind: "barcode", data: $("code-data").value, symbology: type,
             height: Number($("bc-height").value), width: Number($("bc-width").value),
             hri: $("bc-hri").value };
  }
  if (state.tab === "receipt") return { kind: "receipt", blocks: state.blocks };
  throw new Error("当前页签不支持打印");
}

function showResult(message, ok) {
  const element = $("result");
  element.textContent = message;
  element.className = "result " + (ok ? "ok" : "err");
}

async function submit(dryRun) {
  try {
    const job = jobForTab();
    job.dry_run = dryRun;
    const data = await api("/api/print", job);
    if (dryRun) {
      $("hex").textContent = `${data.bytes} 字节\n${data.hex}` +
        (data.truncated ? "\n…（已截断）" : "");
      showResult(`试运行：${data.summary}，${data.bytes} 字节`, true);
    } else {
      showResult(`已发送：${data.message}（${data.bytes} 字节）`, true);
      refreshHistory();
    }
  } catch (error) {
    showResult(error.message, false);
  }
}

async function refreshHistory() {
  try {
    const data = await api("/api/history?limit=50");
    const body = $("history-body");
    body.innerHTML = "";
    for (const entry of data.entries) {
      const row = document.createElement("tr");
      row.innerHTML =
        `<td>${entry.id}</td><td>${entry.created_at}</td><td>${entry.kind}</td>` +
        `<td>${entry.summary}</td><td>${entry.bytes}</td>` +
        `<td class="status-${entry.status}">${entry.status}</td>`;
      const actions = document.createElement("td");
      const button = document.createElement("button");
      button.className = "tiny";
      button.textContent = "重打";
      button.onclick = async () => {
        try {
          const result = await api(`/api/reprint/${entry.id}`, {});
          showResult("重打：" + result.message, true);
          refreshHistory();
        } catch (error) { showResult(error.message, false); }
      };
      actions.appendChild(button);
      row.appendChild(actions);
      body.appendChild(row);
    }
  } catch (error) { showResult(error.message, false); }
}

async function loadStatus() {
  const data = await api("/api/status");
  state.settings = data.settings;
  $("version").textContent = "v" + data.version;
  const printer = data.printer;
  const status = $("status");
  status.textContent = `${printer.target} · ${printer.available ? "就绪" : "不可用"} · ` +
    `${data.settings.width_dots} 点 · ${data.settings.encoding}`;
  status.className = "status " + (printer.available ? "ok" : "err");

  $("set-target").value = data.settings.target;
  $("set-width").value = String(data.settings.width_dots);
  $("set-encoding").value = data.settings.encoding;
  $("set-cut").value = data.settings.cut;
  $("set-feed").value = data.settings.feed_lines;
  $("set-chinese").checked = !!data.settings.chinese_mode;
  $("image-width").value = String(data.settings.width_dots);
}

async function saveSettings() {
  try {
    const data = await api("/api/settings", {
      target: $("set-target").value.trim(),
      width_dots: Number($("set-width").value),
      encoding: $("set-encoding").value,
      cut: $("set-cut").value,
      feed_lines: Number($("set-feed").value),
      chinese_mode: $("set-chinese").checked,
    });
    state.settings = data.settings;
    await loadStatus();
    renderPreview();
    showResult("设置已保存", true);
  } catch (error) { showResult(error.message, false); }
}

async function testPrint() {
  try {
    const data = await api("/api/test", {});
    showResult(`自检页已发送：${data.message}（${data.bytes} 字节）`, true);
    refreshHistory();
  } catch (error) { showResult(error.message, false); }
}

/* ------------------------------------------------------------------- init */
function bind() {
  document.querySelectorAll(".tab").forEach((button) =>
    button.onclick = () => switchTab(button.dataset.tab));
  document.querySelectorAll("[data-add]").forEach((button) =>
    button.onclick = () => {
      state.blocks.push(BLOCK_DEFAULTS[button.dataset.add]());
      renderBlocks(); renderPreview();
    });

  ["text-body", "text-align", "text-size", "text-font", "text-underline"]
    .forEach((id) => $(id).oninput = renderPreview);
  ["text-bold", "text-invert"].forEach((id) => $(id).onchange = renderPreview);

  $("dropzone").onclick = () => $("image-file").click();
  $("image-file").onchange = async (event) => {
    if (!event.target.files[0]) return;
    sourceImage = await loadImage(event.target.files[0]);
    rebuildBitmap();
  };
  for (const type of ["dragover", "dragleave", "drop"]) {
    $("dropzone").addEventListener(type, async (event) => {
      event.preventDefault();
      $("dropzone").classList.toggle("drag", type === "dragover");
      if (type === "drop" && event.dataTransfer.files[0]) {
        sourceImage = await loadImage(event.dataTransfer.files[0]);
        rebuildBitmap();
      }
    });
  }
  ["image-dither", "image-threshold", "image-width", "image-trim"]
    .forEach((id) => $(id).oninput = () => {
      $("threshold-out").textContent = $("image-threshold").value;
      rebuildBitmap();
    });

  $("code-type").onchange = () => {
    const isQr = $("code-type").value === "qr";
    $("qr-options").hidden = !isQr;
    $("barcode-options").hidden = isQr;
    renderPreview();
  };
  ["code-data", "qr-module", "qr-ecc", "bc-height", "bc-width", "bc-hri"]
    .forEach((id) => $(id).oninput = () => {
      $("qr-module-out").textContent = $("qr-module").value;
      $("bc-height-out").textContent = $("bc-height").value;
      $("bc-width-out").textContent = $("bc-width").value;
      renderPreview();
    });

  $("btn-json-export").onclick = () => {
    $("receipt-json").value = JSON.stringify(state.blocks, null, 2);
  };
  $("btn-json-import").onclick = () => {
    try {
      const parsed = JSON.parse($("receipt-json").value);
      if (!Array.isArray(parsed)) throw new Error("需要一个数组");
      state.blocks = parsed;
      renderBlocks(); renderPreview();
    } catch (error) { showResult("JSON 解析失败：" + error.message, false); }
  };

  $("btn-print").onclick = () => submit(false);
  $("btn-dry").onclick = () => submit(true);
  $("btn-save").onclick = saveSettings;
  $("btn-test").onclick = testPrint;
  $("btn-history-refresh").onclick = refreshHistory;
  $("btn-history-clear").onclick = async () => {
    try {
      const data = await api("/api/history/clear", {});
      showResult(`已清空 ${data.removed} 条历史`, true);
      refreshHistory();
    } catch (error) { showResult(error.message, false); }
  };
}

async function init() {
  bind();
  state.blocks = [
    { type: "text", text: "示例小票", align: "center", size: "double", bold: true,
      underline: 0, invert: false, font: "a" },
    { type: "divider", char: "-" },
    { type: "kv", key: "订单号", value: "20260923-0001", bold: false },
    { type: "row", cells: ["测试商品", "1", "12.00"], widths: [28, 6, 12],
      aligns: ["left", "center", "right"], bold: false },
    { type: "kv", key: "合计", value: "¥ 12.00", bold: true },
    { type: "qr", data: "https://example.com", module: 6, ecc: "M", align: "center" },
  ];
  renderBlocks();
  try { await loadStatus(); } catch (error) { showResult(error.message, false); }
  renderPreview();
}

init();
