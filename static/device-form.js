// Device form helpers: "+ Add new…" in the category/status drop-downs, and the
// automatic "Replace by" date.
(function () {
  "use strict";

  const form = document.querySelector("form.device-form");
  if (!form) return;
  const NEW = "__new__";

  // ---------------------------------------------------- "+ Add new…" options

  form.querySelectorAll("select[data-new-option]").forEach(function (select) {
    let previous = select.value;
    select.addEventListener("focus", function () { previous = select.value; });
    select.addEventListener("change", function () {
      if (select.value !== NEW) {
        previous = select.value;
        return;
      }
      const kind = select.dataset.newOption === "status" ? "status" : "category";
      const name = (window.prompt("New " + kind + " name:") || "").trim();
      if (!name) {
        select.value = previous;
        return;
      }
      // Reuse an existing entry if the name only differs in upper/lower case.
      const existing = Array.prototype.find.call(select.options, function (o) {
        return o.value !== NEW && o.value.toLowerCase() === name.toLowerCase();
      });
      if (existing) {
        select.value = existing.value;
      } else {
        const option = new Option(name, name, true, true);
        select.insertBefore(option, select.querySelector('option[value="' + NEW + '"]'));
      }
      previous = select.value;
    });
  });

  // ---------------------------------------------------- "Replace by" date

  const replace = form.elements["replacement_date"];
  const purchase = form.elements["purchase_date"];
  const made = form.elements["manufacture_date"];
  if (!replace) return;
  const years = parseInt(replace.dataset.lifespanYears, 10) || 3;

  function plusYears(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
    if (!m) return "";
    const y = parseInt(m[1], 10) + years;
    let d = m[3];
    if (m[2] === "02" && d === "29") d = "28"; // leap day -> 28 Feb
    return y + "-" + m[2] + "-" + d;
  }

  function suggested() {
    return plusYears(purchase && purchase.value) || plusYears(made && made.value);
  }

  // The date counts as automatic if it's empty or matches what we would have suggested,
  // so a date the user typed in themselves is never overwritten.
  let auto = !replace.value || replace.value === suggested();
  replace.addEventListener("input", function () { auto = !replace.value; });

  function update() {
    if (!auto) return;
    replace.value = suggested();
  }
  [purchase, made].forEach(function (el) {
    if (el) {
      el.addEventListener("input", update);
      el.addEventListener("change", update);
    }
  });
})();
