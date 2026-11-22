/* Add and remove rows of a Django formset in the browser
 * (templates/horses/partials/_ownership_rows.html, the new arrival page's
 * other owners).
 *
 *   <div x-data="formsetRows('ownership_shares')">
 *     {{ formset.management_form }}
 *     <div x-ref="rows"> ...one [data-formset-row] per form... </div>
 *     <template x-ref="blank"> ...the empty form's row (__prefix__)... </template>
 *     <button type="button" @click="add()">Add owner</button>
 *   </div>
 *
 * add() copies the blank row with the next form number and adds one to
 * TOTAL_FORMS, so the server reads the new row. remove() takes a row that
 * is not saved yet off the page: Django reads a missing extra form as an
 * empty one and skips it, so the numbers may have a gap. `count` is the
 * number of rows on the page, for "show the share box when there is more
 * than one owner".
 */
(function () {
    'use strict';

    // The blank row's HTML with __prefix__ replaced by the form number.
    function rowHtml(blank, index) {
        return String(blank).replace(/__prefix__/g, String(index));
    }

    function register(Alpine) {
        Alpine.data('formsetRows', function (prefix) {
            return {
                count: 0,
                init: function () {
                    this.count = this.$refs.rows ? this.$refs.rows.querySelectorAll('[data-formset-row]').length : 0;
                },
                total: function () {
                    return document.getElementById('id_' + prefix + '-TOTAL_FORMS');
                },
                add: function () {
                    var total = this.total();
                    if (!total || !this.$refs.blank) return;
                    var index = parseInt(total.value, 10) || 0;
                    this.$refs.rows.insertAdjacentHTML('beforeend', rowHtml(this.$refs.blank.innerHTML, index));
                    total.value = String(index + 1);
                    this.count += 1;
                    var rows = this.$refs.rows;
                    this.$nextTick(function () {
                        var all = rows.querySelectorAll('[data-formset-row]');
                        var last = all[all.length - 1];
                        var first = last && last.querySelector('select, input:not([type=hidden])');
                        if (first) first.focus();
                    });
                },
                remove: function (el) {
                    var row = el.closest('[data-formset-row]');
                    if (!row) return;
                    row.remove();
                    this.count = Math.max(0, this.count - 1);
                }
            };
        });
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { rowHtml: rowHtml };
    }
    if (typeof document !== 'undefined') {
        document.addEventListener('alpine:init', function () { register(window.Alpine); });
    }
})();
