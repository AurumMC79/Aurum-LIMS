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

## Phase 4: Review, approval and reporting ✅
- Second-person test review by QA (accept with e-signature, or return with a comment); reviewers can never
  review a test they entered or signed values on
- Returned tests corrected with reasons and re-signed as a new revision; earlier revisions retained
- QA sample decision (Approved / Rejected) with e-signature; open OOS blocks the decision; non-conforming samples
  cannot be approved
- Certificate of Analysis / Test Report PDFs with full signature manifestation, stored as issued with SHA-256
  fingerprint verification
- Laboratory details for the certificate header; decided samples locked
- Not yet: amended certificates (re-issue after a post-approval correction), bulk/batch release

## Phase 5: Instruments and inventory ✅
- Instrument register with calibration / maintenance intervals and due dates; immutable event history
- Failed calibrations or checks take instruments out of service; overdue or out-of-service instruments are blocked
- Inventory catalogue and lots with expiry, opened date, days-after-opening, certified purity and stock on hand;
  immutable stock movements; low-stock and expiry warnings
- Methods list required equipment and materials; tests can't be signed until they are recorded and valid
- Reference standard purity filled into calculations from the lot certificate
- Not yet: preventive-maintenance task lists, label printing for lots, supplier management

## After Phase 5 ✅
- SOP index (view-only PDFs, revisions), Report a bug, desktop shortcut, Reload, lab roles
- Automatic backups: daily at start-up, before every database update, and on demand; checked, pruned and audited
- LOD / LOQ on number results: reported as ND or < LOQ, with conservative conformance and CoA notes

## Later
- Instrument data import (CSV/TXT exports from HPLC, GC, balances, pH meters, etc.)
- Dashboards: turnaround time, workload, OOS trends, control charts
- Multi-user network deployment (shared database / PostgreSQL)
- Validation package templates (URS, IQ/OQ/PQ protocols, traceability matrix)
