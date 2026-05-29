"""
Shared branded email template for all Wholesale Omniverse agents.
Provides HTML header + body + footer wrapper.
Import and use build_html_email() in any agent.
"""
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
import smtplib
import os

LOGO_PATH     = Path(__file__).parent / "data" / "logo.png"
LOGO_URL      = "https://files.catbox.moe/u534iv.png"
COMPANY_NAME  = "Wholesale Omniverse LLC"
SENDER_NAME   = "Tyreese Lumiere"
SENDER_PHONE  = "207-385-4041"
SENDER_EMAIL  = "WholesaleOmniverse@gmail.com"


def build_html_email(body_html: str, sender_name: str = SENDER_NAME,
                     sender_email: str = SENDER_EMAIL,
                     phone: str = SENDER_PHONE,
                     company: str = COMPANY_NAME) -> str:
    logo_cid = "logo@wholesaleomniverse.com"
    logo_html = (
        f'<img src="cid:{logo_cid}" alt="Wholesale Omniverse" width="116" '
        f'style="display:block;width:116px;height:auto;'
        f'filter:drop-shadow(0 0 10px rgba(245,158,11,0.7));" />'
        if LOGO_PATH.exists() else
        f'<img src="{LOGO_URL}" alt="Wholesale Omniverse" width="116" '
        f'style="display:block;width:116px;height:auto;'
        f'filter:drop-shadow(0 0 10px rgba(245,158,11,0.7));" />'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#f3f4f6;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;">

  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f3f4f6;padding:32px 16px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

        <!-- HEADER -->
        <tr>
          <td style="background-color:#0f172a;border-radius:8px 8px 0 0;padding:20px 32px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="vertical-align:middle;">
                  <p style="margin:0 0 3px;font-size:10px;letter-spacing:3px;color:#94a3b8;text-transform:uppercase;">Real Estate Investment</p>
                  <p style="margin:0 0 3px;font-size:28px;font-weight:900;color:#ffffff;letter-spacing:1px;line-height:1;">
                    WHOLESALE <span style="color:#f59e0b;">OMNIVERSE</span>
                  </p>
                  <p style="margin:0;font-size:10px;letter-spacing:0.3px;">
                    <span style="color:#f59e0b;">Your </span><span style="color:#cbd5e1;">portal </span><span style="color:#f59e0b;">to </span><span style="color:#cbd5e1;">premium </span><span style="color:#f59e0b;">real </span><span style="color:#cbd5e1;">estate</span>
                  </p>
                </td>
                <td style="vertical-align:middle;text-align:right;width:90px;">
                  {logo_html}
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- ACCENT BAR -->
        <tr>
          <td style="background:linear-gradient(90deg,#f59e0b,#ef4444,#f59e0b);height:3px;font-size:0;line-height:0;">&nbsp;</td>
        </tr>

        <!-- BODY -->
        <tr>
          <td style="background-color:#ffffff;padding:40px 40px 28px;border-left:1px solid #e5e7eb;border-right:1px solid #e5e7eb;">
            <p style="margin:0 0 16px;font-size:15px;line-height:1.7;color:#374151;">
              {body_html}
            </p>
          </td>
        </tr>

        <!-- DIVIDER -->
        <tr>
          <td style="background-color:#ffffff;padding:0 40px;border-left:1px solid #e5e7eb;border-right:1px solid #e5e7eb;">
            <hr style="border:none;border-top:1px solid #e5e7eb;margin:0;">
          </td>
        </tr>

        <!-- CONTACT CARD -->
        <tr>
          <td style="background-color:#ffffff;padding:24px 40px 32px;border-left:1px solid #e5e7eb;border-right:1px solid #e5e7eb;">
            <table cellpadding="0" cellspacing="0">
              <tr>
                <td style="padding-right:16px;vertical-align:middle;">
                  <div style="width:44px;height:44px;background-color:#f59e0b;border-radius:50%;text-align:center;line-height:44px;font-size:18px;font-weight:800;color:#0f172a;">W</div>
                </td>
                <td style="vertical-align:middle;">
                  <p style="margin:0;font-size:14px;font-weight:700;color:#0f172a;">{sender_name}</p>
                  <p style="margin:2px 0 0;font-size:13px;color:#6b7280;">{company}</p>
                  <p style="margin:2px 0 0;font-size:13px;color:#6b7280;"><a href="mailto:{sender_email}" style="color:#f59e0b;text-decoration:none;">{sender_email}</a></p>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- TRUST BADGES -->
        <tr>
          <td style="background-color:#f8fafc;padding:16px 40px;border:1px solid #e5e7eb;border-top:none;text-align:center;">
            <table cellpadding="0" cellspacing="0" width="100%">
              <tr>
                <td align="center" style="padding:0 8px;font-size:12px;color:#6b7280;border-right:1px solid #d1d5db;">&#10003;&nbsp; <strong>Free for Sellers</strong></td>
                <td align="center" style="padding:0 8px;font-size:12px;color:#6b7280;border-right:1px solid #d1d5db;">&#10003;&nbsp; <strong>100+ Cash Buyers</strong></td>
                <td align="center" style="padding:0 8px;font-size:12px;color:#6b7280;border-right:1px solid #d1d5db;">&#10003;&nbsp; <strong>15+ Markets</strong></td>
                <td align="center" style="padding:0 8px;font-size:12px;color:#6b7280;">&#10003;&nbsp; <strong>No MLS Listing</strong></td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- FOOTER -->
        <tr>
          <td style="background-color:#0f172a;border-radius:0 0 8px 8px;padding:20px 40px;text-align:center;">
            <p style="margin:0 0 10px;font-size:11px;color:#475569;line-height:1.6;">
              &copy; 2026 {company}. All rights reserved.<br>
              You received this email because your property is on our cash-buyer list.<br>
              To unsubscribe, reply with &ldquo;remove&rdquo; in the subject line.
            </p>
            <a href="mailto:{sender_email}" style="color:#f59e0b;text-decoration:none;font-weight:700;font-size:13px;letter-spacing:0.3px;">{sender_email}</a>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>

</body>
</html>"""


def send_branded_email(
    to_email: str,
    subject: str,
    body_text: str,
    body_html_inner: str,
    smtp_host: str = "",
    smtp_user: str = "",
    smtp_pass: str = "",
    smtp_port: int = 587,
    sender_name: str = SENDER_NAME,
    sender_email: str = "",
    phone: str = SENDER_PHONE,
    company: str = COMPANY_NAME,
    inline_images: dict = None,
    attachments: list = None,
) -> dict:
    """
    Send a branded HTML email. Falls back to SMTP env vars if credentials not passed directly.

    inline_images: {cid: file_path} — referenced in HTML as <img src="cid:CID">
    attachments:   [file_path, ...]  — sent as downloadable file attachments
    """
    smtp_host = smtp_host or os.environ.get("SMTP_HOST", "")
    smtp_user = smtp_user or os.environ.get("SMTP_USER", "")
    smtp_pass = smtp_pass or os.environ.get("SMTP_PASS", "")
    smtp_port = smtp_port or int(os.environ.get("SMTP_PORT", 587))
    sender_email = sender_email or smtp_user

    if not all([smtp_host, smtp_user, smtp_pass]):
        return {"status": "smtp_not_configured"}

    try:
        full_html = build_html_email(
            body_html_inner,
            sender_name=sender_name,
            sender_email=sender_email,
            phone=phone,
            company=company,
        )

        outer = MIMEMultipart("related")
        alt = MIMEMultipart("alternative")
        outer.attach(alt)
        outer["Subject"] = subject
        outer["From"]    = f"{sender_name} <{smtp_user}>"
        outer["To"]      = to_email
        alt.attach(MIMEText(body_text, "plain"))
        alt.attach(MIMEText(full_html, "html"))

        if LOGO_PATH.exists():
            with open(LOGO_PATH, "rb") as f:
                logo_img = MIMEImage(f.read())
            logo_img.add_header("Content-ID", "<logo@wholesaleomniverse.com>")
            logo_img.add_header("Content-Disposition", "inline", filename="logo.png")
            outer.attach(logo_img)

        # Caller-supplied inline images (referenced via cid:<key> in the HTML body)
        for cid, file_path in (inline_images or {}).items():
            p = Path(file_path)
            if p.exists():
                with open(p, "rb") as f:
                    img = MIMEImage(f.read())
                img.add_header("Content-ID", f"<{cid}>")
                img.add_header("Content-Disposition", "inline", filename=p.name)
                outer.attach(img)

        # Caller-supplied downloadable attachments
        from email.mime.base import MIMEBase
        from email import encoders
        for file_path in (attachments or []):
            p = Path(file_path)
            if not p.exists():
                continue
            with open(p, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{p.name}"')
            outer.attach(part)

        with smtplib.SMTP(smtp_host, smtp_port) as s:
            s.ehlo(); s.starttls(); s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, to_email, outer.as_string())

        return {"status": "sent"}
    except Exception as e:
        return {"status": "failed", "error": str(e)}
