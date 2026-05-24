import json
import re

state_file = 'companies_state.json'

with open(state_file, 'r') as f:
    companies = json.load(f)

corrupted_count = 0
cleaned_count = 0

for company in companies:
    url = company.get('career_page_url')
    if not url:
        continue
        
    url = url.strip()
    
    if 'Warning: True color' in url:
        company['career_page_url'] = None
        company['status'] = 'failed'
        corrupted_count += 1
        continue

    # Clean up trailing punctuation
    new_url = re.sub(r'[:.,\s]+$', '', url)
    if new_url != url:
        company['career_page_url'] = new_url
        cleaned_count += 1

with open(state_file, 'w') as f:
    json.dump(companies, f, indent=2)

print(f"Cleaned up {corrupted_count} corrupted entries.")
print(f"Fixed {cleaned_count} URLs with trailing punctuation.")
