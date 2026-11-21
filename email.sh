# Preview first without sending
# python3 emailqwen.py spain_email_leads.csv --limit 3 --dry-run

# Send live using GMAIL_USER and GMAIL_APP_PASSWORD from your environment or .env
python3 emailqwen.py spain_email_leads.csv --limit 81
