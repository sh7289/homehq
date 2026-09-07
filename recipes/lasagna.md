---
name: Lasagna
kind: meal
favorite: true
effort: 5
category: Italian
meal_type: Recipe / Project
protein: Ground beef
season: All
score: High
leftovers: Excellent
freezer_friendly: 'Yes'
garden_herbs: Basil/oregano if in sauce
source: Household
ingredients:
- name: frozen meat sauce
  quantity: null
  unit: null
  staple: false
  fresh: false
- name: ricotta
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: lasagna noodles
  quantity: null
  unit: null
  staple: true
  fresh: false
- name: shredded mozzarella
  quantity: null
  unit: null
  staple: false
  fresh: true
steps:
- id: s1
  action: boil lasagna noodles al dente
  inputs:
  - lasagna noodles
- id: s2
  action: thaw and warm frozen meat sauce
  inputs:
  - frozen meat sauce
- id: s3
  action: layer noodles ricotta sauce and mozzarella
  inputs:
  - s1
  - ricotta
  - s2
  - shredded mozzarella
- id: s4
  action: bake at 375F for 30 min covered
  inputs:
  - s3
- id: s5
  action: rest 10 min before serving
  inputs:
  - s4
---
Uses frozen homemade meat sauce, ricotta, lasagna noodles, shredded mozzarella.

**Serve with:** Garlic bread, salad

**Notes:** Best when sauce is already frozen
