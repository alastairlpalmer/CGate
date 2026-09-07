// Run with: node --test static/js/tests   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const { spreadBadges } = require('../map_layout.js');

const SIZE = 44, GAP = 4, MIN = SIZE + GAP;

function pairwiseMin(points) {
    let min = Infinity;
    for (let i = 0; i < points.length; i++) {
        for (let j = i + 1; j < points.length; j++) {
            min = Math.min(min, Math.hypot(points[i].x - points[j].x, points[i].y - points[j].y));
        }
    }
    return min;
}

test('spreadBadges: badges that do not overlap stay where they are', () => {
    const items = [{ x: 50, y: 50, count: 3 }, { x: 150, y: 50, count: 0 }, { x: 100, y: 150, count: 7 }];
    const out = spreadBadges(items, { size: SIZE, gap: GAP, width: 300, height: 300 });
    assert.deepEqual(out, items.map(({ x, y }) => ({ x, y })));
});

test('spreadBadges: a cluster is pulled apart until no two badges overlap', () => {
    // Nine anchors within a 40 px box — the phone card before this change.
    const items = [];
    for (let i = 0; i < 9; i++) items.push({ x: 160 + (i % 3) * 20, y: 160 + Math.floor(i / 3) * 20, count: i });
    const out = spreadBadges(items, { size: SIZE, gap: GAP, width: 340, height: 340 });
    assert.equal(out.length, 9);
    assert.ok(pairwiseMin(out) >= MIN - 0.5, `closest pair ${pairwiseMin(out)}`);
    out.forEach((p) => {
        assert.ok(p.x >= SIZE / 2 && p.x <= 340 - SIZE / 2, `x ${p.x} inside`);
        assert.ok(p.y >= SIZE / 2 && p.y <= 340 - SIZE / 2, `y ${p.y} inside`);
    });
});

test('spreadBadges: two badges on the same anchor part vertically', () => {
    const out = spreadBadges([{ x: 100, y: 100, count: 2 }, { x: 100, y: 100, count: 2 }], { size: SIZE, gap: GAP });
    assert.equal(out[0].x, 100);
    assert.equal(out[1].x, 100);
    assert.ok(Math.abs(out[1].y - out[0].y) >= MIN - 1e-9);
    assert.ok(out[0].y < out[1].y);
});

test('spreadBadges: the highlighted badge does not move; the lighter badge moves more', () => {
    const out = spreadBadges(
        [{ x: 100, y: 100, count: 14, fixed: true }, { x: 110, y: 100, count: 0 }],
        { size: SIZE, gap: GAP, width: 400, height: 400 },
    );
    assert.deepEqual(out[0], { x: 100, y: 100 });
    assert.ok(out[1].x >= 100 + MIN - 1e-9, `moved to ${out[1].x}`);

    const two = spreadBadges([{ x: 100, y: 100, count: 12 }, { x: 110, y: 100, count: 0 }], { size: SIZE, gap: GAP });
    const heavyMoved = Math.abs(two[0].x - 100), lightMoved = Math.abs(two[1].x - 110);
    assert.ok(lightMoved > heavyMoved, `light ${lightMoved} vs heavy ${heavyMoved}`);
    assert.ok(Math.abs(two[1].x - two[0].x) >= MIN - 1e-9);
});

test('spreadBadges: badges are kept inside the container', () => {
    const out = spreadBadges([{ x: -30, y: 5, count: 1 }, { x: 500, y: 390, count: 1 }], { size: SIZE, width: 300, height: 200 });
    assert.deepEqual(out, [{ x: 22, y: 22 }, { x: 278, y: 178 }]);
});

test('spreadBadges: empty input, and the result is the same every time', () => {
    assert.deepEqual(spreadBadges([], { size: SIZE }), []);
    const items = [{ x: 10, y: 10, count: 1 }, { x: 20, y: 12, count: 5 }, { x: 15, y: 30, count: 0 }, { x: 40, y: 40, count: 2 }];
    const a = spreadBadges(items, { size: SIZE, gap: GAP, width: 200, height: 200 });
    const b = spreadBadges(items, { size: SIZE, gap: GAP, width: 200, height: 200 });
    assert.deepEqual(a, b);
    assert.ok(pairwiseMin(a) >= MIN - 0.5);
});

test('spreadBadges: small fixed obstacles (a name label) push badges away without moving', () => {
    // The highlighted badge at (100, 100) with its name below it, as a row of
    // fixed 12 px obstacles at y = 134; a neighbour anchored on the name.
    const items = [
        { x: 100, y: 100, count: 14, fixed: true },
        { x: 110, y: 140, count: 0 },
        { x: 90, y: 134, count: 0, fixed: true, r: 12 },
        { x: 110, y: 134, count: 0, fixed: true, r: 12 },
    ];
    const out = spreadBadges(items, { size: SIZE, gap: GAP, width: 400, height: 400 });
    assert.deepEqual(out[0], { x: 100, y: 100 });
    assert.deepEqual(out[2], { x: 90, y: 134 });
    assert.deepEqual(out[3], { x: 110, y: 134 });
    const moved = out[1];
    assert.ok(Math.hypot(moved.x - 100, moved.y - 100) >= MIN - 1e-6, 'clear of the badge');
    assert.ok(Math.hypot(moved.x - 90, moved.y - 134) >= 22 + 12 + GAP - 1e-6, 'clear of the label');
    assert.ok(Math.hypot(moved.x - 110, moved.y - 134) >= 22 + 12 + GAP - 1e-6, 'clear of the label');
});
