# Aurum LIMS roadmap

Aurum LIMS is being built in phases. Each phase plugs into the foundation from
Phase 1: user accounts, roles, the audit trail and electronic signatures.

## Phase 1: Foundation ✅
- Windows desktop app (Python + PySide6), SQLite database with versioned migrations
- First-run set-up, sign-in, roles (Administrator, QA Reviewer, Analyst, Viewer)
- Password policy, expiry, history; account lockout; inactivity auto-lock
- Append-only, hash-chained audit trail with integrity check and CSV export
- Electronic signature service (password re-entry and signature meaning)
- Admin pages for users and security settings

## Phase 2: Samples and chain of custody
- Sample types, sample points/sources, customers or projects
- Sample registration with automatic unique IDs (configurable format, e.g. `AU-2026-00001`)
- Barcode/label printing (Code 128 / QR)
- Storage locations (building → room → fridge/freezer → shelf → box/position)
- Chain of custody: receipt (condition, temperature), transfers, aliquots/sub-samples, disposal
- Sample status lifecycle: Registered → Received → In Testing → Testing Complete → Reviewed → Approved → Disposed

## Phase 3: Methods, specifications and results
- Test methods / SOP references with versioning
- Analytes, units, result types (numeric, text, pass/fail), significant figures and rounding rules
- Specifications (limits per product/sample type, with versioning)
- Result entry with automatic in/out-of-specification flags
- Calculated results (formulas), replicate handling, LOD/LOQ reporting
- Out-of-specification (OOS) investigation records

## Phase 4: Review, approval and reporting
- Review/approval workflow using electronic signatures (Performed by → Reviewed by → Approved by)
- Second-person review rules (the reviewer cannot be the analyst)
- Certificates of Analysis (PDF) with signature manifestation
- Record locking after approval; controlled amendments with reason for change

## Phase 5: Instruments and inventory
- Instrument register, calibration and maintenance schedules with due-date warnings
- Block use of instruments that are out of calibration
- Reagents, reference standards and consumables: lots, expiry, opened dates, stock levels
- Link results to the instrument and reagent lots used

## Later
- Instrument data import (CSV/TXT exports from HPLC, GC, balances, pH meters, etc.)
- Dashboards: turnaround time, workload, OOS trends, control charts
- Multi-user network deployment (shared database / PostgreSQL)
- Validation package templates (URS, IQ/OQ/PQ protocols, traceability matrix)
