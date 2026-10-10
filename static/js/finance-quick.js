// Inbox quick categorize: show whose the "Default person" is for the picked category.
// The server applies the same default when the person is left on Default.
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('[data-quick-category]').forEach(function (category) {
    var row = category.closest('tr');
    var person = row && row.querySelector('[data-quick-person]');
    if (!person) return;
    var label = person.options[0];
    var update = function () {
      var picked = category.options[category.selectedIndex];
      var who = picked && picked.dataset.person;
      label.textContent = who ? 'Default (' + who.charAt(0).toUpperCase() + who.slice(1) + ')' : 'Default person';
    };
    category.addEventListener('change', update);
    update();
  });
});
