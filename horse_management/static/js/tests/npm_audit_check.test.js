// Run with: node --test static/js/tests   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const { check } = require('../../../tools/npm_audit_check.js');

const ALLOW_BRACES = { 'GHSA-vfj7-8cjw-p6xm': 'example entry' };

function report(...advisories) {
    const vulnerabilities = {};
    for (const [name, id, severity] of advisories) {
        vulnerabilities[name] = {
            severity,
            via: [{ name, severity, title: name, url: `https://github.com/advisories/${id}` }],
        };
    }
    // A package flagged only through a dependency path carries no advisory.
    vulnerabilities.tailwindcss = { severity: 'high', via: ['braces'] };
    return { vulnerabilities };
}

test('an allowlisted advisory does not block', () => {
    const result = check(report(['braces', 'GHSA-vfj7-8cjw-p6xm', 'high']), ALLOW_BRACES);
    assert.equal(result.blocking.length, 0);
    assert.equal(result.accepted.length, 1);
    assert.deepEqual(result.stale, []);
});

test('a high advisory outside the allowlist blocks', () => {
    const result = check(report(
        ['braces', 'GHSA-vfj7-8cjw-p6xm', 'high'],
        ['source-map-js', 'GHSA-68fv-2mgg-jv7q', 'high'],
    ), ALLOW_BRACES);
    assert.deepEqual(result.blocking.map((a) => a.id), ['GHSA-68fv-2mgg-jv7q']);
});

test('moderate advisories do not block', () => {
    const result = check(report(['postcss-selector-parser', 'GHSA-rj75-hqrm-r3gf', 'moderate']));
    assert.equal(result.blocking.length, 0);
});

test('the default allowlist is empty, so any high advisory blocks', () => {
    const result = check(report(['braces', 'GHSA-vfj7-8cjw-p6xm', 'high']));
    assert.equal(result.blocking.length, 1);
});

test('an allowlisted advisory that is gone is reported as stale', () => {
    const result = check({ vulnerabilities: {} }, ALLOW_BRACES);
    assert.deepEqual(result.stale, ['GHSA-vfj7-8cjw-p6xm']);
});
