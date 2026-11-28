"""JavaScript that runs inside the application page: read every form field and the page's state."""
from __future__ import annotations

# Returns a list of fields. Every element we may touch gets a data-aa attribute so Python can find it again.
# Field kinds: text, textarea, select, radio, checkbox (single), checkboxes (group), file, combobox,
# listbox (Workday-style button that opens a list), multiselect (Workday prompt), buttons (Yes/No button group),
# date (segmented month/day/year inputs).
EXTRACT_JS = r"""
() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const textOf = el => el ? norm(el.innerText || el.textContent) : '';
  const isVisible = el => {
    if (!el || !el.isConnected) return false;
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    // trap fields positioned far off the page (document coordinates, so scrolling doesn't count)
    if (r.right + window.scrollX < -500 || r.bottom + window.scrollY < -500) return false;
    return true;
  };
  const labelFor = el => el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
  const shown = el => {
    if (isVisible(el)) return true;
    const l = labelFor(el); if (l && isVisible(l)) return true;
    const w = el.closest('label'); if (w && isVisible(w)) return true;
    if (el.type === 'file') return true;   // usually hidden behind an "Attach" button
    const p = el.parentElement; return !!(p && isVisible(p) && (el.type === 'radio' || el.type === 'checkbox'));
  };
  const byIds = ids => ids.split(/\s+/).map(id => textOf(document.getElementById(id))).filter(Boolean).join(' ');
  const ownLabel = el => {
    let t = '';
    const lb = el.getAttribute('aria-labelledby'); if (lb) t = byIds(lb);
    if (!t) { const l = labelFor(el); if (l) t = textOf(l); }
    if (!t) { const l = el.closest('label'); if (l) t = textOf(l); }
    if (!t) t = norm(el.getAttribute('aria-label'));
    if (!t && el.type !== 'radio' && el.type !== 'checkbox') { const l = prevLabelEl(el); if (l) t = textOf(l); }
    return t;
  };
  // A label's for= may point at a wrapper (Ashby "Start Date" -> the div holding Month and Year), or at nothing.
  const labelTargets = (l, el) => {
    if (!l.htmlFor || l.htmlFor === el.id) return true;
    const t = document.getElementById(l.htmlFor);
    return !t || t.contains(el) || !t.matches('input, select, textarea, button');
  };
  // A label that sits just before a control without being linked to it (Ashby education fields, many custom forms).
  const prevLabelEl = el => {
    let node = el;
    for (let d = 0; d < 4 && node.parentElement; d++) {
      node = node.parentElement;
      if (node === document.body) break;
      const ctrls = [...node.querySelectorAll(CONTROLS)];
      const labs = [...node.querySelectorAll('label, legend')].filter(l =>
        !l.querySelector(CONTROLS) && labelTargets(l, el) &&
        (l.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) && textOf(l));
      if (labs.length) {
        const l = labs[labs.length - 1];
        const between = ctrls.filter(c => c !== el && (l.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING) && (c.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING));
        return between.every(c => c.tagName === 'SELECT' && el.tagName === 'SELECT') ? l : null;
      }
      if (ctrls.length > 3) break;
    }
    return null;
  };
  // Required markers drawn with CSS (Ashby: class "_required_…" + ::after "*").
  const reqLabel = l => !!l && (/(^|[\s_-])required/i.test(String(l.className || '')) ||
      /\*/.test(getComputedStyle(l, '::after').content || '') || /\*/.test(getComputedStyle(l, '::before').content || ''));
  const entryLabel = el => { const e = el.closest('[class*="field-entry" i], [class*="fieldEntry" i], [data-automation-id^="formField"]'); return e ? e.querySelector('label, legend') : null; };
  const section = el => {
    if (el.closest('[data-field-path*="education" i], [class*="education" i], [id*="education" i], [data-automation-id*="education" i]')) return 'Education';
    if (el.closest('[data-field-path*="experience" i], [class*="work-experience" i], [class*="workExperience" i], [data-automation-id*="workExperience" i], [id*="employment-history" i]')) return 'Work experience';
    return '';
  };
  const SKIP_ZONE = 'header, nav, footer, [role=navigation], [role=search], .g-recaptcha, .h-captcha, [class*="captcha" i], [data-automation-id="utilityMenu"]';
  const CONTROLS = 'input:not([type=hidden]), select, textarea, [role=combobox], button[aria-haspopup=listbox]';

  // Restrict to an open modal if there is one with fields in it (Handshake, Workday sign-in, etc.)
  const COOKIE = /cookie|privacy (notice|preferences|settings)|consent|gdpr|tracking technologies/i;
  let root = document;
  const dialogs = [...document.querySelectorAll('[role=dialog], [aria-modal=true]')].filter(d => isVisible(d) && d.querySelector(CONTROLS) && !COOKIE.test((d.innerText || '').slice(0, 400)));
  if (dialogs.length) root = dialogs[dialogs.length - 1];

  const groupQuestion = (anchor, members, optionTexts) => {
    const fs = anchor.closest('fieldset');
    if (fs && root.contains(fs)) {
      const lg = fs.querySelector('legend');
      if (lg && textOf(lg)) return textOf(lg);
    }
    const grp = anchor.closest('[role=radiogroup], [role=group]');
    if (grp) {
      const lb = grp.getAttribute('aria-labelledby');
      const t = lb ? byIds(lb) : norm(grp.getAttribute('aria-label'));
      if (t) return t;
    }
    const mine = new Set(members);
    const strip = [];
    for (const m of members) { strip.push(m); const l = labelFor(m); if (l) strip.push(l); const w = m.closest('label'); if (w) strip.push(w); }
    let node = anchor.parentElement;
    for (let d = 0; node && d < 8; d++, node = node.parentElement) {
      if (node === document.body || node.tagName === 'FORM' || node === root) break;
      const others = [...node.querySelectorAll(CONTROLS)].filter(c => !mine.has(c) && shown(c) && !c.closest(SKIP_ZONE));
      if (others.length) break;
      if (strip.some(x => x === node || x.contains(node))) continue;
      strip.forEach(x => x.setAttribute('data-aa-strip', '1'));
      const c = node.cloneNode(true);
      strip.forEach(x => x.removeAttribute('data-aa-strip'));
      c.querySelectorAll('[data-aa-strip], select, option, script, style, [role=listbox]').forEach(x => x.remove());
      let t = norm(c.textContent);
      for (const o of optionTexts) if (o && o.length > 2) t = t.split(o).join(' ');
      t = norm(t);
      if (t.length > 1) return t.slice(0, 600);
    }
    return '';
  };
  const required = (el, q) => {
    if (el.required || el.getAttribute('aria-required') === 'true' || /[\*✱]/.test(q || '') || /\(required\)/i.test(q || '')) return true;
    const own = labelFor(el) || el.closest('label') || prevLabelEl(el);
    return own ? reqLabel(own) : reqLabel(entryLabel(el));
  };
  const groupRequired = (anchor, q) => /[\*✱]/.test(q || '') || reqLabel(entryLabel(anchor)) ||
      reqLabel(anchor.closest('fieldset') && anchor.closest('fieldset').querySelector('legend'));
  const clean = q => norm((q || '').replace(/[\*✱]/g, ' ').replace(/\(required\)/ig, ' '));

  window.__aaN = window.__aaN || 0;
  const tag = el => { if (!el.dataset.aa) el.dataset.aa = String(++window.__aaN); return el.dataset.aa; };
  const gtag = el => { if (!el.dataset.aag) el.dataset.aag = 'g' + String(++window.__aaN); return el.dataset.aag; };

  const fields = [];
  const done = new Set();
  const all = [...root.querySelectorAll(CONTROLS + ', [role=spinbutton]')];

  // Yes/No style button groups (Ashby and others): 2-6 sibling type=button buttons with short labels.
  const btnParents = new Set();
  for (const b of root.querySelectorAll('button')) {
    if ((b.getAttribute('type') || '').toLowerCase() === 'submit' && !b.hasAttribute('data-option')) continue;
    const p = b.parentElement;
    if (!p || btnParents.has(p) || b.closest(SKIP_ZONE) || b.closest('[role=listbox]')) continue;
    const sibs = [...p.children].filter(c => c.tagName === 'BUTTON' && isVisible(c));
    if (sibs.length < 2 || sibs.length > 6) continue;
    const labels = sibs.map(textOf);
    if (labels.some(l => !l || l.length > 40)) continue;
    if (!labels.some(l => /^(yes|no)$/i.test(l)) && !p.closest('[class*="question" i], [class*="field" i]')) continue;
    btnParents.add(p);
    const hiddenState = [...p.querySelectorAll('input')];   // e.g. Ashby keeps the Yes/No value in a hidden checkbox
    hiddenState.forEach(i => done.add(i));
    const q = groupQuestion(p, [...sibs, ...hiddenState], labels);
    if (!q) continue;
    const id = gtag(p);
    sibs.forEach((s, k) => s.dataset.aa = `${id}.${k}`);
    const sel = sibs.find(s => s.getAttribute('aria-pressed') === 'true' || /(selected|active|checked)/i.test(s.className));
    fields.push({id, kind: 'buttons', question: clean(q), required: groupRequired(p, q), options: labels, current: sel ? textOf(sel) : ''});
  }

  for (const el of all) {
    if (done.has(el) || el.disabled || el.closest(SKIP_ZONE)) continue;
    const type = (el.getAttribute('type') || '').toLowerCase();
    // Plain buttons are skipped, except dropdowns built from buttons (Workday's <button type="button" aria-haspopup="listbox">).
    if (['submit', 'button', 'image', 'reset', 'search'].includes(type) && el.getAttribute('aria-haspopup') !== 'listbox') continue;
    const name = el.getAttribute('name') || '';
    if (/captcha|g-recaptcha-response|honeypot/i.test(name + ' ' + el.id)) continue;
    if (!shown(el)) continue;

    if (type === 'radio' || type === 'checkbox') {
      let members = [];
      if (name) members = [...root.querySelectorAll(`input[type=${type}][name="${CSS.escape(name)}"]`)];
      if (members.length < 2) {
        const fs = el.closest('fieldset, [role=group], [role=radiogroup]');
        if (fs) members = [...fs.querySelectorAll(`input[type=${type}]`)];
      }
      if (members.length < 2 && type === 'radio') members = [el];
      members = members.filter(m => shown(m) && !m.disabled);
      members.forEach(m => done.add(m));
      if (type === 'checkbox' && members.length < 2) {
        const own = ownLabel(el);
        const ctx = groupQuestion(el, [el], [own]);
        const q = ctx && !own.includes(ctx) ? `${ctx} — ${own}` : (own || ctx);
        fields.push({id: tag(el), kind: 'checkbox', question: clean(q), required: required(el, q), options: [], current: el.checked ? 'checked' : ''});
        continue;
      }
      const opts = members.map(m => ownLabel(m) || m.value);
      const q = groupQuestion(members[0], members, opts);
      const id = gtag(members[0]);
      members.forEach((m, k) => m.dataset.aa = `${id}.${k}`);
      const req = members.some(m => m.required || m.getAttribute('aria-required') === 'true') || groupRequired(members[0], q);
      const cur = members.filter(m => m.checked).map(m => ownLabel(m) || m.value);
      fields.push({id, kind: type === 'radio' ? 'radio' : 'checkboxes', question: clean(q), required: req, options: opts, current: cur.join(' | ')});
      continue;
    }

    if (el.getAttribute('role') === 'spinbutton' || /dateSection(Month|Day|Year)/.test(el.dataset.automationId || '')) {
      const wrap = el.closest('[data-automation-id*="dateInputWrapper" i], [data-automation-id*="dateWidget" i], [class*="date" i]') || el.parentElement;
      const parts = [...wrap.querySelectorAll('[role=spinbutton], input[data-automation-id*="dateSection"]')];
      parts.forEach(p => done.add(p));
      const kinds = parts.map(p => { const s = (p.dataset.automationId || p.getAttribute('aria-label') || p.placeholder || '').toLowerCase(); return s.includes('month') ? 'month' : s.includes('day') ? 'day' : s.includes('year') ? 'year' : ''; });
      const q = groupQuestion(parts[0], parts, ['MM', 'DD', 'YYYY', 'Month', 'Day', 'Year']);
      const id = gtag(parts[0]);
      parts.forEach((p, k) => p.dataset.aa = `${id}.${k}`);
      fields.push({id, kind: 'date', question: clean(q), required: required(parts[0], q), options: kinds, current: parts.map(p => p.value).join('/')});
      continue;
    }

    done.add(el);
    if (type !== 'file' && el.getAttribute('aria-hidden') === 'true') continue;
    const own = ownLabel(el);
    let q = own || groupQuestion(el, [el], []) || norm(el.placeholder) || name;
    // Dropdowns labelled only by their placeholder (Workday: aria-label "Select One Required"): use the question around them.
    if (/^(select( one)?|choose( one)?|please select|--)\s*(required)?\.?$/i.test(q)) {
      const pl = prevLabelEl(el);
      q = (pl && textOf(pl)) || groupQuestion(el, [el], [textOf(el)]) || q;
    }
    const GENERIC_SEARCH = /^(search|search jobs|search this site|search the site|search careers|search by keyword|keywords?)(\.\.\.|…)?$/i;
    const promptBox = /searchBox/.test(el.dataset.automationId || '') || !!el.closest('[data-automation-id="multiselectInputContainer"]');
    if (!promptBox && (el.getAttribute('role') === 'searchbox' || GENERIC_SEARCH.test(q) || GENERIC_SEARCH.test(norm(el.placeholder)))) continue;
    const sec = section(el);
    if (sec && type !== 'file' && !new RegExp(sec.split(' ')[0], 'i').test(q)) q = `${sec}: ${q}`;
    const base = {id: tag(el), question: '', required: false, options: [], current: '', name, autocomplete: el.getAttribute('autocomplete') || '', inputType: type};

    if (el.tagName === 'SELECT') {
      const opts = [...el.options].filter(o => o.value !== '' && !/^(select|choose|please select|--)/i.test(norm(o.text))).map(o => norm(o.text));
      const cur = el.selectedIndex >= 0 && el.value ? norm(el.options[el.selectedIndex].text) : '';
      const ph = el.options.length && el.options[0].value === '' ? norm(el.options[0].text).replace(/[.…]+$/, '') : '';
      if (ph && !/^(select|choose|please|--|none)/i.test(ph) && !q.toLowerCase().includes(ph.toLowerCase())) q = `${q} (${ph})`;
      fields.push({...base, kind: 'select', question: clean(q), required: required(el, q), options: opts.slice(0, 4000), multiple: el.multiple, current: cur});
    } else if (type === 'file') {
      const ctx = groupQuestion(el, [el], []);
      q = norm(`${ctx} ${own}`) || q;
      q = norm(q.replace(/choose files?|(or )?drag (and|&) drop( files?)?( here)?|upload( a)? files?|\battach\b|dropbox|google drive|enter manually|accepted (file )?formats?:?[^.]*?(?=resume|cv|cover|$)|file exceeds the maximum upload size[^.]*\.( please try a smaller size\.)?|svgs not supported by this browser\.?|success!?|\b[0-9a-f]{8}\b|autofill from resume.*?(?=resume|$)/ig, ' '));
      const hasFile = (el.files && el.files.length) ? el.files[0].name : '';
      fields.push({...base, kind: 'file', question: clean(q).slice(0, 300), required: required(el, q), accept: el.accept || '', current: hasFile});
    } else if (el.tagName === 'BUTTON') {
      let cur = textOf(el);
      if (/^select one$|^select$|^choose/i.test(cur)) cur = '';
      fields.push({...base, kind: 'listbox', question: clean(q.replace(cur, '')), required: required(el, q), current: cur});
    } else if (el.tagName === 'TEXTAREA') {
      fields.push({...base, kind: 'textarea', question: clean(q), required: required(el, q), current: el.value, maxlength: el.maxLength > 0 ? el.maxLength : null});
    } else {
      const isMulti = /searchBox/.test(el.dataset.automationId || '') || !!el.closest('[data-automation-id="multiselectInputContainer"]');
      const isCombo = el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') === 'list' || el.hasAttribute('aria-haspopup') && el.getAttribute('aria-haspopup') !== 'false';
      if (isMulti) {
        const box = el.closest('[data-automation-id="multiselectInputContainer"]')?.parentElement;
        const sel = box ? [...box.querySelectorAll('[data-automation-id="selectedItem"]')].map(textOf).join(' | ') : '';
        fields.push({...base, kind: 'multiselect', question: clean(q), required: required(el, q), current: sel});
      } else if (isCombo) {
        // the chosen value is shown in a sibling element a few levels up (react-select: .select__single-value)
        let single = null, anc = el;
        for (let k = 0; k < 4 && anc && !single; k++) {
          anc = anc.parentElement;
          if (anc) single = anc.querySelector('[class*="single-value" i], [class*="singleValue" i], [class*="multi-value__label" i], [class*="multiValue" i]');
        }
        fields.push({...base, kind: 'combobox', question: clean(q), required: required(el, q), current: el.value || (single ? textOf(single) : '')});
      } else {
        fields.push({...base, kind: 'text', question: clean(q), required: required(el, q), current: el.value, maxlength: el.maxLength > 0 ? el.maxLength : null});
      }
    }
    if (fields.length > 160) break;
  }
  return fields.filter(f => f.question || f.kind === 'file');
}
"""

# Page-level state: buttons we might press, visible errors, CAPTCHA, success text.
STATE_JS = r"""
() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const isVisible = el => { if (!el || !el.isConnected) return false; const st = getComputedStyle(el); if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const COOKIE = /cookie|privacy (notice|preferences|settings)|consent|gdpr|tracking technologies/i;
  const CONTROLS = 'input:not([type=hidden]), select, textarea, [role=combobox], button[aria-haspopup=listbox]';
  let root = document;
  const dialogs = [...document.querySelectorAll('[role=dialog], [aria-modal=true]')].filter(d => isVisible(d) && !COOKIE.test((d.innerText || '').slice(0, 400)) && (d.querySelector(CONTROLS) || /sign in|create account|apply|submit/i.test(d.innerText || '')));
  if (dialogs.length) root = dialogs[dialogs.length - 1];
  window.__aabN = window.__aabN || 0;
  const buttons = [];
  for (const b of root.querySelectorAll('button, input[type=submit], a[role=button], [role=button], a[data-automation-id]')) {
    if (!isVisible(b) || b.disabled || b.getAttribute('aria-disabled') === 'true') continue;
    if (b.closest('header, nav, footer, [role=navigation]')) continue;
    const text = norm(b.innerText || b.value || b.getAttribute('aria-label'));
    if (!text || text.length > 60) continue;
    if (!b.dataset.aab) b.dataset.aab = String(++window.__aabN);
    buttons.push({id: b.dataset.aab, text, type: (b.getAttribute('type') || '').toLowerCase(), auto: b.dataset.automationId || ''});
  }
  // Greenhouse's newer forms: <p id="{field}-error" class="helper-text helper-text--error">, present only while there's an error.
  const errSel = '[role=alert], [aria-live=assertive], .error, .errors, .field-error, .error-message, [class*="error-message" i], [class*="errorMessage" i], [data-automation-id="errorMessage"], [data-automation-id="inputAlert"], .application--error, [id$="-error"], [id$="_error"], [class*="helper-text--error" i], [class*="error-text" i], [class*="input-error" i]';
  const errors = [...new Set([...document.querySelectorAll(errSel)].filter(isVisible).map(e => norm(e.innerText)).filter(t => t && t.length < 300))].slice(0, 15);
  const invalid = [...document.querySelectorAll('[aria-invalid=true]')].filter(isVisible).map(e => e.dataset.aa || '').filter(Boolean);
  const frames = [...document.querySelectorAll('iframe')].map(f => ({src: f.src || '', vis: isVisible(f), w: f.getBoundingClientRect().width, h: f.getBoundingClientRect().height}));
  const captchaChallenge = frames.some(f => f.vis && /recaptcha\/(api2|enterprise)\/bframe|hcaptcha.*(challenge|frame=challenge)|challenges\.cloudflare\.com/.test(f.src) && f.w > 100 && f.h > 100)
    || frames.some(f => f.vis && /recaptcha\/(api2|enterprise)\/anchor/.test(f.src) && f.h > 50 && !/size=invisible/.test(f.src))
    || frames.some(f => f.vis && /hcaptcha\.com.*checkbox/.test(f.src) && f.h > 50);
  const pw = [...document.querySelectorAll('input[type=password]')].filter(isVisible).length;
  return {
    url: location.href, title: document.title,
    // The whole page: on hosted boards the form (and any code prompt or thank-you under it) sits below a long description.
    text: norm(document.body ? document.body.innerText : '').slice(0, 200000),
    buttons, errors, invalid, captchaChallenge, passwordFields: pw, inDialog: root !== document,
    cookieButtons: [...document.querySelectorAll('button, a[role=button], [role=button]')].filter(b => {
      if (!isVisible(b)) return false;
      const box = b.closest('[role=dialog], [class*="cookie" i], [id*="cookie" i], [class*="consent" i], [id*="consent" i], [id*="onetrust" i], [class*="onetrust" i]');
      return !!box && COOKIE.test((box.innerText || '').slice(0, 600));
    }).map(b => { if (!b.dataset.aab) b.dataset.aab = String(++window.__aabN); return {id: b.dataset.aab, text: norm(b.innerText || b.getAttribute('aria-label'))}; }),
  };
}
"""

SUCCESS_RE = (r"thank(s| you)\s+(so much\s+)?for\s+(applying|your application|your interest|submitting)|"
              r"application\s+(has been\s+|was\s+)?(submitted|received|sent|complete)|"
              r"(we('ve| have)|we)\s+(successfully\s+)?received\s+your\s+application|"
              r"successfully\s+(submitted|applied)|you('ve| have)\s+(successfully\s+)?applied|"
              r"your application (is|has been) (on its way|submitted|received)|congratulations.*submitted")
CODE_PROMPT_RE = r"(security|verification|confirmation)\s+code|enter\s+the\s+(\d+-character\s+)?code|code\s+(was\s+)?sent\s+to"

# Every visible, typeable text input with what it says about itself (label, name, id, placeholder, autocomplete,
# and the label of a surrounding group, for one-character boxes). Python decides which one wants the emailed code.
CODE_INPUTS_JS = r"""
() => {
  window.__aaN = window.__aaN || 0;
  window.__aaDoc = window.__aaDoc || Math.random().toString(36).slice(2);   // tells a new page's inputs from the old one's
  const tag = el => { if (!el.dataset.aa) el.dataset.aa = String(++window.__aaN); return el.dataset.aa; };
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const vis = el => {
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const text = el => el ? norm(el.innerText || el.textContent).slice(0, 120) : '';
  const byIds = ids => (ids || '').split(/\s+/).filter(Boolean).map(id => text(document.getElementById(id))).join(' ');
  const out = [];
  for (const el of document.querySelectorAll('input')) {
    const type = (el.getAttribute('type') || 'text').toLowerCase();
    if (!['text', 'tel', 'number', 'password', 'search', ''].includes(type) || el.disabled || el.readOnly || !vis(el)) continue;
    const lab = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
    const grp = el.closest('[role=group], fieldset, [aria-label], [aria-labelledby]');
    const grpLabel = grp && grp !== el ? norm(grp.getAttribute('aria-label')) || byIds(grp.getAttribute('aria-labelledby'))
                                         || text(grp.querySelector('legend')) : '';
    const hint = norm([text(lab), text(el.closest('label')), el.getAttribute('aria-label'), byIds(el.getAttribute('aria-labelledby')),
                       el.placeholder, el.name, el.id, el.getAttribute('data-testid'), grpLabel].filter(Boolean).join(' | '));
    out.push({id: tag(el), doc: window.__aaDoc, maxlength: el.maxLength > 0 ? el.maxLength : null, value: el.value,
              ac: (el.getAttribute('autocomplete') || '').toLowerCase(), hint});
  }
  return out;
}
"""
SUBMIT_RE = r"^(submit|submit application|submit your application|send application|apply|apply now|finish|complete application|submit my application|confirm and submit)$"
NEXT_RE = r"^(next|continue|save and continue|save & continue|next step|proceed|review|review application|continue to next step)$"
