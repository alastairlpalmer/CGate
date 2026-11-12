// Farrier page, "Work done" tiles (templates/farrier/_horse_row.html).
// Tapping a tile with a price puts that price in the Cost box. Tapping a
// tile with no price clears a price that a tile put there, but keeps a
// price the farrier typed. One listener on the document, so it also works
// for rows that HTMX swaps in.
(function () {
  document.addEventListener('change', function (event) {
    var tile = event.target;
    if (!tile.matches || !tile.matches('[data-work-form] input[name="choice"]')) return;
    var form = tile.closest('[data-work-form]');
    var cost = form && form.querySelector('input[name="price"]');
    if (!cost) return;
    if (tile.dataset.price) {
      cost.value = tile.dataset.price;
      return;
    }
    var tilePrices = Array.prototype.map.call(
      form.querySelectorAll('input[name="choice"][data-price]'),
      function (other) { return other.dataset.price; }
    );
    if (tilePrices.indexOf(cost.value.trim()) !== -1) {
      cost.value = '';
      cost.focus();
    }
  });
})();
