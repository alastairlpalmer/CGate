// Pedigree diagram highlight (templates/pedigree/_tree.html, plan §8.3).
//
// Point at a box, or move the keyboard focus to it: the line from that box
// back to the horse is highlighted, and every copy of the same record (a
// horse that appears twice) with it. The diagram itself is drawn by the
// server; without this script it still shows everything.
(function () {
    'use strict';

    // 'SDS' -> ['', 'S', 'SD', 'SDS']: the boxes on the way back to the horse.
    function prefixes(code) {
        var out = [];
        for (var i = 0; i <= code.length; i++) {
            out.push(code.slice(0, i));
        }
        return out;
    }

    function clear(tree) {
        tree.querySelectorAll('.is-hot').forEach(function (el) { el.classList.remove('is-hot'); });
    }

    function trace(node) {
        var tree = node.closest('.ped-tree');
        if (!tree) { return; }
        clear(tree);
        var code = node.getAttribute('data-code') || '';
        prefixes(code).forEach(function (part, i) {
            var box = tree.querySelector('.ped-node[data-code="' + part + '"]');
            if (box) { box.classList.add('is-hot'); }
            if (i > 0) {
                var line = tree.querySelector('path[data-code="' + part + '"]');
                if (line) { line.classList.add('is-hot'); }
            }
        });
        var id = node.getAttribute('data-id');
        if (id) {
            tree.querySelectorAll('.ped-node[data-id="' + id + '"]').forEach(function (el) {
                el.classList.add('is-hot');
            });
        }
    }

    function nodeFrom(target) {
        return target && target.closest ? target.closest('.ped-node[data-code]') : null;
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { prefixes: prefixes };
    }
    if (typeof document === 'undefined') { return; }

    document.addEventListener('pointerover', function (e) {
        var node = nodeFrom(e.target);
        if (node) { trace(node); }
    });
    document.addEventListener('pointerout', function (e) {
        var tree = e.target && e.target.closest ? e.target.closest('.ped-tree') : null;
        if (tree && !tree.contains(e.relatedTarget)) { clear(tree); }
    });
    document.addEventListener('focusin', function (e) {
        var node = nodeFrom(e.target);
        if (node) { trace(node); }
    });
    document.addEventListener('focusout', function (e) {
        var tree = e.target && e.target.closest ? e.target.closest('.ped-tree') : null;
        if (tree && !tree.contains(e.relatedTarget)) { clear(tree); }
    });
})();
