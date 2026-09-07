---
name: Burgers & Fries
kind: meal
favorite: true
effort: 1
category: American
meal_type: Formula / Emergency
protein: Beef
season: All
score: High
leftovers: Fair
freezer_friendly: 'No'
source: Household
ingredients:
- name: McCormick & Schmick's burger patties
  quantity: 1
  unit: four-pack
  staple: false
  fresh: false
- name: frozen oven fries
  quantity: null
  unit: null
  staple: false
  fresh: false
steps:
- id: s1
  action: bake frozen oven fries at 425F
  inputs:
  - frozen oven fries
- id: s2
  action: pan-sear burger patties until cooked through
  inputs:
  - McCormick & Schmick's burger patties
- id: s3
  action: plate burgers and fries
  inputs:
  - s2
  - s1
---
Premade patties preferred, higher quality if possible; McCormick & Schmick's four-pack if available.

**Serve with:** Frozen oven fries

**Notes:** Low-effort fallback
