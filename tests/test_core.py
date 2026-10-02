from app.evidence import coverage, needs_check, numbers_backed, said_by_client, verify_evidence
from app.transcripts import Line, chunk_ranges, parse_transcript


def test_parse_bracket_lines():
    lines = parse_transcript("[00:01:40] Meera: That's approved.\n[1:02:15] Arjun: Great.")
    assert lines[0] == Line("00:01:40", "Meera", "That's approved.")
    assert lines[1] == Line("01:02:15", "Arjun", "Great.")


def test_parse_vtt_and_srt():
    vtt = ("WEBVTT\n\n1\n00:00:01.000 --> 00:00:04.000\n<v Ravi>We approve the logo.</v>\n\n"
           "2\n00:00:05,000 --> 00:00:07,000\nArjun: Great.")
    assert [(ln.t, ln.speaker, ln.text) for ln in parse_transcript(vtt)] == [
        ("00:00:01", "Ravi", "We approve the logo."), ("00:00:05", "Arjun", "Great.")]


def test_parse_plain_notes_and_hindi_names():
    lines = parse_transcript("Meera approved product pages.\nसुरेश: हाँ ठीक है")
    assert lines[0].t is None and lines[0].speaker == ""
    assert lines[1].speaker == "सुरेश"


def test_chunk_ranges_overlap_and_cover():
    lines = [Line(None, "A", "x" * 100) for _ in range(100)]
    r = chunk_ranges(lines, max_chars=2000)
    assert r[0][0] == 0 and r[-1][1] == 100 and len(r) > 1
    for (_, b), (c, _) in zip(r, r[1:]):
        assert c < b


def test_verify_prefers_exact_line():
    lines = parse_transcript("[00:00:40] M: The template looks good, approved.\n"
                             "[00:01:55] M: I want a short video loop of the oil being poured.")
    assert verify_evidence(lines, 1, "I want a short video loop of the oil being poured") == (1, True)
    assert verify_evidence(lines, 0, "I want a short video loop of the oil being poured") == (1, True)


def test_verify_rejects_invented_quote():
    lines = parse_transcript("[00:00:40] M: Razorpay is fine.")
    assert verify_evidence(lines, 0, "Razorpay is the best, we signed yesterday")[1] is False


def test_numbers_backed():
    lines = parse_transcript("A: Yes, five product pages.\nB: Launch 27 October.")
    assert numbers_backed(lines, 1, "27 October", "")
    assert not numbers_backed(lines, 0, "7 pages", "")
    assert numbers_backed(lines, 1, "27 October 2026", "")


def test_said_by_client():
    assert said_by_client("Meera Kapoor, Rahul", "Meera") is True
    assert said_by_client("Meera", "Asha") is False
    assert said_by_client("", "Asha") is None


def test_needs_check():
    assert needs_check({"verified": False})
    assert not needs_check({"verified": True, "numbers_ok": True, "role_ok": None, "confidence": "high"})
    assert coverage("", "x") == 0
