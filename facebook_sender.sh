# Uses qwen2.5:7b by default (or pass --model qwen3:4b for faster CPU generation)
# python3 send_facebook.py spain_facebook_leads.csv --use-chrome --limit 2 --dry-run
python3 send_facebook.py spain_facebook_leads.csv --use-chrome --limit 15 --min-delay 3 --max-delay 6
