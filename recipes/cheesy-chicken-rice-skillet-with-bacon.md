---
name: Cheesy Chicken & Rice Skillet with Bacon
kind: meal
favorite: true
effort: 3
category: Chicken / Rice
meal_type: Recipe
protein: Chicken breast + bacon
season: All
score: High
leftovers: Good
freezer_friendly: 'No'
source: Picky Palate
ingredients:
- name: chicken
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: bacon
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: rice
  quantity: null
  unit: null
  staple: true
  fresh: false
- name: cheese
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: chicken broth
  quantity: null
  unit: null
  staple: true
  fresh: false
steps:
- id: s1
  action: cook bacon until crispy
  inputs:
  - bacon
- id: s2
  action: sear chicken in bacon fat
  inputs:
  - chicken
  - s1
- id: s3
  action: toast rice 2 min
  inputs:
  - rice
  - s1
- id: s4
  action: simmer rice and chicken 20 min
  inputs:
  - s3
  - s2
  - chicken broth
- id: s5
  action: stir in cheese until melted
  inputs:
  - s4
  - cheese
---
Chicken, bacon, rice, cheese, chicken broth.

**Serve with:** One-pan meal

**Notes:** Comfort food; Steve-friendly
