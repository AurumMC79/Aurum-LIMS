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

## Phase 2: Samples and chain of custody ✅
- Sample types in categories (raw material, product, stability, environmental, other) that decide the form fields
- Custom sample ID formats built from tokens, lab-wide or per sample type, with yearly/monthly/daily restarts
- Sample registration (single or batch), with optional receipt into storage straight away
- Nested storage locations with storage conditions
- Chain of custody: receipt (condition, temperature), transfers to locations or people, aliquots, disposal
  with electronic signature, cancellation, audited corrections
- Dashboard counts: awaiting receipt, in the lab, overdue
- Not yet: barcode/label printing (deferred at the lab's request)

## Phase 3: Methods, specifications and results ✅
- Versioned test methods (Draft → QA-approved Active → Retired) with SOP references
- Inputs and results (number, text, pass/fail), units, decimals, replicates
- Calculated results with safe, exact-decimal formulas evaluated per replicate; mean and %RSD
- USP 7.20 rounding (half-up to the reporting decimals) before comparison with the limit
- Versioned specifications with range / NLT / NMT / < / > / text / pass / report-only limits
- Tests assigned to samples by hand; specification chosen per sample (matching specs suggested)
- Live calculation while entering results; reasons required for changes to saved values
- Analyst e-signature locks results; out-of-spec results require a comment and open an OOS investigation
- OOS investigation log, closed by QA with a conclusion and e-signature
- Not yet: LOD/LOQ handling ("< LOQ" results), system suitability criteria, instrument file import

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
