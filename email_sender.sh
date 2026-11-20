# 1. Preview first without sending anything
python3 send_emails.py spain_email_leads.csv --dry-run --limit 3

# 2. Send up to 30 emails safely
python3 send_emails.py spain_email_leads.csv --limit 30
