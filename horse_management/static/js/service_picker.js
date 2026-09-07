/**
 * Services directory picker (billing.forms.ServicePickerMixin).
 *
 * A record form's "Service" <select> carries, per option, the item's price
 * (data-price) and the record fields it fills (data-fill, JSON). Choosing a
 * service copies the price into the cost input and locks it behind an
 * "Override price" button; choosing "Other" unlocks everything and clears
 * what the picker filled in. The server repeats the fill for a form posted
 * without JavaScript, so this only saves clicks — it never decides price.
 *
 * Runs on every htmx:load, so pop-up sheets and boosted page swaps pick it
 * up without per-page wiring. The pure part (decide) is exported for
 * static/js/tests.
 */
(function (root) {
    'use strict';

    var OTHER = 'other';

    /**
     * What the form should do for the selected option.
     * option: {value, price, fill} (or null); costValue: the cost input's
     * current text. A preset whose cost differs from its price counts as
     * overridden — the input stays editable and the button offers the
     * set price back.
     */
    function decide(option, costValue) {
        if (!option || !option.value) {
            return { mode: 'none' };
        }
        if (option.value === OTHER) {
            return { mode: 'other' };
        }
        var price = option.price == null ? '' : String(option.price);
        var typed = costValue == null ? '' : String(costValue).trim();
        var overridden = typed !== '' && Number(typed) !== Number(price);
        return {
            mode: 'preset',
            price: price,
            fill: option.fill || {},
            overridden: overridden
        };
    }

    function formatPrice(price) {
        var n = Number(price);
        return '£' + (isNaN(n) ? price : n.toFixed(2));
    }

    var api = { decide: decide, formatPrice: formatPrice, OTHER: OTHER };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
        return;
    }
    root.YardwayServicePicker = api;

    if (typeof document === 'undefined') {
        return;
    }

    // ── DOM wiring ───────────────────────────────────────────────────

    function readOption(select) {
        var o = select.options[select.selectedIndex];
        if (!o) {
            return null;
        }
        var fill = {};
        if (o.dataset.fill) {
            try { fill = JSON.parse(o.dataset.fill); } catch (e) { fill = {}; }
        }
        return { value: o.value, price: o.dataset.price || null, fill: fill };
    }

    function costInput(select) {
        var form = select.form;
        if (!form) {
            return null;
        }
        return form.querySelector('[data-service-cost]')
            || form.querySelector('[name="cost"]')
            || form.querySelector('[name="amount"]');
    }

    function fieldIn(form, name) {
        return form ? form.querySelector('[name="' + name + '"]') : null;
    }

    function overrideButton(cost, create) {
        var wrap = cost.parentNode;
        var btn = wrap ? wrap.querySelector('.service-price-override') : null;
        if (!btn && create && wrap) {
            btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'service-price-override text-xs text-forest underline mt-1';
            cost.insertAdjacentElement('afterend', btn);
        }
        return btn;
    }

    function lock(cost, price) {
        cost.readOnly = true;
        cost.classList.add('bg-sage-50');
        cost.dataset.servicePrice = price;
        var btn = overrideButton(cost, true);
        if (btn) {
            btn.textContent = 'Override price';
            btn.hidden = false;
        }
    }

    // Editable, but the set price is one click away.
    function unlockOverridden(cost, price) {
        cost.readOnly = false;
        cost.classList.remove('bg-sage-50');
        cost.dataset.servicePrice = price;
        var btn = overrideButton(cost, true);
        if (btn) {
            btn.textContent = 'Use set price (' + formatPrice(price) + ')';
            btn.hidden = false;
        }
    }

    function unlock(cost) {
        cost.readOnly = false;
        cost.classList.remove('bg-sage-50');
        delete cost.dataset.servicePrice;
        var btn = overrideButton(cost, false);
        if (btn) {
            btn.hidden = true;
        }
    }

    function applied(select) {
        if (!select._serviceApplied) {
            select._serviceApplied = {};
        }
        return select._serviceApplied;
    }

    // Copy the item's defaults in. Selects always follow the service
    // (their default is never "blank"); text inputs only when empty or
    // still holding what a previous pick filled in.
    function fillFields(select, fill) {
        var form = select.form;
        var previous = applied(select);
        var next = {};
        Object.keys(fill).forEach(function (name) {
            var field = fieldIn(form, name);
            if (!field) {
                return;
            }
            var value = String(fill[name]);
            if (field.tagName === 'SELECT' || !field.value || field.value === String(previous[name])) {
                field.value = value;
            }
            next[name] = value;
        });
        select._serviceApplied = next;
    }

    function clearFilled(select) {
        var form = select.form;
        var previous = applied(select);
        Object.keys(previous).forEach(function (name) {
            var field = fieldIn(form, name);
            if (field && field.tagName !== 'SELECT' && field.value === String(previous[name])) {
                field.value = '';
            }
        });
        select._serviceApplied = {};
    }

    function firstFillField(select) {
        var names = Object.keys(applied(select));
        for (var i = 0; i < names.length; i++) {
            var field = fieldIn(select.form, names[i]);
            if (field && field.tagName !== 'SELECT') {
                return field;
            }
        }
        return null;
    }

    /** On change: apply the pick. initial=true only restores the locked
        state after a re-render (validation error) without touching values. */
    function apply(select, initial) {
        var cost = costInput(select);
        var state = decide(readOption(select), cost ? cost.value : '');
        if (state.mode === 'preset') {
            if (!initial) {
                if (cost) {
                    cost.value = state.price;
                }
                fillFields(select, state.fill);
                state.overridden = false;
            } else {
                select._serviceApplied = state.fill;
            }
            if (cost) {
                if (state.overridden) {
                    unlockOverridden(cost, state.price);
                } else {
                    lock(cost, state.price);
                }
            }
            return;
        }
        if (cost) {
            if (!initial && cost.readOnly && cost.dataset.servicePrice === cost.value) {
                cost.value = '';
            }
            unlock(cost);
        }
        if (!initial) {
            var focus = firstFillField(select) || cost;
            clearFilled(select);
            if (state.mode === OTHER && focus) {
                focus.focus();
            }
        }
    }

    function init(select) {
        if (select.dataset.servicePickerReady) {
            return;
        }
        select.dataset.servicePickerReady = '1';
        apply(select, true);
    }

    function initWithin(rootEl) {
        if (!rootEl || !rootEl.querySelectorAll) {
            return;
        }
        if (rootEl.matches && rootEl.matches('select[data-service-picker]')) {
            init(rootEl);
        }
        rootEl.querySelectorAll('select[data-service-picker]').forEach(init);
    }

    document.addEventListener('change', function (e) {
        var t = e.target;
        if (t && t.matches && t.matches('select[data-service-picker]')) {
            apply(t, false);
        }
    });

    document.addEventListener('click', function (e) {
        var btn = e.target && e.target.closest ? e.target.closest('.service-price-override') : null;
        if (!btn) {
            return;
        }
        var cost = btn.previousElementSibling;
        if (!cost || !cost.dataset) {
            return;
        }
        var price = cost.dataset.servicePrice || '';
        if (cost.readOnly) {
            unlockOverridden(cost, price);
            cost.focus();
            cost.select();
        } else {
            cost.value = price;
            lock(cost, price);
        }
    });

    document.addEventListener('htmx:load', function (e) {
        initWithin(e.detail && e.detail.elt ? e.detail.elt : document);
    });
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { initWithin(document); });
    } else {
        initWithin(document);
    }
})(typeof window !== 'undefined' ? window : this);
