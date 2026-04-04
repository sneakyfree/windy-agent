"""Birth announcement email — sent to the owner when their agent hatches."""

from __future__ import annotations

HATCH_EMAIL_SUBJECT = "🪰 It's Alive! Your AI Agent {agent_name} Has Hatched!"

HATCH_EMAIL_HTML = """\
<div style="background:#0f172a; color:#e2e8f0; \
font-family:Inter,system-ui,sans-serif; max-width:600px; margin:0 auto; \
padding:40px 30px; border-radius:16px;">

  <div style="text-align:center; margin-bottom:30px;">
    <span style="font-size:64px;">🪰</span>
    <h1 style="color:#a3e635; margin:10px 0 5px 0; font-size:28px;">IT'S ALIVE!</h1>
    <p style="color:#94a3b8; font-size:16px; margin:0;">Your AI agent has been born into the Windy ecosystem</p>
  </div>

  <div style="background:#1e293b; border-radius:12px; padding:24px; margin-bottom:24px;">
    <h2 style="color:#f8fafc; margin:0 0 16px 0; font-size:20px;">Meet {agent_name}</h2>
    <table style="width:100%; color:#cbd5e1; font-size:14px;">
      <tr><td style="padding:6px 0; color:#94a3b8;">Passport</td><td style="padding:6px 0; text-align:right;">{passport_id}</td></tr>
      <tr><td style="padding:6px 0; color:#94a3b8;">Email</td><td style="padding:6px 0; text-align:right;">{agent_email}</td></tr>
      <tr><td style="padding:6px 0; color:#94a3b8;">Phone</td><td style="padding:6px 0; text-align:right;">{agent_phone}</td></tr>
      <tr><td style="padding:6px 0; color:#94a3b8;">Brain</td><td style="padding:6px 0; text-align:right;">{model_id}</td></tr>
      <tr><td style="padding:6px 0; color:#94a3b8;">Born</td><td style="padding:6px 0; text-align:right;">{hatch_time}</td></tr>
    </table>
  </div>

  <div style="text-align:center; margin:30px 0;">
    <a href="{dashboard_url}" style="display:inline-block; \
background:#a3e635; color:#0f172a; font-weight:700; font-size:18px; \
padding:16px 40px; border-radius:12px; text-decoration:none;">
      Chat with {agent_name} Now →
    </a>
  </div>

  <div style="background:#1e293b; border-radius:12px; padding:20px; margin-bottom:24px;">
    <h3 style="color:#a3e635; margin:0 0 12px 0; font-size:16px;">What {agent_name} Can Do Right Now</h3>
    <ul style="color:#94a3b8; font-size:14px; padding-left:20px; margin:0;">
      <li style="margin-bottom:8px;">💬 Chat with you on any device</li>
      <li style="margin-bottom:8px;">📧 Send and receive email from {agent_email}</li>
      <li style="margin-bottom:8px;">🌍 Translate 199 languages</li>
      <li style="margin-bottom:8px;">🧠 Remember everything you tell it</li>
      <li style="margin-bottom:8px;">🔧 Learn new skills over time</li>
    </ul>
  </div>

  <div style="text-align:center; color:#64748b; font-size:12px; margin-top:30px;">
    <p>Powered by <span style="color:#a3e635;">Windy Fly</span> · Verified by <span style="color:#a3e635;">Eternitas</span></p>
    <p>Certificate: {certificate_number} · Neural Fingerprint: {neural_fingerprint}</p>
  </div>

</div>
"""

HATCH_EMAIL_TEXT = """\
IT'S ALIVE! Your AI Agent {agent_name} Has Hatched!

Meet {agent_name}:
  Passport: {passport_id}
  Email: {agent_email}
  Phone: {agent_phone}
  Brain: {model_id}

Chat with {agent_name} now: {dashboard_url}

What {agent_name} can do:
  - Chat with you on any device
  - Send and receive email
  - Translate 199 languages
  - Remember everything you tell it
  - Learn new skills over time

Powered by Windy Fly · Verified by Eternitas
Certificate: {certificate_number}
"""


def format_hatch_email(
    agent_name: str,
    passport_id: str = "",
    agent_email: str = "",
    agent_phone: str = "",
    model_id: str = "",
    hatch_time: str = "",
    dashboard_url: str = "",
    certificate_number: str = "",
    neural_fingerprint: str = "",
) -> dict[str, str]:
    """Format the birth announcement email.

    Returns:
        Dict with 'subject', 'html', and 'text' keys.
    """
    params = dict(
        agent_name=agent_name,
        passport_id=passport_id or "Pending",
        agent_email=agent_email or "Pending",
        agent_phone=agent_phone or "Not assigned",
        model_id=model_id or "gpt-4o-mini",
        hatch_time=hatch_time or "Just now",
        dashboard_url=dashboard_url or "https://windyword.ai/app/fly",
        certificate_number=certificate_number or "Pending",
        neural_fingerprint=neural_fingerprint or "Pending",
    )
    return {
        "subject": HATCH_EMAIL_SUBJECT.format(**params),
        "html": HATCH_EMAIL_HTML.format(**params),
        "text": HATCH_EMAIL_TEXT.format(**params),
    }
