---
name: Spaghetti & Meatballs
kind: meal
favorite: true
effort: 1
category: Italian
meal_type: Assembly / Emergency
protein: Beef meatballs
season: All
score: High
leftovers: Good
freezer_friendly: Uses freezer sauce
garden_herbs: Basil/oregano
source: Household
ingredients:
- name: premade meatballs
  quantity: null
  unit: null
  staple: false
  fresh: false
- name: frozen meat sauce
  quantity: null
  unit: null
  staple: false
  fresh: false
- name: spaghetti
  quantity: null
  unit: null
  staple: true
  fresh: false
- name: frozen garlic bread
  quantity: null
  unit: null
  staple: false
  fresh: false
steps:
- id: s1
  action: boil water and cook spaghetti
  inputs:
  - spaghetti
- id: s2
  action: heat meat sauce and meatballs together
  inputs:
  - frozen meat sauce
  - premade meatballs
- id: s3
  action: bake frozen garlic bread at 200C for 10 min
  inputs:
  - frozen garlic bread
- id: s4
  action: plate spaghetti and top with sauce
  inputs:
  - s1
  - s2
  - s3
---
Premade meatballs + frozen homemade meat sauce + spaghetti.

**Serve with:** Frozen garlic bread

**Notes:** Very easy if sauce exists
