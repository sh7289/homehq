---
name: Peas & Pancetta
kind: meal
favorite: true
effort: 2
category: Side Dish
meal_type: Recipe
protein: Pancetta
season: All
score: High
leftovers: Fair
freezer_friendly: 'No'
source: Williams Sonoma
ingredients:
- name: frozen peas
  quantity: null
  unit: null
  staple: false
  fresh: false
- name: pancetta
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: shallot
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: butter
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: parmesan
  quantity: null
  unit: null
  staple: true
  fresh: false
steps:
- id: s1
  action: dice and cook pancetta until crisp
  inputs:
  - pancetta
- id: s2
  action: mince shallot and sauté in butter
  inputs:
  - shallot
  - butter
- id: s3
  action: add frozen peas and cook 5 min
  inputs:
  - s2
  - frozen peas
- id: s4
  action: combine with pancetta and finish
  inputs:
  - s3
  - s1
  - parmesan
---
Frozen peas, pancetta, shallot/onion, butter, Parmesan.

**Serve with:** Great with steak, chicken, pork

**Notes:** Elevated side dish
