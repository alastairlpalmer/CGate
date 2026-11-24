/* C-vet at a desk (docs/C_VET_PLAN.md, section 7h, step 2).

   Only a wide screen loads this file (static/cvet/app.js, loadDesk). It
   adds what a keyboard, a pointer and a printer make possible. It holds
   no data and no rule of its own: it reads the app's state and calls
   the app's functions (window.CvetApp).

     - the keyboard: "/" to search, the arrows in a list, Ctrl+Enter to save
     - Prices as a table, with a search
     - a client's page: how to reach the client, under its horses
     - print: one visit, or one horse's record

   Text goes on the page with textContent only, as in app.js. */
(function () {
    'use strict';

    var app = window.CvetApp;
    if (!app || window.CvetDesk) return;
    var $ = app.$;
    var el = app.el;
    var state = app.state;
    var core = app.core;
    var priceQuery = '';

    function visible(node) { return !!node && node.offsetParent !== null; }

    // ── The keyboard ─────────────────────────────────────────────────────

    // The search box of what is on the screen now.
    function searchBox() {
        if (!$('picker').hidden) return $('picker-search');
        var ids = { clients: ['client-search'], client: ['search', 'client-search'], prices: ['price-filter'] }[state.view] || [];
        return ids.map($).filter(visible)[0] || null;
    }

    // Up and down: from a search box into its list, and from row to row.
    function step(event) {
        var from = document.activeElement;
        var scope = from && from.closest('.sheet, main');
        if (!scope || !from.matches('input[type=search], .horse, .pick, .rowlink')) return;
        var stops = Array.prototype.filter.call(
            scope.querySelectorAll('input[type=search], .horse, .pick, .rowlink'), visible);
        var next = stops[stops.indexOf(from) + (event.key === 'ArrowDown' ? 1 : -1)];
        if (!next) return;
        event.preventDefault();
        next.focus();
    }

    document.addEventListener('keydown', function (event) {
        if (!app.isWide()) return;
        var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName);
        if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
            // Save the note from anywhere on the horse screen.
            if (state.view === 'horse' && $('picker').hidden && $('horse-sheet').hidden) {
                event.preventDefault();
                $('save').click();
            }
        } else if (event.key === '/' && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) {
            var box = searchBox();
            if (box) {
                event.preventDefault();
                box.focus();
                box.select();
            }
        } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            step(event);
        }
    });

    // ── Prices as a table ────────────────────────────────────────────────

    function cell(tag, className, text) {
        return el(tag, className, text);
    }

    function renderPriceTable() {
        var filter = $('price-filter');
        if (filter) filter.hidden = !app.isWide();
        if (!app.isWide()) return;
        if (!filter) {
            filter = el('input');
            filter.id = 'price-filter';
            filter.type = 'search';
            filter.placeholder = 'Find a price   ( / )';
            filter.autocomplete = 'off';
            filter.setAttribute('aria-label', 'Find a price');
            filter.addEventListener('input', function () {
                priceQuery = filter.value;
                renderPriceTable();
            });
            $('prices').parentNode.insertBefore(filter, $('prices'));
        }
        var types = {};
        (state.meta.vaccinationTypes || []).forEach(function (type) { types[type.id] = type.name; });
        var words = priceQuery.toLowerCase().split(/\s+/).filter(Boolean);
        var rows = state.prices.filter(function (price) {
            var text = (price.name + ' ' + (price.category || 'Other')).toLowerCase();
            return words.every(function (word) { return text.indexOf(word) !== -1; });
        }).sort(function (a, b) {
            return (a.category || '￿').localeCompare(b.category || '￿') || a.name.localeCompare(b.name);
        });

        var table = el('table', 'table card');
        var head = el('tr');
        [['Name', ''], ['Category', ''], ['Records a vaccination', ''], ['VAT', 'num'], ['Price', 'num']].forEach(function (column) {
            head.appendChild(cell('th', column[1], column[0]));
        });
        table.appendChild(head);
        rows.forEach(function (price) {
            var row = el('tr', 'prow');
            var name = el('td');
            // The name is the button: the row is in reach of the keyboard.
            var link = app.button('rowlink' + (price.isActive ? '' : ' off'), price.name, function (event) {
                event.stopPropagation();
                app.editPrice(price);
            });
            name.appendChild(link);
            if (!price.isActive) name.appendChild(el('small', '', ' Hidden'));
            row.appendChild(name);
            row.appendChild(cell('td', '', price.category || 'Other'));
            row.appendChild(cell('td', '', types[price.vaccinationTypeId] || ''));
            row.appendChild(cell('td', 'num', Number(price.vatRate) + '%'));
            row.appendChild(cell('td', 'num strong', core.money(price.pence)));
            row.addEventListener('click', function () { app.editPrice(price); });
            table.appendChild(row);
        });
        app.fill($('prices'), rows.length ? [table] : [el('p', 'muted', state.prices.length
            ? 'No price has these words.' : 'No prices yet. Tap Add a price.')]);
    }

    // ── A client's page ──────────────────────────────────────────────────

    function linkTo(href, text) {
        var link = el('a', '', text);
        link.href = href;
        return link;
    }

    // Under the client's tabs: how to reach the client. Its visits are on
    // its Timeline tab.
    function renderClientPage() {
        var client = app.clientById(state.client);
        var box = $('client-desk');
        if (!app.isWide() || state.view !== 'client' || !client) {
            if (box) box.hidden = true;
            return;
        }
        if (!box) {
            box = el('div');
            box.id = 'client-desk';
            $('view-client').appendChild(box);
        }
        box.hidden = false;
        var nodes = [];
        var details = [
            ['Contact', client.contact_name],
            ['Phone', client.phone, 'tel:'],
            ['Email', client.email, 'mailto:'],
            ['Address', client.address]
        ].filter(function (row) { return row[1]; });
        if (details.length) {
            var list = el('dl', 'card');
            details.forEach(function (row) {
                list.appendChild(el('dt', '', row[0]));
                var value = el('dd');
                if (row[2]) value.appendChild(linkTo(row[2] + row[1].replace(/\s+/g, ''), row[1]));
                else value.textContent = row[1];
                list.appendChild(value);
            });
            nodes.push(el('h3', '', 'Details'), list);
        }
        app.fill(box, nodes);
        box.hidden = nodes.length === 0;
    }

    // ── Print ────────────────────────────────────────────────────────────

    // Print one part of the screen, with every fold in it open, under a
    // line that says whose record it is.
    function printNode(node, heading) {
        var head = el('p', 'print-head', heading);
        if (node.tagName === 'DETAILS') node.open = true;
        Array.prototype.forEach.call(node.querySelectorAll('details'), function (part) { part.open = true; });
        node.insertBefore(head, node.firstChild);
        node.classList.add('printing');
        document.body.classList.add('print-one');
        function done() {
            window.removeEventListener('afterprint', done);
            node.classList.remove('printing');
            document.body.classList.remove('print-one');
            if (head.parentNode) head.parentNode.removeChild(head);
        }
        window.addEventListener('afterprint', done);
        // A fold fills itself a moment after it opens.
        setTimeout(function () { window.print(); }, 200);
    }

    function printedBy() {
        return (state.meta.practice || 'C-vet') + ' · printed ' + core.shortDate(core.localDay(new Date()), '0');
    }

    function addPrintToVisit(row, box) {
        if (!app.isWide()) return;
        box.appendChild(app.button('link calm', 'Print this visit', function () {
            printNode(box.parentNode, printedBy() + ' · visit for ' + row.clientName + ', ' + core.shortDate(row.day, '0'));
        }));
    }

    function addPrintToHistory(horse, box) {
        if (!app.isWide() || !box) return;
        var client = app.clientById(horse.client_id);
        box.appendChild(app.button('link calm', 'Print this record', function () {
            printNode(box, printedBy() + ' · ' + horse.name + (client ? ', ' + client.name : ''));
        }));
    }

    // ── What the app tells this file ─────────────────────────────────────

    window.CvetDesk = function (what, a, b) {
        if (what === 'view') renderClientPage();
        else if (what === 'prices') renderPriceTable();
        else if (what === 'visit') addPrintToVisit(a, b);
        else if (what === 'history') addPrintToHistory(a, b);
    };

    // The screen was drawn before this file arrived: add its part now.
    if (state.view === 'prices') app.renderPrices();
    else renderClientPage();
})();
