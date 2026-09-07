---
name: Chicken Enchiladas
kind: meal
favorite: true
effort: 3
category: Mexican
meal_type: Recipe / Freezer-Friendly
protein: Chicken breast
season: All
score: High
leftovers: Good
freezer_friendly: Uses freezer sauce
garden_herbs: Cilantro optional
source: Household
ingredients:
- name: chicken
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: flour tortillas
  quantity: null
  unit: null
  staple: false
  fresh: false
- name: shredded cheese
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: white onion
  quantity: null
  unit: null
  staple: false
  fresh: true
- name: red enchilada sauce
  quantity: null
  unit: null
  staple: false
  fresh: false
steps:
- id: s1
  action: cook chicken until tender
  inputs:
  - chicken
- id: s2
  action: shred chicken and mix with onion
  inputs:
  - s1
  - white onion
- id: s3
  action: fill tortillas with chicken mixture
  inputs:
  - s2
  - flour tortillas
- id: s4
  action: roll tortillas and arrange in dish
  inputs:
  - s3
- id: s5
  action: pour enchilada sauce over tortillas
  inputs:
  - s4
  - red enchilada sauce
- id: s6
  action: top with cheese and bake at 350F for 25 min
  inputs:
  - s5
  - shredded cheese
---
Chicken, flour tortillas, shredded cheese, white onion, red enchilada sauce.

**Serve with:** Rice, refried beans, salad, chips and salsa

**Notes:** Supported by frozen red enchilada sauce
