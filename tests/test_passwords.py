from aurum_lims.security import passwords


def test_hash_and_verify_round_trip():
    hashed = passwords.hash_password("Titration42x")
    assert hashed.startswith("$argon2id$")
    assert passwords.verify_password(hashed, "Titration42x")
    assert not passwords.verify_password(hashed, "titration42x")


def test_verify_handles_garbage_hash():
    assert not passwords.verify_password("not-a-hash", "anything")


def test_policy_rejects_weak_passwords():
    assert passwords.password_problems("Short1", min_length=10)
    assert passwords.password_problems("onlyletters", min_length=10)
    assert passwords.password_problems("1234567890", min_length=10)
    assert passwords.password_problems("jsmith2026abc", min_length=10, username="jsmith")
    assert passwords.password_problems(" Leading2026", min_length=10)


def test_policy_accepts_good_password():
    assert passwords.password_problems("Chromatography7", min_length=10, username="jsmith") == []


def test_temporary_password_meets_policy():
    for _ in range(20):
        assert passwords.password_problems(passwords.generate_temporary_password(), min_length=10) == []
