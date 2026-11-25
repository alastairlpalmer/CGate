/* C-vet at a desk (docs/C_VET_PLAN.md, section 7h, step 2).

   Only a wide screen loads this file (static/cvet/app.js, loadDesk). It
   adds what a keyboard, a pointer and a printer make possible. It holds
   no data and no rule of its own: it reads the app's state and calls
   the app's functions (window.CvetApp).

     - the keyboard: "/" to search, the arrows in a list, Ctrl+Enter to save
     - a list as a table: a client's horses, Invoices, Prices. Each one
       has filters over it, and the head of a column sorts by it
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

    function visible(node) { return !!node && node.offsetParent !== null; }

    // ── The keyboard ─────────────────────────────────────────────────────

    // The search box of what is on the screen now.
    function searchBox() {
        if (!$('picker').hidden) return $('picker-search');
        var ids = { clients: ['client-search'], client: ['search', 'client-search'], prices: ['price-filter'], bills: ['bill-filter'] }[state.view] || [];
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

    // ── A list as a table, with filters over it ──────────────────────────

    function compare(a, b) {
        if (a === b) return 0;
        // No value: at the end.
        if (a === '' || a == null) return 1;
        if (b === '' || b == null) return -1;
        return typeof a === 'number' ? a - b : String(a).localeCompare(String(b), undefined, { numeric: true });
    }

    // columns: { label, cls, sort(row) -> a value, cell(row) -> a node or text }.
    // view: { sort: the index of a column or null, dir: 1 or -1 }.
    // options: group(row) -> the name of its group, none: the words for "no
    // value", onRow(row), mark(row, tr).
    function drawTable(columns, rows, view, redraw, options) {
        rows = rows.map(function (row, index) { return [row, index]; });
        var column = columns[view.sort];
        var group = options.group;
        rows.sort(function (a, b) {
            return (group ? compare(group(a[0]), group(b[0])) : 0) ||
                (column && column.sort ? compare(column.sort(a[0]), column.sort(b[0])) * view.dir : 0) || a[1] - b[1];
        });
        var table = el('table', 'table card');
        var head = el('tr');
        columns.forEach(function (item, index) {
            var th = el('th', item.cls || '');
            th.scope = 'col';
            if (item.sort) {
                // First tap: A to Z. Second: Z to A. Third: the order of the list again.
                var sorter = app.button('sort', item.label, function () {
                    if (view.sort !== index) { view.sort = index; view.dir = 1; }
                    else if (view.dir === 1) view.dir = -1;
                    else { view.sort = null; view.dir = 1; }
                    redraw();
                });
                th.appendChild(sorter);
                if (view.sort === index) th.setAttribute('aria-sort', view.dir === 1 ? 'ascending' : 'descending');
            } else {
                th.textContent = item.label;
            }
            head.appendChild(th);
        });
        var top = el('thead');
        top.appendChild(head);
        table.appendChild(top);
        var body = el('tbody');
        var last = null;
        rows.forEach(function (pair) {
            var row = pair[0];
            if (group) {
                var name = group(row) || '';
                if (name !== last) {
                    last = name;
                    var band = el('tr', 'band');
                    var cell = el('td', '', name || options.none);
                    cell.colSpan = columns.length;
                    cell.appendChild(el('small', '', String(rows.filter(function (other) {
                        return (group(other[0]) || '') === name;
                    }).length)));
                    band.appendChild(cell);
                    body.appendChild(band);
                }
            }
            var tr = el('tr', 'prow');
            columns.forEach(function (item) {
                var td = el('td', item.cls || '');
                var value = item.cell(row);
                if (value && value.nodeType) td.appendChild(value);
                else td.textContent = value == null ? '' : value;
                tr.appendChild(td);
            });
            tr.addEventListener('click', function () { options.onRow(row); });
            if (options.mark) options.mark(row, tr);
            body.appendChild(tr);
        });
        table.appendChild(body);
        return table;
    }

    // The name of a row is its button: the row is in reach of the keyboard.
    function rowLink(text, onClick, className) {
        return app.button('rowlink' + (className || ''), text, function (event) {
            event.stopPropagation();
            onClick();
        });
    }

    // A box of the filters, made once: it keeps the cursor while the list changes.
    function control(tag, id, label, onChange) {
        var node = el(tag);
        node.id = id;
        node.setAttribute('aria-label', label);
        if (tag === 'input') {
            node.type = 'search';
            node.autocomplete = 'off';
            node.placeholder = label;
        }
        node.addEventListener(tag === 'select' ? 'change' : 'input', function () { onChange(node.value); });
        return node;
    }

    // The choices of a filter: "All", then each value with how many rows have it.
    function setChoices(select, all, rows, valueOf, none, view, key) {
        var counts = {};
        rows.forEach(function (row) {
            var value = valueOf(row) || '';
            counts[value] = (counts[value] || 0) + 1;
        });
        var values = Object.keys(counts).sort(compare);
        if (view[key] !== null && !(view[key] in counts)) view[key] = null;
        app.fill(select, [[null, all]].concat(values.map(function (value) {
            return [value, (value || none) + ' (' + counts[value] + ')'];
        })).map(function (choice, index) {
            var option = el('option', '', choice[1]);
            option.value = String(index);
            return option;
        }));
        select.selectedIndex = view[key] === null ? 0 : values.indexOf(view[key]) + 1;
        select.values = [null].concat(values);
        // One value: nothing to choose.
        select.hidden = values.length < 2;
    }

    function chosen(select) { return select.values[select.selectedIndex]; }

    function found(node, shown, all, one, many, extra, onClear) {
        var words = (shown === all ? '' : shown + ' of ') + all + ' ' + (all === 1 ? one : many) + (extra || '');
        app.fill(node, [document.createTextNode(words + (onClear ? ' · ' : ''))].concat(
            onClear ? [app.button('link calm', 'Clear filters', onClear)] : []));
    }

    function wordsIn(text, query) {
        text = text.toLowerCase();
        return query.toLowerCase().split(/\s+/).filter(Boolean).every(function (word) { return text.indexOf(word) !== -1; });
    }

    // ── The horses of a client ───────────────────────────────────────────

    var horseView = { sort: null, dir: 1, owner: null, place: null, due: false, group: '', client: null };
    try { horseView.group = localStorage.getItem('cvet-group') || ''; } catch (error) { /* no store: no memory */ }

    function renderHorses(list, seen) {
        var bar = $('horse-filters');
        var box = $('horse-table');
        if (!app.isWide()) {
            if (bar) bar.hidden = box.hidden = true;
            return;
        }
        var redraw = app.render;
        if (!bar) {
            bar = el('span');
            bar.id = 'horse-filters';
            bar.style.display = 'contents';
            bar.appendChild(control('select', 'horse-owner', 'Owner', function () {
                horseView.owner = chosen($('horse-owner'));
                redraw();
            }));
            bar.appendChild(control('select', 'horse-place', 'Location', function () {
                horseView.place = chosen($('horse-place'));
                redraw();
            }));
            var due = app.button('tile', 'Due', function () {
                horseView.due = !horseView.due;
                redraw();
            });
            due.id = 'horse-due-only';
            bar.appendChild(due);
            var group = control('select', 'horse-group', 'Group', function (value) {
                horseView.group = value;
                try { localStorage.setItem('cvet-group', value); } catch (error) { /* as above */ }
                redraw();
            });
            [['', 'No groups'], ['owner', 'Group by owner'], ['location', 'Group by location']].forEach(function (choice) {
                var option = el('option', '', choice[1]);
                option.value = choice[0];
                group.appendChild(option);
            });
            bar.appendChild(group);
            bar.appendChild(el('span', 'found'));
            $('select-toggle').parentNode.insertBefore(bar, $('select-toggle'));
            bar.parentNode.classList.add('filters');
            box = el('div');
            box.id = 'horse-table';
            $('horses').parentNode.insertBefore(box, $('horses').nextSibling);
        }
        bar.hidden = box.hidden = false;
        if (horseView.client !== state.client) {
            horseView.client = state.client;
            horseView.owner = horseView.place = null;
            horseView.due = false;
        }
        var day = app.today();
        var all = app.clientHorses(state.client);
        setChoices($('horse-owner'), 'All owners', all, function (horse) { return horse.owner; }, 'No owner', horseView, 'owner');
        setChoices($('horse-place'), 'All locations', all, function (horse) { return horse.location; }, 'No location', horseView, 'place');
        $('horse-group').value = horseView.group;
        var dueCount = all.filter(function (horse) { return core.horseDue(horse, day); }).length;
        if (!dueCount) horseView.due = false;
        var dueOnly = $('horse-due-only');
        dueOnly.hidden = !dueCount;
        dueOnly.textContent = 'Due (' + dueCount + ')';
        dueOnly.className = 'tile' + (horseView.due ? ' on' : '');
        dueOnly.setAttribute('aria-pressed', horseView.due ? 'true' : 'false');

        var rows = list.filter(function (horse) {
            return (horseView.owner === null || (horse.owner || '') === horseView.owner) &&
                (horseView.place === null || (horse.location || '') === horseView.place) &&
                (!horseView.due || core.horseDue(horse, day));
        });
        var filtered = horseView.owner !== null || horseView.place !== null || horseView.due || $('search').value;
        found(bar.querySelector('.found'), rows.length, all.length, 'horse', 'horses', '', filtered ? function () {
            horseView.owner = horseView.place = null;
            horseView.due = false;
            $('search').value = '';
            redraw();
        } : null);

        function open(horse) {
            if (state.selecting) app.toggleSelected(horse);
            else app.openHorse(horse);
        }
        var columns = [];
        if (state.selecting || Object.keys(seen).length) {
            columns.push({ label: '', cls: 'tick', cell: function (horse) {
                var picked = !!state.selected[horse.id];
                return state.selecting ? el('span', 'mark' + (picked ? ' picked' : ''), picked ? '✓' : '')
                    : seen[horse.id] ? el('span', 'mark done', '✓') : '';
            } });
        }
        columns.push({ label: 'Horse', sort: function (horse) { return horse.name; }, cell: function (horse) {
            var link = rowLink(horse.name, function () { open(horse); });
            if (!horse.unsent && !horse.refused) return link;
            var cell = el('span');
            cell.appendChild(link);
            cell.appendChild(el('small', '', horse.unsent ? ' not sent yet' : ' not saved'));
            return cell;
        } });
        if (all.some(function (horse) { return horse.about; })) {
            columns.push({ label: 'Description', cls: 'dim', sort: function (horse) { return horse.about; }, cell: function (horse) { return horse.about; } });
        }
        if (horseView.group !== 'owner') {
            columns.push({ label: 'Owner', sort: function (horse) { return horse.owner; }, cell: function (horse) { return horse.owner; } });
        }
        if (horseView.group !== 'location') {
            columns.push({ label: 'Location', sort: function (horse) { return horse.location; }, cell: function (horse) { return horse.location; } });
        }
        columns.push({ label: 'Due', sort: function (horse) {
            var due = core.horseDue(horse, day);
            return due ? due.date : '';
        }, cell: function (horse) {
            var due = core.horseDue(horse, day);
            return due ? el('span', 'due ' + due.status, app.dueText(due)) : '';
        } });
        if (horseView.sort !== null && horseView.sort >= columns.length) horseView.sort = null;
        app.fill(box, rows.length ? [drawTable(columns, rows, horseView, redraw, {
            group: horseView.group === 'owner' ? function (horse) { return horse.owner; }
                : horseView.group === 'location' ? function (horse) { return horse.location; } : null,
            none: horseView.group === 'owner' ? 'No owner' : 'No location',
            onRow: open,
            mark: function (horse, tr) {
                if (state.selecting) tr.setAttribute('aria-selected', state.selected[horse.id] ? 'true' : 'false');
            }
        })] : list.length ? [el('p', 'empty', 'No horse fits these choices.')] : []);
    }

    // ── Invoices as a table ──────────────────────────────────────────────

    var billView = { sort: null, dir: 1, to: null, query: '' };

    function renderBillTable(shown) {
        var box = $('bill-desk');
        if (!app.isWide()) {
            if (box) box.hidden = true;
            return;
        }
        function redraw() { renderBillTable(shown); }
        if (!box) {
            box = el('div');
            box.id = 'bill-desk';
            var bar = el('div', 'filters');
            bar.appendChild(control('input', 'bill-filter', 'Find an invoice', function (value) {
                billView.query = value;
                app.render();
            }));
            bar.appendChild(control('select', 'bill-to', 'Billed to', function () {
                billView.to = chosen($('bill-to'));
                app.render();
            }));
            bar.appendChild(el('span', 'found'));
            box.appendChild(bar);
            box.appendChild(el('div'));
            $('bills').parentNode.insertBefore(box, $('bills'));
        }
        box.hidden = app.bills().length === 0;
        setChoices($('bill-to'), 'All clients', shown, function (bill) { return bill.to; }, 'No name', billView, 'to');
        var rows = shown.filter(function (bill) {
            return (billView.to === null || (bill.to || '') === billView.to) && wordsIn(bill.number + ' ' + bill.to + ' ' +
                (bill.horses || []).map(function (horse) { return horse.name; }).join(' '), billView.query);
        });
        var total = rows.reduce(function (sum, bill) { return sum + (core.toPence(bill.total) || 0); }, 0);
        found(box.querySelector('.found'), rows.length, shown.length, 'invoice', 'invoices', ' · ' + core.money(total),
            billView.to !== null || billView.query ? function () {
                billView.to = null;
                billView.query = $('bill-filter').value = '';
                app.render();
            } : null);
        var day = app.today();
        app.fill(box.lastChild, rows.length ? [drawTable([
            { label: 'Invoice', sort: function (bill) { return bill.number; }, cell: function (bill) {
                return rowLink(bill.number, function () { app.openBill(bill); });
            } },
            { label: 'Date', sort: function (bill) { return bill.date; }, cell: function (bill) { return core.shortDate(bill.date, day); } },
            { label: 'Billed to', sort: function (bill) { return bill.to; }, cell: function (bill) { return bill.to; } },
            { label: 'Horses', cls: 'dim', cell: function (bill) {
                return (bill.horses || []).map(function (horse) { return horse.name; }).join(', ');
            } },
            { label: 'Emailed', cls: 'dim', sort: function (bill) { return bill.emailed_at; }, cell: function (bill) {
                return bill.emailed_at ? core.shortDate(bill.emailed_at.slice(0, 10), day) : '';
            } },
            { label: 'Status', sort: function (bill) { return bill.paid_on ? '9' + bill.paid_on : bill.date; }, cell: app.paidPill },
            { label: 'Amount', cls: 'num strong', sort: function (bill) { return core.toPence(bill.total) || 0; }, cell: function (bill) {
                return core.money(core.toPence(bill.total) || 0);
            } }
        ], rows, billView, redraw, { onRow: app.openBill })] : shown.length ? [el('p', 'empty', 'No invoice fits these choices.')] : []);
    }

    // ── Prices as a table ────────────────────────────────────────────────

    var priceView = { sort: null, dir: 1, category: null, query: '' };

    function renderPriceTable() {
        var bar = $('price-filters');
        if (bar) bar.hidden = !app.isWide();
        if (!app.isWide()) return;
        if (!bar) {
            bar = el('div', 'filters');
            bar.id = 'price-filters';
            var filter = control('input', 'price-filter', 'Find a price', function (value) {
                priceView.query = value;
                renderPriceTable();
            });
            filter.placeholder = 'Find a price   ( / )';
            bar.appendChild(filter);
            bar.appendChild(control('select', 'price-pick-category', 'Category', function () {
                priceView.category = chosen($('price-pick-category'));
                renderPriceTable();
            }));
            bar.appendChild(el('span', 'found'));
            $('prices').parentNode.insertBefore(bar, $('prices'));
        }
        function category(price) { return price.category || 'Other'; }
        setChoices($('price-pick-category'), 'All categories', state.prices, category, 'Other', priceView, 'category');
        var types = {};
        (state.meta.vaccinationTypes || []).forEach(function (type) { types[type.id] = type.name; });
        var rows = state.prices.filter(function (price) {
            return (priceView.category === null || category(price) === priceView.category) &&
                wordsIn(price.name + ' ' + category(price), priceView.query);
        }).sort(function (a, b) { return a.name.localeCompare(b.name); });
        found(bar.querySelector('.found'), rows.length, state.prices.length, 'price', 'prices', '',
            priceView.category !== null || priceView.query ? function () {
                priceView.category = null;
                priceView.query = $('price-filter').value = '';
                renderPriceTable();
            } : null);
        var grouped = priceView.sort === null && priceView.category === null;
        app.fill($('prices'), rows.length ? [drawTable([
            { label: 'Name', sort: function (price) { return price.name; }, cell: function (price) {
                var link = rowLink(price.name, function () { app.editPrice(price); }, price.isActive ? '' : ' off');
                if (price.isActive) return link;
                var cell = el('span');
                cell.appendChild(link);
                cell.appendChild(el('small', '', ' Hidden'));
                return cell;
            } }
        ].concat(grouped ? [] : [
            { label: 'Category', cls: 'dim', sort: category, cell: category }
        ], [
            { label: 'Records a vaccination', cls: 'dim', cell: function (price) { return types[price.vaccinationTypeId] || ''; } },
            { label: 'VAT', cls: 'num dim', sort: function (price) { return Number(price.vatRate); }, cell: function (price) { return Number(price.vatRate) + '%'; } },
            { label: 'Price', cls: 'num strong', sort: function (price) { return price.pence; }, cell: function (price) { return core.money(price.pence); } }
        ]), rows, priceView, renderPriceTable, {
            // Under their categories, with "Other" last, until the vet sorts the list.
            group: grouped ? function (price) { return price.category; } : null,
            none: 'Other',
            onRow: app.editPrice
        })] : [el('p', 'empty', state.prices.length ? 'No price fits these choices.' : 'No prices yet. Tap Add a price.')]);
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

    // In the head of the History card, beside its title.
    function addPrintToHistory(horse, box) {
        var old = $('print-record');
        if (old) old.parentNode.removeChild(old);
        if (!app.isWide() || !box) return;
        var client = app.clientById(horse.client_id);
        var print = app.button('link calm', 'Print this record', function () {
            printNode(box, printedBy() + ' · ' + horse.name + (client ? ', ' + client.name : ''));
        });
        print.id = 'print-record';
        $('history').previousElementSibling.appendChild(print);
    }

    // ── What the app tells this file ─────────────────────────────────────

    window.CvetDesk = function (what, a, b) {
        if (what === 'view') renderClientPage();
        else if (what === 'prices') renderPriceTable();
        else if (what === 'horses') renderHorses(a, b);
        else if (what === 'bills') renderBillTable(a);
        else if (what === 'visit') addPrintToVisit(a, b);
        else if (what === 'history') addPrintToHistory(a, b);
    };

    // The screen was drawn before this file arrived: add its part now.
    app.render();
})();
