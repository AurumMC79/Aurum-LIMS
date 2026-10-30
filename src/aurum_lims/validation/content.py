"""What the validation package says: user requirements, OQ test scripts and PQ scenarios.

This is the single source for the URS, the IQ / OQ / PQ protocols and the
traceability matrix, so they always agree with each other. The tests in
``tests/test_validation.py`` check that every requirement is covered by at
least one test and that every test points at real requirements.

The test scripts describe how this version of Aurum LIMS actually behaves
(button names, default settings). The laboratory adapts them to its own SOPs
before approving the protocols.
"""

from __future__ import annotations

from dataclasses import dataclass, field

HIGH, MEDIUM, LOW = "High", "Medium", "Low"


@dataclass(frozen=True)
class Requirement:
    id: str
    area: str
    text: str
    risk: str  # GxP impact if the requirement is not met
    reference: str = ""  # regulation / guidance it supports


@dataclass(frozen=True)
class TestCase:
    id: str
    title: str
    requirements: tuple[str, ...]
    objective: str
    prerequisites: str
    steps: tuple[tuple[str, str], ...]  # (action, expected result)


@dataclass(frozen=True)
class Area:
    code: str
    name: str
    tests: list[TestCase] = field(default_factory=list)


P11 = "21 CFR 11"
ANNEX11 = "EU GMP Annex 11"
ISO = "ISO/IEC 17025"

REQUIREMENTS: list[Requirement] = [
    # ----- access and security
    Requirement("URS-SEC-01", "Security", "Every user signs in with a unique personal account (username and password); "
                "shared accounts are not needed for any function.", HIGH, f"{P11}.10(d), {P11}.300(a)"),
    Requirement("URS-SEC-02", "Security", "Passwords are stored only as salted one-way hashes (Argon2id) and are never "
                "shown or stored in readable form.", HIGH, f"{P11}.300"),
    Requirement("URS-SEC-03", "Security", "Passwords meet a configurable policy (minimum length, letters and digits), "
                "expire after a configurable number of days and recent passwords cannot be re-used.", HIGH,
                f"{P11}.300(b)"),
    Requirement("URS-SEC-04", "Security", "Accounts created or reset by an administrator must choose their own password "
                "at the next sign-in.", MEDIUM, f"{P11}.300"),
    Requirement("URS-SEC-05", "Security", "An account is locked after a configurable number of consecutive failed "
                "sign-ins and can only be unlocked by an administrator.", HIGH, f"{P11}.300(d)"),
    Requirement("URS-SEC-06", "Security", "The screen locks after a configurable period of inactivity and can only be "
                "unlocked with the signed-in user's password.", MEDIUM, f"{P11}.10(d), {ANNEX11} §12"),
    Requirement("URS-SEC-07", "Security", "Access is role-based: each role can only use the functions it is permitted, "
                "and the software enforces this independently of what the screen shows.", HIGH,
                f"{P11}.10(d), {P11}.10(g), {ANNEX11} §12"),
    Requirement("URS-SEC-08", "Security", "User accounts are deactivated, never deleted, so every record stays "
                "attributable to a person.", HIGH, f"{P11}.10(e), ALCOA+"),
    Requirement("URS-SEC-09", "Security", "Only a Master Administrator can grant or change the Master Administrator and "
                "IT Support roles, and the last active Master Administrator cannot be removed.", MEDIUM, ANNEX11 + " §12"),
    # ----- audit trail
    Requirement("URS-AUD-01", "Audit trail", "Every creation, change and deletion of GxP data, every sign-in, sign-out "
                "and failed sign-in, and every signature is recorded with user, UTC date/time, old and new values and "
                "the reason for change.", HIGH, f"{P11}.10(e), {ANNEX11} §9"),
    Requirement("URS-AUD-02", "Audit trail", "Changes to recorded data require a reason.", HIGH, f"{ANNEX11} §9"),
    Requirement("URS-AUD-03", "Audit trail", "Audit trail entries cannot be changed or deleted, and any tampering is "
                "detected by verifying the hash chain.", HIGH, f"{P11}.10(e), {P11}.10(c)"),
    Requirement("URS-AUD-04", "Audit trail", "The audit trail can be searched, filtered and exported (CSV) for review "
                "and inspection; exports are themselves audited.", MEDIUM, f"{P11}.10(b), {ANNEX11} §9"),
    # ----- electronic signatures
    Requirement("URS-ESG-01", "Electronic signatures", "Electronic signatures require re-entry of the password and "
                "record the signer's name, date/time and meaning (performed, reviewed, approved, rejected, reopened).",
                HIGH, f"{P11}.50, {P11}.200"),
    Requirement("URS-ESG-02", "Electronic signatures", "Signatures are permanently linked to the record they sign and "
                "cannot be removed or copied to another record.", HIGH, f"{P11}.70"),
    # ----- samples
    Requirement("URS-SMP-01", "Samples", "Samples are registered with a unique ID generated from a configurable "
                "format, with sample type, material, lot, priority and due date.", HIGH, ISO + " 7.4"),
    Requirement("URS-SMP-02", "Samples", "The chain of custody (registration, receipt with condition, transfers, "
                "aliquots, disposal) is recorded permanently with who, when and where.", HIGH, ISO + " 7.4"),
    Requirement("URS-SMP-03", "Samples", "Sample details can be corrected only with a reason until QA has decided "
                "the sample; decided samples are locked.", HIGH, f"{ANNEX11} §10"),
    Requirement("URS-SMP-04", "Samples", "Samples are logged in through the laboratory's own intake form, designed in "
                "the software by authorised users. Each change to the form is a new version with a reason, and each "
                "sample keeps an unchangeable record of exactly what was entered.", HIGH,
                f"{ANNEX11} §4.8, ALCOA+"),
    Requirement("URS-SMP-05", "Samples", "Barcodes (GS1 supplier codes, separated values or single values) can be "
                "scanned into the intake form according to versioned scan settings; the user sees what was filled "
                "in before submitting, and the scanned code is kept with the sample's intake record.", MEDIUM,
                f"{ANNEX11} §5, §6"),
    # ----- methods and specifications
    Requirement("URS-MTH-01", "Methods & specifications", "Test methods (parameters, units, decimals, replicates, "
                "formulas, required equipment) and specifications are version-controlled and must be approved by QA "
                "with an electronic signature before use.", HIGH, f"{ISO} 7.2, {ANNEX11} §10"),
    Requirement("URS-MTH-02", "Methods & specifications", "Each test keeps the exact method version it was run with; "
                "a new version does not change tests already run.", HIGH, ISO + " 7.2"),
    # ----- results
    Requirement("URS-RES-01", "Results", "Calculations use exact decimal arithmetic; results are averaged across "
                "replicates and rounded half-up to the method's decimals before comparison with the limit.", HIGH,
                "USP General Notices 7.20, " + ISO + " 7.11"),
    Requirement("URS-RES-02", "Results", "Each result is compared with its specification limit and flagged Pass / Fail; "
                "a failing result opens an OOS investigation.", HIGH, "FDA OOS guidance (2022)"),
    Requirement("URS-RES-03", "Results", "Results below the method's LOD are reported as ND and below the LOQ as "
                "< LOQ, and conform only if every value under that bound would meet the limit.", HIGH, ISO + " 7.8"),
    Requirement("URS-RES-04", "Results", "The analyst signs completed results; signed results are frozen, and "
                "corrections after a review return create a new revision while earlier revisions are kept.", HIGH,
                f"{P11}.10(e), ALCOA+"),
    Requirement("URS-RES-05", "Results", "A test cannot be signed until the required instruments and materials are "
                "recorded and are valid (in service, calibrated, maintained, not expired).", HIGH, ISO + " 6.4, 6.6"),
    Requirement("URS-RES-06", "Results", "Values can be imported from instrument CSV / TXT exports through approved "
                "templates; the imported file is stored unchanged with its fingerprint and the analyst checks the "
                "values before saving.", HIGH, f"{ANNEX11} §5, {P11}.10(a)"),
    # ----- OOS
    Requirement("URS-OOS-01", "OOS", "OOS investigations are tracked through their phases with root cause and "
                "conclusion, and can only be closed by QA with an electronic signature. Open investigations block "
                "the sample decision.", HIGH, "FDA OOS guidance (2022)"),
    # ----- review and certificates
    Requirement("URS-REV-01", "Review & approval", "Every signed test is reviewed by a second person who did not "
                "enter or sign its results; the reviewer accepts with a signature or returns it with a comment.",
                HIGH, f"{ISO} 7.8, {P11}.10(g)"),
    Requirement("URS-REV-02", "Review & approval", "QA approves or rejects each sample with an electronic signature; a "
                "sample with non-conforming results cannot be approved.", HIGH, ISO + " 7.8"),
    Requirement("URS-REV-03", "Review & approval", "Approval issues a Certificate of Analysis and rejection a Test "
                "Report, stored exactly as issued with a SHA-256 fingerprint that is checked whenever it is opened.",
                HIGH, f"{ISO} 7.8, {P11}.10(b)"),
    Requirement("URS-REV-04", "Review & approval", "An issued certificate can only be corrected by QA reopening the "
                "sample with a signature and reason; the amended certificate names the one it supersedes, which stays "
                "on record.", HIGH, ISO + " 7.8.8"),
    # ----- equipment and inventory
    Requirement("URS-EQP-01", "Equipment", "Instruments are registered with calibration and maintenance schedules; "
                "events are recorded permanently, failed checks take the instrument out of service and overdue or "
                "out-of-service instruments cannot be used.", HIGH, ISO + " 6.4"),
    Requirement("URS-EQP-02", "Equipment", "Preventive-maintenance plans with checklists can be defined per instrument; "
                "completed checklists are kept in the instrument history and overdue plans block use.", MEDIUM,
                ISO + " 6.4"),
    Requirement("URS-INV-01", "Inventory", "Reagent and standard lots are tracked with expiry (including use-by after "
                "opening), stock on hand and every stock movement; expired lots cannot be used.", HIGH, ISO + " 6.6"),
    Requirement("URS-INV-02", "Inventory", "Reference standard purity is taken from the lot certificate and filled into "
                "calculations automatically.", HIGH, ISO + " 6.5"),
    Requirement("URS-INV-03", "Inventory", "Suppliers are recorded with an approval status set only by QA; lots cannot "
                "be received from disqualified suppliers.", MEDIUM, ISO + " 6.6"),
    # ----- documents, reports, data protection
    Requirement("URS-DOC-01", "Documents", "Controlled SOPs and forms are available read-only to all users; only "
                "authorised users upload revisions, which are stored unchanged with a fingerprint.", MEDIUM, ISO + " 8.3"),
    Requirement("URS-RPT-01", "Reports", "Turnaround, workload, OOS trends and control charts can be produced for a "
                "chosen period and exported.", LOW, ISO + " 7.7"),
    Requirement("URS-DAT-01", "Data protection", "The database is backed up automatically (daily and before every "
                "update), each backup is checked for integrity, and backups can be restored by a documented procedure.",
                HIGH, f"{ANNEX11} §7, {P11}.10(c)"),
    Requirement("URS-DAT-02", "Data protection", "Records cannot be deleted through the software; immutable records "
                "(audit trail, signatures, custody, results, certificates, events, stock movements, imported files, "
                "intake records) "
                "are protected in the database itself.", HIGH, f"{P11}.10(c), ALCOA+"),
    Requirement("URS-DAT-03", "Data protection", "All dates and times are recorded in UTC and shown in local time.",
                MEDIUM, f"{P11}.10(e)"),
]

# ---------------------------------------------------------------------------
# OQ: function by function
# ---------------------------------------------------------------------------

S = "Signed in as"
OQ: list[Area] = [
    Area("SEC", "Access control and security", [
        TestCase("OQ-SEC-01", "Unique accounts and first sign-in", ("URS-SEC-01", "URS-SEC-04", "URS-AUD-01"),
                 "Show that each user has a personal account and must set their own password.",
                 f"{S} an administrator. A test username not yet used.",
                 (("Users → New user. Enter username oqanalyst1, a name, role Analyst and a temporary password. Save.",
                   "The account is created and listed as Active."),
                  ("Try to create a second user with the same username.",
                   "The software refuses: the username is already in use."),
                  ("Sign out and sign in as oqanalyst1 with the temporary password.",
                   "A window asks for a new password before anything else can be done."),
                  ("Choose a new password that meets the policy.", "The main window opens for oqanalyst1."),
                  ("As an administrator, open Audit Trail and filter for oqanalyst1.",
                   "USER_CREATED, LOGIN_SUCCESS and PASSWORD_CHANGED entries show user, UTC time and details."))),
        TestCase("OQ-SEC-02", "Password policy, expiry and history", ("URS-SEC-02", "URS-SEC-03"),
                 "Show that weak or re-used passwords are refused.",
                 f"{S} oqanalyst1 (from OQ-SEC-01). Settings → Security at defaults (minimum length 10, history 5).",
                 (("My Account → Change password. Enter a new password of 9 characters.",
                   "Refused: the minimum length is shown."),
                  ("Enter a 12-character password with letters only.", "Refused: letters and digits are required."),
                  ("Enter the current password as the new password.",
                   "Refused: it must differ from the last 5 passwords."),
                  ("Enter a valid new password.", "Accepted; PASSWORD_CHANGED is in the audit trail."),
                  ("Open the database file in a text viewer (optional, IT) or inspect the users table.",
                   "Only Argon2id hashes ($argon2id$...) are stored; no readable passwords."))),
        TestCase("OQ-SEC-03", "Account lockout and unlock", ("URS-SEC-05", "URS-AUD-01"),
                 "Show that repeated failed sign-ins lock the account.",
                 "Settings → Security: failed sign-ins before lockout = 5. Test account oqanalyst1.",
                 (("Sign in as oqanalyst1 with a wrong password 5 times.",
                   "The 5th attempt locks the account; further attempts, even with the right password, are refused."),
                  ("As an administrator open Users.", "oqanalyst1 shows as Locked."),
                  ("Select oqanalyst1 → Unlock.", "The account is unlocked."),
                  ("Sign in as oqanalyst1 with the correct password.", "Sign-in succeeds."),
                  ("Check the audit trail.", "LOGIN_FAILED ×5, ACCOUNT_LOCKED and USER_UNLOCKED are recorded."))),
        TestCase("OQ-SEC-04", "Inactivity lock", ("URS-SEC-06",),
                 "Show that an unattended session locks.",
                 "Settings → Security: lock screen after inactivity = 1 minute (restore afterwards).",
                 (("Sign in and leave the computer untouched for over 1 minute.",
                   "The lock screen appears; the data behind it is hidden."),
                  ("Enter a wrong password.", "Refused; SESSION_UNLOCK_FAILED is audited."),
                  ("Enter the correct password.", "The session continues where it was."),
                  ("Restore the inactivity setting to the approved value, giving a reason.",
                   "SETTING_CHANGED shows the old and new value and the reason."))),
        TestCase("OQ-SEC-05", "Role-based access", ("URS-SEC-07", "URS-SEC-09"),
                 "Show that roles limit what users can do.",
                 "Accounts with roles Analyst, Viewer, QA Reviewer and Administrator.",
                 (("Sign in as the Viewer.", "Users, Methods and Audit Trail pages are not offered; data is read-only."),
                  ("Sign in as the Analyst.", "Samples and Testing can be used; Users, Methods approval and the Review "
                   "decision are not available."),
                  ("Sign in as an Administrator (not Master) → Users → edit a user.",
                   "Master Administrator and IT Support are not offered as roles."),
                  ("Sign in as the QA Reviewer.", "Methods can be approved and samples decided; Users is not offered."),
                  ("As the only Master Administrator, try to deactivate yourself or change your own role.",
                   "Refused: the last active Master Administrator must remain."))),
        TestCase("OQ-SEC-06", "Accounts are deactivated, not deleted", ("URS-SEC-08", "URS-DAT-02"),
                 "Show that records stay attributable.",
                 f"{S} an administrator; oqanalyst1 has recorded at least one action.",
                 (("Users → select oqanalyst1.", "There is no Delete function, only Deactivate."),
                  ("Deactivate oqanalyst1 with a reason.", "The account shows Inactive; sign-in is refused."),
                  ("Open a record oqanalyst1 created (audit trail entry).", "Still shows oqanalyst1's name."),
                  ("Reactivate the account with a reason.", "USER_DEACTIVATED and USER_REACTIVATED are audited."))),
    ]),
    Area("AUD", "Audit trail and electronic signatures", [
        TestCase("OQ-AUD-01", "Audit trail content and reason for change", ("URS-AUD-01", "URS-AUD-02", "URS-DAT-03"),
                 "Show that changes are recorded with old/new values and a reason.",
                 f"{S} an analyst with a registered sample.",
                 (("Samples → select the sample → Edit. Change the lot number and save.",
                   "A reason for the change is required before saving."),
                  ("Enter a reason and save.", "The new lot number is shown."),
                  ("Audit Trail → search for the sample ID.",
                   "SAMPLE_UPDATED shows the old and new lot number, the reason, the user and the time in UTC."),
                  ("Compare the time shown on the sample screens with the UTC time in the audit trail.",
                   "Screens show local time; the audit trail stores UTC; they correspond."))),
        TestCase("OQ-AUD-02", "Audit trail cannot be altered", ("URS-AUD-03", "URS-DAT-02"),
                 "Show that the audit trail is protected and tampering is detected.",
                 "Administrator; IT support for the database step. Use a COPY of the database, never production.",
                 (("Audit Trail → Verify integrity.", "\"Audit trail intact\" with the number of entries checked."),
                  ("Look for any edit or delete function on audit entries.", "None exists."),
                  ("(IT, on a copy) Try UPDATE or DELETE on audit_entries with a SQLite tool.",
                   "Rejected by the database: records are immutable."),
                  ("(IT, on a copy) Bypass the protection, alter one entry, then run Verify integrity on that copy.",
                   "The altered entry is reported as broken."))),
        TestCase("OQ-AUD-03", "Search and export", ("URS-AUD-04",),
                 "Show that the audit trail can be reviewed and exported.",
                 "Administrator or QA.",
                 (("Audit Trail → filter by action and date range.", "Only matching entries are listed."),
                  ("Export CSV.", "A CSV file opens in Excel with all columns, including the hashes."),
                  ("Look at the newest audit entries.", "AUDIT_EXPORTED records who exported what and where."))),
        TestCase("OQ-ESG-01", "Electronic signature components", ("URS-ESG-01", "URS-ESG-02"),
                 "Show that signatures need the password and record the meaning.",
                 "An analyst with a test ready to sign.",
                 (("Testing → open the test → Save and sign as complete….",
                   "A signature window states the meaning (Performed by) and asks for the password."),
                  ("Enter a wrong password.", "Refused; ESIGN_FAILED is audited; the test is not signed."),
                  ("Enter the correct password.", "The test is signed."),
                  ("Open the sample details.", "Electronic signatures list: meaning, full name, username, date/time."),
                  ("Look for a way to remove or move the signature.", "None exists."))),
    ]),
    Area("SMP", "Samples and chain of custody", [
        TestCase("OQ-SMP-01", "Registration and unique IDs", ("URS-SMP-01",),
                 "Show that samples get unique IDs in the configured format.",
                 "Sample type with the format AU-{YYYY}-{SEQ:5} (Sample types page).",
                 (("Samples → Register samples: quantity 3, enter material, lot, priority, due date.",
                   "Three samples are created with consecutive IDs, e.g. AU-2026-00012 … 00014."),
                  ("Register one more sample.", "It receives the next number; no ID is ever repeated."))),
        TestCase("OQ-SMP-04", "Sample intaking form", ("URS-SMP-04", "URS-SMP-01", "URS-DAT-02"),
                 "Show that the intake form can be designed, is versioned, and records what was entered.",
                 "An administrator or QA user (designer) and an analyst (intake).",
                 (("Sample Intaking → Design form…. Add a custom field 'PO number' (required) and a dropdown 'Grade' "
                   "with choices; Save as new version with a description.",
                   "The page shows the new form version; INTAKE_FORM_SAVED is in the audit trail."),
                  ("As analyst, submit the form without the PO number.", "Refused: PO number must be filled in."),
                  ("Fill in all required fields, choose a storage location, submit.",
                   "A sample ID is created; the sample is Received into the chosen location."),
                  ("Open the sample's details.", "The intake record shows the form version and every value entered."),
                  ("Rename the PO field and save a new version; open the earlier sample again.",
                   "The earlier sample still shows the original label and value."))),
        TestCase("OQ-SMP-05", "Barcode scanning", ("URS-SMP-05", "URS-SMP-04"),
                 "Show that scanned barcodes fill the intake form correctly and are recorded.",
                 "Scan settings mapping GS1 (01), (10), (17) and a separated-value layout; the laboratory's barcode "
                 "scanner; test labels whose contents are known.",
                 (("Design form… → Barcode scanning…: paste the test codes into Try a code.",
                   "Each value is read correctly and shows the field it fills."),
                  ("On Sample Intaking, scan a GS1 supplier label.",
                   "Product number, lot and expiry date are filled in exactly as on the label; parts not used are "
                   "listed."),
                  ("Scan a code that contains an unknown sample type.", "It is reported as not fitting; nothing "
                   "wrong is filled in."),
                  ("Submit the sample and open its details.", "The intake record lists the scanned code(s)."))),
        TestCase("OQ-SMP-02", "Chain of custody", ("URS-SMP-02", "URS-DAT-02"),
                 "Show that every movement of a sample is recorded permanently.",
                 "A registered sample; two storage locations.",
                 (("Receive the sample: condition, temperature, location.", "Status Received; custody event recorded."),
                  ("Transfer it to the second location.", "Custody event with from/to, user and time."),
                  ("Create an aliquot.", "A new sample linked to its parent."),
                  ("Dispose of the aliquot with a method.", "Status Disposed."),
                  ("Open Sample details.", "The chain of custody lists every step; nothing can be edited or deleted."))),
        TestCase("OQ-SMP-03", "Decided samples are locked", ("URS-SMP-03",),
                 "Show that an approved sample cannot be changed.",
                 "An approved sample (e.g. from OQ-REV-02).",
                 (("Try Samples → Edit on the approved sample.", "Refused: details can no longer be changed."),
                  ("Try to assign another test.", "Refused: the sample is decided."))),
    ]),
    Area("MTH", "Methods and specifications", [
        TestCase("OQ-MTH-01", "Method approval and versions", ("URS-MTH-01", "URS-MTH-02", "URS-ESG-01"),
                 "Show that methods are approved before use and versions are kept.",
                 "Administrator (author) and QA Reviewer (approver).",
                 (("Methods → New method with inputs, a calculated result (formula), decimals and replicates. Save.",
                   "Saved as Draft v1. It cannot be assigned to a sample yet."),
                  ("As QA: Approve & activate, with signature.", "Status Active; signature recorded."),
                  ("Assign the method to a sample.", "The test shows the method v1."),
                  ("Methods → New version; change a parameter; QA approves v2.", "v2 Active, v1 Retired."),
                  ("Open the earlier test.", "It still shows and calculates with v1."))),
        TestCase("OQ-MTH-02", "Specification approval", ("URS-MTH-01",),
                 "Show that specifications need QA approval.",
                 "Administrator and QA Reviewer.",
                 (("Specifications → New specification with range, NMT and text limits. Save.", "Draft v1."),
                  ("As QA approve with signature.", "Active; can be set on samples."),
                  ("Try to edit the active specification.", "Only possible by creating a new version."))),
    ]),
    Area("RES", "Results and calculations", [
        TestCase("OQ-RES-01", "Calculation and USP rounding", ("URS-RES-01", "URS-RES-02"),
                 "Show that calculations are exact and rounding is half-up after averaging.",
                 "Active method: result = a / b × 100, 1 decimal, 2 replicates; spec 95.0 – 105.0. Prepare the "
                 "expected answers by hand first.",
                 (("Enter a1=10, b1=10.4; a2=10, b2=10.5.", "Replicates 96.15…, 95.23…; mean 95.69…; reported 95.7."),
                  ("Enter values whose mean is exactly x.x5 (e.g. replicates 95.05 and 95.05).",
                   "Reported 95.1 (half rounds up)."),
                  ("Enter values giving a mean of 94.96.", "Reported 95.0 → Pass (rounded value compared)."),
                  ("Enter values giving 94.94.", "Reported 94.9 → Fail."))),
        TestCase("OQ-RES-02", "OOS is raised", ("URS-RES-02", "URS-OOS-01"),
                 "Show that failing results open an investigation.",
                 "A test with a failing result (OQ-RES-01 last step).",
                 (("Save and sign as complete….", "A comment is required for the failing result."),
                  ("Enter a comment and sign.", "An OOS investigation (OOS-YYYY-NNNN) is opened automatically."),
                  ("Review → try to decide the sample.", "Blocked: the open OOS must be closed first."))),
        TestCase("OQ-RES-03", "ND and < LOQ", ("URS-RES-03",),
                 "Show that results below LOD / LOQ are reported correctly.",
                 "Method result with decimals 2, LOD 0.02, LOQ 0.05; specification NMT 0.10.",
                 (("Enter 0.012.", "Reported ND; Pass."),
                  ("Enter 0.03.", "Reported < 0.05; Pass."),
                  ("Enter 0.05.", "Reported 0.05 (quantified)."),
                  ("With a method LOQ of 0.20 and the same NMT 0.10 limit, enter 0.15.",
                   "Reported < 0.20; Fail (conformance cannot be shown)."),
                  ("Approve a sample with an ND result and open its certificate.",
                   "ND shown without a unit, with a footnote giving the LOD."))),
        TestCase("OQ-RES-04", "Signed results are frozen; corrections create revisions", ("URS-RES-04", "URS-REV-01"),
                 "Show that signed results cannot be silently changed.",
                 "A signed test; a reviewer.",
                 (("Try to change a value on the signed test.", "Not possible; the fields are read-only."),
                  ("As reviewer: Return for correction with a comment.", "Test status Returned; comment shown."),
                  ("As analyst: correct a value (reason required) and sign again.", "Revision 2 created."),
                  ("Open the test history.", "Revision 1 results are still on record."))),
        TestCase("OQ-RES-05", "Equipment and materials must be valid", ("URS-RES-05", "URS-EQP-01", "URS-INV-01"),
                 "Show that a test cannot be signed with invalid equipment or materials.",
                 "Method requiring an HPLC and a reference standard; one overdue instrument and one expired lot.",
                 (("Try to sign without recording equipment.", "Refused: the required equipment is listed."),
                  ("Record the overdue instrument.", "Refused: calibration overdue."),
                  ("Record the expired lot.", "Refused: lot expired."),
                  ("Record a valid instrument and lot, then sign.", "Signed."))),
        TestCase("OQ-RES-06", "Standard purity from the lot", ("URS-INV-02",),
                 "Show that standard purity is taken from the lot certificate.",
                 "Method where purity_std is filled from a reference-standard lot with purity 99.8 %.",
                 (("Open the test.", "The purity field cannot be typed in."),
                  ("Record the reference standard lot.", "Purity 99.8 is filled in and used in the calculation."))),
        TestCase("OQ-RES-07", "Instrument data import", ("URS-RES-06", "URS-DAT-02"),
                 "Show that imported values are correct and traceable.",
                 "An import template for the method; an example export file whose values were checked by hand.",
                 (("Result entry → Import from file…, choose the template and file.",
                   "The values found and any warnings are listed before anything is filled."),
                  ("Fill in values.", "The form shows the values exactly as in the file (trailing zeros kept); "
                   "nothing is saved yet."),
                  ("Save the results.", "Values saved; the imported file is listed with the test."),
                  ("Save imported file… and compare with the original (e.g. file hash).", "Identical."),
                  ("Audit Trail.", "RESULTS_IMPORTED with file name, SHA-256 and values."))),
    ]),
    Area("REV", "Review, approval and certificates", [
        TestCase("OQ-REV-01", "Second-person review", ("URS-REV-01",),
                 "Show that the person who did the work cannot review it.",
                 "A test signed by user A, who also holds a reviewer role.",
                 (("As user A: Review → try to accept the test.", "Refused: a second person must review."),
                  ("As user B (QA Reviewer): accept with signature.", "Status Reviewed; Reviewed by user B."))),
        TestCase("OQ-REV-02", "Sample decision and certificate", ("URS-REV-02", "URS-REV-03", "URS-ESG-01"),
                 "Show that QA decides samples and certificates are issued intact.",
                 "A sample with all tests reviewed and conforming; another with a failing result.",
                 (("As an Analyst, try to decide a sample.", "Not available."),
                  ("As QA: Approve and sign….", "A Certificate of Analysis CoA-YYYY-NNNN is issued."),
                  ("Open the certificate PDF.", "Lab details, results, conclusion and every signature are shown."),
                  ("For the failing sample, try Approve.", "Refused; only Reject (with comment) is possible."),
                  ("(IT, on a copy) alter the stored PDF bytes and open it.",
                   "Refused: the fingerprint does not match."))),
        TestCase("OQ-REV-03", "Amended certificate", ("URS-REV-04", "URS-ESG-01"),
                 "Show that certificates are corrected only through a controlled amendment.",
                 "An approved sample with a certificate.",
                 (("As QA: Samples → details → Reopen for amendment…, give a reason, tick one test, sign.",
                   "The test returns to the analyst; signature meaning Reopened by."),
                  ("Correct, re-sign, review and approve again.", "Certificate …-A1 is issued."),
                  ("Open the amended certificate.", "It names the certificate it supersedes and the reason."),
                  ("Certificates page.", "The original is marked Superseded and still opens, with a warning."))),
        TestCase("OQ-OOS-01", "OOS closure", ("URS-OOS-01", "URS-ESG-01"),
                 "Show that only QA closes OOS investigations.",
                 "An open OOS (from OQ-RES-02); a Supervisor and a QA Reviewer.",
                 (("As Supervisor: record investigation notes and phase.", "Saved."),
                  ("As Supervisor: try to close.", "Not permitted."),
                  ("As QA: close with conclusion, root cause and signature.", "Status Closed; OOS_CLOSED audited."))),
    ]),
    Area("EQP", "Equipment, inventory and suppliers", [
        TestCase("OQ-EQP-01", "Calibration and out of service", ("URS-EQP-01",),
                 "Show that failed checks take an instrument out of service.",
                 "An in-service instrument.",
                 (("Record calibration → Fail.", "Status Out of service; it cannot be recorded on tests."),
                  ("Record Returned to service with a note.", "Status In service."),
                  ("Record calibration → Pass.", "Next due date calculated from the interval."))),
        TestCase("OQ-EQP-02", "Maintenance plans", ("URS-EQP-02",),
                 "Show that maintenance checklists are recorded and enforced.",
                 "An instrument; an administrator and an analyst.",
                 (("Maintenance plans → New plan with three tasks, one needing a reading, first due yesterday.",
                   "The instrument shows maintenance overdue and Usable = No."),
                  ("As analyst: Perform…, mark all OK, enter the reading.", "Result Pass; next due moves on."),
                  ("Perform again with one Problem found (description required).", "Instrument Out of service."),
                  ("History → double-click the entry.", "The completed checklist is shown read-only."))),
        TestCase("OQ-INV-01", "Lots, expiry and stock", ("URS-INV-01", "URS-DAT-02"),
                 "Show that lots are tracked and expired lots are blocked.",
                 "An item with 'use within 30 days after opening'.",
                 (("Receive a lot with expiry in one year.", "Internal ID LOT-YYYY-NNNN; stock as received."),
                  ("Mark opened.", "Expiry becomes the earlier of the printed expiry and opened + 30 days."),
                  ("Stock count with a reason; then Discard with a reason.", "Each movement listed permanently."))),
        TestCase("OQ-INV-02", "Supplier approval", ("URS-INV-03",),
                 "Show that only QA approves suppliers and disqualified suppliers are blocked.",
                 "A Chemical Hygiene Officer and a QA Reviewer.",
                 (("As CHO: New supplier, try to set Approved.", "The status cannot be changed; saved as Under "
                   "evaluation."),
                  ("Receive a lot from it.", "A warning asks for confirmation."),
                  ("As QA: set Disqualified with a reason.", "Saved; SUPPLIER_UPDATED audited."),
                  ("Try to receive another lot from it.", "Refused."))),
    ]),
    Area("DAT", "Documents, reports and data protection", [
        TestCase("OQ-DOC-01", "SOP index", ("URS-DOC-01",),
                 "Show that documents are controlled.",
                 "QA user and an analyst.",
                 (("As QA: upload an SOP PDF revision.", "Listed with revision and effective date."),
                  ("As analyst: open it.", "Opens read-only; no upload or edit functions."),
                  ("Upload a new revision.", "The previous revision is superseded but kept."))),
        TestCase("OQ-RPT-01", "Reports", ("URS-RPT-01",),
                 "Show that reports agree with the records.",
                 "Several approved samples in the last 30 days.",
                 (("Reports → Last 30 days.", "Samples decided equals the number of samples approved/rejected."),
                  ("Control chart for a result.", "Every signed result in the period is listed in order."),
                  ("Export to CSV.", "The file matches the table; REPORT_EXPORTED is audited."))),
        TestCase("OQ-DAT-01", "Backups and restore", ("URS-DAT-01",),
                 "Show that backups are made, checked and restorable.",
                 "Administrator; a test computer or copy for the restore step.",
                 (("Settings → Backups → Back up now.", "A backup is listed and its integrity check passes."),
                  ("Check selected backup.", "OK – integrity check passed."),
                  ("Restart the app after more than the interval (or set 1 hour).", "An automatic backup is made."),
                  ("On a test computer, restore a backup as described on the Backups tab and run Verify integrity.",
                   "The app opens the restored data; the audit trail is intact."))),
        TestCase("OQ-DAT-02", "No deletion of records", ("URS-DAT-02",),
                 "Show that GxP records cannot be deleted.",
                 "Any user with full rights.",
                 (("Look for delete functions on samples, results, signatures, certificates, audit entries, "
                   "instrument events and lots.", "None exist; only cancel / dispose / discard / deactivate with "
                   "a reason, which are themselves recorded."),)),
    ]),
]

# ---------------------------------------------------------------------------
# PQ: the laboratory's own work, end to end
# ---------------------------------------------------------------------------

PQ: list[TestCase] = [
    TestCase("PQ-01", "Routine release testing", (
        "URS-SMP-01", "URS-SMP-02", "URS-RES-01", "URS-RES-04", "URS-RES-05", "URS-REV-01", "URS-REV-02",
        "URS-REV-03", "URS-AUD-01"),
             "Run a real release sample through the whole workflow using the laboratory's approved methods.",
             "Production accounts; approved methods and specifications for one product; calibrated equipment.",
             (("Register and receive a real sample per the sampling SOP.", "ID, receipt and custody as per SOP."),
              ("Assign the release tests; perform the analysis; enter or import results; record equipment and "
               "materials; sign.", "Results match an independent manual calculation."),
              ("Second-person review of each test.", "Reviewed by a different person."),
              ("QA decision and certificate.", "Certificate content matches the laboratory's CoA requirements."),
              ("Review the audit trail for the sample.", "Every step is attributable and complete."))),
    TestCase("PQ-02", "OOS handling in practice", ("URS-RES-02", "URS-OOS-01", "URS-REV-02"),
             "Handle an out-of-specification result according to the OOS SOP.",
             "A sample/result known or prepared to fail (e.g. spiked or training sample).",
             (("Enter and sign the failing result.", "OOS opened automatically with the result and limit."),
              ("Carry out the Phase I investigation per SOP and record it.", "Notes, root cause recorded."),
              ("QA closes the OOS and decides the sample.", "Decision consistent with the OOS conclusion."))),
    TestCase("PQ-03", "Instrument import with the laboratory's own instruments",
             ("URS-RES-06", "URS-EQP-01", "URS-EQP-02"),
             "Show the import templates work with real exports from each instrument in use.",
             "An approved import template per instrument type; real export files.",
             (("For each instrument, import a real export into a test.", "Every value matches the instrument "
               "report; warnings are understood."),
              ("Perform one scheduled maintenance plan on a real instrument.", "Checklist recorded; due date moved."))),
    TestCase("PQ-04", "Certificate correction and records review", ("URS-REV-04", "URS-AUD-04", "URS-RPT-01"),
             "Exercise the amendment and periodic review processes.",
             "An approved sample whose certificate needs a (training) correction.",
             (("Amend the certificate per the document-control SOP.", "Amended certificate supersedes the original."),
              ("Perform a periodic audit-trail review and export for the period.", "Review documented per SOP."),
              ("Produce the monthly reports (turnaround, OOS, control charts).", "Figures agree with the records."))),
    TestCase("PQ-05", "Backup, restore and business continuity", ("URS-DAT-01", "URS-DAT-02"),
             "Show that the laboratory can recover from loss of the computer.",
             "Backups stored on the approved location (e.g. network drive); a spare computer.",
             (("Install Aurum LIMS on the spare computer and restore the latest backup.", "Data complete up to the "
               "backup; Verify integrity passes."),
              ("Record the time taken and any data re-entered.", "Within the laboratory's recovery objectives."))),
]


def all_tests() -> list[TestCase]:
    return [t for area in OQ for t in area.tests] + PQ


def requirement_ids() -> list[str]:
    return [r.id for r in REQUIREMENTS]
