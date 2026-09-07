/* The Needs action list on the dashboard (templates/partials/dashboard/attention.html).
 *
 * The rows are server-rendered, most urgent first. `attentionList` shows
 * the first `cap` rows that pass the page's filter chip (the `filter`
 * state on the dashboard root) and folds the rest behind "Show N more",
 * so the card stays a short checklist and Next 14 days fits under it.
 * A row asks `shown($el)`; the answer changes with the chip and the
 * button, nothing is fetched and nothing moves.
 */
(function () {
    'use strict';

    document.addEventListener('alpine:init', function () {
        Alpine.data('attentionList', function (cap) {
            return {
                cap: cap || 4,
                expanded: false,
                rows: [],

                init: function () {
                    this.rows = Array.prototype.map.call(this.$el.querySelectorAll('.attn-row'), function (li) {
                        return { id: li.id, tags: (li.dataset.filter || '').split(' ') };
                    });
                },

                // The rows that pass the chip, in page order. `filter` is
                // the dashboard root's state, reached through Alpine's scope.
                matching: function () {
                    var filter = this.filter || 'all';
                    return this.rows.filter(function (row) {
                        return filter === 'all' || row.tags.indexOf(filter) !== -1;
                    });
                },

                shown: function (el) {
                    var rows = this.matching();
                    for (var i = 0; i < rows.length; i++) {
                        if (rows[i].id === el.id) return this.expanded || i < this.cap;
                    }
                    return false;
                },

                hidden: function () {
                    return Math.max(0, this.matching().length - this.cap);
                }
            };
        });
    });
})();
