#!/usr/bin/env node
/*
 * npm audit, with a short allowlist of advisories that have no fix yet.
 *
 * `npm audit --audit-level=high` has no way to accept one advisory, so a
 * single unfixable alert keeps the security check red and hides any new
 * one behind it. This reads `npm audit --json` from stdin and fails only
 * on a high or critical advisory that is not listed below.
 *
 * Usage: npm audit --json | node tools/npm_audit_check.js
 *
 * Each entry says why it is accepted. Remove it once a fixed version
 * exists (npm audit then stops reporting it, and this script says so).
 */

'use strict';

const ALLOWED = {
    // Empty: nothing is accepted today. An entry is 'GHSA-…': 'why', and
    // goes once npm audit stops reporting it (this script says when).
};

const BLOCKING = new Set(['high', 'critical']);

function advisories(report) {
    const found = new Map();
    for (const vuln of Object.values(report.vulnerabilities || {})) {
        for (const via of vuln.via || []) {
            // A string `via` is a dependency path; its advisory is listed
            // on the package it names, so only objects are advisories.
            if (typeof via !== 'object' || !via.url) continue;
            const id = via.url.split('/').pop();
            found.set(id, { id, name: via.name, severity: via.severity, title: via.title });
        }
    }
    return [...found.values()];
}

function check(report, allowed = ALLOWED) {
    const all = advisories(report);
    const blocking = all.filter((a) => BLOCKING.has(a.severity) && !(a.id in allowed));
    const accepted = all.filter((a) => a.id in allowed);
    const stale = Object.keys(allowed).filter((id) => !all.some((a) => a.id === id));
    return { blocking, accepted, stale };
}

function main() {
    let input = '';
    process.stdin.on('data', (chunk) => { input += chunk; });
    process.stdin.on('end', () => {
        let report;
        try {
            report = JSON.parse(input);
        } catch (err) {
            console.error('Could not read npm audit JSON:', err.message);
            process.exit(2);
        }
        if (report.error) {
            console.error('npm audit failed:', report.error.summary || report.error);
            process.exit(2);
        }
        const { blocking, accepted, stale } = check(report);
        for (const a of accepted) {
            console.log(`Accepted ${a.id} (${a.severity}, ${a.name}): ${ALLOWED[a.id]}`);
        }
        for (const id of stale) {
            console.log(`No longer reported: ${id}. Remove it from ALLOWED in tools/npm_audit_check.js.`);
        }
        if (blocking.length) {
            console.error('High or critical advisories:');
            for (const a of blocking) {
                console.error(`  ${a.id} (${a.severity}, ${a.name}): ${a.title}`);
            }
            console.error('Run `npm audit` for details and fix paths.');
            process.exit(1);
        }
        console.log('npm audit: no high or critical advisories outside the allowlist.');
    });
}

if (require.main === module) {
    main();
}

module.exports = { ALLOWED, advisories, check };
