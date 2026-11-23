// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const { nextIndex, placeFixed } = require('../more_menu.js');

test('nextIndex: Down from the button goes to the first item', () => {
    assert.equal(nextIndex(-1, 4, 'ArrowDown'), 0);
});

test('nextIndex: Up from the button goes to the last item', () => {
    assert.equal(nextIndex(-1, 4, 'ArrowUp'), 3);
});

test('nextIndex: Down and Up wrap round', () => {
    assert.equal(nextIndex(3, 4, 'ArrowDown'), 0);
    assert.equal(nextIndex(0, 4, 'ArrowUp'), 3);
    assert.equal(nextIndex(1, 4, 'ArrowDown'), 2);
});

test('nextIndex: Home and End jump to the ends', () => {
    assert.equal(nextIndex(2, 4, 'Home'), 0);
    assert.equal(nextIndex(1, 4, 'End'), 3);
});

test('nextIndex: an empty menu has nothing to focus', () => {
    assert.equal(nextIndex(-1, 0, 'ArrowDown'), -1);
});

test('nextIndex: other keys leave focus where it is', () => {
    assert.equal(nextIndex(2, 4, 'a'), 2);
});

// ── placeFixed: where a menu inside a clipping table goes ──

const button = (top, right) => ({ top, bottom: top + 32, right, left: right - 32 });

test('placeFixed: under the button, right edges lined up', () => {
    assert.deepEqual(placeFixed(button(100, 900), 200, 150, 1440, 900), { top: 138, left: 700 });
});

test('placeFixed: above the button when there is no room below', () => {
    assert.deepEqual(placeFixed(button(800, 900), 200, 150, 1440, 900), { top: 644, left: 700 });
});

test('placeFixed: stays below when there is no room above either', () => {
    assert.equal(placeFixed(button(60, 900), 200, 900, 1440, 900).top, 98);
});

test('placeFixed: never past the left or right edge of the window', () => {
    assert.equal(placeFixed(button(100, 120), 200, 150, 390, 844).left, 8);
    assert.equal(placeFixed(button(100, 400), 200, 150, 390, 844).left, 182);
});

test('placeFixed: a menu for a button at the start of a row opens rightward', () => {
    assert.equal(placeFixed(button(100, 120), 200, 150, 1440, 900, true).left, 88);
});

// ── The shared menu of a list (rowMenu) ──

const { rowUrl, rowTitle, menuData, rowMenuData } = require('../more_menu.js');

test('rowUrl: the row takes the place of the 0', () => {
    assert.equal(rowUrl('/horses/0/move/', '52'), '/horses/52/move/');
    assert.equal(rowUrl('/horses/0/quick-edit/', 7), '/horses/7/quick-edit/');
});

test('rowUrl: only the first 0 is a place', () => {
    assert.equal(rowUrl('/horses/0/photos/0/', '9'), '/horses/9/photos/0/');
});

test('rowTitle: the horse takes the place of {name}', () => {
    assert.equal(rowTitle('Move {name}', 'Ace'), 'Move Ace');
    assert.equal(rowTitle('Add photos for {name}', "D'Artagnan"), "Add photos for D'Artagnan");
});

test('a menu names its private fields, so Alpine keeps them on the menu', () => {
    // A field a component does not name goes on the outermost x-data of
    // the page. Two menus then share it, and one removes the other's
    // listener.
    for (const key of ['_onMove', '_host']) {
        assert.ok(key in menuData(), key);
    }
    for (const key of ['_onMove', '_host', '_trigger', '_onClick', '_onKey']) {
        assert.ok(key in rowMenuData(), key);
    }
});

function fakeItem(data) {
    const attrs = {};
    return { dataset: data, attrs, setAttribute: (k, v) => { attrs[k] = v; } };
}

function fakeButton(pk, name, pageHref) {
    const attrs = { 'data-row-menu': pk, 'data-name': name };
    return {
        attrs,
        getAttribute: (k) => attrs[k],
        setAttribute: (k, v) => { attrs[k] = v; },
        // 'tr, li' is the row (for its link); 'td, li' is the cell that is lifted.
        closest: (selector) => (selector === 'tr, li'
            ? { querySelector: () => ({ getAttribute: () => pageHref }) }
            : { classList: { add() {}, remove() {} } }),
        isConnected: true,
    };
}

function fakeMenu(items) {
    const menu = rowMenuData();
    const panelAttrs = {};
    menu.$refs = { panel: { setAttribute: (k, v) => { panelAttrs[k] = v; }, querySelectorAll: () => items } };
    menu.$nextTick = () => {};
    menu.panelAttrs = panelAttrs;
    return menu;
}

test('rowMenu: the panel is filled for the row whose button was used', () => {
    const move = fakeItem({ rowHref: '/horses/0/move/', rowGet: '/horses/0/move/', rowTitle: 'Move {name}' });
    const edit = fakeItem({ rowHref: '/horses/0/edit/', rowGet: '/horses/0/quick-edit/', rowTitle: 'Edit {name}' });
    const page = fakeItem({ rowPage: '' });
    const menu = fakeMenu([move, edit, page]);
    menu.fill(fakeButton('52', 'Ace', '/horses/52/?from=sort%3Downer'));
    assert.deepEqual(move.attrs, { href: '/horses/52/move/', 'hx-get': '/horses/52/move/', 'data-popup-title': 'Move Ace' });
    assert.deepEqual(edit.attrs, { href: '/horses/52/edit/', 'hx-get': '/horses/52/quick-edit/', 'data-popup-title': 'Edit Ace' });
    assert.deepEqual(page.attrs, { href: '/horses/52/?from=sort%3Downer' });
    assert.equal(menu.panelAttrs['aria-label'], 'More actions for Ace');
});

test('rowMenu: a second row closes the first and takes the panel', () => {
    const menu = fakeMenu([]);
    const first = fakeButton('1', 'Ace', '/horses/1/');
    const second = fakeButton('2', 'Bella', '/horses/2/');
    menu.openFor(first, false);
    assert.equal(menu.open, true);
    assert.equal(first.attrs['aria-expanded'], 'true');
    menu.openFor(second, false);
    assert.equal(menu.open, true);
    assert.equal(first.attrs['aria-expanded'], 'false');
    assert.equal(second.attrs['aria-expanded'], 'true');
    assert.equal(menu.trigger(), second);
});

test('rowMenu: the same button closes the menu; a key on it does not', () => {
    const menu = fakeMenu([]);
    const button = fakeButton('1', 'Ace', '/horses/1/');
    menu.openFor(button, false);
    menu.openFor(button, true);
    assert.equal(menu.open, true);
    menu.openFor(button, false);
    assert.equal(menu.open, false);
    assert.equal(button.attrs['aria-expanded'], 'false');
});
