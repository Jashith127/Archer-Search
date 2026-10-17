// Archer Setup: the dot-matrix field and the setup flow. DOM-only (no innerHTML), like archer.js.
// The backend is ArcherSetup.exe; every /api call carries the per-run token. In its own frameless
// window (WebView2) the page draws the window controls and posts drag/minimize/close.
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const TOKEN = $("meta[name=setup-token]").content;
const HOST = window.chrome && window.chrome.webview;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(path, body) {
  const init = { method: body === undefined ? "GET" : "POST", headers: { "X-Setup-Token": TOKEN } };
  if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
  const resp = await fetch("/api/" + path, init);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || resp.statusText);
  return data;
}

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid.nodeType ? kid : String(kid));
  return el;
}

function icon(id) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg"), use = document.createElementNS(NS, "use");
  use.setAttribute("href", "#" + id);
  svg.append(use);
  return svg;
}

let toastTimer;
function toast(text) {
  const t = $("[data-toast]");
  t.textContent = text;
  t.classList.add("is-shown");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("is-shown"), 1800);
}

const short = (name) => name.replace(/^(Google|Microsoft) /, "");
const elapsed = (ms) => { const s = Math.round(ms / 1000); return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`; };

// ------------------------------------------------------------------ logos
// Each provider's own mark in its brand colours: SearXNG, DuckDuckGo and Brave from Simple Icons
// (CC0, simple-icons 13.21.0), DuckDuckGo on its orange disc; Google's four-colour G.
const LOGOS = {
  searxng: { box: 24, parts: [{ fill: "#3050ff", d: "m13.716 17.261 6.873 6.582L24 20.282l-6.824-6.536a9.11 9.11 0 0 0 1.143-4.43c0-5.055-4.105-9.159-9.16-9.159S0 4.261 0 9.316c0 5.055 4.104 9.159 9.159 9.159a9.11 9.11 0 0 0 4.557-1.214ZM9.159 2.773a6.546 6.546 0 0 1 6.543 6.543 6.545 6.545 0 0 1-6.543 6.542 6.545 6.545 0 0 1-6.542-6.542 6.545 6.545 0 0 1 6.542-6.543ZM7.26 5.713a4.065 4.065 0 0 1 4.744.747 4.064 4.064 0 0 1 .707 4.749l1.157.611a5.376 5.376 0 0 0-.935-6.282 5.377 5.377 0 0 0-6.274-.987l.601 1.162Z" }] },
  // DuckDuckGo's full-colour mark (gilbarbara/logos, CC0, commit 4a5b550).
  duckduckgo: { box: 256, parts: [
    { fill: "#de5833", d: "M128.145,18.841 C188.147,18.841 236.788,67.482 236.788,127.484 C236.788,187.485 188.147,236.126 128.145,236.126 C68.144,236.126 19.503,187.485 19.503,127.484 C19.503,67.482 68.144,18.841 128.145,18.841" },
    { fill: "#de5833", d: "M128.143,254.922 C198.526,254.922 255.583,197.865 255.583,127.482 C255.583,57.099 198.526,0.042 128.143,0.042 C57.76,0.042 0.703,57.099 0.703,127.482 C0.703,197.865 57.76,254.922 128.143,254.922 L128.143,254.922 Z M128.143,244.302 C63.625,244.302 11.323,192 11.323,127.482 C11.323,62.964 63.625,10.662 128.143,10.662 C192.661,10.662 244.963,62.964 244.963,127.482 C244.963,192 192.661,244.302 128.143,244.302 L128.143,244.302 Z" },
    { fill: "#d5d7d8", d: "M9.219,12.13 C9.198,10.303 10.525,9.28 12.288,8.563 C11.481,8.695 10.708,8.897 10.012,9.209 C8.174,10.042 6.8,13.196 6.813,14.689 C15.736,13.787 28.931,14.411 38.58,17.291 C39.292,17.191 40.004,17.091 40.727,17.008 C31.103,12.735 19.661,11.085 9.219,12.13", at: [66, 42] },
    { fill: "#d5d7d8", d: "M11.048,1.15 C11.249,1.114 11.453,1.089 11.655,1.055 C9.73,1.294 8.715,1.982 7.27,2.219 C8.839,2.358 14.784,5.133 18.52,6.659 C19.044,6.46 19.516,6.239 19.901,5.978 C17.964,5.692 13.151,1.342 11.048,1.15", at: [66, 42] },
    { fill: "#d5d7d8", d: "M20.326,22.756 C19.791,22.962 19.283,23.177 18.843,23.408 C4.298,31.071 -2.127,48.97 1.702,70.418 C5.197,89.936 19.522,156.623 26.081,187.989 C27.996,188.662 29.934,189.287 31.896,189.854 C26.055,161.614 10.112,87.614 6.392,66.831 C2.621,45.688 6.29,30.517 20.326,22.756", at: [66, 42] },
    { fill: "#ffffff", d: "M79.184,176.618 C78.944,176.778 78.658,176.928 78.353,177.074 C78.127,177.95 77.782,178.614 77.279,178.948 C74.275,180.941 65.79,181.945 61.31,180.941 C60.539,180.77 59.955,180.487 59.499,180.098 C51.85,184.345 40.85,189.999 38.616,188.725 C35.126,186.724 34.62,160.274 35.126,153.783 C35.495,148.883 52.707,156.82 61.08,160.983 C62.931,159.254 67.466,158.097 71.48,157.704 C65.415,142.943 60.942,126.059 63.684,114.086 C59.894,111.445 54.871,105.32 55.917,98.927 C56.723,94.061 69.297,84.855 78.2,84.299 C87.123,83.736 89.904,83.863 97.338,82.083 C97.71,81.994 98.119,81.896 98.547,81.793 C103.123,65.706 92.148,37.719 79.906,25.472 C75.915,21.481 69.779,18.969 62.865,17.643 C60.206,13.994 55.917,10.505 49.845,7.274 C38.57,1.288 24.636,-1.149 11.655,1.055 C11.453,1.089 11.249,1.114 11.048,1.15 C13.151,1.342 17.964,5.692 19.901,5.978 C19.516,6.239 19.044,6.46 18.52,6.659 C16.702,7.351 14.231,7.771 12.288,8.563 C10.525,9.28 9.198,10.303 9.219,12.13 C19.661,11.085 31.103,12.735 40.727,17.008 C40.004,17.091 39.292,17.191 38.58,17.291 C31.755,18.251 25.482,20.019 20.95,22.41 C20.737,22.521 20.535,22.641 20.326,22.756 C6.29,30.517 2.621,45.688 6.392,66.831 C10.112,87.614 26.1589994,162.759995 31.9999994,190.999995 C41.6029994,193.778995 50.5819993,195.999995 61.0799993,195.999995 C69.9919993,195.999995 80.7110013,194.024995 89.0000013,191.999995 C86.0890013,186.386995 82.344,180.18 80.036,175.729 C79.728,176.096 79.485,176.417 79.184,176.618 Z M85.057,70.057 C81.253,70.057 78.15,66.963 78.15,63.137 C78.15,59.329 81.253,56.234 85.057,56.234 C88.882,56.234 91.973,59.329 91.973,63.137 C91.973,66.963 88.882,70.057 85.057,70.057 L85.057,70.057 Z M89.218,44.049 C89.218,44.049 84.861,41.56 81.48,41.602 C74.532,41.693 72.64,44.763 72.64,44.763 C72.64,44.763 73.806,37.445 82.691,38.913 C87.508,39.714 89.218,44.049 89.218,44.049 L89.218,44.049 Z M15.963,53.046 C15.963,53.046 12.834,46.073 21.173,42.656 C29.521,39.238 33.586,44.601 33.586,44.601 C33.586,44.601 27.524,41.859 21.63,45.563 C15.746,49.263 15.963,53.046 15.963,53.046 L15.963,53.046 Z M23.253,67.908 C23.253,63.462 26.848,59.864 31.3,59.864 C35.74,59.864 39.34,63.462 39.34,67.908 C39.34,72.355 35.74,75.949 31.3,75.949 C26.848,75.951 23.253,72.355 23.253,67.908 L23.253,67.908 Z", at: [66, 42] },
    { fill: "#2d4f8e", d: "M39.34,67.908 C39.34,63.462 35.74,59.864 31.3,59.864 C26.848,59.864 23.253,63.462 23.253,67.908 C23.253,72.355 26.848,75.951 31.3,75.949 C35.74,75.949 39.34,72.355 39.34,67.908 L39.34,67.908 Z M34.862,67.317 C33.72,67.32 32.781,66.381 32.781,65.227 C32.781,64.07 33.717,63.139 34.862,63.139 C36.016,63.139 36.952,64.07 36.952,65.227 C36.952,66.381 36.016,67.317 34.862,67.317 L34.862,67.317 Z", at: [66, 42] },
    { fill: "#ffffff", d: "M34.862,63.139 C33.717,63.139 32.781,64.07 32.781,65.227 C32.781,66.381 33.72,67.32 34.862,67.317 C36.016,67.317 36.952,66.381 36.952,65.227 C36.952,64.07 36.016,63.139 34.862,63.139", at: [66, 42] },
    { fill: "#2d4f8e", d: "M85.057,56.234 C81.253,56.234 78.15,59.329 78.15,63.137 C78.15,66.963 81.253,70.057 85.057,70.057 C88.882,70.057 91.973,66.963 91.973,63.137 C91.973,59.329 88.882,56.234 85.057,56.234 L85.057,56.234 Z M88.124,62.628 C87.149,62.628 86.344,61.835 86.344,60.839 C86.344,59.856 87.149,59.049 88.124,59.049 C89.141,59.049 89.921,59.856 89.921,60.839 C89.921,61.835 89.141,62.628 88.124,62.628 L88.124,62.628 Z", at: [66, 42] },
    { fill: "#ffffff", d: "M88.124,59.049 C87.149,59.049 86.344,59.856 86.344,60.839 C86.344,61.835 87.149,62.628 88.124,62.628 C89.141,62.628 89.921,61.835 89.921,60.839 C89.921,59.856 89.141,59.049 88.124,59.049", at: [66, 42] },
    { fill: "#4c5fac", d: "M33.586,44.601 C33.586,44.601 29.521,39.238 21.173,42.656 C12.834,46.073 15.963,53.046 15.963,53.046 C15.963,53.046 15.746,49.263 21.63,45.563 C27.524,41.859 33.586,44.601 33.586,44.601", at: [66, 42] },
    { fill: "#4c5fac", d: "M82.691,38.913 C73.806,37.445 72.64,44.763 72.64,44.763 C72.64,44.763 74.532,41.693 81.48,41.602 C84.861,41.56 89.218,44.049 89.218,44.049 C89.218,44.049 87.508,39.714 82.691,38.913", at: [66, 42] },
    { fill: "#fdd209", d: "M78.2,84.299 C69.297,84.855 56.723,94.061 55.917,98.927 C54.871,105.32 59.894,111.445 63.684,114.086 C63.694,114.093 63.705,114.102 63.715,114.109 C67.504,116.745 92.733,125.256 105.25,125.02 C117.781,124.776 138.361,117.102 136.101,110.953 C133.851,104.802 113.412,116.38 92.094,114.404 C76.306,112.937 73.519,105.864 77.015,100.698 C81.412,94.205 89.421,101.93 102.631,97.977 C115.861,94.035 134.363,86.979 141.228,83.137 C157.101,74.288 134.586,70.618 129.268,73.073 C124.227,75.402 106.681,79.83 98.547,81.793 C98.119,81.896 97.71,81.994 97.338,82.083 C89.904,83.863 87.123,83.736 78.2,84.299", at: [66, 42] },
    { fill: "#65bc46", d: "M24.316,17.97 C24.316,17.049 25.057,16.234 26.233,15.539 C26.266,14.98 26.57,14.46 27.08,13.983 C18.707,9.82 1.495,1.883 1.126,6.783 C0.62,13.274 1.126,39.724 4.616,41.725 C6.85,42.999 17.85,37.345 25.499,33.098 C23.292,31.217 24.316,26.651 24.316,17.97", at: [100, 189] },
    { fill: "#65bc46", d: "M45.925,28.686 C45.961,28.7 46,28.715 46.036,28.729 C52.89,31.374 66.534,36.353 69.497,35.266 C73.492,33.739 72.492,1.813 68.008,0.796 C64.416,-0.001 50.665,9.688 45.255,13.635 C46.212,17.676 47.37,25.68 45.925,28.686", at: [100, 189] },
    { fill: "#43a244", d: "M29.214,31.611 C24.719,30.615 26.221,26.118 26.221,15.64 C26.221,15.606 26.231,15.573 26.233,15.539 C25.057,16.234 24.316,17.049 24.316,17.97 C24.316,26.651 23.292,31.217 25.499,33.098 C25.955,33.487 26.539,33.77 27.31,33.941 C31.79,34.945 40.275,33.941 43.279,31.948 C43.782,31.614 44.127,30.95 44.353,30.074 C40.837,31.758 33.329,32.547 29.214,31.611", at: [100, 189] },
    { fill: "#65bc46", d: "M27.08,13.983 C26.57,14.46 26.266,14.98 26.233,15.539 C26.231,15.573 26.221,15.606 26.221,15.64 C26.221,26.118 24.719,30.615 29.214,31.611 C33.329,32.547 40.837,31.758 44.353,30.074 C44.658,29.928 44.944,29.778 45.184,29.618 C45.485,29.417 45.728,29.096 45.925,28.686 C47.37,25.68 46.212,17.676 45.255,13.635 C45.044,12.746 44.844,12.046 44.683,11.636 C44.27,10.614 41.089,10.351 37.48,10.704 C33.466,11.097 28.931,12.254 27.08,13.983", at: [100, 189] },
  ] },
  google: { box: 48, parts: [
    { fill: "#4285f4", d: "M45.12 24.5c0-1.56-.14-3.06-.4-4.5H24v8.51h11.84c-.51 2.75-2.06 5.08-4.39 6.64v5.52h7.11c4.16-3.83 6.56-9.47 6.56-16.17z" },
    { fill: "#34a853", d: "M24 46c5.94 0 10.92-1.97 14.56-5.33l-7.11-5.52c-1.97 1.32-4.49 2.1-7.45 2.1-5.73 0-10.58-3.87-12.31-9.07H4.34v5.7C7.96 41.07 15.4 46 24 46z" },
    { fill: "#fbbc05", d: "M11.69 28.18C11.25 26.86 11 25.45 11 24s.25-2.86.69-4.18v-5.7H4.34C2.85 17.09 2 20.45 2 24c0 3.55.85 6.91 2.34 9.88l7.35-5.7z" },
    { fill: "#ea4335", d: "M24 10.75c3.23 0 6.13 1.11 8.41 3.29l6.31-6.31C34.91 4.18 29.93 2 24 2 15.4 2 7.96 6.93 4.34 14.12l7.35 5.7c1.73-5.2 6.58-9.07 12.31-9.07z" },
  ] },
  brave: { box: 24, parts: [{ fill: "#fb542b", d: "M15.68 0l2.096 2.38s1.84-.512 2.709.358c.868.87 1.584 1.638 1.584 1.638l-.562 1.381.715 2.047s-2.104 7.98-2.35 8.955c-.486 1.919-.818 2.66-2.198 3.633-1.38.972-3.884 2.66-4.293 2.916-.409.256-.92.692-1.38.692-.46 0-.97-.436-1.38-.692a185.796 185.796 0 01-4.293-2.916c-1.38-.973-1.712-1.714-2.197-3.633-.247-.975-2.351-8.955-2.351-8.955l.715-2.047-.562-1.381s.716-.768 1.585-1.638c.868-.87 2.708-.358 2.708-.358L8.321 0h7.36zm-3.679 14.936c-.14 0-1.038.317-1.758.69-.72.373-1.242.637-1.409.742-.167.104-.065.301.087.409.152.107 2.194 1.69 2.393 1.866.198.175.489.464.687.464.198 0 .49-.29.688-.464.198-.175 2.24-1.759 2.392-1.866.152-.108.254-.305.087-.41-.167-.104-.689-.368-1.41-.741-.72-.373-1.617-.69-1.757-.69zm0-11.278s-.409.001-1.022.206-1.278.46-1.584.46c-.307 0-2.581-.434-2.581-.434S4.119 7.152 4.119 7.849c0 .697.339.881.68 1.243l2.02 2.149c.192.203.59.511.356 1.066-.235.555-.58 1.26-.196 1.977.384.716 1.042 1.194 1.464 1.115.421-.08 1.412-.598 1.776-.834.364-.237 1.518-1.19 1.518-1.554 0-.365-1.193-1.02-1.413-1.168-.22-.15-1.226-.725-1.247-.95-.02-.227-.012-.293.284-.851.297-.559.831-1.304.742-1.8-.089-.495-.95-.753-1.565-.986-.615-.232-1.799-.671-1.947-.74-.148-.068-.11-.133.339-.175.448-.043 1.719-.212 2.292-.052.573.16 1.552.403 1.632.532.079.13.149.134.067.579-.081.445-.5 2.581-.541 2.96-.04.38-.12.63.288.724.409.094 1.097.256 1.333.256s.924-.162 1.333-.256c.408-.093.329-.344.288-.723-.04-.38-.46-2.516-.541-2.961-.082-.445-.012-.45.067-.579.08-.129 1.059-.372 1.632-.532.573-.16 1.845.009 2.292.052.449.042.487.107.339.175-.148.069-1.332.508-1.947.74-.615.233-1.476.49-1.565.986-.09.496.445 1.241.742 1.8.297.558.304.624.284.85-.02.226-1.026.802-1.247.95-.22.15-1.413.804-1.413 1.169 0 .364 1.154 1.317 1.518 1.554.364.236 1.355.755 1.776.834.422.079 1.08-.4 1.464-1.115.384-.716.039-1.422-.195-1.977-.235-.555.163-.863.355-1.066l2.02-2.149c.341-.362.68-.546.68-1.243 0-.697-2.695-3.96-2.695-3.96s-2.274.436-2.58.436c-.307 0-.972-.256-1.585-.461-.613-.205-1.022-.206-1.022-.206z" }] },
};

const LOGO_SIZE = 112;  // the chosen logo in the left panel (.stage-logo)

function logo(name) {
  const NS = "http://www.w3.org/2000/svg", spec = LOGOS[name], svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${spec.box} ${spec.box}`);
  svg.setAttribute("aria-hidden", "true");
  for (const part of spec.parts) {
    const el = document.createElementNS(NS, part.circle ? "circle" : "path");
    if (part.circle) { el.setAttribute("cx", part.circle); el.setAttribute("cy", part.circle); el.setAttribute("r", part.circle); }
    else el.setAttribute("d", part.d);
    if (part.at) el.setAttribute("transform", `translate(${part.at[0]} ${part.at[1]})`);
    el.setAttribute("fill", part.fill);
    svg.append(el);
  }
  return svg;
}

// ------------------------------------------------------------------ the field
// A grid of rounded dots behind the whole window. Each moment gives it a target shape at an anchor
// (the middle of the hero, or the left panel); dots ease toward their new brightness in a wave from
// the centre, so moving the anchor makes the mark travel. On top: pointer glow, twinkle,
// a scanning band, progress (the shape lights from the bottom up) and ripples.
const hash = (x, y) => { let h = (x * 374761393 + y * 668265263) ^ 0x5bd1e995; h = Math.imul(h ^ (h >>> 13), 1274126177); return ((h ^ (h >>> 16)) >>> 0) / 4294967295; };
class Field {
  constructor(canvas) {
    this.c = canvas; this.g = canvas.getContext("2d");
    this.pitch = 8; this.tempo = 1; this.kind = "none"; this.progress = null; this.scan = false; this.glow = 0; this.ripples = [];
    this.anchor = { x: .5, y: .4, size: 168 };  // x and y as fractions of the window
    this.mx = this.my = -1e4;
    const app = document.querySelector(".app");
    app.addEventListener("pointermove", (e) => { const r = canvas.getBoundingClientRect(); this.mx = e.clientX - r.left; this.my = e.clientY - r.top; });
    app.addEventListener("pointerleave", () => { this.mx = this.my = -1e4; });
    canvas.parentElement.addEventListener("pointerdown", (e) => { const r = canvas.getBoundingClientRect(); this.ripple(e.clientX - r.left, e.clientY - r.top, 0.5); });
    new ResizeObserver(() => this.layout()).observe(canvas);
    requestAnimationFrame((t) => this.frame(t));
  }

  layout() {
    const w = this.c.clientWidth, h = this.c.clientHeight, dpr = Math.min(devicePixelRatio || 1, 2);
    if (!w || !h) return;
    this.c.width = Math.round(w * dpr); this.c.height = Math.round(h * dpr);
    this.g.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.w = w; this.h = h; this.dpr = dpr;
    const p = this.pitch;
    this.cols = Math.floor(w / p); this.rows = Math.floor(h / p);
    this.ox = (w - this.cols * p) / 2 + p / 2; this.oy = (h - this.rows * p) / 2 + p / 2;
    const n = this.cols * this.rows;
    this.cur = new Float32Array(n); this.from = new Float32Array(n); this.to = new Float32Array(n);
    this.t0 = new Float32Array(n); this.seed = new Float32Array(n);
    // Each dot's colour, eased from one shape's to the next (logos are drawn in their own colours).
    this.rgbFrom = new Uint8Array(n * 3).fill(237); this.rgbTo = new Uint8Array(n * 3).fill(237);
    this.tints = new Map();
    for (let k = 0; k < n; k++) this.seed[k] = hash(k % this.cols, (k / this.cols) | 0);
    // One pre-rendered dot, stamped with varying alpha: far cheaper than a path per dot.
    const sq = p * 0.74, sp = document.createElement("canvas");
    sp.width = sp.height = Math.ceil(sq * dpr) + 2;
    const sg = sp.getContext("2d"); sg.scale(dpr, dpr); sg.fillStyle = "#ededed";
    sg.beginPath(); sg.roundRect(1 / dpr, 1 / dpr, sq, sq, sq * 0.22); sg.fill();
    this.sprite = sp; this.sq = sq;
    this.background = document.createElement("canvas");
    this.background.width = this.c.width; this.background.height = this.c.height;
    const bg = this.background.getContext("2d"); bg.scale(dpr, dpr); bg.globalAlpha = .045;
    for (let r = 0; r < this.rows; r++) for (let c = 0; c < this.cols; c++)
      bg.drawImage(sp, this.ox + c * p - sq / 2, this.oy + r * p - sq / 2, sp.width / dpr, sp.height / dpr);
    this.shape(this.kind, true, true);
  }

  // A dot sprite in one colour, quantised so a colour morph needs only a few hundred sprites.
  tint(r, g, b) {
    const q = (v) => Math.min(255, Math.round(v / 17) * 17), key = (q(r) << 16) | (q(g) << 8) | q(b);
    let sp = this.tints.get(key);
    if (!sp) {
      const dpr = this.dpr, sq = this.sq;
      sp = document.createElement("canvas");
      sp.width = sp.height = Math.ceil(sq * dpr) + 2;
      const sg = sp.getContext("2d"); sg.scale(dpr, dpr); sg.fillStyle = `rgb(${q(r)},${q(g)},${q(b)})`;
      sg.beginPath(); sg.roundRect(1 / dpr, 1 / dpr, sq, sq, sq * 0.22); sg.fill();
      this.tints.set(key, sp);
    }
    return sp;
  }

  // Where the artwork sits: x and y as fractions of the window, size in pixels.
  place(anchor) {
    this.anchor = anchor;
    if (this.cols) this.shape(this.kind, false, true);
  }

  sample(kind) {
    const cols = this.cols, rows = this.rows, p = this.pitch;
    const map = new Float32Array(cols * rows);
    const x = this.anchor.x * this.w, y = this.anchor.y * this.h, size = this.anchor.size;
    this.caret = null;
    this.box = { top: (y - size / 2) / p, bottom: (y + size / 2) / p };
    const SS = 4, off = document.createElement("canvas");
    off.width = cols * SS; off.height = rows * SS;
    const g = off.getContext("2d", { willReadFrequently: true }), scale = SS / p;
    g.scale(scale, scale);
    if (kind.startsWith("logo:")) {
      // The real logo, rasterised into the dot grid at exactly where its crisp copy sits.
      const spec = LOGOS[kind.slice(5)], L = LOGO_SIZE;
      g.translate(x - L / 2, y - L / 2); g.scale(L / spec.box, L / spec.box);
      for (const part of spec.parts) {
        g.fillStyle = part.fill;
        if (part.circle) { g.beginPath(); g.arc(part.circle, part.circle, part.circle, 0, Math.PI * 2); g.fill(); }
        else { g.save(); if (part.at) g.translate(part.at[0], part.at[1]); g.fill(new Path2D(part.d)); g.restore(); }
      }
      const data = g.getImageData(0, 0, off.width, off.height).data, rgb = new Uint8Array(cols * rows * 3).fill(237);
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
        let a = 0, R = 0, G = 0, B = 0;
        for (let j = 0; j < SS; j++) for (let i = 0; i < SS; i++) {
          const o = ((r * SS + j) * off.width + c * SS + i) * 4, al = data[o + 3];
          a += al; R += data[o] * al; G += data[o + 1] * al; B += data[o + 2] * al;
        }
        const k = r * cols + c;
        map[k] = a / (SS * SS * 255);
        if (a) { rgb[k * 3] = R / a; rgb[k * 3 + 1] = G / a; rgb[k * 3 + 2] = B / a; }
      }
      return { map, rgb };
    }
    g.translate(x - size / 2, y - size / 2); g.scale(size / 100, size / 100);
    g.fillStyle = g.strokeStyle = "#fff"; g.lineWidth = 5.5; g.lineCap = "round"; g.lineJoin = "round";
    if (kind === "ascent") {
      g.beginPath(); g.moveTo(50, 3); g.lineTo(95, 95); g.lineTo(71, 95); g.lineTo(50, 49); g.lineTo(29, 95); g.lineTo(5, 95); g.closePath(); g.fill();
    } else if (kind === "window") {
      g.strokeRect(5, 12, 90, 76); g.beginPath(); g.moveTo(5, 31); g.lineTo(95, 31); g.stroke();
      for (let i = 0; i < 3; i++) g.fillRect(13 + i * 9, 20, 3, 3);
      g.fillRect(16, 43, 20, 4); g.fillRect(16, 55, 20, 4); g.fillRect(45, 43, 36, 5); g.fillRect(45, 56, 30, 4); g.fillRect(65, 72, 17, 6);
    } else if (kind === "caret") {
      g.strokeRect(3, 29, 94, 42); g.beginPath(); g.arc(23, 48, 8, 0, Math.PI * 2); g.moveTo(29, 54); g.lineTo(35, 60); g.moveTo(51, 39); g.lineTo(51, 61); g.stroke();
      this.caret = [(x - size / 2 + size * .47) / p, (x - size / 2 + size * .55) / p];
    }
    if (kind !== "none") {
      const data = g.getImageData(0, 0, off.width, off.height).data;
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
        let a = 0;
        for (let j = 0; j < SS; j++) for (let i = 0; i < SS; i++) a += data[((r * SS + j) * off.width + c * SS + i) * 4 + 3];
        map[r * cols + c] = a / (SS * SS * 255);
      }
    }
    return { map, rgb: null };
  }

  shape(kind, instant, force) {
    if (kind === this.kind && !instant && !force) return;
    this.kind = kind; this.dirty = true;
    if (!this.cols) return;
    const { map, rgb } = this.sample(kind), now = performance.now();
    const cx = this.anchor.x * this.cols, cy = this.anchor.y * this.rows, maxD = Math.hypot(this.cols, this.rows) * .6;
    this.settlesAt = now + 2600; this.settled = false;
    this.active = [];
    for (let k = 0; k < map.length; k++) {
      if (map[k] > .001 || this.cur[k] > .001) this.active.push(k);
      const c = k % this.cols, r = (k / this.cols) | 0;
      this.from[k] = instant ? map[k] : this.cur[k];
      this.to[k] = map[k];
      // A dot keeps the colour it shows now and eases to the new shape's; a dot that is coming or
      // going takes the colour of the shape it belongs to.
      for (let ch = 0; ch < 3; ch++) {
        const next = rgb ? rgb[k * 3 + ch] : 237, prev = this.rgbTo[k * 3 + ch];
        const shown = this.cur[k] >= .02, kept = map[k] >= .02;
        this.rgbFrom[k * 3 + ch] = instant || !shown ? next : prev;
        this.rgbTo[k * 3 + ch] = shown && !kept ? prev : next;
      }
      // A wave outward from the shape's centre, roughened per dot so it never looks mechanical.
      this.t0[k] = now + (instant || REDUCED ? -1e5 : ((Math.hypot(c - cx, r - cy) / maxD) * 560 + this.seed[k] * 260) * this.tempo);
    }
  }

  // Scatter every dot first, then let the shape assemble out of the noise (the opening moment).
  intro(kind) {
    this.kind = "none";
    if (!this.cols || REDUCED) { this.shape(kind, true); return; }
    for (let k = 0; k < this.cur.length; k++) this.cur[k] = this.seed[k] > 0.86 ? this.seed[k] * 0.8 : 0;
    this.shape(kind);
    const now = performance.now();
    for (let k = 0; k < this.t0.length; k++) this.t0[k] = now + 200 + this.seed[k] * 1000;
  }

  ripple(x, y, strength = 1) {
    if (REDUCED || !this.w) return;
    this.ripples = this.ripples.slice(-3);
    this.ripples.push({ x: x ?? this.anchor.x * this.w, y: y ?? this.anchor.y * this.h, t: performance.now(), s: strength });
  }

  frame(now) {
    requestAnimationFrame((t) => this.frame(t));
    if (!this.cols || document.hidden || now - (this.lastFrame || 0) < 1000 / 30) return;
    if (REDUCED && !this.dirty && this.lastProgress === this.progress) return;
    this.lastFrame = now; this.lastProgress = this.progress; this.dirty = false;
    if (!this.settled && now > this.settlesAt) { this.active = this.active.filter(k => this.to[k] > .001); this.settled = true; }
    const g = this.g, p = this.pitch, cols = this.cols, rows = this.rows, sp = this.sprite;
    const DUR = 700 * this.tempo, half = this.sq / 2;
    g.clearRect(0, 0, this.w, this.h);
    g.drawImage(this.background, 0, 0, this.w, this.h);
    this.ripples = this.ripples.filter((r) => now - r.t < 2400);
    const box = this.box, prog = this.progress;
    const progRow = box && prog != null ? box.bottom - (box.bottom - box.top) * prog : null;
    const scanY = this.scan ? ((now / 2300) % 1) * (rows + 16) - 8 : null;
    const blink = this.caret && Math.floor(now / 560) % 2 === 0;
    const drift = REDUCED || this.still ? 0 : Math.sin(now / 6500) * 6;
    const effects = !REDUCED && (this.scan || this.ripples.length || this.mx > -1e3);
    const count = effects ? cols * rows : this.active.length;
    for (let n = 0; n < count; n++) {
      const k = effects ? n : this.active[n], r = (k / cols) | 0, c = k % cols;
      const y = this.oy + r * p, x = this.ox + c * p;
      let t = (now - this.t0[k]) / DUR;
      t = t < 0 ? 0 : t > 1 ? 1 : t;
      const e = 1 - Math.pow(1 - t, 3);
      const v = (this.cur[k] = this.from[k] + (this.to[k] - this.from[k]) * e);
      let a = v * 0.82;
      if (v > 0.02) {
        if (progRow != null) {
          // Progress: the shape fills from the bottom; a bright seam rides the edge.
          const lit = r >= progRow ? 1 : 0.2, seam = Math.exp(-Math.pow((r - progRow) / 1.3, 2));
          a = v * 0.82 * lit + seam * v * 0.6;
        }
        if (this.caret && !blink && c >= this.caret[0] - 0.5 && c <= this.caret[1] + 0.5) a = v * 0.15;
        if (!REDUCED) a += Math.sin(now * 0.0021 + this.seed[k] * 40) * 0.05 * v;  // twinkle
        a += this.glow * v * 0.25;
      }
      if (scanY != null) a += Math.exp(-Math.pow((r - scanY) / 2.2, 2)) * (0.1 + v * 0.5);
      if (this.mx > -1e3) {
        const dx = x - this.mx, dy = y - this.my, d2 = dx * dx + dy * dy;
        if (d2 < 9000) a += Math.exp(-d2 / 1900) * (0.22 + v * 0.35);
      }
      for (const rp of this.ripples) {
        const age = (now - rp.t) / 1000, rad = age * 320, d = Math.hypot(x - rp.x, y - rp.y);
        a += Math.exp(-Math.pow((d - rad) / 20, 2)) * 0.7 * rp.s * (1 - age / 2.4);
      }
      if (a < 0.012) continue;
      const k3 = k * 3, fr = this.rgbFrom, to = this.rgbTo;
      const dot = fr[k3] === 237 && fr[k3 + 1] === 237 && fr[k3 + 2] === 237 && to[k3] === 237 && to[k3 + 1] === 237 && to[k3 + 2] === 237 ? sp
        : this.tint(fr[k3] + (to[k3] - fr[k3]) * e, fr[k3 + 1] + (to[k3 + 1] - fr[k3 + 1]) * e, fr[k3 + 2] + (to[k3 + 2] - fr[k3 + 2]) * e);
      if (dot !== sp) a *= 1.2;  // brand colours read darker than the near-white ink
      g.globalAlpha = a > 1 ? 1 : a;
      g.drawImage(dot, x - half, y - half + drift, dot.width / this.dpr, dot.height / this.dpr);
    }
    g.globalAlpha = 1;
  }
}

// Where the artwork sits: centre stage on the hero, in the middle of the left panel during setup.
// Measured from the canvas, which always spans the window.
const HERO = () => ({ x: .5, y: .36, size: Math.min(field.h * .36, 176) });
const PANEL = () => {
  const stage = $(".pane").offsetLeft;  // layout position: ignores the slide-in transform
  return stage > 40 ? { x: stage / 2 / field.w, y: .46, size: Math.min(stage * .62, field.h * .42, 176) } : HERO();
};

// ------------------------------------------------------------------ flow
// hero → welcome (where results come from, and options) → install → done, or for an installed
// Archer: hero → remove → removing → removed. The install view shows both jobs.
const PROVIDERS = {
  searxng: {},
  duckduckgo: {},
  google: { key: "Serper API key", link: "https://serper.dev/api-key" },
  brave: { key: "Brave Search API key", link: "https://brave.com/search/api/" },
};
const S = { view: null, info: null, choice: "searxng", launched: false, onboarded: false, timers: [], busy: false };
let field;

const nav = {
  back: $("[data-back]"), primary: $("[data-primary]"), label: $("[data-primary-label]"),
  secondary: $("[data-secondary]"), status: $("[data-status]"),
};
let onPrimary = null, onSecondary = null, onBack = null;
nav.primary.addEventListener("click", () => onPrimary && !nav.primary.disabled && onPrimary());
nav.secondary.addEventListener("click", () => onSecondary && onSecondary());
nav.back.addEventListener("click", () => onBack && onBack());

function setNav({ label, primary, disabled = false, working = false, danger = false, secondary = null, back = null, status = "", tone = "" }) {
  nav.label.textContent = label;
  onPrimary = primary;
  nav.primary.disabled = disabled;
  nav.primary.classList.toggle("is-hidden", !primary);
  nav.primary.classList.toggle("is-working", working);
  nav.primary.classList.toggle("btn--danger", danger);
  onSecondary = secondary && secondary.run;
  nav.secondary.hidden = !secondary;
  if (secondary) nav.secondary.textContent = secondary.label;
  onBack = back;
  nav.back.hidden = !back;
  status_(status, tone);
}
function status_(text, tone = "") {
  nav.status.textContent = text;
  nav.status.className = "status" + (tone ? " is-" + tone : "");
  nav.status.title = text;
}
function later(fn, ms) { const id = setTimeout(fn, ms); S.timers.push(id); return id; }
function clearTimers() { S.timers.forEach(clearTimeout); S.timers = []; }
function busy(on) { S.busy = on; document.body.classList.toggle("is-busy", on); }

// ------------------------------------------------------------------ hero
const heroGo = $("[data-hero-go]"), heroAlt = $("[data-hero-alt]");
let heroPrimary = null, heroSecondary = null;
heroGo.addEventListener("click", () => heroPrimary && heroPrimary());
heroAlt.addEventListener("click", () => heroSecondary && heroSecondary());

function hero(opening) {
  const info = S.info;
  S.view = "hero";
  document.body.classList.add("is-hero");
  document.body.classList.remove("is-choosing");
  logoTimers.forEach(clearTimeout); logoTimers = [];
  field.still = false;
  if (!opening) { field.place(HERO()); field.shape("ascent"); field.progress = null; field.glow = 0.15; }
  $("[data-hero-sub]").textContent = info.uninstall ? "Remove Archer from this PC?"
    : info.existing ? "Archer is installed on this PC." : "Your own search engine, running on this PC.";
  const set = (btn, label, run) => { btn.hidden = !label; btn.textContent = label || ""; return run; };
  if (info.uninstall) {
    heroPrimary = set(heroGo, "Uninstall", () => leaveHero("remove"));
    heroSecondary = set(heroAlt, "Keep Archer", quit);
  } else {
    heroPrimary = set(heroGo, info.existing ? "Update" : "Get started", () => leaveHero("welcome"));
    heroSecondary = set(heroAlt, info.existing ? "Uninstall" : "", () => leaveHero("remove"));
  }
  heroGo.classList.toggle("btn--danger", !!info.uninstall);
}

// The mark glides into the left panel while setup slides in over the right.
function leaveHero(view) {
  document.body.classList.remove("is-hero", "is-intro");
  field.place(PANEL());
  field.ripple(undefined, undefined, 0.5);
  go(view);
}

function backToHero() {
  clearTimers();
  closeSheet(true);
  hero();
  later(() => heroGo.focus(), 300);
}

// ------------------------------------------------------------------ views
const SECTION = { removing: "install" };
const PIP = { welcome: 0, install: 1, done: 2, remove: 0, removing: 1, removed: 2 };

async function go(view) {
  clearTimers();
  closeSheet(true);
  const prev = S.view && S.view !== "hero" && $(`[data-view=${SECTION[S.view] || S.view}]`);
  const el = $(`[data-view=${SECTION[view] || view}]`);
  if (prev && prev !== el && !REDUCED) { prev.classList.add("is-leaving"); await sleep(150); }
  $$("[data-view]").forEach((v) => { if (v !== el) { v.hidden = true; v.classList.remove("is-leaving", "is-entering"); } });
  S.view = view;
  document.body.classList.toggle("is-choosing", view === "welcome");
  logoTimers.forEach(clearTimeout); logoTimers = [];
  field.still = view === "welcome";
  field.tempo = 1;
  $$("[data-rise]", el).forEach((n, i) => n.style.setProperty("--d", i));
  el.hidden = false;
  el.scrollTop = 0;
  el.classList.remove("is-entering"); void el.offsetWidth; el.classList.add("is-entering");
  $$("[data-pips] li").forEach((li, i) => {
    li.classList.toggle("is-current", i === PIP[view]);
    li.classList.toggle("is-done", i < PIP[view]);
  });
  field.progress = null; field.scan = false; field.glow = 0;
  VIEWS[view]();
}

function setState(li, state, detail) {
  li.classList.remove("is-busy", "is-ok", "is-bad");
  if (state) li.classList.add("is-" + state);
  const glyph = $(".state", li);
  glyph.replaceChildren();
  const id = { ok: "i-check", bad: "i-x" }[state];
  if (id) glyph.append(icon(id));
  if (detail !== undefined) { const d = $("em", li); if (d) d.textContent = detail; }
}

// Pixel progress: a row of cells, lit up to the current fraction with a glowing leading edge.
const shown = { pct: 0 };
function meter(fraction, done, bad) {
  const bar = $("[data-dotbar]");
  if (!bar.children.length) {
    const n = 36;
    bar.style.setProperty("--cells", n);
    for (let i = 0; i < n; i++) { const c = h("i"); c.style.setProperty("--k", i); bar.append(c); }
  }
  const cells = [...bar.children], lit = Math.round(fraction * cells.length);
  cells.forEach((c, i) => { c.classList.toggle("on", i < lit); c.classList.toggle("edge", !done && !bad && i === lit - 1); });
  bar.classList.toggle("is-done", !!done);
  bar.classList.toggle("is-bad", !!bad);
  // The counter eases toward the real value rather than jumping.
  const pct = $("[data-pct]"), target = Math.round(fraction * 100);
  cancelAnimationFrame(shown.raf);
  const step = () => {
    if (shown.pct === target) return;
    shown.pct += Math.sign(target - shown.pct) * Math.max(1, Math.round(Math.abs(target - shown.pct) / 6));
    pct.textContent = shown.pct;
    shown.raf = requestAnimationFrame(step);
  };
  step();
}
function consoleLines(lines) {
  const pre = $("[data-console]");
  const stick = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8;
  for (const line of lines) {
    pre.querySelectorAll(".last").forEach((n) => n.classList.remove("last"));
    pre.append(h("span", { class: "last" }, line + "\n"));
  }
  while (pre.childNodes.length > 400) pre.firstChild.remove();
  if (lines.length && stick) pre.scrollTop = pre.scrollHeight;
}

// Polls a backend job until it ends, reporting each snapshot.
async function follow(name, onTick) {
  let since = 0;
  for (;;) {
    let snap;
    try { snap = await api(`job?name=${name}&since=${since}`); }
    catch { await sleep(800); continue; }
    since = snap.next ?? since;
    onTick(snap);
    if (snap.state !== "running") return snap;
    await sleep(300);
  }
}

const VIEWS = {
  welcome() {
    field.shape("none");
    stageLogo(S.choice);
    const existing = S.info.existing;
    $("[data-welcome-title]").textContent = existing ? "Update Archer" : "Install Archer";
    $("[data-welcome-sub]").textContent = existing
      ? "Your settings and history stay as they are."
      : "Your own search engine, running on this PC.";
    options();
    renderChoice();
    setNav({ label: existing ? "Update" : "Install", primary: install, back: backToHero, status: "No admin rights needed." });
  },

  install() {
    field.shape("ascent"); field.progress = 0;
    runJob({
      job: "install",
      title: S.info.existing ? "Updating Archer" : "Installing Archer",
      doneTitle: S.info.existing ? "Archer is updated" : "Archer is installed",
      tasks: [["files", "Unpack Archer"], ["uv", "Get uv, the Python installer"], ["packages", "Python and packages"],
              ["searxng", "SearXNG on this PC"], ["shortcuts", "Shortcuts and startup"], ["finish", "Save settings"]]
        .filter(([key]) => key !== "searxng" || S.choice === "searxng"),
      start: () => api("install", { autostart: $("[data-autostart]").checked }),
      progress: (p) => p,
      done: () => setNav({ label: "Continue", primary: () => go("done") }),
    });
  },

  done() {
    field.shape(S.launched && !S.onboarded ? "window" : "ascent"); field.glow = 0.5;
    [0, 420, 900].forEach((ms, i) => later(() => field.ripple(undefined, undefined, 1 - i * 0.25), ms));
    $("[data-done-title]").textContent = S.info.existing ? "Archer is up to date" : "Archer is ready";
    const b = S.info.browser;
    $("[data-browser-letter]").textContent = b.id === "other" ? "?" : short(b.name)[0];
    $("[data-browser-text]").textContent = b.id === "other" ? "Make Archer your browser's search engine" : `Make Archer the search engine in ${short(b.name)}`;
    personaliseRow();
    const form = $("[data-try]");
    form.onsubmit = (e) => {
      e.preventDefault();
      const q = form.q.value.trim();
      api("open", { q }).catch(() => {});
      toast(q ? `Searching for “${q}”` : "Opening Archer");
      field.ripple();
    };
    later(() => form.q.focus(), 500);
    setNav({ label: "Finish", primary: quit, secondary: { label: "Open Archer", run: () => { api("open", {}); toast("Opening Archer"); } } });
  },

  remove() {
    field.shape("ascent");
    const dir = $("[data-remove-dir]");
    dir.textContent = S.info.dir; dir.title = S.info.dir;
    setNav({ label: "Uninstall", danger: true, primary: () => go("removing"), back: backToHero, status: "Nothing else on this PC is touched." });
  },

  // The mark drains away as Archer comes off the PC.
  removing() {
    field.shape("ascent"); field.progress = 1;
    runJob({
      job: "uninstall",
      title: "Removing Archer",
      doneTitle: "Archer is removed",
      tasks: [["stop", "Stop Archer"], ["shortcuts", "Shortcuts and startup"], ["searxng", "SearXNG"], ["files", "Archer's files"]],
      start: () => api("uninstall", { deleteData: $("[data-delete-data]").checked }),
      progress: (p) => 1 - p,
      done: () => later(() => go("removed"), 900),
    });
  },

  removed() {
    field.progress = null; field.shape("none");
    $("[data-removed-sub]").textContent = $("[data-delete-data]").checked
      ? "Its settings and search history are gone too."
      : `Your settings and history are still in ${S.info.dir}.`;
    setNav({ label: "Close", primary: quit });
  },
};

// ------------------------------------------------------------------ welcome: options
function options() {
  const dir = $("[data-dir]");
  dir.textContent = S.info.dir; dir.title = S.info.dir;
  const auto = $("[data-autostart]").checked;
  const folder = S.info.dir.replace(/^.*\\(Programs\\Archer)$/i, "…\\$1");
  $("[data-options-sum]").textContent = `${folder} · ${auto ? "starts with Windows" : "starts from its shortcut"}`;
}
$("[data-options-open]").addEventListener("click", () => openSheet("options"));
$("[data-autostart]").addEventListener("change", options);
$("[data-pick-dir]").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  try {
    await api("folder", {});
    S.info = await api("state");
    if (S.view === "welcome") VIEWS.welcome();
  } finally { btn.disabled = false; }
});

// ------------------------------------------------------------------ welcome: where results come from
function renderChoice() {
  $$("[data-choice]").forEach((el) => {
    const on = el.dataset.choice === S.choice;
    el.setAttribute("aria-checked", on);
    el.tabIndex = on ? 0 : -1;
  });
  const p = PROVIDERS[S.choice], input = $("[data-key]"), box = $("[data-keybox]");
  box.classList.toggle("is-open", !!p.key);
  input.tabIndex = $("[data-key-link]").tabIndex = p.key ? 0 : -1;
  if (!p.key) return;
  $("[data-key-label]").textContent = p.key;
  input.placeholder = S.info.keys && S.info.keys[S.choice] ? "Saved key kept · paste to replace" : "Optional";
}

// The chosen service's logo, crisp in the left panel. Switching breaks it into dots in its own
// colours, lets the dots flow into the next logo's shape and colours, then resolves them into the
// crisp new logo.
let logoTimers = [];
function stageLogo(name) {
  const box = $("[data-stage-logo]");
  if (!box.children.length) for (const key of Object.keys(LOGOS)) { const svg = logo(key); svg.dataset.name = key; box.append(svg); }
  const shownNow = $("svg.is-shown", box), crisp = (key) => $(`svg[data-name=${key}]`, box);
  logoTimers.forEach(clearTimeout); logoTimers = [];
  field.still = true;
  field.tempo = .4;  // a quick, snappy handoff between logos
  if (!shownNow || REDUCED) {
    $$("svg", box).forEach((svg) => svg.classList.toggle("is-shown", svg.dataset.name === name));
    field.shape("none");
    return;
  }
  if (shownNow.dataset.name === name) return;
  const step = (fn, ms) => logoTimers.push(setTimeout(fn, ms));
  field.shape("logo:" + shownNow.dataset.name, true);   // pixelize: the dots take the logo's place
  shownNow.classList.remove("is-shown");
  step(() => field.shape("logo:" + name), 70);          // the dots flow into the next logo
  step(() => { crisp(name).classList.add("is-shown"); field.shape("none"); }, 480);  // and resolve
}

function choose(value) {
  if (S.choice === value) return;
  $("[data-key]").value = "";
  S.choice = value;
  stageLogo(value);
  renderChoice();
}
$$("[data-choice]").forEach((el) => {
  el.addEventListener("click", () => choose(el.dataset.choice));
  el.addEventListener("keydown", (e) => {
    const all = $$("[data-choice]"), i = all.indexOf(el);
    if (e.key === " " || e.key === "Enter") { e.preventDefault(); choose(el.dataset.choice); }
    if (["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight"].includes(e.key)) {
      e.preventDefault();
      const next = all[(i + (e.key === "ArrowDown" || e.key === "ArrowRight" ? 1 : all.length - 1)) % all.length];
      choose(next.dataset.choice); next.focus();
    }
  });
});
$("[data-key-link]").addEventListener("click", () => window.open(PROVIDERS[S.choice].link, "_blank", "noopener"));
$("[data-key]").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); nav.primary.click(); } });

// ------------------------------------------------------------------ install and uninstall
async function install() {
  const key = PROVIDERS[S.choice].key ? $("[data-key]").value.trim() : "";
  await api("choice", { provider: S.choice, key });
  $("[data-key]").value = "";
  go("install");
}

function tab(name) {
  $$("[data-tab]").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === name));
  $$("[data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== name));
  if (name === "log") { const pre = $("[data-console]"); pre.scrollTop = pre.scrollHeight; }
}
$$("[data-tab]").forEach((b) => b.addEventListener("click", () => tab(b.dataset.tab)));

// Runs one backend job in the install view: a task per step, one bar, the log a tab away.
async function runJob(job) {
  const view = S.view, list = job.tasks;
  tab("steps");
  $("[data-install-title]").textContent = job.title;
  $("[data-console]").replaceChildren();
  const box = $("[data-tasks]");
  box.replaceChildren(...list.map(([key, label]) => h("li", { "data-task": key }, h("span", { class: "state" }), label, h("em"))));
  const tasks = Object.fromEntries($$("li", box).map((li) => [li.dataset.task, li]));
  const order = list.map(([key]) => key), began = {}, labels = Object.fromEntries(list);
  const now = $("[data-now]");
  const t0 = performance.now();
  shown.pct = 0; $("[data-pct]").textContent = "0"; meter(0);
  now.className = "sub"; now.textContent = "Getting ready…";
  setNav({ label: view === "removing" ? "Removing" : "Installing", primary: () => {}, disabled: true, working: true,
    status: view === "removing" ? "This takes a few seconds." : "This usually takes one to three minutes." });
  busy(true);

  try { await job.start(); }
  catch (e) { return failed(e.message, ""); }
  const snap = await follow(job.job, (s) => {
    const at = s.state === "done" ? order.length : order.indexOf(s.step);
    if (at >= 0) order.forEach((key, i) => {
      const li = tasks[key];
      if (i < at) {
        if (!li.classList.contains("is-ok")) setState(li, "ok", began[key] ? elapsed(performance.now() - began[key]) : "");
      } else if (i === at) {
        began[key] = began[key] || performance.now();
        if (s.state === "failed") setState(li, "bad", "");
        else if (!li.classList.contains("is-busy")) { setState(li, "busy", ""); now.textContent = labels[key] + "…"; }
      }
    });
    const p = s.state === "done" ? 1 : s.progress;
    meter(p); field.progress = job.progress(p);
    field.scan = s.step === "searxng" && s.state === "running" && view === "install";
    consoleLines(s.log || []);
    const last = (s.log || []).slice(-1)[0];
    if (last && s.state === "running") { status_(last); nav.status.classList.add("is-log"); }
  });
  field.scan = false;
  if (snap.state !== "done") return failed(snap.error || "It stopped before finishing.", snap.step);

  busy(false);
  meter(1, true); field.progress = job.progress(1);
  if (view === "install") { field.glow = 0.5; field.ripple(); later(() => field.ripple(undefined, undefined, 0.6), 380); }
  $("[data-install-title]").textContent = job.doneTitle;
  now.className = "sub is-ok"; now.textContent = `Finished in ${elapsed(performance.now() - t0)}.`;
  setNav({ label: "Continue", primary: () => {}, disabled: true, status: "" });
  job.done();
}

function failed(message, step) {
  busy(false); field.scan = false;
  const view = S.view, now = $("[data-now]");
  now.className = "sub is-bad"; now.textContent = message; now.title = message;
  meter(shown.pct / 100, false, true); field.progress = null;
  // SearXNG has a ready alternative: the rest of Archer is already in place by then.
  const alt = view === "install" && step === "searxng"
    ? { label: "Use DuckDuckGo", run: async () => { S.choice = "duckduckgo"; await api("choice", { provider: "duckduckgo" }); go("install"); } }
    : null;
  setNav({ label: "Try again", primary: () => go(view), back: () => go(view === "removing" ? "remove" : "welcome"), secondary: alt,
    status: "Trying again picks up where it stopped.", tone: "bad" });
}

// ------------------------------------------------------------------ done: personalise
function personaliseRow() {
  const text = $("[data-personalise-text]"), end = $("[data-personalise-end]");
  end.className = "row-end";
  if (S.onboarded) {
    text.textContent = "Done. Change it any time from the tray.";
    end.classList.add("is-ok"); end.replaceChildren(icon("i-check"));
  } else if (S.launched) {
    text.textContent = "Finish in the Archer window that opened";
    end.replaceChildren(h("span", { class: "pulse" }), "Waiting");
  } else {
    text.textContent = "Results, AI answers and your location";
    end.replaceChildren(icon("i-chevron"));
  }
}
$("[data-personalise]").addEventListener("click", async () => {
  const r = await api("launch", {}).catch(() => ({ ok: false }));
  if (!r.ok) { status_("Couldn't start Archer. Use the Archer shortcut on your desktop.", "bad"); return; }
  toast("Archer is opening");
  if (S.launched) return;
  S.launched = true;
  personaliseRow();
  field.shape("window"); field.ripple();
  const poll = async () => {
    const o = await api("onboarded").catch(() => ({}));
    if (o.onboarded) {
      S.onboarded = true;
      if (S.view === "done") { personaliseRow(); field.shape("ascent"); field.glow = 0.5; field.ripple(); }
      return;
    }
    setTimeout(poll, 1500);
  };
  setTimeout(poll, 1500);
});

// ------------------------------------------------------------------ done: address-bar sheet
const sheets = $$("[data-sheet]");
const openSheetEl = () => sheets.find((x) => !x.hidden);

// A sheet slides over the pane: the options from the setup screen, or the address-bar steps.
function openSheet(name) {
  const sheet = $(`[data-sheet=${name}]`);
  if (name === "browser") { fillBrowserSheet(); field.shape("caret"); }
  sheet.hidden = false;
  sheet.classList.remove("is-leaving");
  later(() => $("[data-sheet-close]", sheet).focus(), 50);
}
function fillBrowserSheet() {
  const b = S.info.browser;
  $("[data-browser-name]").textContent = b.id === "other" ? "your browser" : short(b.name);
  $("[data-steps-firefox]").hidden = !b.firefox;
  $("[data-steps-chromium]").hidden = b.firefox;
  const open = $("[data-open-settings]");
  open.hidden = !b.canOpen;
  $("[data-open-settings-label]").textContent = `Open ${short(b.name)} settings`;
  const rows = [["Name", "Archer"], [b.firefox ? "Keyword" : "Shortcut", "ar"], ["URL", S.info.searchUrl]];
  if (!b.firefox) rows.push(["Suggestions URL", S.info.suggestUrl]);
  $("[data-copies]").replaceChildren(...rows.map(([label, value]) => {
    const btn = h("button", { type: "button", class: "btn" }, icon("i-copy"), "Copy");
    btn.addEventListener("click", async () => {
      await copy(value);
      btn.replaceChildren(icon("i-check"), "Copied");
      btn.classList.add("is-done");
      setTimeout(() => { btn.replaceChildren(icon("i-copy"), "Copy"); btn.classList.remove("is-done"); }, 1600);
    });
    return h("div", { class: "copy-row" }, h("span", {}, label), h("code", { title: value }, value), btn);
  }));
}
function closeSheet(instant) {
  const sheet = openSheetEl();
  if (!sheet) return;
  const done = () => { sheet.hidden = true; sheet.classList.remove("is-leaving"); };
  if (instant || REDUCED) done();
  else { sheet.classList.add("is-leaving"); setTimeout(done, 190); }
  if (instant) return;
  if (sheet.dataset.sheet === "browser" && S.view === "done") { field.shape(S.launched && !S.onboarded ? "window" : "ascent"); $("[data-browser]").focus(); }
  if (sheet.dataset.sheet === "options") $("[data-options-open]").focus();
}
$("[data-browser]").addEventListener("click", () => openSheet("browser"));
$$("[data-sheet-close]").forEach((b) => b.addEventListener("click", () => closeSheet()));
$("[data-open-settings]").addEventListener("click", async () => {
  const b = S.info.browser;
  const r = await api("browser/settings", {}).catch(() => ({ ok: false }));
  toast(r.ok ? `Opened ${short(b.name)}'s search settings` : "Couldn't open it. Open your browser's settings and search for “search engine”.");
});

async function copy(text) {
  try { await navigator.clipboard.writeText(text); return; } catch { /* fall through */ }
  const ta = h("textarea", { class: "offscreen" });
  ta.value = text; document.body.append(ta); ta.select();
  document.execCommand("copy"); ta.remove();
}

// ------------------------------------------------------------------ window
const dialog = $("[data-dialog]");
async function quit() {
  await api("quit", {}).catch(() => {});
  window.close();
}
function requestClose() {
  if (!S.busy) { quit(); return; }
  dialog.hidden = false;
  $("[data-dialog-cancel]").focus();
}
$("[data-dialog-cancel]").addEventListener("click", () => { dialog.hidden = true; });
$("[data-dialog-ok]").addEventListener("click", quit);
$("[data-win=close]").addEventListener("click", requestClose);
$("[data-win=minimize]").addEventListener("click", () => HOST && HOST.postMessage("minimize"));
// Fallback for runtimes without app-region: a press on the strip asks the host to move the window.
$(".titlebar").addEventListener("pointerdown", (e) => {
  if (HOST && e.button === 0 && !e.target.closest("button")) HOST.postMessage("drag");
});
if (HOST) HOST.addEventListener("message", (e) => { if (e.data === "close-requested") requestClose(); });

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!dialog.hidden) dialog.hidden = true;
    else if (openSheetEl()) closeSheet();
    return;
  }
  // Enter moves on, unless it belongs to a field, a button or a choice.
  if (e.key === "Enter" && !e.repeat && dialog.hidden && !openSheetEl() && !e.target.closest("input, button, [role=radio], form")) {
    e.preventDefault();
    if (S.view === "hero") heroGo.click(); else nav.primary.click();
  }
});
addEventListener("resize", () => { if (field && S.view && S.view !== "hero") field.place(PANEL()); });

// ------------------------------------------------------------------ start
async function start() {
  document.body.classList.toggle("is-hosted", !!HOST);
  $$("[data-logo]").forEach((el) => el.append(logo(el.dataset.logo)));
  // The name rises letter by letter once the mark has assembled.
  const title = $("[data-hero-title]");
  title.replaceChildren(...[...title.textContent].map((ch, i) => { const s = h("span", {}, ch); s.style.setProperty("--i", i); return s; }));
  field = new Field($("[data-field]"));
  await document.fonts.load('600 46px "DM Sans"').catch(() => {});
  S.info = await api("state");
  if (PROVIDERS[S.info.provider]) S.choice = S.info.provider;
  $("[data-preview]").hidden = !S.info.dry;
  field.layout();
  field.place(HERO());
  field.intro("ascent");
  hero(true);
  if (!REDUCED) {
    field.scan = true;
    later(() => { field.scan = false; field.ripple(undefined, undefined, 0.8); field.glow = 0.3; }, 1300);
    later(() => { field.glow = 0.15; }, 2200);
  }
  setInterval(() => api("ping", {}).catch(() => {}), 5000);
  later(() => heroGo.focus({ preventScroll: true }), REDUCED ? 0 : 1800);
}
start();
