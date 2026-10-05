/*
 * Lattix Locus browser extension: page helper (LOCUS-350).
 *
 * Injected on demand (scripting.executeScript) into the extension's isolated
 * world, only to carry out a command the Locus gateway already authorized.
 * Element refs live in this isolated world (a WeakRef map), never in the DOM,
 * so page scripts cannot see or forge them.
 *
 * Floors enforced here as well as in Locus: secret field values (password,
 * card, CVV, SSN, one-time code) are never returned; typing, key presses and
 * selections into those fields are refused; an element must still match the
 * digest of the facts the gateway authorized.
 */
(() => {
  "use strict";
  if (globalThis.__locusContent) return;

  const MAX_ELEMENTS = 200;
  const SELECTOR = [
    "a[href]", "button", "input:not([type=hidden])", "select", "textarea", "summary",
    "[role=button]", "[role=link]", "[role=checkbox]", "[role=radio]", "[role=tab]",
    "[role=menuitem]", "[role=option]", "[role=switch]", "[role=textbox]", "[role=combobox]",
    '[contenteditable=""]', "[contenteditable=true]", "h1", "h2", "h3",
  ].join(", ");
  const SECRET_AUTOCOMPLETE = new Set([
    "current-password", "new-password", "one-time-code", "cc-number", "cc-csc", "cc-exp",
    "cc-exp-month", "cc-exp-year",
  ]);
  const SECRET_TEXT = new RegExp(
    "\\b(password|passwd|passcode|passphrase|pwd|pin|cvv2?|cvc2?|csc|security code|" +
      "card number|credit card|debit card|ccnum|cc num(ber)?|cc csc|cc cvv|ssn|" +
      "social security|otp|one time (password|passcode|code)|verification code|2fa|mfa|totp|" +
      "auth(entication)? code)\\b"
  );
  const ENTRY_CONTROLS = new Set(["fill", "type", "press", "select"]);
  const SECRET_SELECTOR = [
    "input[type=password i]",
    ...Array.from(SECRET_AUTOCOMPLETE, (t) => `[autocomplete~='${t}' i]`),
  ].join(", ");

  const refs = new Map();
  const byElement = new WeakMap();
  let nextRef = 1;
  let overlays = [];

  const txt = (s) => String(s || "").replace(/\s+/g, " ").trim().slice(0, 300);
  const norm = (s) =>
    String(s || "")
      .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
      .toLowerCase()
      .split(/[^a-z0-9]+/)
      .filter(Boolean)
      .join(" ");

  function fail(code, message) {
    return { error: { code, message: message || code } };
  }

  function isSecret(el, name, label, fieldId) {
    const type = (el.getAttribute("type") || "").toLowerCase();
    const auto = (el.getAttribute("autocomplete") || "").toLowerCase().split(/\s+/);
    if (type === "password") return true;
    if (auto.some((token) => SECRET_AUTOCOMPLETE.has(token))) return true;
    return SECRET_TEXT.test(norm(`${name} ${label} ${fieldId}`));
  }

  function labelsOf(el) {
    let text = el.labels && el.labels.length ? Array.from(el.labels, (l) => l.innerText).join(" ") : "";
    const by = el.getAttribute("aria-labelledby");
    if (by) {
      text += " " + by.split(/\s+/).map((id) => {
        const node = document.getElementById(id);
        return node ? node.innerText : "";
      }).join(" ");
    }
    return text;
  }

  // The element description the gateway classifies on (same shape as the
  // agent browser's _FACTS_JS). Never contains a secret field's value.
  function describe(el) {
    const tag = el.tagName.toLowerCase();
    const rawType = (el.getAttribute("type") || "").toLowerCase();
    const type = rawType || (tag === "button" ? "submit" : "");
    const auto = (el.getAttribute("autocomplete") || "").toLowerCase();
    const labelText = labelsOf(el);
    const isButtonInput = tag === "input" && ["submit", "button", "reset", "image"].includes(type);
    const ownText = ["button", "a", "summary", "option", "h1", "h2", "h3"].includes(tag) ||
      ["button", "link", "tab", "menuitem", "option"].includes(el.getAttribute("role") || "")
      ? el.innerText : "";
    const name = txt(el.getAttribute("aria-label") || labelText || ownText ||
      (isButtonInput ? el.value : "") || el.getAttribute("alt") || el.getAttribute("title") ||
      el.getAttribute("placeholder") || "");
    const label = txt([labelText, el.getAttribute("placeholder"), el.getAttribute("title")]
      .filter(Boolean).join(" "));
    const implicit = { a: "link", button: "button", select: "combobox", textarea: "textbox",
      summary: "button", h1: "heading", h2: "heading", h3: "heading" };
    let role = el.getAttribute("role") || implicit[tag] || "";
    if (!role && tag === "input") {
      role = isButtonInput ? "button" : (["checkbox", "radio"].includes(type) ? type : "textbox");
    }
    const fieldId = txt([el.getAttribute("name"), el.id].filter(Boolean).join(" "));
    const secret = isSecret(el, name, label, fieldId);
    const form = el.form || el.closest("form");
    const isSubmit = !!form && ((tag === "button" && type === "submit") ||
      (tag === "input" && (type === "submit" || type === "image")));
    const fields = [];
    let formText = "";
    if (form) {
      for (const f of form.querySelectorAll("input, select, textarea")) {
        if (fields.length >= 50) break;
        fields.push({
          type: (f.getAttribute("type") || "").toLowerCase(),
          autocomplete: (f.getAttribute("autocomplete") || "").toLowerCase(),
          name: txt(f.getAttribute("aria-label") || labelsOf(f) || f.getAttribute("placeholder")),
          field_id: txt([f.getAttribute("name"), f.id].filter(Boolean).join(" ")),
        });
      }
      formText = txt(Array.from(form.querySelectorAll("button, input[type=submit], input[type=image]"),
        (b) => b.innerText || b.value || b.getAttribute("aria-label") || "").join(" "));
    }
    const valueOk = !secret && ["input", "textarea", "select"].includes(tag) &&
      !["submit", "button", "reset", "image", "file"].includes(type);
    return {
      tag, type: rawType, role, name, label, autocomplete: auto, field_id: fieldId,
      is_password: type === "password", secret, is_submit: isSubmit, in_form: !!form,
      form_fields: fields, form_text: formText,
      value: valueOk ? txt(el.value).slice(0, 120) : "",
      disabled: !!el.disabled, checked: !!el.checked,
    };
  }

  // FNV-1a over the classification facts (not the value): "is this still the
  // element the gateway authorized?"
  function digest(facts) {
    const material = JSON.stringify([
      facts.tag, facts.type, facts.role, facts.name, facts.label, facts.autocomplete,
      facts.field_id, facts.secret, facts.is_submit, facts.in_form, facts.form_text,
      facts.form_fields,
    ]);
    let hash = 0x811c9dc5;
    for (let i = 0; i < material.length; i += 1) {
      hash ^= material.charCodeAt(i);
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return hash.toString(16).padStart(8, "0");
  }

  function refFor(el) {
    let ref = byElement.get(el);
    if (!ref) {
      ref = `e${nextRef}`;
      nextRef += 1;
      byElement.set(el, ref);
      refs.set(ref, new WeakRef(el));
    }
    return ref;
  }

  function resolve(ref) {
    const holder = refs.get(String(ref || ""));
    const el = holder ? holder.deref() : null;
    if (!el || !el.isConnected) return null;
    return el;
  }

  function visible(el) {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return false;
    const style = getComputedStyle(el);
    return style.visibility !== "hidden" && style.display !== "none";
  }

  function setValue(el, value) {
    el.focus();
    if (el.isContentEditable) {
      el.textContent = value;
      el.dispatchEvent(new InputEvent("input", { bubbles: true }));
      return;
    }
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function press(el, key) {
    el.focus();
    const init = { key, bubbles: true, cancelable: true };
    const proceed = el.dispatchEvent(new KeyboardEvent("keydown", init));
    el.dispatchEvent(new KeyboardEvent("keyup", init));
    const enter = ["enter", "return", "numpadenter"].includes(key.toLowerCase());
    const form = el.form || el.closest("form");
    if (proceed && enter && form && typeof form.requestSubmit === "function") form.requestSubmit();
  }

  function choose(el, value) {
    if (el.tagName.toLowerCase() !== "select") {
      el.click();
      return;
    }
    const option = Array.from(el.options).find((o) => o.value === value || txt(o.text) === value);
    if (!option) throw new Error("no such option");
    el.value = option.value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  const ops = {
    observe(args) {
      const elements = [];
      for (const el of document.querySelectorAll(SELECTOR)) {
        if (elements.length >= MAX_ELEMENTS) break;
        if (!visible(el)) continue;
        const facts = describe(el);
        elements.push({
          ref: refFor(el), tag: facts.tag, role: facts.role, name: facts.name, label: facts.label,
          type: facts.type, autocomplete: facts.autocomplete, field_id: facts.field_id,
          secret: facts.secret, value: facts.secret ? "" : facts.value, disabled: facts.disabled,
        });
      }
      const max = Math.min(Number(args.max_chars) || 16000, 50000);
      const text = document.body ? String(document.body.innerText || "") : "";
      return { url: location.href, title: document.title, elements, text: text.slice(0, max) };
    },

    inspect(args) {
      const el = resolve(args.ref);
      if (!el) return fail("stale_ref", "unknown or stale element ref; observe again");
      const facts = describe(el);
      facts.value = "";
      return { facts, digest: digest(facts) };
    },

    act(args) {
      const el = resolve(args.ref);
      if (!el) return fail("stale_ref", "unknown or stale element ref; observe again");
      const facts = describe(el);
      if (!args.expect_digest || digest(facts) !== args.expect_digest) {
        return fail("element_changed", "the element changed since it was authorized");
      }
      const control = String(args.control || "");
      if (ENTRY_CONTROLS.has(control) && facts.secret) {
        return fail("secret_field", "Locus never types into password, card or code fields");
      }
      if (facts.disabled) return fail("disabled", "the element is disabled");
      const value = String(args.value || "");
      el.scrollIntoView({ block: "center", inline: "nearest" });
      if (control === "click") el.click();
      else if (control === "fill" || control === "type") setValue(el, value);
      else if (control === "press") press(el, value);
      else if (control === "select") choose(el, value);
      else return fail("bad_control", "unknown control");
      return { done: true };
    },

    scroll(args) {
      const value = String(args.value || "down").toLowerCase();
      const page = Math.max(200, Math.round(window.innerHeight * 0.8));
      if (value === "top") window.scrollTo(0, 0);
      else if (value === "bottom") window.scrollTo(0, document.documentElement.scrollHeight);
      else if (value === "up") window.scrollBy(0, -page);
      else if (/^-?\d{1,6}$/.test(value)) window.scrollBy(0, Number(value));
      else window.scrollBy(0, page);
      return { done: true, scroll_y: Math.round(window.scrollY) };
    },

    mask(args) {
      for (const node of overlays) node.remove();
      overlays = [];
      if (!args.on) return { masked: 0 };
      const targets = new Set(document.querySelectorAll(SECRET_SELECTOR));
      for (const el of document.querySelectorAll("input, textarea")) {
        const facts = describe(el);
        if (facts.secret) targets.add(el);
      }
      for (const el of targets) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) continue;
        const box = document.createElement("div");
        box.style.cssText = "position:fixed;z-index:2147483647;background:#000;pointer-events:none;" +
          `left:${rect.left}px;top:${rect.top}px;width:${rect.width}px;height:${rect.height}px`;
        document.documentElement.appendChild(box);
        overlays.push(box);
      }
      return { masked: overlays.length };
    },
  };

  globalThis.__locusContent = {
    run(op, args) {
      try {
        const fn = Object.prototype.hasOwnProperty.call(ops, op) ? ops[op] : null;
        if (!fn) return fail("unknown_op", "unknown page operation");
        return fn(args || {});
      } catch (error) {
        return fail("page_error", String((error && error.message) || error).slice(0, 200));
      }
    },
  };
})();
