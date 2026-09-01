# LH₂ BWB running log

Auto-appended by `pipeline/run_case.py`, newest entry last.


---

**Concept regression_iter — Iteration 3**

Assumptions: 12497 m, M0.75, W = 7300 kg (MTOM), Cfe 0.0030, tank vol 10.0 m³, LH2 fill 90%, TSFC 6.63e-06 kg/(N·s)

Geometry: Sref 69.120 m², AR 3.493, S_wet 151.3 m²

Aero: CL 0.14720, alpha 4.625°, e 0.5713, CD0 0.00657, CD_total 0.01018, L/D 14.46, Cma -0.3083/rad, static margin ~12.2% MAC

Mass/Range: fuel 637.2 kg, Wf 6662.8 kg, ln(Wi/Wf) 0.09133, Range ≈ 4496 km / 2428 nm

Conclusion: L/D 14.46 at CL 0.1472, range ~2428 nm.

---

**Concept P1 — Iteration 4**

Assumptions: 12497 m, M0.75, W = 7300 kg (MTOM), Cfe 0.0030, tank vol 10.0 m³, LH2 fill 90%, TSFC 6.63e-06 kg/(N·s)

Geometry: Sref 69.120 m², AR 3.493, S_wet 151.3 m²

Aero: CL 0.14720, alpha 4.625°, e 0.5713, CD0 0.00657, CD_total 0.01018, L/D 14.46, Cma -0.3083/rad, static margin ~12.2% MAC

Mass/Range: fuel 637.2 kg, Wf 6662.8 kg, ln(Wi/Wf) 0.09133, Range ≈ 4496 km / 2428 nm

Conclusion: L/D 14.46 at CL 0.1472, range ~2428 nm.

---

**Concept P1 — Iteration 4**

Assumptions: 12497 m, M0.75, W = 7300 kg (MTOM), Cfe 0.0030, tank vol 10.0 m³, LH2 fill 90%, TSFC 6.63e-06 kg/(N·s)

Geometry: Sref 69.120 m², AR 3.493, S_wet 151.3 m²

Aero: CL 0.14720, alpha 4.625°, e 0.5713, CD0 0.00657, CD_total 0.01018, L/D 14.46, Cma -0.3083/rad, static margin ~12.2% MAC

Mass/Range: fuel 637.2 kg, Wf 6662.8 kg, ln(Wi/Wf) 0.09133, Range ≈ 4496 km / 2428 nm

Conclusion: L/D 14.46 at CL 0.1472, range ~2428 nm.

---

**Concept P1 — Iteration 5**

Assumptions: 12497 m, M0.75, W = 7300 kg (MTOM), Cfe 0.0030, tank vol 10.0 m³, LH2 fill 90%, TSFC 6.63e-06 kg/(N·s)

Geometry: Sref 69.130 m², AR 3.492, S_wet 151.6 m²

Aero: CL 0.14720, alpha 4.612°, e 0.5740, CD0 0.00658, CD_total 0.01024, L/D 14.38, Cma -0.3096/rad, static margin ~12.1% MAC

Mass/Range: fuel 637.2 kg, Wf 6662.8 kg, ln(Wi/Wf) 0.09133, Range ≈ 4469 km / 2413 nm

Conclusion: L/D 14.38 at CL 0.1472, range ~2413 nm.

---

**Concept P1 — Iteration 6**

Assumptions: 12497 m, M0.75, W = 7300 kg (MTOM), Cfe 0.0030, tank vol 10.0 m³, LH2 fill 90%, TSFC 6.63e-06 kg/(N·s)

Geometry: Sref 73.706 m², AR 3.275, S_wet 161.2 m²

Aero: CL 0.13800, alpha 4.655°, e 0.5471, CD0 0.00656, CD_total 0.01001, L/D 13.78, Cma -0.3272/rad, static margin ~13.7% MAC

Mass/Range: fuel 637.2 kg, Wf 6662.8 kg, ln(Wi/Wf) 0.09133, Range ≈ 4285 km / 2314 nm

Conclusion: L/D 13.78 at CL 0.1380, range ~2314 nm.

---

**Concept P1 — Iteration 5**

Assumptions: 12497 m, M0.75, W = 7300 kg (MTOM), Cfe 0.0030, tank vol 10.0 m³, LH2 fill 90%, TSFC 6.63e-06 kg/(N·s)

Geometry: Sref 69.130 m², AR 3.492, S_wet 151.6 m²

Aero: CL 0.14720, alpha 4.612°, e 0.5740, CD0 0.00658, CD_total 0.01024, L/D 14.38, Cma -0.3096/rad, static margin ~12.1% MAC

Mass/Range: fuel 637.2 kg, Wf 6662.8 kg, ln(Wi/Wf) 0.09133, Range ≈ 4469 km / 2413 nm (quick, no reserves)

MTOM closure: assumed 7300 kg -> converged 7360.2 kg (+60.2 kg). centerbody 2832, systems 957, propulsion 730, tank_system 637, outer_wing 310, landing_gear 258, payload 800, crew 200, fuel 637.2 kg.

MTOM gate: MTOM 7360.2 kg vs cap 7300 kg is nominally FAIL by 60.2 kg (0.83%), but THE VERDICT IS UNDECIDED: it flips within the plausible range of centerbody areal density sigma (verdict changes across 40.0-100.0), tank gravimetric efficiency eta_g (verdict changes across 0.3-0.6). Both are unsourced placeholders. Read this as 'MTOM lands within a few percent of the cap and which side is currently decided by numbers nobody has sourced', NOT as a settled pass or fail. Sourcing them is what settles it.

Reserve profile: trip 472.8 kg (74% of loaded fuel), reserves + contingency 164.4 kg (26%), range 2405 km / 1299 nm.

Placeholder sensitivity (MTOM kg, P=under cap / F=over). Both inputs are UNSOURCED — sigma has no citation at all, eta_g's midpoint is a choice:
  sigma kg/m²: 40->6039P, 60->7360F, 80->8678F, 100->9994F
  eta_g:       0.30->8547F, 0.40->7805F, 0.50->7360F, 0.60->7063P

Conclusion: L/D 14.38 at CL 0.1472; reserve-based range 1299 nm (quick no-reserve estimate 2413 nm, 46% optimistic). Flags: MTOM 7360 kg vs 7300 kg cap is nominally FAIL by 60 kg (0.83%), but the verdict is UNDECIDED — it flips on centerbody areal density sigma (verdict changes across 40.0-100.0); tank gravimetric efficiency eta_g (verdict changes across 0.3-0.6), both unsourced placeholders. Do not read this as a settled pass or fail; reserves + contingency consume 26% of loaded fuel.

---

**Concept P1 — Iteration 7**

Assumptions: 12497 m, M0.75, W = 7300 kg (MTOM), Cfe 0.0030, tank vol 10.0 m³, LH2 fill 90%, TSFC 6.63e-06 kg/(N·s)

Geometry: Sref 70.138 m², AR 4.003, S_wet 154.2 m²

Aero: CL 0.14500, alpha 0.585°, e 0.7362, CD0 0.00660, CD_total 0.00975, L/D 14.88, Cma -0.3482/rad, static margin ~13.0% MAC

Mass/Range: fuel 637.2 kg, Wf 6662.8 kg, ln(Wi/Wf) 0.09133, Range ≈ 4626 km / 2498 nm (quick, no reserves)

MTOM closure: assumed 7300 kg -> converged 7336.7 kg (+36.7 kg). centerbody 2958, systems 954, propulsion 727, tank_system 637, outer_wing 366, landing_gear 257, payload 600, crew 200, fuel 637.2 kg.

MTOM gate: MTOM 7336.7 kg vs cap 7300 kg is nominally FAIL by 36.7 kg (0.50%), but THE VERDICT IS UNDECIDED: it flips within the plausible range of centerbody areal density sigma (verdict changes across 40.0-100.0), tank gravimetric efficiency eta_g (verdict changes across 0.3-0.6). Both are unsourced placeholders. Read this as 'MTOM lands within a few percent of the cap and which side is currently decided by numbers nobody has sourced', NOT as a settled pass or fail. Sourcing them is what settles it.

Reserve profile: trip 477.2 kg (75% of loaded fuel), reserves + contingency 160.0 kg (25%), range 2533 km / 1368 nm.

Placeholder sensitivity (MTOM kg, P=under cap / F=over). Both inputs are UNSOURCED — sigma has no citation at all, eta_g's midpoint is a choice:
  sigma kg/m²: 40->5948P, 60->7337F, 80->8721F, 100->10101F
  eta_g:       0.30->8529F, 0.40->7784F, 0.50->7337F, 0.60->7038P

Conclusion: L/D 14.88 at CL 0.1450; reserve-based range 1368 nm (quick no-reserve estimate 2498 nm, 45% optimistic). Flags: Oswald e moved +28% vs iteration_5 (0.5740 -> 0.7362) — check spanwise loading/twist before reading anything into the L/D change; MTOM 7337 kg vs 7300 kg cap is nominally FAIL by 37 kg (0.50%), but the verdict is UNDECIDED — it flips on centerbody areal density sigma (verdict changes across 40.0-100.0); tank gravimetric efficiency eta_g (verdict changes across 0.3-0.6), both unsourced placeholders. Do not read this as a settled pass or fail; reserves + contingency consume 25% of loaded fuel.
