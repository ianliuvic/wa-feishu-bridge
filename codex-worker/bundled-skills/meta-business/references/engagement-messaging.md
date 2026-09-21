# Engagement, messaging, and leads

## Comments

```bash
python3 scripts/comments.py list instagram MEDIA_ID
python3 scripts/comments.py list page POST_ID
python3 scripts/comments.py reply instagram COMMENT_ID --message "Reply" --dry-run
python3 scripts/comments.py hide page COMMENT_ID --hidden true --dry-run
python3 scripts/comments.py delete instagram COMMENT_ID --dry-run
```

Read the comment and surrounding thread before moderation. Require explicit authorization for replies, hiding, reactions, or deletion.

## Messaging

```bash
python3 scripts/messaging.py conversations --platform messenger
python3 scripts/messaging.py conversations --platform instagram
python3 scripts/messaging.py messages CONVERSATION_ID
python3 scripts/messaging.py send-text --recipient-id SCOPED_ID --text "Reply" --dry-run
python3 scripts/messaging.py send-media --recipient-id SCOPED_ID --type image --url "https://example.com/image.jpg" --dry-run
```

Recipient IDs are channel-scoped; do not substitute Page, Instagram, or ordinary user IDs. Observe Meta's messaging window, message-tag, utility, paid-message, consent, and regional rules. A granted permission does not make every outbound message policy-eligible.

## Leads

```bash
python3 scripts/leads.py forms
python3 scripts/leads.py leads FORM_ID --limit 100
python3 scripts/leads.py lead LEAD_ID
```

Lead responses are personal data. Minimize output, do not place them in logs or repositories, and disclose only fields required by the user's task.
