/* C-vet: what needs a signal (docs/C_VET_PLAN.md, section 5).

   To change the price list, to sign out, and to mark an invoice as
   paid. None of them works with no signal, so the phone does not need this code to open the app or to
   work at a yard. The app fetches this file the first time one of
   them is used (static/cvet/app.js, online), and the service worker
   keeps it from then on.

   It holds no data: it reads the app's state and calls the app's
   functions (window.CvetApp). Text goes on the page with textContent
   only, as in app.js. */
(function () {
    'use strict';

    var app = window.CvetApp;
    if (!app || window.CvetOnline) return;
    var $ = app.$;
    var el = app.el;
    var fill = app.fill;
    var say = app.say;
    var state = app.state;
    var core = app.core;
    var config = app.config;
    var newId = app.newId;
    var getSession = app.getSession;
    var request = app.request;
    var post = app.post;
    var put = app.put;
    var fromServerPrice = app.fromServerPrice;
    var lossQuestion = app.lossQuestion;
    var clearEverything = app.clearEverything;
    var setHorses = app.setHorses;
    var setClients = app.setClients;
    var renderPrices = app.renderPrices;
    var DENIED_TEXT = app.DENIED_TEXT;
    var setMeta = app.setMeta;
    var bills = app.bills;
    var openBill = app.openBill;
    var render = app.render;

    // Open the form at the top of the screen: for a new price (null), or
    // for one of the list.
    function editPrice(price) {
        state.editingPrice = price;
        state.priceFormOpen = true;
        $('price-name').value = price ? price.name : '';
        $('price-category').value = price ? price.category || '' : '';
        // The categories in use, offered as the vet types.
        fill($('price-categories'), core.priceCategories(state.prices.map(function (other) {
            return { name: other.name, category: other.category, isActive: true };
        })).filter(function (name) { return name !== 'Other'; }).map(function (name) {
            var option = el('option');
            option.value = name;
            return option;
        }));
        $('price-amount').value = price ? core.decimal(price.pence) : '';
        $('price-vat').value = price ? String(Number(price.vatRate)) : '20';
        renderVaccinationTypes(price ? price.vaccinationTypeId : null);
        $('price-message').textContent = '';
        renderPrices();
        window.scrollTo(0, 0);
        $('price-name').focus({ preventScroll: true });
    }

    function closePriceForm() {
        state.editingPrice = null;
        state.priceFormOpen = false;
        renderPrices();
    }

    // The yard's vaccinations. A price marked as one of them also puts
    // the dose in the yard's vaccination records when the visit is billed.
    function renderVaccinationTypes(selected) {
        var types = state.meta.vaccinationTypes || [];
        var select = $('price-vaccination');
        $('price-vaccination-row').hidden = types.length === 0;
        var options = [el('option', '', 'No')];
        options[0].value = '';
        types.forEach(function (type) {
            var option = el('option', '', type.name);
            option.value = String(type.id);
            options.push(option);
        });
        fill(select, options);
        select.value = selected ? String(selected) : '';
    }

    function savePrice(event, flipActive) {
        if (event) event.preventDefault();
        var message = $('price-message');
        var editing = state.editingPrice;
        var name = $('price-name').value.trim();
        var pence = core.toPence($('price-amount').value);
        var vat = core.toPence($('price-vat').value);
        if (!name) { message.textContent = 'Type a name.'; return; }
        if (pence === null) { message.textContent = 'Type the price as a number, for example 65 or 65.50.'; return; }
        if (vat === null || vat >= 10000) { message.textContent = 'Type the VAT rate as a number, for example 20.'; return; }
        var form = new FormData();
        form.append('id', editing ? editing.id : newId());
        form.append('name', name);
        form.append('category', $('price-category').value.trim());
        form.append('price', core.decimal(pence));
        form.append('vat_rate', core.decimal(vat));
        form.append('vaccination_type_id', $('price-vaccination').value);
        var active = editing ? editing.isActive : true;
        if (flipActive) active = !active;
        form.append('is_active', active ? 'true' : 'false');
        message.textContent = '';
        say('Saving…');
        getSession().then(function (result) {
            if (result.kind !== 'ok') say('');
            if (result.kind === 'offline') { message.textContent = 'No signal. A price change needs a signal.'; return; }
            if (result.kind !== 'ok') { message.textContent = result.kind === 'denied' ? DENIED_TEXT : 'Sign in again to change a price.'; return; }
            return post(config.pricesUrl, form, result.session.csrf).then(function (reply) {
                if (reply.kind !== 'ok' || (reply.status !== 200 && reply.status !== 201)) {
                    say('');
                    message.textContent = reply.kind === 'offline' ? 'No signal. The price was not saved.' : 'The price was not saved.';
                    return;
                }
                var saved = fromServerPrice(reply.data);
                state.prices = state.prices.filter(function (price) { return price.id !== saved.id; }).concat([saved])
                    .sort(function (a, b) { return a.name.localeCompare(b.name); });
                return put('prices', saved).then(function () {
                    closePriceForm();
                    say(saved.name + ' saved.');
                });
            });
        });
    }

    function forgetThisPhone() {
        setHorses([]);
        setClients([]);
        state.prices = [];
        state.entries = [];
        state.meta = {};
        state.draft = null;
        var jobs = [clearEverything()];
        // The stored page too, so the app does not open for the next
        // person who holds the phone.
        if (window.caches && caches.keys) {
            jobs.push(caches.keys().then(function (names) {
                return Promise.all(names.filter(function (name) {
                    return name.indexOf('cvet-') === 0;
                }).map(function (name) { return caches.delete(name); }));
            }));
        }
        if (navigator.serviceWorker && navigator.serviceWorker.getRegistrations) {
            jobs.push(navigator.serviceWorker.getRegistrations().then(function (registrations) {
                return Promise.all(registrations.map(function (registration) {
                    return registration.unregister();
                }));
            }));
        }
        return Promise.all(jobs);
    }

    // Sign out needs a signal: the server must end the sign-in. With no
    // signal, Clear this phone removes the data and the sign-in stays.
    function signOut() {
        if (!window.confirm(lossQuestion('Signing out', 'Sign out and remove the clients, the horses and all notes from this phone?'))) return;
        say('Signing out…');
        getSession().then(function (result) {
            if (result.kind === 'offline') {
                say('No signal. Sign out needs a signal. To remove the data now, tap Clear this phone.');
                return;
            }
            var done = result.kind === 'ok' ? request(config.logoutUrl, {
                method: 'POST',
                headers: { 'X-CSRFToken': result.session.csrf },
                redirect: 'manual'
            }, 20000) : Promise.resolve();
            return done.then(forgetThisPhone).then(function () {
                window.location.href = config.loginUrl;
            });
        }).catch(function (error) {
            say('Sign out did not finish: ' + error.message);
        });
    }

    // Mark an invoice as paid today, or as not paid again.
    function markPaid(bill, paid, message) {
        var form = new FormData();
        if (!paid) form.append('paid', 'no');
        message.textContent = '';
        getSession().then(function (result) {
            if (result.kind !== 'ok') {
                message.textContent = result.kind === 'offline' ? 'No signal. The invoice was not changed.'
                    : result.kind === 'denied' ? DENIED_TEXT : 'Sign in again to change an invoice.';
                return;
            }
            return post(config.billsApiUrl + bill.id + '/paid/', form, result.session.csrf).then(function (reply) {
                if (reply.kind !== 'ok' || reply.status !== 200) {
                    message.textContent = reply.kind === 'offline' ? 'No signal. The invoice was not changed.' : 'The invoice was not changed.';
                    return;
                }
                return setMeta('billList', bills().map(function (other) {
                    return other.id === reply.data.id ? reply.data : other;
                })).then(function () {
                    say(reply.data.number + (reply.data.paid_on ? ' marked as paid.' : ' marked as not paid.'));
                    openBill(reply.data);
                    render();
                });
            });
        });
    }

    $('price-form').addEventListener('submit', function (event) { savePrice(event, false); });
    $('price-hide').addEventListener('click', function () { savePrice(null, true); });
    $('price-cancel').addEventListener('click', closePriceForm);

    window.CvetOnline = { editPrice: editPrice, signOut: signOut, markPaid: markPaid };
})();
