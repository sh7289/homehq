---
name: Steak or Chicken + Baked Potato Night
kind: meal
favorite: true
effort: 3
category: American
meal_type: Formula
protein: Steak or chicken breast
season: All
score: High
leftovers: Fair
freezer_friendly: 'No'
garden_herbs: Rosemary/thyme optional
source: Household
ingredients:
- name: steak
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: chicken
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: potatoes
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: green beans
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: broccoli
  quantity: null
  unit: null
  staple: false
  fresh: true
steps:
- id: s1
  action: bake potatoes at 200C for 50 min
  inputs:
  - potatoes
- id: s2
  action: sear steak and chicken until golden
  inputs:
  - steak
  - chicken
- id: s3
  action: finish meat in oven at 200C for 15 min
  inputs:
  - s2
- id: s4
  action: warm green beans and broccoli
  inputs:
  - green beans
  - broccoli
- id: s5
  action: plate meat, potatoes, and vegetables
  inputs:
  - s3
  - s1
  - s4
---
Steak ideally from butcher; chicken or steak seared/finished in oven.

**Serve with:** Loaded baked potatoes; canned green beans, broccoli, fresh green beans, simple salad

**Notes:** Reliable meat + potato + veg dinner
