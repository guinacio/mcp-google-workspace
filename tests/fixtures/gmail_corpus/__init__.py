"""Synthetic Gmail corpus for the body-cleaning tests and token measurement.

Every message is generated here, in the shape ``users.messages.get`` returns
with ``format=full``. Nothing comes from a real mailbox: senders use
example.com/.org/.net, and the text is written for the corpus. The markup
imitates patterns common in real mail (large ``<style>`` blocks, Outlook
conditional comments, pre-headers padded with zero-width characters,
tracking pixels, table layouts, quoted reply chains, entities, calendar
invitations) so the cleaner meets them in tests.

``CORPUS`` is ordered and named; ``html_heavy`` marks the newsletter-style
messages whose first page must shrink by at least 40% against the 1.0.1
cleaner (see ``tests/test_gmail_body_hygiene.py``).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

# Pre-header padding as Gmail's snippet shows it and as it appears in markup
# (about 300 invisible characters and 50 no-break spaces once decoded).
SNIPPET_PADDING = " \u034f\u200c\u00a0" * 40
ESCAPED_PADDING = " &#847;&zwnj;&#847;&zwnj;&#847;&zwnj;&nbsp;" * 50


@dataclass(frozen=True)
class CorpusMessage:
    name: str
    message: dict[str, Any]
    html_heavy: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)


def _data(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _part(mime_type: str, text: str) -> dict[str, Any]:
    return {"mimeType": mime_type, "filename": "", "body": {"size": len(text.encode()), "data": _data(text)}}


def _attachment(filename: str, mime_type: str, size: int, attachment_id: str) -> dict[str, Any]:
    return {"mimeType": mime_type, "filename": filename, "body": {"size": size, "attachmentId": attachment_id}}


def _message(
    message_id: str,
    *,
    sender: str,
    subject: str,
    snippet: str,
    parts: list[dict[str, Any]],
    mime_type: str = "multipart/alternative",
    extra_headers: dict[str, str] | None = None,
    labels: tuple[str, ...] = ("INBOX",),
) -> dict[str, Any]:
    headers = {
        "From": sender,
        "To": "Reader <reader@example.net>",
        "Subject": subject,
        "Date": "Tue, 03 Nov 2026 09:15:00 +0000",
        **(extra_headers or {}),
    }
    payload: dict[str, Any] = {"mimeType": mime_type, "headers": [{"name": k, "value": v} for k, v in headers.items()]}
    if len(parts) == 1 and not parts[0]["mimeType"].startswith("multipart/"):
        payload.update({key: value for key, value in parts[0].items() if key != "mimeType"})
        payload["mimeType"] = parts[0]["mimeType"]
    else:
        payload["parts"] = parts
        payload["body"] = {"size": 0}
    return {
        "id": message_id,
        "threadId": f"t-{message_id}",
        "labelIds": list(labels),
        "snippet": snippet,
        "internalDate": "1793696100000",
        "payload": payload,
    }


_NEWSLETTER_HEADERS = {"List-Unsubscribe": "<https://news.example.com/unsubscribe>", "Precedence": "bulk"}


def _css(prefix: str, rules: int) -> str:
    """A stylesheet of the size and shape email builders emit."""
    lines = [
        "body,table,td,a{-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%}",
        "table,td{mso-table-lspace:0pt;mso-table-rspace:0pt}",
        "img{-ms-interpolation-mode:bicubic;border:0;height:auto;line-height:100%;outline:none}",
        f".{prefix}-rollover:hover .{prefix}-rollover-first {{ max-height:0px!important; display:none!important }}",
        f".{prefix}-rollover:hover .{prefix}-rollover-second {{ max-height:none!important; display:block!important }}",
    ]
    for index in range(rules):
        lines.append(
            f".{prefix}-col-{index} {{ width:{100 + index % 500}px !important; max-width:{100 + index % 500}px "
            f"!important; padding:{index % 24}px {index % 16}px !important; font-family:Inter,Arial,sans-serif; }}"
        )
    lines.append("@media only screen and (max-width:600px){")
    for index in range(rules // 3):
        lines.append(f"  .{prefix}-m-{index} {{ width:100% !important; display:block !important; text-align:left !important }}")
    lines.append("}")
    return "\n".join(lines)


def _td(content: str, *, size: int = 16, extra: str = "") -> str:
    return (
        f'<td class="mj-column" align="left" valign="top" style="font-family:Inter,Arial,sans-serif;'
        f'font-size:{size}px;line-height:24px;color:#1a1a1a;padding:0 24px 16px 24px;{extra}">{content}</td>'
    )


def _row(content: str, **kwargs: Any) -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse;mso-table-lspace:0pt;mso-table-rspace:0pt"><tr>{_td(content, **kwargs)}</tr></table>'
    )


def _mso_button(label: str, href: str) -> str:
    return (
        f'<!--[if mso]><v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" href="{href}" '
        'style="height:44px;v-text-anchor:middle;width:220px;" arcsize="10%" fillcolor="#163300">'
        f'<center style="color:#ffffff;font-size:16px;">{label}</center></v:roundrect><![endif]-->'
        f'<!--[if !mso]><!--><a href="{href}" style="background:#163300;border-radius:4px;color:#ffffff;'
        f'display:inline-block;padding:12px 24px;text-decoration:none">{label}</a><!--<![endif]-->'
    )


def _pixel(token: str) -> str:
    return (
        f'<img src="https://t.example.com/open/{token}.gif" width="1" height="1" alt="" '
        'style="display:block;height:1px;width:1px;border:0">'
    )


def _shell(title: str, style: str, body: str, *, preheader: str = "") -> str:
    hidden = (
        '<div style="display:none;font-size:1px;color:#ffffff;line-height:1px;max-height:0px;max-width:0px;'
        f'opacity:0;overflow:hidden;mso-hide:all">{preheader}{ESCAPED_PADDING}</div>'
        if preheader
        else ""
    )
    return (
        '<!DOCTYPE html><html xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">'
        f'<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{title}</title>'
        '<!--[if mso]><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch>'
        "</o:OfficeDocumentSettings></xml><![endif]-->"
        f"<style type=\"text/css\">{style}</style></head>"
        f'<body style="margin:0;padding:0;background:#f2f4f0">{hidden}'
        '<center><table role="presentation" width="600" align="center" cellpadding="0" cellspacing="0" '
        f'style="max-width:600px;background:#ffffff">{body}</table></center></body></html>'
    )


def _footer(company: str, domain: str) -> str:
    return _row(
        f"You are receiving this email because you have an account with {company}.<br>"
        f'<a href="https://{domain}/preferences?u=8f3a">Manage your email preferences</a> &middot; '
        f'<a href="https://{domain}/unsubscribe?u=8f3a&amp;l=news">Unsubscribe</a> &middot; '
        f'<a href="https://{domain}/privacy">Privacy policy</a><br>'
        f"{company} Ltd., 1 Example Street, Example City EX1 2MP",
        size=12,
    )


def _newsletter_fintech() -> CorpusMessage:
    # Mirrors the 87 KB audit case: one HTML part, a large stylesheet, a padded
    # pre-header, Outlook buttons, a security notice and a long footer.
    sections = [
        _row("<h1 style=\"font-size:28px\">Set a secure code to be shown in all genuine Example Pay emails.</h1>", size=28),
        _row(
            "Fraudsters sometimes copy the look of our emails. From today you can choose a short phrase that we "
            "will print at the top of every email we send you, so you can tell a real one at a glance.",
        ),
        _row(_mso_button("Set my secure code", "https://pay.example.com/settings/security?src=email&amp;cid=4471")),
        _row(
            "<h2>How it works</h2><p>Open Settings, choose Security and pick a phrase only you would know. "
            "It takes about a minute. We will never ask you for this phrase, and we will never ask for your "
            "password or a verification code by email or phone.</p>"
            "<p>If an email claims to be from us but does not show your phrase, do not click any link in it. "
            "Forward it to <a href=\"mailto:phishing@pay.example.com\">phishing@pay.example.com</a> and delete it.</p>",
        ),
        _row(
            '<table role="presentation" width="100%"><tr>'
            + _td('<img src="https://cdn.example.com/img/shield.png" width="48" alt="">', extra="width:64px")
            + _td("<strong>You&#39;re getting noticed.</strong> Your account was viewed from a new device on "
                  "2 November. If this was you, there is nothing to do.")
            + "</tr></table>"
        ),
        _row(_mso_button("Review recent activity", "https://pay.example.com/activity?src=email&amp;cid=4471")),
    ]
    filler_rows = "".join(
        _row(f'<span class="ep-col-{index}" style="display:block;height:8px;line-height:8px;font-size:8px">&nbsp;</span>', size=8)
        for index in range(99)
    )
    html = _shell(
        "Example Pay",
        _css("ep", 230),
        "".join(sections) + filler_rows + _footer("Example Pay", "pay.example.com") + _pixel("ep-4471"),
        preheader="Choose a phrase we will show in every email from us.",
    )
    return CorpusMessage(
        "newsletter_fintech_security_87kb",
        _message(
            "c01",
            sender="Example Pay <noreply@pay.example.com>",
            subject="Make it easy to spot a genuine email from us",
            snippet="You&#39;re getting noticed" + SNIPPET_PADDING,
            parts=[_part("text/html", html)],
            mime_type="text/html",
            extra_headers=_NEWSLETTER_HEADERS,
        ),
        html_heavy=True,
        tags=("newsletter", "html_only", "preheader_padding", "mso"),
    )


def _newsletter_retail() -> CorpusMessage:
    products = "".join(
        f'<td style="font-size:0;padding:0"><div style="display:inline-block;width:280px;vertical-align:top;font-size:16px">'
        f'<p style="font-size:16px;margin:0">{name}&nbsp;&ndash;&nbsp;now&nbsp;{price}</p>'
        f'<a href="https://shop.example.com/p/{sku}?utm_source=email&amp;utm_campaign=autumn">Shop {name}</a></div></td>'
        for name, price, sku in [
            ("Wool scarf", "&euro;19", "ws-01"),
            ("Rain jacket", "&euro;79", "rj-02"),
            ("Leather boots", "&euro;119", "lb-03"),
            ("Knit beanie", "&euro;12", "kb-04"),
        ]
    )
    body = (
        _row("<h1>Autumn sale: up to 40% off</h1><p>Our biggest sale of the season ends Sunday at midnight.</p>")
        + f'<tr>{products}</tr>'
        + _row("Free returns within 30 days. Prices include VAT.", size=12)
        + _footer("Example Shop", "shop.example.com")
        + _pixel("shop-991")
    )
    html = _shell("Autumn sale", _css("sh", 160), body, preheader="Up to 40% off coats, boots and knitwear.")
    return CorpusMessage(
        "newsletter_retail_sale",
        _message(
            "c02",
            sender="Example Shop <news@shop.example.com>",
            subject="Autumn sale: up to 40% off",
            snippet="Up to 40% off coats, boots and knitwear." + SNIPPET_PADDING,
            parts=[_part("text/plain", "This email contains HTML content."), _part("text/html", html)],
            extra_headers=_NEWSLETTER_HEADERS,
        ),
        html_heavy=True,
        tags=("newsletter", "placeholder_text_part", "font_size_zero_columns"),
    )


def _newsletter_travel() -> CorpusMessage:
    deals = "".join(
        _row(
            f"<h3>{city}</h3><p>Return flights from {price}, travel between January and March.</p>"
            + _mso_button(f"See {city} deals", f"https://travel.example.org/deals/{slug}?ref=nl")
        )
        for city, price, slug in [("Lisbon", "&pound;89", "lis"), ("Vienna", "&pound;104", "vie"), ("Kyoto", "&pound;612", "kix")]
    )
    body = (
        _row("<h1>Winter escapes from &pound;89</h1>")
        + '<!--[if mso]><table role="presentation" width="600"><tr><td>Outlook-only banner text</td></tr></table><![endif]-->'
        + f'<tr><td><div style="mso-hide:all">{_row("Swipe to see more destinations.")}</div></td></tr>'
        + deals
        + _footer("Example Travel", "travel.example.org")
        + _pixel("tr-17")
    )
    html = _shell("Winter escapes", _css("tr", 200), body, preheader="Lisbon, Vienna and Kyoto this winter.")
    return CorpusMessage(
        "newsletter_travel_mso",
        _message(
            "c03",
            sender="Example Travel <hello@travel.example.org>",
            subject="Winter escapes from £89",
            snippet="Lisbon, Vienna and Kyoto this winter." + SNIPPET_PADDING,
            parts=[_part("text/html", html)],
            mime_type="text/html",
            extra_headers=_NEWSLETTER_HEADERS,
        ),
        html_heavy=True,
        tags=("newsletter", "html_only", "mso", "mso_hide_visible_content"),
    )


def _newsletter_software() -> CorpusMessage:
    body = (
        _row("<h1>Release 4.2 is out</h1><p>This release adds offline mode and speeds up search.</p>")
        + _row(
            "<p>Upgrade with:</p><pre style=\"font-family:monospace\">example-cli upgrade --channel stable\n"
            "example-cli doctor</pre><p>Read the <a href=\"https://docs.example.com/release-notes/4.2\">"
            "release notes</a> for the full list of changes.</p>"
        )
        + '<script type="application/ld+json">{"@context":"http://schema.org","@type":"EmailMessage",'
        '"potentialAction":{"@type":"ViewAction","url":"https://docs.example.com/release-notes/4.2"}}</script>'
        + "<noscript><p>Your client does not run scripts.</p></noscript>"
        + "<template><p>Template text that is never rendered.</p></template>"
        + _footer("Example Software", "example.com")
    )
    html = _shell("Release 4.2", _css("sw", 140), body, preheader="Offline mode and faster search.")
    return CorpusMessage(
        "newsletter_software_release",
        _message(
            "c04",
            sender="Example Software <updates@example.com>",
            subject="Release 4.2 is out",
            snippet="Offline mode and faster search." + SNIPPET_PADDING,
            parts=[_part("text/html", html)],
            mime_type="text/html",
            extra_headers=_NEWSLETTER_HEADERS,
        ),
        html_heavy=True,
        tags=("newsletter", "script", "noscript", "template", "pre"),
    )


def _newsletter_news_digest() -> CorpusMessage:
    stories = "".join(
        _row(
            f'<a href="https://news.example.net/story/{index}?utm_medium=email&amp;utm_content=h{index}">'
            f"<strong>{headline}</strong></a><br>{summary} "
            f'<a href="https://news.example.net/story/{index}?utm_medium=email&amp;utm_content=r{index}">Read more</a>'
            '<span aria-hidden="true" style="font-size:1px;line-height:1px">&#8199;&#65279;&#847;</span>'
            '<div hidden>Hidden duplicate for a mobile layout.</div>'
        )
        for index, (headline, summary) in enumerate(
            [
                ("City council approves new cycle lanes", "The plan adds 40 km of protected lanes by 2028."),
                ("Library extends opening hours", "Branches will open until 9 pm on weekdays from December."),
                ("Local bakery wins regional award", "The jury praised its sourdough and seasonal pastries."),
                ("Rail works close the north line for two weekends", "Replacement buses will run every ten minutes."),
                ("School music festival returns", "Twelve schools will perform at the town hall in spring."),
            ]
        )
    )
    body = _row("<h1>Your morning briefing</h1>") + stories + _footer("Example News", "news.example.net")
    html = _shell("Morning briefing", _css("nw", 150), body, preheader="Cycle lanes, longer library hours and more.")
    return CorpusMessage(
        "newsletter_news_digest_links",
        _message(
            "c05",
            sender="Example News <briefing@news.example.net>",
            subject="Your morning briefing",
            snippet="Cycle lanes, longer library hours and more." + SNIPPET_PADDING,
            parts=[_part("text/html", html)],
            mime_type="text/html",
            extra_headers=_NEWSLETTER_HEADERS,
        ),
        html_heavy=True,
        tags=("newsletter", "repeated_links", "aria_hidden", "hidden_attribute"),
    )


def _newsletter_event() -> CorpusMessage:
    body = (
        '<tr><td><span style="visibility:hidden;opacity:0;color:transparent;height:0;width:0">'
        "Save your seat for the community meetup.</span></td></tr>"
        + _row(
            '<div class="ev-rollover"><img class="ev-rollover-first" src="https://cdn.example.org/a.png" alt="">'
            '<div class="ev-rollover-second" style="max-height:0;overflow:hidden">Hover text for desktop clients.</div></div>'
        )
        + _row("<h1>Community meetup, 14 November</h1><p>Talks, demos and pizza at the Example Hall, doors 18:00.</p>")
        + _row(_mso_button("Register for free", "https://events.example.org/meetup-14-nov?src=email"))
        + _footer("Example Events", "events.example.org")
    )
    style = _css("ev", 120) + "</style><style>" + _css("ev2", 60)
    html = _shell("Community meetup", style, body)
    return CorpusMessage(
        "newsletter_event_hidden_variants",
        _message(
            "c06",
            sender="Example Events <events@example.org>",
            subject="Community meetup, 14 November",
            snippet="Save your seat for the community meetup." + SNIPPET_PADDING,
            parts=[_part("text/html", html)],
            mime_type="text/html",
            extra_headers=_NEWSLETTER_HEADERS,
        ),
        html_heavy=True,
        tags=("newsletter", "visibility_hidden", "max_height_zero", "two_style_blocks"),
    )


def _newsletter_nonprofit() -> CorpusMessage:
    body = (
        _row(
            "<h1>Winter appeal &mdash; every coat counts</h1>"
            "<p>Last year we handed out 2&#8239;400 coats. This year we&rsquo;d like to reach 3&#8239;000, "
            "and we can&#39;t do it without you&hellip;</p>"
            "<p>&ldquo;It made the difference between staying in and going out,&rdquo; one volunteer told us.</p>"
            "<p>Donate a coat at any partner shop, or give online in two minutes. Your gift is "
            "tax&shy;deductible.</p>"
        )
        + _row(_mso_button("Give online", "https://give.example.org/winter?src=mail"))
        + _row("&#10240;&#10240;&#10240;" + "&zwnj;&nbsp;" * 60, size=1)
        + _footer("Example Aid", "give.example.org")
        + _pixel("aid-2")
    )
    html = _shell("Winter appeal", _css("na", 130), body, preheader="Help us reach 3,000 coats this winter.")
    return CorpusMessage(
        "newsletter_nonprofit_entities",
        _message(
            "c07",
            sender="Example Aid <hello@give.example.org>",
            subject="Winter appeal — every coat counts",
            snippet="Help us reach 3,000 coats this winter. We&#39;d like to reach 3 000" + SNIPPET_PADDING,
            parts=[_part("text/html", html)],
            mime_type="text/html",
            extra_headers=_NEWSLETTER_HEADERS,
        ),
        html_heavy=True,
        tags=("newsletter", "entities", "soft_hyphen", "braille_blank_padding"),
    )


def _receipt() -> CorpusMessage:
    items = "".join(
        f"<tr><td>{name}</td><td>{qty}</td><td>&euro;{price}</td></tr>"
        for name, qty, price in [("USB-C cable 2 m", 2, "18.00"), ("Laptop stand", 1, "39.90"), ("Shipping", 1, "4.90")]
    )
    body = (
        _row("<h1>Thanks for your order</h1><p>Order EX-20931 was placed on 2 November 2026.</p>")
        + _row(f'<table role="presentation"><tr><th>Item</th><th>Qty</th><th>Price</th></tr>{items}'
               "<tr><td>Total</td><td></td><td>&euro;80.80</td></tr></table>")
        + _row('<a href="https://orders.example.com/EX-20931?k=aa1">Track your parcel</a>')
        + _footer("Example Store", "orders.example.com")
        + _pixel("ord-20931")
    )
    html = _shell("Order EX-20931", _css("rc", 90), body, preheader="Your order EX-20931 is confirmed.")
    return CorpusMessage(
        "receipt_order_table",
        _message(
            "c08",
            sender="Example Store <orders@example.com>",
            subject="Your order EX-20931",
            snippet="Your order EX-20931 is confirmed." + SNIPPET_PADDING,
            parts=[_part("text/html", html)],
            mime_type="text/html",
        ),
        html_heavy=True,
        tags=("transactional", "table_cells", "tracking_pixel"),
    )


def _reply_chain_plain() -> CorpusMessage:
    text = (
        "Thursday at 10 works for me. I'll book the small meeting room.\n\n"
        "Ana\n\n"
        "On Tue, 3 Nov 2026 at 08:02, Ben Example <ben@example.com> wrote:\n"
        "> Could we meet this week to go through the budget draft?\n"
        "> Thursday or Friday morning would suit me.\n>\n"
        "> On Mon, 2 Nov 2026 at 17:40, Ana Example <ana@example.org> wrote:\n"
        ">> I've shared the budget draft in the team folder.\n"
    )
    html = "<div dir=\"ltr\">" + text.split("On Tue")[0].replace("\n", "<br>") + "</div>"
    return CorpusMessage(
        "reply_chain_plain_quotes",
        _message(
            "c09",
            sender="Ana Example <ana@example.org>",
            subject="Re: Budget draft",
            snippet="Thursday at 10 works for me. I&#39;ll book the small meeting room. Ana On Tue, 3 Nov 2026",
            parts=[_part("text/plain", text), _part("text/html", html)],
        ),
        tags=("reply_chain", "text_part"),
    )


def _reply_chain_outlook() -> CorpusMessage:
    text = (
        "Hi Ben,\r\n\r\nThe signed contract is attached to the original email. Let me know if anything is missing.\r\n\r\n"
        "Best regards,\r\nCarla\r\n-- \r\nCarla Example | Operations | Example Ltd.\r\n+44 20 0000 0000\r\n\r\n"
        "From: Ben Example <ben@example.com>\r\nSent: Monday, November 2, 2026 4:11 PM\r\n"
        "To: Carla Example <carla@example.com>\r\nSubject: Contract\r\n\r\nCould you send the signed contract?\r\n"
    )
    return CorpusMessage(
        "reply_chain_outlook_signature",
        _message(
            "c10",
            sender="Carla Example <carla@example.com>",
            subject="RE: Contract",
            snippet="Hi Ben, The signed contract is attached to the original email. Let me know if anything is missing.",
            parts=[_part("text/plain", text)],
            mime_type="text/plain",
        ),
        tags=("reply_chain", "outlook_header", "signature"),
    )


def _reply_chain_html_only() -> CorpusMessage:
    html = (
        '<div dir="ltr">Sounds good, see you there.<div><br></div><div>Dan</div></div><br>'
        '<div class="gmail_quote"><div dir="ltr" class="gmail_attr">On Mon, 2 Nov 2026 at 12:00, '
        'Eva Example &lt;<a href="mailto:eva@example.net">eva@example.net</a>&gt; wrote:<br></div>'
        '<blockquote class="gmail_quote" style="margin:0px 0px 0px 0.8ex;border-left:1px solid rgb(204,204,204)">'
        "Lunch on Wednesday at the usual place?</blockquote></div>"
    )
    return CorpusMessage(
        "reply_chain_html_gmail_quote",
        _message(
            "c11",
            sender="Dan Example <dan@example.net>",
            subject="Re: Lunch",
            snippet="Sounds good, see you there. Dan On Mon, 2 Nov 2026 at 12:00, Eva Example &lt;eva@example.net&gt; wrote:",
            parts=[_part("text/html", html)],
            mime_type="text/html",
        ),
        tags=("reply_chain", "html_only", "gmail_quote"),
    )


def _reply_chain_mobile() -> CorpusMessage:
    text = (
        "Yes, please go ahead and order the replacement part.\n\nSent from my phone\n\n"
        "On 2 Nov 2026, at 19:20, Finn Example <finn@example.org> wrote:\n\n"
        "> The printer needs a new fuser unit. Shall I order one?\n"
    )
    return CorpusMessage(
        "reply_chain_mobile",
        _message(
            "c12",
            sender="Gina Example <gina@example.org>",
            subject="Re: Printer",
            snippet="Yes, please go ahead and order the replacement part. Sent from my phone On 2 Nov 2026",
            parts=[_part("text/plain", text)],
            mime_type="text/plain",
        ),
        tags=("reply_chain", "mobile_footer"),
    )


def _plain_short() -> CorpusMessage:
    text = "Hi,\n\nCan we move tomorrow's call to 3 pm? Something came up in the morning.\n\nThanks,\nHugo\n"
    return CorpusMessage(
        "plain_text_short",
        _message(
            "c13",
            sender="Hugo Example <hugo@example.com>",
            subject="Call tomorrow",
            snippet="Hi, Can we move tomorrow&#39;s call to 3 pm? Something came up in the morning. Thanks, Hugo",
            parts=[_part("text/plain", text)],
            mime_type="text/plain",
        ),
        tags=("plain_text",),
    )


def _plain_long() -> CorpusMessage:
    topics = ["planning", "hiring", "budget", "support", "security", "infrastructure", "documentation", "training"]
    paragraphs = []
    for index in range(60):
        topic = topics[index % len(topics)]
        paragraphs.append(
            f"{index + 1}. On {topic}, the team reviewed the open items from last quarter and agreed on next steps. "
            f"The main risk is that the {topic} work depends on two other groups, so we will confirm dates with "
            f"them before the end of the month. Progress will be reported in the weekly update, and anything "
            f"blocked for more than a week will be raised at the steering meeting."
        )
    text = "Hello all,\n\nHere is the quarterly report in full.\n\n" + "\n\n".join(paragraphs) + "\n\nRegards,\nIris\n"
    return CorpusMessage(
        "plain_text_long_report",
        _message(
            "c14",
            sender="Iris Example <iris@example.com>",
            subject="Quarterly report",
            snippet="Hello all, Here is the quarterly report in full. 1. On planning, the team reviewed the open items",
            parts=[_part("text/plain", text)],
            mime_type="text/plain",
        ),
        tags=("plain_text", "long", "paging"),
    )


def _html_entities() -> CorpusMessage:
    html = (
        "<p>Hi Jo,</p><p>Here&#39;s the &quot;final&quot; agenda for Friday&nbsp;&ndash; R&amp;D first, then "
        "sales&nbsp;&amp;&nbsp;marketing.</p><p>Cheers,<br>Kai</p>"
    )
    return CorpusMessage(
        "html_only_entities",
        _message(
            "c15",
            sender="Kai Example <kai@example.net>",
            subject="Agenda",
            snippet="Hi Jo, Here&#39;s the &quot;final&quot; agenda for Friday – R&amp;D first, then sales &amp; marketing.",
            parts=[_part("text/html", html)],
            mime_type="text/html",
        ),
        tags=("html_only", "entities"),
    )


def _forwarded() -> CorpusMessage:
    text = (
        "FYI, see the supplier's note below.\n\n"
        "---------- Forwarded message ---------\n"
        "From: Supplier Example <sales@supplier.example.com>\n"
        "Date: Mon, 2 Nov 2026 at 09:30\n"
        "Subject: Price update\n"
        "To: <purchasing@example.com>\n\n"
        "Dear customer,\n\nFrom 1 December our prices rise by 3%. The attached list shows the new prices.\n\n"
        "Kind regards,\nSupplier Example\n"
    )
    return CorpusMessage(
        "forwarded_with_attachment",
        _message(
            "c16",
            sender="Lea Example <lea@example.com>",
            subject="Fwd: Price update",
            snippet="FYI, see the supplier&#39;s note below. ---------- Forwarded message --------- From: Supplier Example",
            parts=[
                {"mimeType": "multipart/alternative", "filename": "", "body": {"size": 0}, "parts": [_part("text/plain", text)]},
                _attachment("price-list-2027.pdf", "application/pdf", 48_211, "att-c16-1"),
            ],
            mime_type="multipart/mixed",
        ),
        tags=("forwarded", "attachment"),
    )


def _portuguese() -> CorpusMessage:
    html = (
        "<p>Olá, Mário,</p><p>A reunião de condomínio foi remarcada para quinta-feira, às 19h30, no salão "
        "de festas. A pauta inclui a manutenção do elevador e a previsão orçamentária de 2027.</p>"
        "<p>Pedimos a gentileza de confirmar presença até terça-feira.</p><p>Atenciosamente,<br>Administração</p>"
    )
    text = (
        "Olá, Mário,\n\nA reunião de condomínio foi remarcada para quinta-feira, às 19h30, no salão de festas. "
        "A pauta inclui a manutenção do elevador e a previsão orçamentária de 2027.\n\n"
        "Pedimos a gentileza de confirmar presença até terça-feira.\n\nAtenciosamente,\nAdministração\n"
    )
    return CorpusMessage(
        "portuguese_notice",
        _message(
            "c17",
            sender="Administração <adm@condominio.example.com>",
            subject="Reunião remarcada",
            snippet="Olá, Mário, A reunião de condomínio foi remarcada para quinta-feira, às 19h30, no salão de festas.",
            parts=[_part("text/plain", text), _part("text/html", html)],
        ),
        tags=("non_ascii", "portuguese"),
    )


def _german() -> CorpusMessage:
    text = (
        "Sehr geehrte Frau Müller,\n\nvielen Dank für Ihre Anfrage. Die Lieferung der Büromöbel erfolgt "
        "voraussichtlich in der 47. Kalenderwoche. Für Rückfragen stehe ich Ihnen gern zur Verfügung.\n\n"
        "Bitte beachten Sie: „Sonderanfertigungen“ sind vom Umtausch ausgeschlossen.\n\n"
        "Mit freundlichen Grüßen\nJörg Weiß\n-- \nBeispiel GmbH · Straße 1 · 12345 Beispielstadt\n"
    )
    return CorpusMessage(
        "german_text_only",
        _message(
            "c18",
            sender="Jörg Weiß <joerg@beispiel.example.com>",
            subject="Ihre Anfrage zur Lieferung",
            snippet="Sehr geehrte Frau Müller, vielen Dank für Ihre Anfrage. Die Lieferung der Büromöbel erfolgt",
            parts=[_part("text/plain", text)],
            mime_type="text/plain",
        ),
        tags=("non_ascii", "german", "signature"),
    )


def _calendar_invite() -> CorpusMessage:
    ics = (
        "BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nPRODID:-//Example//Calendar//EN\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
        "DTSTART:20261110T140000Z\r\nDTEND:20261110T143000Z\r\nSUMMARY:Design review\r\n"
        "ORGANIZER;CN=Mia Example:mailto:mia@example.com\r\nUID:design-review-20261110@example.com\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    text = (
        "Mia Example has invited you to Design review\n\nTuesday 10 Nov 2026 14:00 – 14:30 (UTC)\n\n"
        "Joining info: https://meet.example.com/abc-defg-hij?pli=1\n\nReply for reader@example.net: Yes / No / Maybe\n\n"
        "Invitation from Example Calendar. You are receiving this email because you are an attendee of the event.\n"
    )
    html = (
        '<table role="presentation"><tr><td><h2 style="font-size:20px">Design review</h2></td></tr>'
        "<tr><td>Tuesday 10 Nov 2026 &#8901; 14:00 &ndash; 14:30 (UTC)</td></tr>"
        '<tr><td><a href="https://meet.example.com/abc-defg-hij?pli=1">Join with Example Meet</a></td></tr>'
        '<tr><td><a href="https://calendar.example.com/rsvp?st=yes">Yes</a> <a href="https://calendar.example.com/rsvp?st=no">No</a>'
        ' <a href="https://calendar.example.com/rsvp?st=maybe">Maybe</a></td></tr></table>'
    )
    return CorpusMessage(
        "calendar_invite",
        _message(
            "c19",
            sender="Mia Example <mia@example.com>",
            subject="Invitation: Design review @ Tue 10 Nov 2026 14:00 - 14:30 (UTC)",
            snippet="Mia Example has invited you to Design review Tuesday 10 Nov 2026 14:00 – 14:30 (UTC)",
            parts=[
                {
                    "mimeType": "multipart/alternative",
                    "filename": "",
                    "body": {"size": 0},
                    "parts": [_part("text/plain", text), _part("text/html", html), _part("text/calendar", ics)],
                },
                _attachment("invite.ics", "application/ics", len(ics), "att-c19-1"),
            ],
            mime_type="multipart/mixed",
        ),
        tags=("calendar", "attachment"),
    )


def _attachments() -> CorpusMessage:
    text = "Hello,\n\nPlease find the October invoice and the usage report attached.\n\nBest,\nNoah\n"
    return CorpusMessage(
        "plain_text_two_attachments",
        _message(
            "c20",
            sender="Noah Example <billing@example.net>",
            subject="Invoice October 2026",
            snippet="Hello, Please find the October invoice and the usage report attached. Best, Noah",
            parts=[
                _part("text/plain", text),
                _attachment("invoice-2026-10.pdf", "application/pdf", 91_034, "att-c20-1"),
                _attachment("usage-2026-10.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 23_502, "att-c20-2"),
            ],
            mime_type="multipart/mixed",
        ),
        tags=("plain_text", "attachment"),
    )


CORPUS: tuple[CorpusMessage, ...] = (
    _newsletter_fintech(),
    _newsletter_retail(),
    _newsletter_travel(),
    _newsletter_software(),
    _newsletter_news_digest(),
    _newsletter_event(),
    _newsletter_nonprofit(),
    _receipt(),
    _reply_chain_plain(),
    _reply_chain_outlook(),
    _reply_chain_html_only(),
    _reply_chain_mobile(),
    _plain_short(),
    _plain_long(),
    _html_entities(),
    _forwarded(),
    _portuguese(),
    _german(),
    _calendar_invite(),
    _attachments(),
)
