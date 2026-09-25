// "Fill from photos" panel on the device form.
//
// Barcodes are read entirely in the browser (native BarcodeDetector where the
// browser has it, otherwise the bundled ZXing library), so that part is free and
// works offline. "Read label with AI" uploads the photos to /api/extract.
(function () {
  "use strict";

  const panel = document.getElementById("photo-fill");
  if (!panel) return;
  const form = document.querySelector("form.device-form");
  const input = panel.querySelector("input[type=file]");
  const aiButton = panel.querySelector("[data-ai]");
  const statusEl = panel.querySelector(".pf-status");
  const previewsEl = panel.querySelector(".pf-previews");
  const barcodesEl = panel.querySelector(".pf-barcodes");
  const conflictsEl = panel.querySelector(".pf-conflicts");

  const MAX_PHOTOS = 4;
  const UPLOAD_MAX_EDGE = 2000; // px; keeps uploads small while labels stay legible
  const SCAN_MAX_EDGE = 2000;

  let photos = [];
  let barcodes = [];

  function setStatus(text, kind) {
    statusEl.textContent = text || "";
    statusEl.className = "pf-status" + (kind ? " " + kind : "");
  }

  function normalize(value) {
    return (value || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  }

  async function decodeImage(blob) {
    if (window.createImageBitmap) {
      try {
        return await createImageBitmap(blob, { imageOrientation: "from-image" });
      } catch (e) {
        /* fall through to <img> */
      }
    }
    return new Promise(function (resolve, reject) {
      const url = URL.createObjectURL(blob);
      const img = new Image();
      img.onload = function () { resolve(img); };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error("unreadable image")); };
      img.src = url;
    });
  }

  function isHeic(file) {
    return /^image\/hei[cf]/i.test(file.type) || /\.hei[cf]$/i.test(file.name || "");
  }

  let heicLibrary = null;
  function loadHeicLibrary() {
    // Only fetched when a HEIC photo can't be opened natively (e.g. Chrome/Edge/Firefox).
    if (!heicLibrary) {
      heicLibrary = new Promise(function (resolve, reject) {
        const script = document.createElement("script");
        script.src = panel.dataset.heicUrl;
        script.onload = function () { resolve(window.heic2any); };
        script.onerror = function () { heicLibrary = null; reject(new Error("HEIC converter didn't load")); };
        document.head.appendChild(script);
      });
    }
    return heicLibrary;
  }

  // Returns {image, blob}. `blob` is what gets uploaded if re-encoding fails.
  async function loadPhoto(file) {
    try {
      return { image: await decodeImage(file), blob: file };
    } catch (e) {
      if (!isHeic(file)) throw new Error("Couldn't open " + file.name + ". Try a JPEG or PNG photo.");
    }
    // iPhone HEIC photo in a browser that can't show HEIC: convert it to JPEG here.
    setStatus("Converting iPhone photo " + file.name + "…", "busy");
    try {
      const heic2any = await loadHeicLibrary();
      let jpeg = await heic2any({ blob: file, toType: "image/jpeg", quality: 0.92 });
      if (Array.isArray(jpeg)) jpeg = jpeg[0];
      return { image: await decodeImage(jpeg), blob: jpeg };
    } catch (e) {
      throw new Error("Couldn't convert " + file.name + " (HEIC). Try exporting it as a JPEG.");
    }
  }

  function toCanvas(image, maxEdge, crop) {
    const sx = crop ? crop.x : 0;
    const sy = crop ? crop.y : 0;
    const sw = crop ? crop.w : image.width;
    const sh = crop ? crop.h : image.height;
    const scale = Math.min(1, maxEdge / Math.max(sw, sh));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(sw * scale);
    canvas.height = Math.round(sh * scale);
    canvas.getContext("2d").drawImage(image, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
    return canvas;
  }

  // ------------------------------------------------------------ barcodes

  let nativeDetector = null;
  async function getNativeDetector() {
    if (nativeDetector !== null) return nativeDetector;
    nativeDetector = false;
    if ("BarcodeDetector" in window) {
      try {
        const formats = await window.BarcodeDetector.getSupportedFormats();
        if (formats.length) nativeDetector = new window.BarcodeDetector({ formats: formats });
      } catch (e) {
        /* unsupported on this platform */
      }
    }
    return nativeDetector;
  }

  function zxingDecode(canvas) {
    const Z = window.ZXing;
    const hints = new Map();
    hints.set(Z.DecodeHintType.TRY_HARDER, true);
    const reader = new Z.MultiFormatReader();
    reader.setHints(hints);
    try {
      const source = new Z.HTMLCanvasElementLuminanceSource(canvas);
      return reader.decode(new Z.BinaryBitmap(new Z.HybridBinarizer(source))).getText();
    } catch (e) {
      return null; // NotFoundException etc.
    }
  }

  async function scanImage(image) {
    const found = [];
    const detector = await getNativeDetector();
    if (detector) {
      try {
        const results = await detector.detect(toCanvas(image, SCAN_MAX_EDGE));
        results.forEach(function (r) { found.push(r.rawValue); });
        return found;
      } catch (e) {
        /* fall back to ZXing */
      }
    }
    if (!window.ZXing) return found;
    // ZXing returns one barcode per decode, and labels often carry several,
    // so scan the whole photo and then overlapping horizontal strips.
    const regions = [null];
    const h = image.height, w = image.width;
    for (let i = 0; i < 4; i++) {
      regions.push({ x: 0, y: Math.round((h * i) / 5), w: w, h: Math.round((h * 2) / 5) });
    }
    regions.forEach(function (crop) {
      const text = zxingDecode(toCanvas(image, SCAN_MAX_EDGE, crop));
      if (text) found.push(text);
    });
    return found;
  }

  async function scanAll() {
    barcodes = [];
    for (const photo of photos) {
      try {
        const values = await scanImage(photo.image);
        values.forEach(function (v) {
          v = (v || "").trim();
          if (v.length >= 4 && barcodes.indexOf(v) === -1) barcodes.push(v);
        });
      } catch (e) {
        /* ignore unreadable image */
      }
    }
    renderBarcodes();
  }

  function renderBarcodes(aiSerial) {
    barcodesEl.innerHTML = "";
    if (!photos.length) return;
    if (!barcodes.length) {
      barcodesEl.innerHTML = '<p class="muted">No barcodes found in these photos.</p>';
      return;
    }
    const heading = document.createElement("p");
    heading.innerHTML = "<strong>Barcodes found</strong> (read exactly, no AI needed). Use one as:";
    barcodesEl.appendChild(heading);
    barcodes.forEach(function (value) {
      const row = document.createElement("div");
      row.className = "pf-barcode";
      const code = document.createElement("code");
      code.textContent = value;
      row.appendChild(code);
      [["serial_number", "Serial #"], ["model_number", "Model #"], ["asset_tag", "Asset tag"]].forEach(
        function (pair) {
          const b = document.createElement("button");
          b.type = "button";
          b.className = "secondary small";
          b.textContent = pair[1];
          b.addEventListener("click", function () { setField(pair[0], value, true); });
          row.appendChild(b);
        }
      );
      if (aiSerial && matchesSerial(value, aiSerial)) {
        const ok = document.createElement("span");
        ok.className = "pf-match";
        ok.textContent = "✓ matches serial read by AI";
        row.appendChild(ok);
      }
      barcodesEl.appendChild(row);
    });
  }

  function matchesSerial(barcode, serial) {
    const b = normalize(barcode), s = normalize(serial);
    // Some labels prefix the serial in the barcode (e.g. Lenovo "1S...", "S...").
    return s.length >= 4 && (b === s || b.endsWith(s));
  }

  // ------------------------------------------------------------ form fields

  function setField(name, value, overwrite) {
    const el = form.elements[name];
    if (!el || !value) return false;
    if (el.value.trim() && !overwrite) return false;
    el.value = value;
    el.classList.add("autofilled");
    // Let other form logic react (e.g. the "Replace by" date follows the manufacture date).
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  function applyFields(fields) {
    conflictsEl.innerHTML = "";
    const filled = [];
    Object.keys(fields).forEach(function (name) {
      const el = form.elements[name];
      if (!el) return;
      const current = el.value.trim();
      if (!current) {
        setField(name, fields[name]);
        filled.push(name);
      } else if (normalize(current) !== normalize(fields[name])) {
        addConflict(name, current, fields[name]);
      }
    });
    const nameEl = form.elements["name"];
    const suggested = [fields.manufacturer, fields.model].filter(Boolean).join(" ");
    if (nameEl && !nameEl.value.trim() && suggested) {
      setField("name", suggested);
      filled.push("name");
    }
    return filled;
  }

  function addConflict(name, current, suggested) {
    const label = form.elements[name].closest("label");
    const labelText = label ? label.firstChild.textContent.trim() : name;
    const row = document.createElement("div");
    row.className = "pf-conflict";
    row.appendChild(document.createTextNode(labelText + ": photo says "));
    const code = document.createElement("code");
    code.textContent = suggested;
    row.appendChild(code);
    row.appendChild(document.createTextNode(" (form has "));
    const cur = document.createElement("code");
    cur.textContent = current;
    row.appendChild(cur);
    row.appendChild(document.createTextNode(") "));
    const use = document.createElement("button");
    use.type = "button";
    use.className = "secondary small";
    use.textContent = "Use photo value";
    use.addEventListener("click", function () {
      setField(name, suggested, true);
      row.remove();
    });
    row.appendChild(use);
    conflictsEl.appendChild(row);
  }

  // ------------------------------------------------------------ AI

  function canvasToBlob(canvas) {
    return new Promise(function (resolve) { canvas.toBlob(resolve, "image/jpeg", 0.9); });
  }

  async function readWithAI() {
    if (!photos.length) return;
    aiButton.disabled = true;
    setStatus("Reading label… this usually takes 5–20 seconds.", "busy");
    try {
      const data = new FormData();
      for (const photo of photos) {
        const blob = await canvasToBlob(toCanvas(photo.image, UPLOAD_MAX_EDGE));
        data.append("photos", blob || photo.file, "photo.jpg");
      }
      const resp = await fetch(panel.dataset.extractUrl, { method: "POST", body: data });
      let body;
      try {
        body = await resp.json();
      } catch (e) {
        throw new Error("The server returned an unexpected response (" + resp.status + ").");
      }
      if (!resp.ok) throw new Error(body.error || "Something went wrong.");

      const fields = body.fields || {};
      if (!Object.keys(fields).length) {
        setStatus("Couldn't read any details. Try a closer, sharper photo of the label.", "error");
        return;
      }
      const filled = applyFields(fields);
      renderBarcodes(fields.serial_number);
      let msg = filled.length
        ? "Filled in " + filled.length + " field(s), highlighted below. Please check them before saving."
        : "Nothing new to fill in.";
      if (fields.serial_number && barcodes.some(function (b) { return matchesSerial(b, fields.serial_number); })) {
        msg += " The serial number matches a barcode on the label.";
      }
      if (body.notes) msg += " Note: " + body.notes;
      setStatus(msg, "success");
    } catch (e) {
      setStatus(e.message, "error");
    } finally {
      aiButton.disabled = false;
    }
  }

  // ------------------------------------------------------------ wiring

  input.addEventListener("change", async function () {
    photos = [];
    previewsEl.innerHTML = "";
    conflictsEl.innerHTML = "";
    const files = Array.prototype.slice.call(input.files, 0, MAX_PHOTOS);
    let problem = input.files.length > MAX_PHOTOS
      ? "Only the first " + MAX_PHOTOS + " photos will be used." : null;
    setStatus(problem || (files.length ? "Opening photos…" : ""), problem ? "error" : "busy");
    for (const file of files) {
      try {
        const loaded = await loadPhoto(file);
        photos.push({ file: loaded.blob, image: loaded.image });
        const thumb = toCanvas(loaded.image, 160);
        thumb.className = "pf-thumb";
        previewsEl.appendChild(thumb);
      } catch (e) {
        problem = e.message;
      }
    }
    if (aiButton) aiButton.disabled = !photos.length;
    if (photos.length) setStatus(problem || "Scanning for barcodes…", problem ? "error" : "busy");
    await scanAll();
    if (problem) {
      setStatus(problem, "error");
    } else if (photos.length) {
      setStatus(aiButton ? "Click “Read label with AI” to fill in the details." : "");
    }
  });

  if (aiButton) aiButton.addEventListener("click", readWithAI);
})();
