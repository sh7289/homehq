---
name: Salmon, Potato & Vegetable
kind: meal
favorite: true
effort: 1
category: Seafood
meal_type: Formula
protein: Salmon
season: All
score: High
leftovers: Fair
freezer_friendly: 'No'
garden_herbs: Dill
source: Household
ingredients:
- name: salmon
  quantity: null
  unit: null
  staple: false
  fresh: false
- name: potato
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
  action: thaw salmon in refrigerator
  inputs:
  - salmon
- id: s2
  action: boil potatoes until tender
  inputs:
  - potato
- id: s3
  action: steam broccoli 5 min
  inputs:
  - broccoli
- id: s4
  action: season and pan-sear salmon
  inputs:
  - s1
- id: s5
  action: plate salmon with vegetables
  inputs:
  - s4
  - s2
  - s3
---
Salmon kept in freezer.

**Serve with:** Potatoes + broccoli/asparagus/green beans

**Notes:** Good protein option; bring back into rotation
