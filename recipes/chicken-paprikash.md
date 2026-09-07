---
name: Chicken Paprikash
kind: meal
favorite: false
effort: 3
category: Eastern European
meal_type: Recipe / Craving
protein: Chicken breast
season: Cooler weather
score: Medium
leftovers: Good
freezer_friendly: 'No'
source: General recipe
ingredients:
- name: onion
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: garlic
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: paprika
  quantity: null
  unit: null
  staple: true
  fresh: false
- name: stock
  quantity: null
  unit: null
  staple: true
  fresh: false
- name: sour cream
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: flour
  quantity: null
  unit: null
  staple: true
  fresh: false
steps:
- id: s1
  action: sauté onion and garlic
  inputs:
  - onion
  - garlic
- id: s2
  action: bloom paprika, add stock
  inputs:
  - s1
  - paprika
  - stock
- id: s3
  action: simmer 30 min until reduced
  inputs:
  - s2
- id: s4
  action: stir in sour cream and flour
  inputs:
  - s3
  - sour cream
  - flour
- id: s5
  action: simmer 5 min until thickened
  inputs:
  - s4
---
Onion, garlic, paprika, stock, sour cream; flour optional.

**Serve with:** Egg noodles preferred; rice acceptable

**Notes:** Specific craving meal
