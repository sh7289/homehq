(function () {
  "use strict";

  // Progressive enhancement for the recipe ingredient/step row editors (see
  // recipe_ingredients.html, recipe_form.html and recipe_steps.html): without
  // this script, the server-rendered rows still submit as ordinary form
  // fields (editing a name/quantity/unit or a fresh/staple flag works fine),
  // but Add/Remove/Move row and the ingredient/step picker's live behavior
  // need JS to add and remove DOM, so this is what wires that up.
  //
  // Both editors keep a small in-memory model and always re-render their row
  // list from it on any structural change (add/remove/move/toggle), rather
  // than patching the DOM in place -- with the handful of rows a recipe
  // actually has, a full rebuild is simpler to get right than incremental
  // DOM surgery, and it's the only way the step picker's "only offer earlier
  // steps" choices can be recomputed correctly after a reorder.
  //
  // Each editor also keeps its row model and its "Advanced" raw-text
  // textarea in sync in both directions (see `_select_ingredients` /
  // `_select_steps` in app.py, which decide server-side which one to trust
  // based on the `*_source` hidden field this script flips) -- so switching
  // between the two views never drops whatever was last typed.

  function el(tag, className) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    return node;
  }

  function hiddenInput(name, value) {
    var input = el("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    return input;
  }

  function textField(labelText, id, name, value, opts) {
    opts = opts || {};
    var wrap = el("div", "field");
    var label = el("label");
    label.setAttribute("for", id);
    label.textContent = labelText;
    var input = el("input");
    input.type = "text";
    input.id = id;
    input.name = name;
    input.value = value || "";
    if (opts.required) input.required = true;
    if (opts.inputmode) input.setAttribute("inputmode", opts.inputmode);
    if (opts.placeholder) input.placeholder = opts.placeholder;
    wrap.appendChild(label);
    wrap.appendChild(input);
    return { wrap: wrap, input: input };
  }

  function checkLabel(className, labelText, name, value, checked) {
    var wrap = el("label", className);
    var input = el("input");
    input.type = "checkbox";
    input.name = name;
    input.value = value;
    input.checked = !!checked;
    wrap.appendChild(input);
    wrap.appendChild(document.createTextNode(" " + labelText));
    return { wrap: wrap, input: input };
  }

  function rowActionButtons(canMoveUp, canMoveDown, handlers) {
    var wrap = el("div", "editor-row__actions");

    var up = el("button", "btn btn--sm btn--ghost");
    up.type = "button";
    up.textContent = "Move up";
    up.disabled = !canMoveUp;
    up.addEventListener("click", handlers.up);
    wrap.appendChild(up);

    var down = el("button", "btn btn--sm btn--ghost");
    down.type = "button";
    down.textContent = "Move down";
    down.disabled = !canMoveDown;
    down.addEventListener("click", handlers.down);
    wrap.appendChild(down);

    var remove = el("button", "btn btn--sm btn--ghost");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.addEventListener("click", handlers.remove);
    wrap.appendChild(remove);

    return wrap;
  }

  function moveItem(array, index, delta) {
    var target = index + delta;
    if (target < 0 || target >= array.length) return false;
    var item = array.splice(index, 1)[0];
    array.splice(target, 0, item);
    return true;
  }

  // Writes to `field.value` only when it isn't the element the user is
  // currently typing into -- overwriting a focused field fights its cursor
  // and can drop the very keystroke that triggered the sync.
  function setIfNotFocused(field, value) {
    if (document.activeElement !== field) field.value = value;
  }

  // ---------------------------------------------------------------------
  // Ingredients
  // ---------------------------------------------------------------------

  function parseIngredientLines(text) {
    var rows = [];
    (text || "").split("\n").forEach(function (line) {
      if (!line.trim()) return;
      var parts = line.split("|").map(function (p) { return p.trim(); });
      var flags = (parts[3] || "").toLowerCase();
      rows.push({
        name: parts[0] || "",
        quantity: parts[1] || "",
        unit: parts[2] || "",
        fresh: flags.indexOf("fresh") !== -1,
        staple: flags.indexOf("staple") !== -1,
      });
    });
    return rows;
  }

  function ingredientsToLines(rows) {
    return rows
      .map(function (row) {
        var flags = [];
        if (row.fresh) flags.push("fresh");
        if (row.staple) flags.push("staple");
        var parts = [row.name || "", row.quantity || "", row.unit || "", flags.join(",")];
        while (parts.length && !parts[parts.length - 1]) parts.pop();
        return parts.join(" | ");
      })
      .join("\n");
  }

  function initIngredientEditor(root) {
    var list = root.querySelector(".editor-rows__list");
    var addBtn = root.querySelector(".js-row-add");
    var sourceField = document.getElementById(root.dataset.sourceField);
    var rawField = document.getElementById(root.dataset.rawField);

    var rows = Array.prototype.map.call(list.querySelectorAll("[data-row]"), function (rowEl) {
      return {
        name: rowEl.querySelector('input[name="ingredient_name"]').value,
        quantity: rowEl.querySelector('input[name="ingredient_quantity"]').value,
        unit: rowEl.querySelector('input[name="ingredient_unit"]').value,
        fresh: rowEl.querySelector('input[name="ingredient_fresh"]').checked,
        staple: rowEl.querySelector('input[name="ingredient_staple"]').checked,
      };
    });

    function markRowsAuthoritative() {
      sourceField.value = "rows";
    }

    function syncRaw() {
      setIfNotFocused(rawField, ingredientsToLines(rows));
    }

    function renderRows() {
      list.textContent = "";
      rows.forEach(function (row, index) {
        list.appendChild(buildRow(row, index));
      });
    }

    function render() {
      renderRows();
      syncRaw();
    }

    function buildRow(row, index) {
      var rowEl = el("div", "editor-row");
      rowEl.dataset.row = "";
      rowEl.appendChild(hiddenInput("ingredient_row", String(index)));

      var formRow = el("div", "form-row");

      var name = textField(
        "Name", "ingredient-name-" + index, "ingredient_name", row.name, { required: true }
      );
      name.input.addEventListener("input", function () {
        row.name = name.input.value;
        markRowsAuthoritative();
        syncRaw();
      });
      formRow.appendChild(name.wrap);

      var quantity = textField(
        "Amount", "ingredient-quantity-" + index, "ingredient_quantity", row.quantity,
        { inputmode: "decimal" }
      );
      quantity.input.addEventListener("input", function () {
        row.quantity = quantity.input.value;
        markRowsAuthoritative();
        syncRaw();
      });
      formRow.appendChild(quantity.wrap);

      var unit = textField("Unit", "ingredient-unit-" + index, "ingredient_unit", row.unit);
      unit.input.addEventListener("input", function () {
        row.unit = unit.input.value;
        markRowsAuthoritative();
        syncRaw();
      });
      formRow.appendChild(unit.wrap);

      rowEl.appendChild(formRow);

      var flags = el("div", "editor-row__flags");
      var fresh = checkLabel("check", "Fresh", "ingredient_fresh", String(index), row.fresh);
      fresh.input.addEventListener("change", function () {
        row.fresh = fresh.input.checked;
        markRowsAuthoritative();
        render();
      });
      flags.appendChild(fresh.wrap);

      var staple = checkLabel("check", "Staple", "ingredient_staple", String(index), row.staple);
      staple.input.addEventListener("change", function () {
        row.staple = staple.input.checked;
        markRowsAuthoritative();
        render();
      });
      flags.appendChild(staple.wrap);

      rowEl.appendChild(flags);

      rowEl.appendChild(
        rowActionButtons(index > 0, index < rows.length - 1, {
          up: function () {
            markRowsAuthoritative();
            moveItem(rows, index, -1);
            render();
          },
          down: function () {
            markRowsAuthoritative();
            moveItem(rows, index, 1);
            render();
          },
          remove: function () {
            markRowsAuthoritative();
            rows.splice(index, 1);
            render();
          },
        })
      );

      return rowEl;
    }

    addBtn.addEventListener("click", function () {
      markRowsAuthoritative();
      rows.push({ name: "", quantity: "", unit: "", fresh: false, staple: false });
      render();
    });

    rawField.addEventListener("input", function () {
      sourceField.value = "advanced";
      rows = parseIngredientLines(rawField.value);
      renderRows();
    });

    render();
  }

  // ---------------------------------------------------------------------
  // Steps
  // ---------------------------------------------------------------------

  function parseStepLines(text) {
    var parsed = [];
    (text || "").split("\n").forEach(function (line, index) {
      if (!line.trim()) return;
      var parts = line.split("|").map(function (p) { return p.trim(); });
      parsed.push({
        id: parts[0] || "s" + (index + 1),
        action: parts[1] || "",
        inputs: parts[2]
          ? parts[2].split(",").map(function (s) { return s.trim(); }).filter(Boolean)
          : [],
      });
    });
    return parsed;
  }

  // Drops any "step:<uid>" reference that isn't one of the rows strictly
  // before it -- this is the "auto-fix" chosen for a move (or a raw-text
  // edit) that would otherwise strand a forward/self/dangling reference:
  // the row UI can only ever represent a valid dependency, so anything that
  // stops being valid silently stops being offered, rather than blocking
  // the move outright. The server enforces the same rule independently
  // (recipe_loader.validate_step_order) as the actual save-time guarantee.
  function pruneInvalidStepRefs(steps) {
    steps.forEach(function (row, index) {
      var earlierUids = steps.slice(0, index).map(function (s) { return s.uid; });
      Array.from(row.inputs).forEach(function (value) {
        if (value.indexOf("step:") === 0 && earlierUids.indexOf(value.slice(5)) === -1) {
          row.inputs.delete(value);
        }
      });
    });
  }

  function stepDisplayId(steps, row) {
    return "s" + (steps.indexOf(row) + 1);
  }

  function stepsToLines(steps) {
    return steps
      .map(function (row) {
        var inputs = Array.from(row.inputs).map(function (value) {
          if (value.indexOf("ingredient:") === 0) return value.slice("ingredient:".length);
          var target = steps.filter(function (s) { return s.uid === value.slice(5); })[0];
          return target ? stepDisplayId(steps, target) : value.slice(5);
        });
        return [stepDisplayId(steps, row), row.action, inputs.join(", ")].join(" | ");
      })
      .join("\n");
  }

  function optionsForRow(steps, index, ingredientNames) {
    var row = steps[index];
    var remaining = new Set(row.inputs);

    var ingredientOptions = ingredientNames.map(function (name) {
      var value = "ingredient:" + name;
      var checked = remaining.has(value);
      if (checked) remaining.delete(value);
      return { value: value, label: name, checked: checked };
    });

    var stepOptions = steps.slice(0, index).map(function (earlier, earlierIndex) {
      var value = "step:" + earlier.uid;
      var checked = remaining.has(value);
      if (checked) remaining.delete(value);
      return {
        value: value,
        label: (earlierIndex + 1) + ". " + (earlier.action || "(untitled step)"),
        checked: checked,
      };
    });

    var orphans = Array.from(remaining).map(function (value) {
      if (value.indexOf("ingredient:") === 0) {
        return { value: value, label: '"' + value.slice(11) + '" (not recognized)' };
      }
      var uid = value.slice(5);
      var target = steps.filter(function (s) { return s.uid === uid; })[0];
      var label = target
        ? 'step "' + (target.action || "untitled") + '" (out of order)'
        : "an unknown step reference";
      return { value: value, label: label };
    });

    return { ingredientOptions: ingredientOptions, stepOptions: stepOptions, orphans: orphans };
  }

  function initStepEditor(root) {
    var list = root.querySelector(".editor-rows__list");
    var addBtn = root.querySelector(".js-row-add");
    var sourceField = document.getElementById(root.dataset.sourceField);
    var rawField = document.getElementById(root.dataset.rawField);
    var ingredientNames = JSON.parse(
      document.getElementById(root.dataset.ingredientsSource).textContent
    );

    var steps = Array.prototype.map.call(list.querySelectorAll("[data-row]"), function (rowEl) {
      var uid = rowEl.querySelector('input[name="step_row"]').value;
      var action = rowEl.querySelector('input[name="step_action"]').value;
      var checked = rowEl.querySelectorAll(
        'input[type="checkbox"][name="step_input__' + uid + '"]:checked'
      );
      var inputs = new Set(Array.prototype.map.call(checked, function (c) { return c.value; }));
      return { uid: uid, action: action, inputs: inputs };
    });

    var nextUid = steps.length;

    function markRowsAuthoritative() {
      sourceField.value = "rows";
    }

    function syncRaw() {
      setIfNotFocused(rawField, stepsToLines(steps));
    }

    function renderRows() {
      list.textContent = "";
      steps.forEach(function (row, index) {
        list.appendChild(buildRow(index));
      });
    }

    function render() {
      renderRows();
      syncRaw();
    }

    function buildRow(index) {
      var row = steps[index];
      var rowEl = el("div", "editor-row");
      rowEl.dataset.row = "";
      rowEl.appendChild(hiddenInput("step_row", row.uid));

      var number = el("p", "editor-row__number");
      number.textContent = "Step " + (index + 1);
      rowEl.appendChild(number);

      var action = textField(
        "What happens", "step-action-" + row.uid, "step_action", row.action,
        { placeholder: "season and sear" }
      );
      action.input.addEventListener("input", function () {
        row.action = action.input.value;
        markRowsAuthoritative();
        syncRaw();
        // Other rows' pickers show this row's action text as their option
        // label -- fixed up lazily on the next full render rather than
        // live here, since a full rebuild while this field has focus would
        // steal the cursor mid-keystroke.
      });
      rowEl.appendChild(action.wrap);

      var usesField = el("div", "field");
      var usesLabel = el("span", "field__help");
      usesLabel.textContent = "Uses";
      usesField.appendChild(usesLabel);

      var options = optionsForRow(steps, index, ingredientNames);
      var allOptions = options.ingredientOptions.concat(options.stepOptions);
      if (allOptions.length || options.orphans.length) {
        var picker = el("div", "picker");
        allOptions.forEach(function (opt) {
          picker.appendChild(pickerOption(row, opt, false));
        });
        options.orphans.forEach(function (opt) {
          picker.appendChild(pickerOption(row, opt, true));
        });
        usesField.appendChild(picker);
      } else {
        var empty = el("p", "picker__empty");
        empty.textContent = "No ingredients or earlier steps to choose from yet.";
        usesField.appendChild(empty);
      }
      rowEl.appendChild(usesField);

      rowEl.appendChild(
        rowActionButtons(index > 0, index < steps.length - 1, {
          up: function () {
            markRowsAuthoritative();
            moveItem(steps, index, -1);
            pruneInvalidStepRefs(steps);
            render();
          },
          down: function () {
            markRowsAuthoritative();
            moveItem(steps, index, 1);
            pruneInvalidStepRefs(steps);
            render();
          },
          remove: function () {
            markRowsAuthoritative();
            steps.splice(index, 1);
            pruneInvalidStepRefs(steps);
            render();
          },
        })
      );

      return rowEl;
    }

    function pickerOption(row, opt, isOrphan) {
      var className = isOrphan ? "picker__option picker__option--orphan" : "picker__option";
      var check = checkLabel(
        className, opt.label, "step_input__" + row.uid, opt.value, opt.checked !== false
      );
      check.input.addEventListener("change", function () {
        if (check.input.checked) {
          row.inputs.add(opt.value);
        } else {
          row.inputs.delete(opt.value);
        }
        markRowsAuthoritative();
        render();
      });
      return check.wrap;
    }

    addBtn.addEventListener("click", function () {
      markRowsAuthoritative();
      steps.push({ uid: String(nextUid++), action: "", inputs: new Set() });
      render();
    });

    rawField.addEventListener("input", function () {
      sourceField.value = "advanced";

      var parsed = parseStepLines(rawField.value);
      var idToUid = {};
      var rebuilt = parsed.map(function (p) {
        var uid = String(nextUid++);
        idToUid[p.id] = uid;
        return { uid: uid, action: p.action, inputs: new Set(), _rawInputs: p.inputs };
      });
      rebuilt.forEach(function (row) {
        row._rawInputs.forEach(function (raw) {
          var targetUid = Object.prototype.hasOwnProperty.call(idToUid, raw) ? idToUid[raw] : null;
          if (targetUid && targetUid !== row.uid) {
            row.inputs.add("step:" + targetUid);
          } else {
            row.inputs.add("ingredient:" + raw);
          }
        });
        delete row._rawInputs;
      });
      pruneInvalidStepRefs(rebuilt);

      steps = rebuilt;
      renderRows();
    });

    render();
  }

  document.addEventListener("DOMContentLoaded", function () {
    var ingredientRoot = document.querySelector("[data-js-ingredient-editor]");
    if (ingredientRoot) initIngredientEditor(ingredientRoot);

    var stepRoot = document.querySelector("[data-js-step-editor]");
    if (stepRoot) initStepEditor(stepRoot);
  });
})();
