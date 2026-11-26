# Preview first without sending
# python3 emailqwen.py spain_email_leads.csv --limit 3 --dry-run

# Send live using GMAIL_USER and GMAIL_APP_PASSWORD from your environment or .env
# python3 emailqwen.py *_email_leads.csv --limit 81
python3 emaill.py ireland_email_leads.csv --limit 80 

# f=(*_email_leads.csv)
# country="${f%_email_leads.csv}"

# mv sent_email.log  "$country/"
# mv "$country"_email_leads.csv "$country/"
