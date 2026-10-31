# Aurum LIMS

A Laboratory Information Management System for QC and regulated laboratories
(GMP, ISO/IEC 17025), built as a Windows desktop application.

**Status:** Phases 1–5 are complete:
1. Foundation
2. Samples and chain of custody
3. Methods, specifications, results and OOS
4. Review, approval and certificates
5. Instruments and inventory

See [docs/ROADMAP.md](docs/ROADMAP.md) for what could come next.

## Finding your way around

The sidebar groups the pages under a few headings. Hover over a heading (or click it to keep it open) to see
its pages, then click one. The heading of the page you are on is highlighted, with the page's name underneath.
Headings only show the pages your role can use.

| Heading | Pages |
|---|---|
| Dashboard | Opens directly |
| Lab work | Sample Intaking, Samples, Testing, Review, OOS, Certificates |
| Equipment & materials | Instruments, Inventory, Storage |
| Quality & reports | Reports, SOP Index, Audit Trail |
| Setup | Methods, Specifications, Sample Types, Users |
| Settings & account | Settings, My Account |

The grouping is defined in `src/aurum_lims/ui/navigation.py` (`NAV_GROUPS`).

## SOP index and bug reports

| Area | What it does |
|---|---|
| SOP Index | Every user can open the current revision of each SOP, form, work instruction or policy (PDF, opened read-only). Forms are listed under their SOP, and you can search by number, title or category. Administrators and QA Reviewers add documents and upload new revisions; a new revision becomes the one in effect, and earlier revisions are kept as superseded copies that only they can open. PDFs are stored in the database exactly as uploaded (max 50 MB), with a fingerprint that is checked whenever they are opened. |
| Report a bug | **Settings → Report a bug** is available to every user. Enter a title, where it happened, severity, what happened, steps to reproduce, and what you expected. You can attach a screenshot: press Win+Shift+S, then click *Paste screenshot*. Users see their own reports; administrators see all of them and set the status (New, In progress, Fixed, Won't fix, Duplicate) with a resolution note. |
| Reading reports (developers / IT) | `.venv\Scripts\python -m aurum_lims.tools.bug_reports` lists open reports. Add `BUG-0001` to show one in full, `--all` to include closed ones, or `--export DIR` to save each as Markdown with its screenshot. The tool opens the database read-only. |

## Instruments and inventory (Phase 5)

| Area | What it does |
|---|---|
| Instruments | Register equipment (ID, category, make, model, serial number, location) with calibration and maintenance intervals. Calibrations, maintenance, qualifications, performance checks, repairs and status changes are recorded as a permanent history. The next due date is calculated from the interval. A **failed** calibration or check takes the instrument **out of service** automatically. |
| Usable instruments | An instrument can be recorded on a test only while it is in service and its calibration and maintenance are not overdue. This is checked again when the analyst signs. |
| Maintenance plans | Each instrument can have preventive-maintenance plans (e.g. *Monthly PM*, every 30 days) with a checklist of tasks; a task can require a reading and state its acceptance criterion. **Perform…** goes through the checklist (OK / Problem found / N/A). The completed checklist is kept in the instrument's permanent history; a problem found takes the instrument out of service, a pass moves the next due date on. An overdue plan blocks the instrument from tests, like an overdue calibration. Plans can be copied to other instruments, and **Maintenance schedule…** lists every plan by due date. |
| Inventory | A catalogue of reagents, reference standards, solvents, columns and consumables. Each received lot gets an internal ID (`LOT-YYYY-NNNN`) and records the supplier lot number, received / opened / expiry dates, certificate reference, certified purity, storage location and quantity on hand. "Use within N days of opening" shortens the expiry automatically. Every stock movement (received, used, returned, adjusted, discarded) is permanently recorded. |
| Method requirements | Each method lists the equipment and materials it needs, e.g. *HPLC system*, *Analytical balance*, *Aurumex reference standard*. A test **cannot be signed** until each one is recorded. Instruments of the wrong category, overdue or out of service, and lots that are expired, discarded or empty, are refused. |
| Suppliers | The **Suppliers** tab on the Inventory page records each supplier's contact, what they supply, qualification reference, approval status (Approved, Conditionally approved, Under evaluation, Disqualified) and review date, with every lot delivered. Items choose their supplier from the list and each lot records who delivered it. Receiving from a supplier that is not approved or overdue for review asks for confirmation; disqualified suppliers are refused. Only QA can change an approval status. |
| Standard purity | A reference-standard requirement can fill a method input (e.g. `purity_std`) with the lot's **certified purity**. The value is never typed by hand, so the calculation always uses the certificate value. |
| Stock | The quantity used on a test is taken out of the lot's stock (and put back if the entry is removed). Items have a minimum stock level; the dashboard shows low stock, lots that are expired or about to expire, and instruments that need attention. |

## Instrument data import

Most instruments can export a CSV / TXT table: chromatography data systems (Empower, Chromeleon, OpenLab),
balances, pH meters, Karl Fischer and autotitrators, UV-Vis.

| Area | What it does |
|---|---|
| Import templates | On the **Methods** page, Admin / QA choose **Import templates…** to describe one instrument's export for that method: the line with the column headings, the separator, decimal comma, and one rule per parameter: the value column, which rows to use (e.g. *Sample Name* contains `{sample_id}`, or `STD` for the standards) and whether those rows are replicates, averaged, or only the first one is used. Load an example file to pick columns from a list and see exactly which values the template finds. Changes are audited. |
| Importing | When entering results, the analyst chooses **Import from file…**, picks the template and the export, sees the values found (with any warnings, e.g. fewer rows than replicates), and fills them into the form. Nothing is saved until the analyst checks and saves the results as usual. |
| Traceability | Every imported file is stored with the test exactly as read (it cannot be changed or deleted), with its SHA-256 fingerprint, the template settings used and the values found. The import is recorded in the audit trail, and reviewers can save the original file from the result screen. |

## Validation package

Administrators and QA can create the documents for validating Aurum LIMS (computer-system validation, GAMP 5
approach) from **Settings → Validation**, as editable Word files:

| Document | Contents |
|---|---|
| Validation Plan | Scope, approach, deliverables, responsibilities, acceptance criteria, deviations, change control. |
| User Requirements Specification | The requirements Aurum LIMS meets, each with an ID, GxP risk rating and the regulation it supports (21 CFR 11, EU Annex 11, ISO/IEC 17025). |
| Installation Qualification | Filled in automatically with this computer's details (versions, database, schema revision, security settings), automatic checks (database integrity, audit-trail chain, protection triggers, backups) and a SHA-256 manifest of every program file. |
| Operational Qualification | Step-by-step test scripts for each function with blank Actual result / Pass-Fail / initials columns. |
| Performance Qualification | End-to-end scenarios to adapt to the laboratory's own products, methods and SOPs. |
| Traceability Matrix | Every requirement and the tests that cover it (also as CSV). |
| Validation Summary Report | Results, deviations and release decision template. |

The overall **program fingerprint** is recorded in the IQ and the audit trail. The Validation tab shows whether the
program has changed since the last package, which is the trigger for change control and re-qualification.
The requirements and test scripts live in `src/aurum_lims/validation/content.py`; a test checks that every
requirement is covered by at least one test.

## Reports

The **Reports** page (everyone) works over a chosen period: last 30 / 90 days, last 12 months, this year or a
custom range. Charts show weeks, or months for periods over about four months. Every table can be exported to
CSV (opens in Excel); exports are recorded in the audit trail.

| Report | What it shows |
|---|---|
| Turnaround | Samples decided in the period: median days from receipt to the first QA decision, the time within which 90 % were decided, % decided by their due date, median by week/month and by sample type. |
| Workload | Samples received and decided per week/month, tests signed and reviewed per person, and the open samples by status right now. |
| OOS | Investigations opened per month, OOS per 100 tests signed, the tests with most OOS, and how they were concluded. |
| Control chart | An individuals chart for any number result (optionally one material): results in the order signed, the mean, control limits at ± 3 sigma (sigma from the average moving range), the specification limits and Cpk. Points beyond 3 sigma, 9 in a row on one side of the mean, or 6 in a row rising or falling are flagged. Results reported as ND / < LOQ are counted but not plotted. |

## Review, approval and certificates (Phase 4)

| Area | What it does |
|---|---|
| Test review | Every signed test goes to the **Review** page. A QA Reviewer checks the raw values and calculations, then either **accepts** it with an electronic signature ("Reviewed by") or **returns** it to the analyst with a comment. Nobody who entered or signed values on a test can review it. |
| Corrections | The analyst sees returned tests on the Testing page with the reviewer's comment. Corrections need a reason. Signing again creates a new **revision** of the results; earlier revisions stay on record. |
| Sample decision | Once every test is reviewed, QA **approves** or **rejects** the sample with an electronic signature. Open OOS investigations block the decision. A sample with non-conforming results cannot be approved. Rejecting needs a comment. Decided samples are locked. |
| Certificates | Approval issues a **Certificate of Analysis** (`CoA-YYYY-NNNN`); rejection issues a **Test Report** (`TR-YYYY-NNNN`). The PDF shows lab details, sample information, results against specification, the conclusion, and every Performed / Reviewed / Approved signature. It is stored exactly as issued with a SHA-256 fingerprint; reprints are identical, and the fingerprint is checked every time. |
| Amended certificates | If an issued certificate needs correcting, QA opens the sample and chooses **Reopen for amendment**, signs with a reason, and ticks any tests that must go back to the analyst. After correction, re-signing and review, the new decision issues an **amended certificate** (`CoA-YYYY-NNNN-A1`, `-A2`, …) that names the certificate it supersedes and the reason. The old certificate stays on record, marked *Superseded* on the Certificates page. |
| Lab details | Administrators set the laboratory name, address, contact line, certificate statement and paper size (Letter/A4) on the **Settings** page. |

## Methods, specifications and results (Phase 3)

| Area | What it does |
|---|---|
| Methods | Each method lists its **inputs** (weights, peak areas, standard purity) and **results** (assay, impurities, pH, appearance). A result can be entered by hand, or **calculated** with a formula that uses the keys of parameters above it, e.g. `area_spl / area_std * wt_std / wt_spl * purity_std`. Formulas may use `+ - * / ^`, brackets, `sqrt ln log10 exp abs min max`, and nothing else. Parameters can have several replicates; a single value (such as the standard weight) is shared by every replicate. |
| Specifications | Limits per result: between, NLT, NMT, <, >, text must match (e.g. *Conforms*), must be Pass, or report only. Limits refer to the method code and result key, so they keep working when a method gets a new version. |
| Versioning | Methods and specifications are edited as **drafts** and approved by **QA with an electronic signature**. To change an active one, create a new version; the old version is retired when the new one is activated. Each test records the exact method version it was run with. |
| Assigning tests | On the Samples page, **Tests…** lets you choose the specification (matching ones are suggested) and tick the tests to run. Tests can be assigned to a person. |
| Result entry | The **Testing** page lists open tests. As values are typed, calculations, mean, %RSD, the reported value and Pass/Fail update live. All arithmetic is exact decimal. The mean is rounded half-up to the method's decimals and the rounded value is compared with the limit (USP General Notices 7.20). A number result can have an LOD and LOQ on the method: below the LOD it is reported as **ND**, below the LOQ as **< LOQ** (e.g. < 0.05), and it passes only if every value under that bound would meet the limit; the certificate explains ND / < LOQ in a footnote. Changing a value that was already saved requires a reason, and the old and new values are audited. |
| Signing | The analyst signs the test as performed. Results are then locked and stored permanently. Sample status follows testing: Received → In Testing → Testing Complete. |
| OOS | A result outside its limit is shown in red. Signing then requires a comment and opens an **OOS investigation** (`OOS-YYYY-NNNN`). Analysts and QA record the phase, notes and root cause; QA closes it with a conclusion and an electronic signature. |

## Samples and chain of custody (Phase 2)

| Area | What it does |
|---|---|
| Sample types | Raw Material, In-Process, Finished Product, Stability, Environmental Monitoring, Water and Other are created on first start-up. Administrators and QA Reviewers can add more on the **Sample Types** page. Each type's category decides which fields the registration form asks for: for example lot and supplier for raw materials, or study and time point for stability. |
| Sample IDs | You set the format yourself from fixed text and tokens: `{SEQ:5}` (running number), `{YYYY}` `{YY}` `{MM}` `{DD}` and `{TYPE}` (type code). The running number restarts every year, month or day, or never. There is one lab-wide format, and any sample type can have its own. The editor shows example IDs as you type and explains mistakes. Default format: `AU-{YYYY}-{SEQ:5}`. |
| Registration | Register one sample or a batch (for example every container of a lot). The samples can also be received into storage in the same step. |
| Sample Intaking | The **Sample Intaking** page logs samples in through the laboratory's own form. **Design form…** (Admin / QA) adds, removes, renames and reorders fields: built-in fields fill in the sample (type, material, lot, quantity, dates, receipt) and custom fields (text, number, date, dropdown, checkbox) capture anything else. Each saved design is a new version with a reason; every sample keeps an unchangeable record of what was entered, shown on its details. Choosing a storage location receives the sample straight away. |
| Barcode scanning | With scanning switched on (Design form… → **Barcode scanning…**), a Scan box sits at the top of Sample Intaking. USB / Bluetooth scanners type the code and press Enter. **GS1** supplier codes (GS1-128, DataMatrix, QR – printed with brackets or raw with the GS separator) fill the fields you choose for each application identifier, e.g. (01) product number, (10) lot, (17) expiry; **separated values** (e.g. `RM|Lactose|L-2210`) fill a field per position; a **single value** fills one field. Several scans can fill one form. The result shows what was filled, what did not fit and what was not used; the scanned codes are kept with the sample's intake record. A test box in the settings shows how a code will be read. |
| Storage | A tree of locations (QC Lab › Fridge 1 › Shelf A) with storage conditions. The **Storage** page shows what is in each location. |
| Chain of custody | Receipt (time, condition, temperature), transfers to a location or to a person, aliquots (`<ID>-A1`, `-A2`, …), disposal and cancellation. Each step records when it happened and when it was entered, and can never be edited. Disposal requires an electronic signature. |
| Corrections | Sample details can be corrected with a reason. The old and new values go into the audit trail. Samples are never deleted. |

## Foundation (Phase 1)

| Area | What it does |
|---|---|
| Accounts | One account per person. First launch creates the Administrator. Accounts are deactivated, never deleted, so every record stays attributable. |
| Roles | **Master Administrator** (everything an Administrator does, plus reloading the app; the only role that can give or remove the Master Administrator and IT Support roles; the first-run account), **Administrator** (users, settings, and drafting methods, specifications, sample types and storage), **IT Support** (reloads the app, handles bug reports, read-only access to records and the audit trail), **Manager** (oversight: sees everything including the review queues, exports the audit trail, manages master data, inventory and SOPs; does not enter, review or approve lab data), **QA Reviewer** (reviews tests, approves samples, methods and specifications, closes OOS investigations, manages master data), **Supervisor** (Analyst plus peer review of colleagues' tests and the audit trail), **Lead Analyst** (Analyst plus peer review of colleagues' tests), **Analyst** (registers and handles samples, enters and signs results), **Chemical Hygiene Officer** (chemical inventory and safety documents; read-only otherwise), **Viewer** (read-only). Administrators cannot approve anything (segregation of duties). The last active Master Administrator cannot be removed. |
| Passwords | Argon2id hashing. Minimum length with a letter and a number. Expiry (default 90 days). Recent passwords cannot be re-used. Temporary passwords must be changed at first sign-in. |
| Lockout | 5 wrong passwords lock the account until an Administrator unlocks it. |
| Inactivity | The screen locks after 15 minutes without input and needs the same user's password to continue. A **Lock** button locks it immediately. |
| Audit trail | Every sign-in, failed attempt, lock, user change, setting change and signature is recorded with UTC time, user, old → new values and reason. Rows cannot be updated or deleted (database triggers), and each row is SHA-256 hash-chained to the previous one. **Verify integrity** detects altered, inserted or removed entries. Exports to CSV. |
| E-signatures | Signing requires re-entering the password and records the signer's printed name, date/time and meaning (Performed / Reviewed / Approved / Rejected by). Signatures cannot be changed. |
| Reason for change | All administrative changes require a written reason, which is stored in the audit trail. |

All settings above can be changed by an Administrator on the **Settings** page.

## Running the app

Requires Windows and Python 3.11 or newer ([python.org](https://www.python.org/downloads/)).

The first time, double-click **`scripts\run.bat`**. It creates a private Python
environment in `.venv` and installs the dependencies, which needs internet.

Then double-click **`scripts\create_desktop_shortcut.bat`** once. It puts an
**Aurum LIMS** icon on the desktop and in the Start menu, and from then on you
start the app from that icon. The shortcut runs the code in this folder, so it
always starts the latest version.

**Reloading without closing (Master Administrators and IT Support only):** the
**Reload** button in the top bar (or Ctrl+Shift+R) restarts Aurum LIMS with the
latest code and runs any database upgrade. You stay signed in and return to the same page. The audit trail
records it as a sign-out followed by a sign-in marked "app reload". When the
code in this folder changes while the app is open, a banner offers to reload.
**F5** just refreshes the data on the current page.

Or, from a terminal in the project folder:

```bash
py -3 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m aurum_lims
```

On first launch you'll be asked to create the Administrator account. Sign in with
it, then add the rest of the lab on the **Users** page.

## Building a stand-alone .exe

```bash
scripts\build_exe.bat
```

This produces `dist\AurumLIMS\AurumLIMS.exe`. Copy the whole `dist\AurumLIMS`
folder to any Windows PC; Python does not need to be installed there.

## Testing set-up: Master Administrator

For trying the system out, the **Master Administrator currently has every permission** of every role
(`MASTER_ADMIN_HAS_ALL_PERMISSIONS` in `security/roles.py`). Before real (GxP) use, set it to `False` so
administration and lab work are separated again; the IQ in the validation package reports a failed
segregation-of-duties check while the testing set-up is on.

## Where data is stored

| What | Location |
|---|---|
| Database | `%APPDATA%\AurumLIMS\aurum.db` |
| Logs | `%APPDATA%\AurumLIMS\logs\` |
| Backups | `%APPDATA%\AurumLIMS\backups\` (or the folder set in Settings → Backups) |

To put the data somewhere else, set the environment variable
`AURUM_LIMS_DATA_DIR` (folder) or `AURUM_LIMS_DATABASE_URL` (full database URL).

**Backups.** Aurum LIMS backs the database up by itself: when it starts, if the
last backup is older than the interval (24 hours by default), and always just
before an update changes the database. Administrators can also press
*Back up now* in Settings → Backups, choose the folder (a network drive is
safest), the interval and how many backups to keep. Every backup is checked
with SQLite's integrity check and recorded in the audit trail. The Backups tab
explains how to restore one. Lab data is never stored in the git repository.

## Development

```bash
.venv\Scripts\python -m pytest
```

The tests cover the security rules (lockout, permissions, password policy,
audit-trail tamper detection, e-signatures) and build the main screens offscreen.
GitHub Actions runs them on every push.

Project layout:

```
src/aurum_lims/
  app.py               entry point, sign-in / sign-out flow
  config.py            data folder and database location
  db/                  SQLAlchemy models and database session
  migrations/          Alembic schema migrations (run automatically at start-up)
  security/            password hashing/policy, roles and permissions
  services/            business rules: auth, users, audit, e-signatures, settings
  ui/                  PySide6 screens (dialogs, main window, pages in ui/views)
tests/                 pytest suite
scripts/               run.bat, build_exe.bat
docs/ROADMAP.md        planned phases
```

Changing the database schema: edit `db/models.py`, then add a migration in
`src/aurum_lims/migrations/versions/` (for example `alembic revision -m "add samples"`
from the project root). The app applies pending migrations at start-up.

## Compliance note

Aurum LIMS is designed to support 21 CFR Part 11, EU GMP Annex 11 and ISO/IEC
17025 data-integrity expectations (ALCOA+). Software alone does not make a lab
compliant. A regulated lab still needs to **validate** the system for its intended
use (URS, risk assessment, IQ/OQ/PQ), and to put procedures in place for account
management, backup and restore, periodic audit-trail review, and change control.
