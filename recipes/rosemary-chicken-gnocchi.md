---
name: Rosemary Chicken Gnocchi
kind: meal
favorite: true
effort: 3
category: Chicken / Gnocchi
meal_type: Recipe
protein: Chicken breast
season: Fall/Winter
score: High
leftovers: Good
freezer_friendly: 'No'
garden_herbs: Rosemary
source: Bev Cooks
ingredients:
- name: chicken
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: gnocchi
  quantity: null
  unit: null
  staple: false
  fresh: false
- name: rosemary
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: garlic
  quantity: null
  unit: null
  staple: false
  fresh: true
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
steps:
- id: s1
  action: season and sear chicken
  inputs:
  - chicken
  - rosemary
- id: s2
  action: sauté garlic
  inputs:
  - garlic
- id: s3
  action: simmer chicken 20 min
  inputs:
  - s1
  - s2
- id: s4
  action: boil gnocchi until floats
  inputs:
  - gnocchi
- id: s5
  action: stir in cream and parmesan
  inputs:
  - s3
  - cream
  - parmesan
- id: s6
  action: combine gnocchi with sauce
  inputs:
  - s4
  - s5
---
Chicken, gnocchi, rosemary, garlic, cream, Parmesan.

**Serve with:** Side salad or simple veg

**Notes:** Comfort food, more interesting than standard chicken/pasta
