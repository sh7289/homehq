(function () {
  "use strict";

  // Progressive enhancement for Cook mode on the recipe detail page
  // (recipe_detail.html). Without this script the page still renders and
  // prints exactly as it does today -- Cook mode is purely a `.is-cooking`
  // class toggled on #recipe-root, which CSS uses to reveal the
  // ingredient/step checkboxes, enlarge the method list, and make the
  // servings control (.scale-bar) sticky.
  //
  // The checkmarks themselves are native <input type="checkbox"> elements
  // already in the markup (see recipe_detail.html) -- checking one off is
  // handled entirely by the browser and CSS (`.cook-check:checked + label`),
  // never by this script. That state is per-tab and resets on reload: it is
  // never sent to the server, never persisted, and never touches inventory.

  var root = document.getElementById("recipe-root");
  var toggle = document.getElementById("cook-mode-toggle");
  if (!root || !toggle) return;

  toggle.addEventListener("click", function () {
    var active = root.classList.toggle("is-cooking");
    toggle.setAttribute("aria-pressed", String(active));
    toggle.textContent = active ? "Exit cook mode" : "Cook mode";
  });
})();
