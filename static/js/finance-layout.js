// Phone layout helpers for Finance pages. Everything still works without this script:
// tables scroll sideways and every collapsible panel starts open.
document.addEventListener('DOMContentLoaded', function () {
  var phone = window.matchMedia('(max-width: 650px)').matches;

  // Label each cell with its column heading so a table can stack into cards on phones.
  document.querySelectorAll('.finance .ledger-table').forEach(function (table) {
    var heads = [];
    table.querySelectorAll('thead tr:last-child th').forEach(function (th) {
      for (var i = 0; i < (th.colSpan || 1); i++) heads.push(th.textContent.trim());
    });
    if (!heads.length) return;
    table.querySelectorAll('tbody tr, tfoot tr').forEach(function (row) {
      var column = 0;
      Array.prototype.forEach.call(row.children, function (cell) {
        if (cell.tagName === 'TD' && heads[column]) cell.setAttribute('data-label', heads[column]);
        column += cell.colSpan || 1;
      });
    });
    table.classList.add('is-stacked');
  });

  if (!phone) return;
  // On phones, fold panels away unless they hold active filters or are the linked section.
  var target = window.location.hash.slice(1);
  document.querySelectorAll('details.ledger-fold, details.budget-filter-fold').forEach(function (panel) {
    if ((!target || panel.id !== target) && !panel.hasAttribute('data-active')) panel.open = false;
  });
  window.addEventListener('hashchange', function () {
    var panel = document.getElementById(window.location.hash.slice(1));
    if (panel && panel.tagName === 'DETAILS') panel.open = true;
  });
});
