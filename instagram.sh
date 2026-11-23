# python3 send_instagram.py *_instagram_leads.csv --limit 30
python3 instagram.py *_instagram_leads.csv 

f=(*_instagram_leads.csv)
country="${f%_instagram_leads.csv}"

mv sent_instagram.log  "$country/"
mv "$country"_instagram_leads.csv "$country/"
