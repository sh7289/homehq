---
name: Marry Me Chicken
kind: meal
favorite: true
effort: 3
category: Chicken / Creamy
meal_type: Recipe
protein: Chicken breast
season: All
score: High
leftovers: Good
freezer_friendly: 'No'
garden_herbs: Basil
source: Pinterest / general
ingredients:
- name: chicken
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: sun-dried tomatoes
  quantity: null
  unit: null
  staple: false
  fresh: false
- name: cream
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: parmesan
  quantity: null
  unit: null
  staple: false
  fresh: false
- name: garlic
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: stock
  quantity: null
  unit: null
  staple: true
  fresh: false
steps:
- id: s1
  action: sear chicken until golden
  inputs:
  - chicken
- id: s2
  action: sauté garlic, add sun-dried tomatoes
  inputs:
  - garlic
  - sun-dried tomatoes
- id: s3
  action: deglaze with stock, simmer 20 min
  inputs:
  - s1
  - s2
  - stock
- id: s4
  action: stir in cream and parmesan
  inputs:
  - s3
  - cream
  - parmesan
- id: s5
  action: simmer until sauce thickens
  inputs:
  - s4
---
Chicken, sun-dried tomatoes, cream, Parmesan, garlic, stock; most recipes are similar.

**Serve with:** Pasta, mashed potatoes, rice, crusty bread

**Notes:** Heather loves sun-dried tomatoes
