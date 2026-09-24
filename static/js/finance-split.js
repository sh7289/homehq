// Shows how much of a transaction is still unassigned while a split is edited.
// The server re-checks the exact total; this is only a typing aid.
document.addEventListener('DOMContentLoaded', function () {
  var form = document.querySelector('.budget-classify');
  if (!form) return;
  var output = form.querySelector('[data-remaining]');
  var kind = form.querySelector('[data-kind]');
  var lines = form.querySelector('[data-lines]');
  var cents = function (text) {
    var clean = String(text || '').replace(/[$,\s]/g, '');
    var negative = /^\(.*\)$/.test(clean);
    clean = clean.replace(/[()]/g, '');
    if (!/^[+-]?(\d+(\.\d{0,2})?|\.\d{1,2})$/.test(clean)) return null;
    var value = Math.round(parseFloat(clean) * 100);
    return negative ? -value : value;
  };
  var total = cents(form.dataset.total);
  var update = function () {
    var movement = kind.value === 'transfer' || kind.value === 'card_payment';
    lines.hidden = movement;
    if (movement || total === null) { output.textContent = ''; return; }
    var sum = 0;
    form.querySelectorAll('[data-amount]').forEach(function (input) {
      var value = cents(input.value);
      if (value !== null) sum += value;
    });
    var left = total - sum;
    output.textContent = left === 0 ? 'Split adds up to the total.'
      : 'Still to assign: ' + (left / 100).toFixed(2);
  };
  form.addEventListener('input', update);
  form.addEventListener('change', update);
  update();
});
