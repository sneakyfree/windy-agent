"""Tests for digital birth certificate generation."""

from __future__ import annotations

import os
import tempfile

from windyfly.birth_certificate import (
    BirthCertificate,
    generate_birth_certificate,
    generate_neural_art,
    generate_neural_fingerprint,
    generate_waveform_signature,
    render_birth_certificate_pdf,
    render_birth_certificate_terminal,
    save_birth_certificate,
)


class TestNeuralFingerprint:
    def test_deterministic(self):
        """Same inputs produce the same fingerprint."""
        fp1 = generate_neural_fingerprint("hi", "hello", "gpt-4o", "ET-L00001", "2026-03-28")
        fp2 = generate_neural_fingerprint("hi", "hello", "gpt-4o", "ET-L00001", "2026-03-28")
        assert fp1 == fp2

    def test_different_inputs(self):
        """Different inputs produce different fingerprints."""
        fp1 = generate_neural_fingerprint("hi", "hello", "gpt-4o", "ET-L00001", "2026-03-28")
        fp2 = generate_neural_fingerprint("hi", "hello", "gpt-4o", "ET-L00002", "2026-03-28")
        assert fp1 != fp2

    def test_format(self):
        """Fingerprint is a 64-char hex string (SHA-256)."""
        fp = generate_neural_fingerprint("a", "b", "c", "d", "e")
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)


class TestWaveformSignature:
    def test_non_empty(self):
        wave = generate_waveform_signature("Hello World, this is Windy Fly!")
        assert len(wave) > 0

    def test_empty_input(self):
        wave = generate_waveform_signature("")
        assert "~" in wave

    def test_width(self):
        wave = generate_waveform_signature("testing", width=30)
        assert len(wave) == 30

    def test_different_text_different_wave(self):
        w1 = generate_waveform_signature("aaaaaaa")
        w2 = generate_waveform_signature("zzzzzzz")
        assert w1 != w2


class TestNeuralArt:
    def test_returns_rows(self):
        art = generate_neural_art("abcdef1234567890" * 4)
        assert len(art) == 7  # default size

    def test_custom_size(self):
        art = generate_neural_art("abcdef1234567890" * 4, size=5)
        assert len(art) == 5

    def test_symmetry(self):
        """Each row should be symmetric."""
        art = generate_neural_art("abcdef1234567890abcdef1234567890")
        for row in art:
            parts = row.split(" ")
            assert parts == list(reversed(parts))


class TestGenerateBirthCertificate:
    def test_generates_all_fields(self):
        cert = generate_birth_certificate(
            agent_name="Test Fly",
            passport_id="ET-L00001",
            first_words="I am alive!",
            model_id="gpt-4o-mini",
            owner_name="Grant",
        )
        assert cert.agent_name == "Test Fly"
        assert cert.passport_id == "ET-L00001"
        assert cert.neural_fingerprint != ""
        assert cert.waveform_signature != ""
        assert cert.certificate_number.startswith("WF-")
        assert cert.first_words == "I am alive!"
        assert cert.owner_name == "Grant"

    def test_defaults_for_missing_fields(self):
        cert = generate_birth_certificate(
            agent_name="Minimal",
            passport_id="ET-L00002",
        )
        assert cert.first_words != ""
        assert cert.neural_fingerprint != ""


class TestTerminalRendering:
    def test_contains_key_info(self):
        cert = generate_birth_certificate(
            agent_name="Terminal Fly",
            passport_id="ET-L00003",
            first_words="Hello world!",
            owner_name="TestOwner",
            email_address="fly@windymail.ai",
        )
        output = render_birth_certificate_terminal(cert)
        assert "CERTIFICATE OF BIRTH" in output
        assert "Terminal Fly" in output
        assert "ET-L00003" in output
        assert "Hello world!" in output
        assert "TestOwner" in output
        assert "fly@windymail.ai" in output

    def test_truncates_long_first_words(self):
        cert = generate_birth_certificate(
            agent_name="Verbose",
            passport_id="ET-L00004",
            first_words="x" * 200,
        )
        output = render_birth_certificate_terminal(cert)
        assert "..." in output


class TestPDFRendering:
    def test_produces_valid_pdf(self):
        cert = generate_birth_certificate(
            agent_name="PDF Fly",
            passport_id="ET-L00005",
            first_words="I exist in PDF form!",
            model_id="claude-sonnet-4-6",
            owner_name="Grant",
            email_address="pdf-fly@windymail.ai",
            phone_number="+15550001234",
        )
        pdf_bytes = render_birth_certificate_pdf(cert)
        assert len(pdf_bytes) > 100
        assert pdf_bytes[:5] == b"%PDF-"

    def test_save_to_file(self):
        cert = generate_birth_certificate(
            agent_name="Save Fly",
            passport_id="ET-L00006",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_birth_certificate(cert, directory=tmpdir)
            assert os.path.exists(path)
            assert path.endswith(".pdf")
            with open(path, "rb") as f:
                assert f.read(5) == b"%PDF-"
            assert cert.pdf_path == path
