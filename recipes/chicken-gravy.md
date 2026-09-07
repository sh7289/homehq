---
name: Chicken & Gravy
kind: meal
favorite: true
effort: 3
category: Comfort Food
meal_type: Recipe
protein: Chicken breast
season: All / Cooler weather
score: High
leftovers: Good
freezer_friendly: 'No'
garden_herbs: Thyme optional
source: Household
ingredients:
- name: chicken
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: white onion
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: flour
  quantity: null
  unit: null
  staple: true
  fresh: false
- name: whole milk
  quantity: null
  unit: null
  staple: false
  fresh: true
steps:
- id: s1
  action: sear chicken until golden
  inputs:
  - chicken
- id: s2
  action: cook diced onion until soft
  inputs:
  - white onion
- id: s3
  action: make roux with flour and pan oil
  inputs:
  - flour
- id: s4
  action: whisk in whole milk for gravy
  inputs:
  - s3
  - whole milk
- id: s5
  action: bake chicken and gravy together 30 min
  inputs:
  - s1
  - s2
  - s4
---
Sear chicken, cook white onion, make thick roux/gravy with whole milk/flour/pan oil, bake together.

**Serve with:** Mashed potatoes preferred; rice acceptable

**Notes:** Comfort food; potatoes preferred
